"""
Indexing Service for KnowledgeOS — Network Drive & SharePoint.

Provides two public orchestration functions:

  index_network_drive(...)  — SMB / Windows network share
  index_sharepoint(...)     — Microsoft SharePoint via Graph API

Both follow the same pipeline:
  1. Connect / authenticate.
  2. Discover supported files.
  3. Compare modification times against index_state.json.
  4. Download new/changed files → extract text → chunk → embed → upsert into Qdrant.
  5. Delete stale Qdrant vectors for re-indexed files before re-inserting.
  6. Persist updated state.
  7. Return a summary dict.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from connectors.network_drive import NetworkDriveConnector
    from db.vector_store import QdrantVectorStore

logger = logging.getLogger("knowledgeos.indexing_service")

# ── State file helpers ─────────────────────────────────────────────────────────

_STATE_FILENAME = "index_state.json"


def load_index_state(cache_dir: str) -> dict:
    """
    Load the persisted index state from *cache_dir/index_state.json*.

    Returns an empty dict if the file does not exist or is corrupt.
    The state maps SMB file paths → ISO-8601 last-indexed timestamp strings.
    """
    state_path = os.path.join(cache_dir, _STATE_FILENAME)
    if not os.path.isfile(state_path):
        logger.debug("No index state file found at %s — starting fresh", state_path)
        return {}

    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        logger.debug("Loaded index state: %d entries from %s", len(state), state_path)
        return state
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read index state %s: %s — starting fresh", state_path, exc)
        return {}


def save_index_state(cache_dir: str, state: dict) -> None:
    """
    Persist *state* dict to *cache_dir/index_state.json*.

    Args:
        cache_dir: Directory containing the state file.
        state:     Mapping of SMB path → ISO-8601 timestamp string.
    """
    os.makedirs(cache_dir, exist_ok=True)
    state_path = os.path.join(cache_dir, _STATE_FILENAME)
    try:
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
        logger.debug("Saved index state: %d entries to %s", len(state), state_path)
    except OSError as exc:
        logger.error("Failed to save index state to %s: %s", state_path, exc)


# ── Per-file vector deletion helper ───────────────────────────────────────────

def _delete_file_vectors(vector_store: "QdrantVectorStore", file_path: str, source: str) -> None:
    """
    Remove all Qdrant points for a specific *file_path* / *source* combination.

    Uses the Qdrant filter API directly because ``QdrantVectorStore.delete_by_source()``
    only filters by source, not by individual file path.

    Parameters
    ----------
    vector_store : Active ``QdrantVectorStore`` instance.
    file_path    : Canonical file path stored in the payload (SMB path or SharePoint URL).
    source       : Source tag, e.g. ``"network_drive"`` or ``"sharepoint"``.
    """
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        vector_store.client.delete(
            collection_name=vector_store.collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(key="source",    match=MatchValue(value=source)),
                    FieldCondition(key="file_path", match=MatchValue(value=file_path)),
                ]
            ),
        )
        logger.debug("Deleted old vectors for %s (source=%s)", file_path, source)
    except Exception as exc:
        logger.warning("Could not delete old vectors for %s: %s", file_path, exc)


# ── Main orchestration function ────────────────────────────────────────────────

def index_network_drive(
    search_service,
    vector_store: "QdrantVectorStore",
    connector: "NetworkDriveConnector",
    cache_dir: str = "./cache/network_drive",
    extract_fn=None,
    make_chunks_fn=None,
    batch_size: int = 64,
) -> dict:
    """
    Index all supported files from the SMB network drive into Qdrant.

    Behaviour
    ---------
    - Creates *cache_dir* if it does not exist.
    - Connects to the network drive; returns an error summary if unreachable.
    - Lists all supported files (pdf, docx, xlsx, txt, csv).
    - For each file checks the last-modified timestamp against ``index_state.json``.
      Skips files that have not changed.
    - Downloads new/modified files to *cache_dir*, extracts text, chunks and embeds.
    - Deletes old Qdrant vectors for the file (if re-indexing) then upserts new ones.
    - Saves updated state; disconnects; returns a summary dict.

    Args:
        search_service:  The app's ``SearchService`` instance (provides the embedding model).
        vector_store:    The ``QdrantVectorStore`` instance.
        connector:       An initialised (but not yet connected) ``NetworkDriveConnector``.
        cache_dir:       Local directory for cached files and state.
        extract_fn:      Text-extraction callable (signature: path -> list[dict]).
                         Defaults to importing ``extract_text`` from app module.
        make_chunks_fn:  Chunking callable (signature: pages, file_path, file_type -> list[dict]).
                         Defaults to importing ``make_chunks`` from app module.
        batch_size:      Embedding batch size (default 64).

    Returns:
        dict with keys: indexed, skipped, failed, total,
                         total_chunks, elapsed_seconds, message
    """
    # Allow callers to inject functions; fall back to importing from app.
    # The fallback is safe here because by the time this function runs the
    # app module is already fully loaded (this is called from a background task).
    if extract_fn is None or make_chunks_fn is None:
        import importlib
        _app = importlib.import_module("app")
        extract_fn      = extract_fn      or _app.extract_text
        make_chunks_fn  = make_chunks_fn  or _app.make_chunks
        batch_size      = batch_size      or _app.BATCH_SIZE

    start_time = time.time()

    # ── Setup ──────────────────────────────────────────────────────────────────
    os.makedirs(cache_dir, exist_ok=True)
    state = load_index_state(cache_dir)

    counters = {"indexed": 0, "skipped": 0, "failed": 0, "total": 0, "total_chunks": 0}

    # ── Connect ────────────────────────────────────────────────────────────────
    logger.info("Connecting to network drive //%s/%s …", connector.host, connector.share)
    connected = connector.connect()
    if not connected:
        msg = (
            f"Could not connect to network drive //{connector.host}/{connector.share}. "
            "Check credentials and network connectivity."
        )
        logger.error(msg)
        return {
            **counters,
            "elapsed_seconds": round(time.time() - start_time, 2),
            "message": msg,
            "error": True,
        }

    try:
        # ── File discovery ─────────────────────────────────────────────────────
        logger.info("Discovering files on the network share …")
        try:
            smb_files = connector.list_files(remote_path="/")
        except Exception as exc:
            logger.error("File discovery failed: %s", exc)
            return {
                **counters,
                "elapsed_seconds": round(time.time() - start_time, 2),
                "message": f"File discovery failed: {exc}",
                "error": True,
            }

        counters["total"] = len(smb_files)
        logger.info("Discovered %d file(s) on the share", counters["total"])

        if not smb_files:
            save_index_state(cache_dir, state)
            return {
                **counters,
                "elapsed_seconds": round(time.time() - start_time, 2),
                "message": "No supported files found on the network drive.",
            }

        # ── Per-file processing ────────────────────────────────────────────────
        new_state = dict(state)  # copy; we'll update as we go

        for smb_path in smb_files:
            logger.info("Processing: %s", smb_path)

            # --- Modification-time check ---
            modified_at: datetime | None = None
            try:
                modified_at = connector.get_file_modified_time(smb_path)
            except Exception as exc:
                logger.warning("Could not get modified time for %s: %s", smb_path, exc)

            if modified_at is not None and smb_path in state:
                try:
                    last_indexed = datetime.fromisoformat(state[smb_path])
                    if modified_at <= last_indexed:
                        logger.debug("Skipping unchanged file: %s", smb_path)
                        counters["skipped"] += 1
                        continue
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "Could not parse stored timestamp for %s (%s) — will re-index",
                        smb_path, exc
                    )

            # --- Download ---
            local_path = connector.download_file(smb_path, cache_dir)
            if local_path is None:
                logger.warning("Download failed for %s — skipping", smb_path)
                counters["failed"] += 1
                continue

            # --- Extract text ---
            try:
                pages = extract_fn(local_path)
            except Exception as exc:
                logger.error("Text extraction failed for %s: %s", local_path, exc)
                counters["failed"] += 1
                continue

            # --- Chunk ---
            ext = Path(local_path).suffix.upper().strip(".")
            chunks = make_chunks_fn(pages, smb_path, ext)  # use SMB path as the canonical file_path
            if not chunks:
                logger.warning("No chunks produced for %s — skipping", smb_path)
                counters["failed"] += 1
                continue

            # Override source field to "network_drive" for all chunks
            for chunk in chunks:
                chunk["source"] = "network_drive"
                chunk["file_path"] = smb_path  # canonical SMB path

            # --- Delete old vectors for this file (idempotent re-index) ---
            if smb_path in state:
                _delete_file_vectors(vector_store, smb_path, source="network_drive")

            # --- Embed ---
            if not search_service.model_loaded:
                logger.error("Embedding model not loaded — aborting indexing run")
                counters["failed"] += len(smb_files) - counters["indexed"] - counters["skipped"]
                break

            try:
                texts = [c["text"] for c in chunks]
                all_vecs: list = []
                for b in range(0, len(texts), batch_size):
                    batch = texts[b : b + batch_size]
                    vecs = search_service.model.encode(batch, show_progress_bar=False)
                    all_vecs.extend(vecs)
                vectors_arr = np.array(all_vecs, dtype=np.float32)
            except Exception as exc:
                logger.error("Embedding failed for %s: %s", smb_path, exc)
                counters["failed"] += 1
                continue

            # --- Upsert into Qdrant ---
            try:
                vector_store.upsert(chunks, vectors_arr)
            except Exception as exc:
                logger.error("Qdrant upsert failed for %s: %s", smb_path, exc)
                counters["failed"] += 1
                continue

            # --- Update state ---
            new_state[smb_path] = (modified_at or datetime.utcnow()).isoformat()
            counters["indexed"] += 1
            counters["total_chunks"] += len(chunks)
            logger.info(
                "✓ Indexed %s → %d chunk(s) (total indexed so far: %d)",
                smb_path, len(chunks), counters["indexed"]
            )

            # Clean up local cache file to save disk space
            try:
                os.remove(local_path)
            except OSError:
                pass  # Non-critical

        # ── Persist state ──────────────────────────────────────────────────────
        save_index_state(cache_dir, new_state)

    finally:
        connector.disconnect()

    elapsed = round(time.time() - start_time, 2)
    summary_msg = (
        f"Network drive indexing complete — "
        f"{counters['indexed']} indexed, "
        f"{counters['skipped']} skipped, "
        f"{counters['failed']} failed "
        f"(elapsed: {elapsed}s)"
    )
    logger.info(summary_msg)

    return {
        **counters,
        "elapsed_seconds": elapsed,
        "message": summary_msg,
    }


# ── SharePoint indexing ────────────────────────────────────────────────────────

def index_sharepoint(
    search_service,
    vector_store: "QdrantVectorStore",
    connector,                         # SharePointConnector instance
    cache_dir: str = "./cache/sharepoint",
    extract_fn=None,
    make_chunks_fn=None,
    batch_size: int = 64,
) -> dict:
    """
    Index all supported files from a SharePoint document library into Qdrant.

    Behaviour
    ---------
    - Creates *cache_dir* if needed.
    - Authenticates the connector; returns an error summary if auth fails.
    - Lists all supported files in the document library.
    - Checks modification timestamps against ``index_state.json``.
    - Downloads new/changed files; extracts, chunks, and embeds each one.
    - Deletes stale Qdrant vectors before reinserting (idempotent).
    - Saves state and returns a summary dict.

    Args
    ----
    search_service:  ``SearchService`` instance (embedding model).
    vector_store:    ``QdrantVectorStore`` instance.
    connector:       Initialised ``SharePointConnector`` (not yet authenticated).
    cache_dir:       Local dir for cached files and ``index_state.json``.
    extract_fn:      Text-extraction callable injected by caller.
    make_chunks_fn:  Chunking callable injected by caller.
    batch_size:      Embedding batch size.

    Returns
    -------
    dict: indexed, skipped, failed, total, total_chunks, elapsed_seconds, message
    """
    # Dependency injection with safe fallback (app is already loaded by this point)
    if extract_fn is None or make_chunks_fn is None:
        import importlib
        _app = importlib.import_module("app")
        extract_fn     = extract_fn     or _app.extract_text
        make_chunks_fn = make_chunks_fn or _app.make_chunks
        batch_size     = batch_size     or _app.BATCH_SIZE

    start_time = time.time()
    os.makedirs(cache_dir, exist_ok=True)
    state = load_index_state(cache_dir)
    counters = {"indexed": 0, "skipped": 0, "failed": 0, "total": 0, "total_chunks": 0}

    # ── Authenticate ───────────────────────────────────────────────────────────
    logger.info("Authenticating with SharePoint …")
    if not connector.authenticate():
        msg = "SharePoint authentication failed. Check SHAREPOINT_* credentials."
        logger.error(msg)
        return {
            **counters,
            "elapsed_seconds": round(time.time() - start_time, 2),
            "message": msg,
            "error": True,
        }

    # ── File discovery ─────────────────────────────────────────────────────────
    logger.info("Discovering files in SharePoint document library …")
    try:
        sp_files = connector.list_files(folder_path="/")
    except Exception as exc:
        logger.error("SharePoint file discovery failed: %s", exc)
        return {
            **counters,
            "elapsed_seconds": round(time.time() - start_time, 2),
            "message": f"File discovery failed: {exc}",
            "error": True,
        }

    counters["total"] = len(sp_files)
    logger.info("Discovered %d file(s) on SharePoint", counters["total"])

    if not sp_files:
        save_index_state(cache_dir, state)
        return {
            **counters,
            "elapsed_seconds": round(time.time() - start_time, 2),
            "message": "No supported files found in the SharePoint document library.",
        }

    # ── Per-file processing ────────────────────────────────────────────────────
    new_state = dict(state)

    for file_info in sp_files:
        # Use web_url as the stable canonical key (drive_item_id could also work)
        canonical_key = file_info.get("web_url") or file_info.get("drive_item_id", "")
        name          = file_info.get("name", "unknown")
        download_url  = file_info.get("download_url", "")
        modified_at   = file_info.get("modified_datetime")   # datetime | None

        logger.info("Processing: %s", name)

        # --- Modification-time check ---
        if modified_at is not None and canonical_key in state:
            try:
                # Make both datetimes timezone-aware for a fair comparison
                last_indexed = datetime.fromisoformat(state[canonical_key])
                if last_indexed.tzinfo is None:
                    from datetime import timezone as _tz
                    last_indexed = last_indexed.replace(tzinfo=_tz.utc)
                if modified_at.tzinfo is None:
                    from datetime import timezone as _tz
                    modified_at = modified_at.replace(tzinfo=_tz.utc)

                if modified_at <= last_indexed:
                    logger.debug("Skipping unchanged file: %s", name)
                    counters["skipped"] += 1
                    continue
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Could not parse stored timestamp for %s (%s) — will re-index",
                    name, exc
                )

        # --- Download ---
        if not download_url:
            logger.warning("No download URL for %s — skipping", name)
            counters["failed"] += 1
            continue

        local_path = os.path.join(cache_dir, name)
        success = connector.download_file(download_url, local_path)
        if not success:
            logger.warning("Download failed for %s — skipping", name)
            counters["failed"] += 1
            continue

        # --- Extract text ---
        try:
            pages = extract_fn(local_path)
        except Exception as exc:
            logger.error("Text extraction failed for %s: %s", local_path, exc)
            counters["failed"] += 1
            _cleanup(local_path)
            continue

        # --- Chunk ---
        ext = Path(local_path).suffix.upper().strip(".")
        chunks = make_chunks_fn(pages, canonical_key, ext)
        if not chunks:
            logger.warning("No chunks produced for %s — skipping", name)
            counters["failed"] += 1
            _cleanup(local_path)
            continue

        # Tag every chunk with SharePoint source metadata
        for chunk in chunks:
            chunk["source"]    = "sharepoint"
            chunk["file_path"] = canonical_key
            chunk["web_url"]   = file_info.get("web_url", "")

        # --- Delete old vectors (idempotent re-index) ---
        if canonical_key in state:
            _delete_file_vectors(vector_store, canonical_key, source="sharepoint")

        # --- Embed ---
        if not search_service.model_loaded:
            logger.error("Embedding model not loaded — aborting SharePoint indexing")
            counters["failed"] += len(sp_files) - counters["indexed"] - counters["skipped"]
            _cleanup(local_path)
            break

        try:
            texts = [c["text"] for c in chunks]
            all_vecs: list = []
            for b in range(0, len(texts), batch_size):
                batch = texts[b : b + batch_size]
                vecs = search_service.model.encode(batch, show_progress_bar=False)
                all_vecs.extend(vecs)
            vectors_arr = np.array(all_vecs, dtype=np.float32)
        except Exception as exc:
            logger.error("Embedding failed for %s: %s", name, exc)
            counters["failed"] += 1
            _cleanup(local_path)
            continue

        # --- Upsert into Qdrant ---
        try:
            vector_store.upsert(chunks, vectors_arr)
        except Exception as exc:
            logger.error("Qdrant upsert failed for %s: %s", name, exc)
            counters["failed"] += 1
            _cleanup(local_path)
            continue

        # --- Update state ---
        new_state[canonical_key] = (
            (modified_at or datetime.utcnow()).isoformat()
        )
        counters["indexed"] += 1
        counters["total_chunks"] += len(chunks)
        logger.info(
            "✓ Indexed %s → %d chunk(s) (total: %d)",
            name, len(chunks), counters["indexed"]
        )
        _cleanup(local_path)

    save_index_state(cache_dir, new_state)

    elapsed = round(time.time() - start_time, 2)
    summary_msg = (
        f"SharePoint indexing complete — "
        f"{counters['indexed']} indexed, "
        f"{counters['skipped']} skipped, "
        f"{counters['failed']} failed "
        f"(elapsed: {elapsed}s)"
    )
    logger.info(summary_msg)
    return {
        **counters,
        "elapsed_seconds": elapsed,
        "message": summary_msg,
    }


def _cleanup(local_path: str) -> None:
    """Silently remove a cached file; non-critical."""
    try:
        os.remove(local_path)
    except OSError:
        pass
