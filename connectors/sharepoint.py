"""
SharePoint Connector for KnowledgeOS.

Uses the Microsoft Graph API (v1.0) to browse and download files from a
SharePoint document library.  Authentication is done via a service principal
(client-credentials flow) using azure-identity's ClientSecretCredential.

Token expiry is handled transparently: if a Graph API call returns 401 the
connector re-authenticates and retries the request exactly once before
raising an error.

Supported operations
--------------------
authenticate()          — obtain / refresh an access token
get_site_id()           — resolve a SharePoint site URL → Graph site ID
get_drive_id()          — get the default document library drive ID
list_files()            — recursively walk the document library
download_file()         — stream a file to a local path
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("knowledgeos.sharepoint")

# Default extensions recognised by the indexing pipeline
_DEFAULT_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".txt", ".csv"}

# Microsoft Graph REST base
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class SharePointConnector:
    """
    Manages Graph API interactions for a single SharePoint site.

    Attributes
    ----------
    tenant_id, client_id, client_secret : str
        Service-principal credentials registered in Azure AD.
    site_url : str
        Full URL of the SharePoint site, e.g.
        ``https://company.sharepoint.com/sites/MySite``.
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        site_url: str,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.site_url = site_url.rstrip("/")

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0   # Unix timestamp

        # Cached IDs — populated lazily
        self._site_id: Optional[str] = None
        self._drive_id: Optional[str] = None

        logger.info(
            "SharePointConnector initialised for %s (tenant=%s, client=%s)",
            site_url, tenant_id, client_id
        )

    # ── Authentication ─────────────────────────────────────────────────────────

    def authenticate(self) -> bool:
        """
        Obtain an access token for the Graph API using client-credentials flow.

        Uses ``azure.identity.ClientSecretCredential`` so that the token cache
        and renewal logic are handled by the Azure SDK.

        Returns
        -------
        True  — token obtained successfully.
        False — authentication failed (error is logged).
        """
        try:
            from azure.identity import ClientSecretCredential

            cred = ClientSecretCredential(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                client_secret=self.client_secret,
            )
            # Acquire a token scoped to Microsoft Graph
            token = cred.get_token("https://graph.microsoft.com/.default")
            self._access_token = token.token
            self._token_expires_at = token.expires_on   # Unix timestamp (int)
            logger.info(
                "✓ SharePoint authenticated (token expires at %s)",
                datetime.fromtimestamp(self._token_expires_at, tz=timezone.utc).isoformat()
            )
            return True

        except ImportError:
            logger.error(
                "azure-identity is not installed. "
                "Run: pip install azure-identity"
            )
            return False
        except Exception as exc:
            logger.error("SharePoint authentication failed: %s", exc)
            return False

    @property
    def is_authenticated(self) -> bool:
        """True if we currently hold a (probably valid) access token."""
        import time
        return (
            self._access_token is not None
            and time.time() < self._token_expires_at - 30   # 30-s safety margin
        )

    # ── Internal HTTP helper ───────────────────────────────────────────────────

    def _graph_get(
        self,
        url: str,
        params: Optional[dict] = None,
        *,
        _retry: bool = True,
    ) -> dict:
        """
        Perform an authenticated GET request to the Graph API.

        On HTTP 401 the connector re-authenticates and retries once
        (``_retry=True``).  All other 4xx/5xx responses raise a
        ``requests.HTTPError``.

        Parameters
        ----------
        url     : Full Graph API URL.
        params  : Optional query-string parameters.
        _retry  : Internal flag — set to False to prevent infinite recursion.

        Returns
        -------
        Parsed JSON body as a dict.
        """
        if not self._access_token:
            raise RuntimeError(
                "Not authenticated. Call authenticate() before making API calls."
            )

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        resp = requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code == 401 and _retry:
            logger.warning("Graph API returned 401 — re-authenticating and retrying …")
            if self.authenticate():
                return self._graph_get(url, params, _retry=False)
            raise PermissionError(
                "Re-authentication failed after 401 from Graph API."
            )

        resp.raise_for_status()
        return resp.json()

    # ── Site & Drive resolution ────────────────────────────────────────────────

    def get_site_id(self) -> Optional[str]:
        """
        Resolve the human-readable site URL to a Graph API site ID.

        Graph endpoint:
        ``GET /sites/{hostname}:/sites/{site-relative-path}``

        Returns ``None`` on failure (error is logged).
        """
        if self._site_id:
            return self._site_id

        try:
            parsed = urlparse(self.site_url)
            hostname = parsed.netloc                    # e.g. company.sharepoint.com
            # Strip the leading /sites/ prefix from the path
            path = parsed.path.lstrip("/")             # e.g. sites/MySite
            url = f"{_GRAPH_BASE}/sites/{hostname}:/{path}"
            data = self._graph_get(url)
            self._site_id = data["id"]
            logger.info("✓ Resolved site ID: %s", self._site_id)
            return self._site_id

        except Exception as exc:
            logger.error("Failed to resolve site ID for %s: %s", self.site_url, exc)
            return None

    def get_drive_id(self) -> Optional[str]:
        """
        Return the drive ID of the site's default document library.

        Graph endpoint: ``GET /sites/{site-id}/drive``

        Returns ``None`` on failure (error is logged).
        """
        if self._drive_id:
            return self._drive_id

        site_id = self.get_site_id()
        if not site_id:
            return None

        try:
            url = f"{_GRAPH_BASE}/sites/{site_id}/drive"
            data = self._graph_get(url)
            self._drive_id = data["id"]
            logger.info("✓ Resolved drive ID: %s", self._drive_id)
            return self._drive_id

        except Exception as exc:
            logger.error("Failed to get drive ID for site %s: %s", site_id, exc)
            return None

    # ── File listing ───────────────────────────────────────────────────────────

    def list_files(
        self,
        folder_path: str = "/",
        extensions: Optional[set] = None,
    ) -> list[dict]:
        """
        Recursively list files in the SharePoint document library.

        Parameters
        ----------
        folder_path :
            Starting folder path relative to the drive root (e.g. ``"/"`` or
            ``"/Reports/2024"``).  A leading slash is normalised away.
        extensions :
            Set of lower-case file extensions to include
            (default: ``{".pdf", ".docx", ".xlsx", ".txt", ".csv"}``).

        Returns
        -------
        List of dicts with keys:
        ``name``, ``download_url``, ``modified_datetime``,
        ``web_url``, ``drive_item_id``, ``size``.

        Access-denied folders are skipped with a warning.
        """
        if extensions is None:
            extensions = _DEFAULT_EXTENSIONS
        extensions = {e.lower() for e in extensions}

        drive_id = self.get_drive_id()
        if not drive_id:
            logger.error("Cannot list files: drive ID could not be resolved")
            return []

        matching: list[dict] = []
        # Iterative stack — avoids Python recursion limits on deep hierarchies
        folders_to_visit: list[str] = []

        # Resolve starting URL
        norm_path = folder_path.strip("/")
        if norm_path:
            root_url = (
                f"{_GRAPH_BASE}/drives/{drive_id}/root:/{norm_path}:/children"
            )
        else:
            root_url = f"{_GRAPH_BASE}/drives/{drive_id}/root/children"

        folders_to_visit.append(root_url)

        while folders_to_visit:
            current_url = folders_to_visit.pop()

            # Graph paginates at 200 items by default; follow @odata.nextLink
            page_url: Optional[str] = current_url
            while page_url:
                try:
                    data = self._graph_get(
                        page_url,
                        params={"$select": "id,name,file,folder,lastModifiedDateTime,size,webUrl,@microsoft.graph.downloadUrl"}
                        if "?" not in page_url else None,
                    )
                except PermissionError:
                    logger.warning("Access denied to %s — skipping folder", current_url)
                    break
                except Exception as exc:
                    logger.error("Error listing %s: %s — skipping", current_url, exc)
                    break

                for item in data.get("value", []):
                    if "folder" in item:
                        # Queue child folder for traversal
                        child_id = item["id"]
                        child_url = (
                            f"{_GRAPH_BASE}/drives/{drive_id}/items/{child_id}/children"
                        )
                        folders_to_visit.append(child_url)
                        logger.debug("Queued folder: %s", item.get("name"))

                    elif "file" in item:
                        name = item.get("name", "")
                        ext = os.path.splitext(name)[1].lower()
                        if ext not in extensions:
                            continue

                        # @microsoft.graph.downloadUrl is a pre-signed, short-lived URL
                        dl_url = item.get("@microsoft.graph.downloadUrl", "")

                        # Parse ISO-8601 modified time → datetime
                        raw_ts = item.get("lastModifiedDateTime", "")
                        try:
                            modified_dt = datetime.fromisoformat(
                                raw_ts.replace("Z", "+00:00")
                            )
                        except (ValueError, AttributeError):
                            modified_dt = None

                        matching.append({
                            "name":              name,
                            "download_url":      dl_url,
                            "modified_datetime": modified_dt,
                            "web_url":           item.get("webUrl", ""),
                            "drive_item_id":     item.get("id", ""),
                            "size":              item.get("size", 0),
                        })
                        logger.debug("Found file: %s", name)

                page_url = data.get("@odata.nextLink")   # None if last page

        logger.info("File discovery complete: %d matching file(s) found", len(matching))
        return matching

    # ── File download ──────────────────────────────────────────────────────────

    def download_file(self, download_url: str, local_path: str) -> bool:
        """
        Stream a file from *download_url* to *local_path*.

        The ``@microsoft.graph.downloadUrl`` is a pre-signed Azure Blob URL
        that does **not** require an Authorization header.

        Parameters
        ----------
        download_url : Pre-signed download URL from Graph API item listing.
        local_path   : Absolute or relative path to write the file to.

        Returns
        -------
        True on success, False on any error.
        """
        if not download_url:
            logger.error("download_file called with empty URL")
            return False

        try:
            os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)

            # Stream to avoid loading large files into memory
            with requests.get(download_url, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(local_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1 MB
                        if chunk:
                            fh.write(chunk)

            size_kb = os.path.getsize(local_path) / 1024
            logger.info("✓ Downloaded %s (%.1f KB)", os.path.basename(local_path), size_kb)
            return True

        except requests.HTTPError as exc:
            logger.error(
                "HTTP error downloading %s: %s", os.path.basename(local_path), exc
            )
        except requests.ConnectionError as exc:
            logger.error(
                "Connection error downloading %s: %s", os.path.basename(local_path), exc
            )
        except OSError as exc:
            logger.error(
                "OS error writing %s: %s", local_path, exc
            )
        except Exception as exc:
            logger.error(
                "Unexpected error downloading %s: %s", os.path.basename(local_path), exc
            )

        return False
