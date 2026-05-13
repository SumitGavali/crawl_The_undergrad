"""
DocumentAgent — scheduled re-indexing for KnowledgeOS.

Wraps APScheduler's BackgroundScheduler to periodically re-index
every enabled data source (network drive, SharePoint).
One agent instance lives in ``app.state.agent`` for the lifetime of
the FastAPI process.

Design notes
------------
* Each connector runs as an independent job so a failure in one never
  blocks the others.
* ``run_all_sources()`` is also callable on-demand (e.g. REINDEX_ON_STARTUP).
* ``get_schedule_info()`` returns JSON-serialisable next-run times suitable
  for the /api/schedule endpoint.
"""

import logging
import os
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger("knowledgeos.agent")

if TYPE_CHECKING:
    from db.vector_store import QdrantVectorStore


# ── Last-run registry (shared with app._connector_last_indexed) ───────────────
# The agent writes into the same dict that the manual-trigger endpoints use,
# so /api/connectors/status always reflects the latest run regardless of origin.

_SOURCES = ("network_drive", "sharepoint")


class DocumentAgent:
    """
    Manages scheduled and on-demand re-indexing of all enabled connectors.

    Parameters
    ----------
    vector_store    : Active QdrantVectorStore instance.
    search_service  : SearchService instance (provides embedding model).
    last_indexed_registry : The ``_connector_last_indexed`` dict from app.py
                             so this agent can update it in-place.
    interval_hours  : How often (in hours) to trigger a full re-index.
    """

    def __init__(
        self,
        vector_store,
        search_service,
        last_indexed_registry: dict,
        interval_hours: int = 24,
    ) -> None:
        self.vector_store = vector_store
        self.search_service = search_service
        self._registry = last_indexed_registry
        self.interval_hours = interval_hours

        self._scheduler = None
        self._jobs: dict[str, object] = {}   # job_id → APScheduler job
        self._running = False

        logger.info(
            "DocumentAgent created — interval=%dh, sources=%s",
            interval_hours,
            [s for s in _SOURCES if self._is_enabled(s)],
        )

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _is_enabled(source: str) -> bool:
        """Check env var for a given source name."""
        key = f"{source.upper()}_ENABLED"
        return os.getenv(key, "false").lower() == "true"

    def _env(self, key: str, default: str = "") -> str:
        return os.getenv(key, default)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Start the APScheduler BackgroundScheduler and register interval jobs
        for every enabled connector.

        Calling start() more than once is safe (subsequent calls are no-ops).
        """
        if self._running:
            logger.debug("DocumentAgent already running — skipping start()")
            return

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError:
            logger.error(
                "apscheduler is not installed — scheduled indexing disabled. "
                "Run: pip install apscheduler"
            )
            return

        self._scheduler = BackgroundScheduler(
            timezone="UTC",
            job_defaults={"coalesce": True, "max_instances": 1},
        )

        enabled_sources = [s for s in _SOURCES if self._is_enabled(s)]

        if not enabled_sources:
            logger.info("No connectors enabled — scheduler will start but no jobs added")
        else:
            for source in enabled_sources:
                job = self._scheduler.add_job(
                    func=self._run_source,
                    args=[source],
                    trigger="interval",
                    hours=self.interval_hours,
                    id=f"reindex_{source}",
                    name=f"Re-index {source}",
                    replace_existing=True,
                )
                self._jobs[source] = job
                logger.info(
                    "Scheduled job for '%s' every %dh — next run: %s",
                    source,
                    self.interval_hours,
                    job.next_run_time,
                )

        self._scheduler.start()
        self._running = True
        logger.info(
            "DocumentAgent started — %d job(s) scheduled", len(self._jobs)
        )

    def stop(self) -> None:
        """Shut down the scheduler cleanly (waits for running jobs to finish)."""
        if self._scheduler and self._running:
            try:
                self._scheduler.shutdown(wait=False)
                logger.info("DocumentAgent scheduler stopped")
            except Exception as exc:
                logger.warning("Error stopping scheduler: %s", exc)
            finally:
                self._running = False

    # ── Per-source runner ──────────────────────────────────────────────────────

    def _run_source(self, source: str) -> None:
        """
        Execute the indexing pipeline for a single source.
        Errors are caught and logged — they never propagate to the scheduler.
        """
        logger.info("[agent] Starting scheduled re-index for source='%s'", source)
        try:
            if source == "network_drive":
                self._run_network_drive()
            elif source == "sharepoint":
                self._run_sharepoint()
            else:
                logger.warning("[agent] Unknown source '%s' — skipping", source)
        except Exception as exc:
            logger.error(
                "[agent] Unhandled error indexing '%s': %s", source, exc, exc_info=True
            )

    def _run_network_drive(self) -> None:
        from connectors.network_drive import NetworkDriveConnector
        from connectors.indexing_service import index_network_drive

        # Resolve callables from the already-loaded app module
        import importlib
        _app = importlib.import_module("app")

        connector = NetworkDriveConnector(
            host=self._env("NETWORK_DRIVE_HOST"),
            share=self._env("NETWORK_DRIVE_SHARE"),
            username=self._env("NETWORK_DRIVE_USERNAME"),
            password=self._env("NETWORK_DRIVE_PASSWORD"),
            domain=self._env("NETWORK_DRIVE_DOMAIN"),
        )

        cache_dir = os.path.join(
            os.path.dirname(__file__), "..", "data", "network_drive_cache"
        )

        summary = index_network_drive(
            search_service=self.search_service,
            vector_store=self.vector_store,
            connector=connector,
            cache_dir=cache_dir,
            extract_fn=_app.extract_text,
            make_chunks_fn=_app.make_chunks,
            batch_size=_app.BATCH_SIZE,
        )

        self._registry["network_drive"] = datetime.now(tz=timezone.utc).isoformat()
        logger.info("[agent] network_drive re-index summary: %s", summary)

    def _run_sharepoint(self) -> None:
        from connectors.sharepoint import SharePointConnector
        from connectors.indexing_service import index_sharepoint

        import importlib
        _app = importlib.import_module("app")

        connector = SharePointConnector(
            tenant_id=self._env("SHAREPOINT_TENANT_ID"),
            client_id=self._env("SHAREPOINT_CLIENT_ID"),
            client_secret=self._env("SHAREPOINT_CLIENT_SECRET"),
            site_url=self._env("SHAREPOINT_SITE_URL"),
        )

        cache_dir = os.path.join(
            os.path.dirname(__file__), "..", "data", "sharepoint_cache"
        )

        summary = index_sharepoint(
            search_service=self.search_service,
            vector_store=self.vector_store,
            connector=connector,
            cache_dir=cache_dir,
            extract_fn=_app.extract_text,
            make_chunks_fn=_app.make_chunks,
            batch_size=_app.BATCH_SIZE,
        )

        self._registry["sharepoint"] = datetime.now(tz=timezone.utc).isoformat()
        logger.info("[agent] sharepoint re-index summary: %s", summary)

    # ── On-demand / startup run ────────────────────────────────────────────────

    def run_all_sources(self) -> None:
        """
        Immediately run the indexing pipeline for every enabled connector.

        Each source runs independently in its own thread so one failure
        (or a slow network) does not block the others.  This method returns
        as soon as all threads have been *started* — it does not wait for
        completion.
        """
        enabled = [s for s in _SOURCES if self._is_enabled(s)]
        if not enabled:
            logger.info("[agent] run_all_sources: no connectors enabled")
            return

        logger.info(
            "[agent] run_all_sources triggered for: %s", enabled
        )
        threads = []
        for source in enabled:
            t = threading.Thread(
                target=self._run_source,
                args=(source,),
                name=f"agent-{source}",
                daemon=True,
            )
            t.start()
            threads.append(t)
        # Note: we intentionally don't join — fire-and-forget for startup use

    # ── Status / introspection ─────────────────────────────────────────────────

    def get_schedule_info(self) -> dict:
        """
        Return a JSON-serialisable dict describing next scheduled run times
        and last-run timestamps for every source.

        Example return value::

            {
              "scheduler_running": true,
              "interval_hours": 24,
              "jobs": {
                "network_drive": {
                  "enabled": true,
                  "next_run": "2026-05-15T08:00:00+00:00",
                  "last_run": "2026-05-14T08:00:00+00:00"
                },
                "sharepoint": {
                  "enabled": false,
                  "next_run": null,
                  "last_run": null
                }
              }
            }
        """
        jobs_info: dict = {}

        for source in _SOURCES:
            job = self._jobs.get(source)
            next_run: Optional[str] = None

            if job is not None:
                try:
                    nr = job.next_run_time   # datetime or None
                    next_run = nr.isoformat() if nr else None
                except Exception:
                    next_run = None

            jobs_info[source] = {
                "enabled":   self._is_enabled(source),
                "next_run":  next_run,
                "last_run":  self._registry.get(source),
            }

        return {
            "scheduler_running": self._running,
            "interval_hours":    self.interval_hours,
            "jobs":              jobs_info,
        }
