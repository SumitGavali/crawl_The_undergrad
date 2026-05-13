"""
Network Drive Connector for KnowledgeOS.

This module provides SMB network drive connectivity using the smbprotocol library.
It enables secure connections to Windows network shares, file discovery, and file operations.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import smbclient

logger = logging.getLogger("knowledgeos.network_drive")


class NetworkDriveConnector:
    """
    Manages SMB connections and file operations for network drives.
    
    Uses smbprotocol library for SMBv2/v3 protocol implementation.
    Provides methods for connecting, listing files, downloading files,
    and querying file metadata from Windows network shares.
    """
    
    def __init__(
        self,
        host: str,
        share: str,
        username: str,
        password: str,
        domain: str = ""
    ):
        """
        Initialize connector with connection parameters.
        
        Args:
            host: SMB server hostname or IP address
            share: Share name (e.g., "Documents")
            username: Authentication username
            password: Authentication password
            domain: Windows domain (optional, defaults to "")
        """
        self.host = host
        self.share = share
        self.username = username
        self.password = password
        self.domain = domain
        self._connected = False
        
        logger.info(
            "NetworkDriveConnector initialized for //%s/%s (user: %s, domain: %s)",
            host, share, username, domain or "(none)"
        )
    
    def connect(self) -> bool:
        """
        Establish SMB connection to the network share.
        
        Uses smbclient.register_session() to establish an authenticated
        session with the SMB server. The session is registered globally
        and will be used for all subsequent file operations.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info("Attempting to connect to //%s/%s", self.host, self.share)
            
            # Register SMB session with credentials
            # This creates a global session that will be used for all smbclient operations
            smbclient.register_session(
                server=self.host,
                username=self.username,
                password=self.password,
                auth_protocol="ntlm",  # Use NTLM authentication
                connection_timeout=30  # 30 second timeout
            )
            
            # Test the connection by attempting to list the share root
            # This verifies that the credentials are valid and the share is accessible
            test_path = f"\\\\{self.host}\\{self.share}"
            try:
                smbclient.listdir(test_path)
                self._connected = True
                logger.info("✓ Successfully connected to //%s/%s", self.host, self.share)
                return True
            except Exception as list_error:
                logger.error(
                    "Connection established but failed to access share //%s/%s: %s",
                    self.host, self.share, list_error
                )
                self._connected = False
                return False
                
        except Exception as e:
            logger.error(
                "Failed to connect to //%s/%s: %s",
                self.host, self.share, e
            )
            self._connected = False
            return False
    
    def disconnect(self) -> None:
        """
        Close the active SMB connection.
        
        Deletes the registered session for this server. After calling this method,
        a new connect() call will be required to perform file operations.
        """
        try:
            if self._connected:
                # Delete the registered session
                smbclient.delete_session(self.host)
                self._connected = False
                logger.info("Disconnected from //%s/%s", self.host, self.share)
            else:
                logger.debug("Disconnect called but no active connection exists")
        except Exception as e:
            logger.warning("Error during disconnect from //%s/%s: %s", self.host, self.share, e)
            # Mark as disconnected even if there was an error
            self._connected = False
    
    @property
    def is_connected(self) -> bool:
        """Check if the connector has an active connection."""
        return self._connected
    
    def download_file(
        self,
        remote_path: str,
        local_cache_dir: str
    ) -> Optional[str]:
        """
        Download a file from the network share to local cache.
        
        Downloads the file using smbclient.open() in binary mode and writes
        it to the local cache directory. The original filename is preserved.
        
        Args:
            remote_path: Full path to file on network share (e.g., "/folder/file.pdf")
            local_cache_dir: Local directory for caching downloaded files
            
        Returns:
            Local file path if successful, None if download fails
        """
        if not self._connected:
            logger.error("Cannot download file: not connected to network drive")
            return None
        
        try:
            # Construct full SMB path
            smb_path = f"\\\\{self.host}\\{self.share}\\{remote_path.lstrip('/').replace('/', '\\')}"
            
            # Extract filename from remote path and create local path
            filename = os.path.basename(remote_path)
            local_path = os.path.join(local_cache_dir, filename)
            
            # Ensure cache directory exists
            os.makedirs(local_cache_dir, exist_ok=True)
            
            logger.debug("Downloading %s to %s", smb_path, local_path)
            
            # Download file using smbclient
            with smbclient.open_file(smb_path, mode="rb") as remote_file:
                with open(local_path, "wb") as local_file:
                    # Read and write in chunks to handle large files efficiently
                    chunk_size = 1024 * 1024  # 1MB chunks
                    while True:
                        chunk = remote_file.read(chunk_size)
                        if not chunk:
                            break
                        local_file.write(chunk)
            
            logger.info("✓ Downloaded %s (%d bytes)", filename, os.path.getsize(local_path))
            return local_path
            
        except PermissionError as e:
            logger.error("Permission denied downloading %s: %s", remote_path, e)
            return None
        except FileNotFoundError as e:
            logger.error("File not found on network share %s: %s", remote_path, e)
            return None
        except OSError as e:
            # Handles locked files, network interruptions, disk space issues
            logger.error("OS error downloading %s: %s", remote_path, e)
            return None
        except Exception as e:
            logger.error("Unexpected error downloading %s: %s", remote_path, e)
            return None
    
    def get_file_modified_time(
        self,
        remote_path: str
    ) -> Optional[datetime]:
        """
        Get the last modified timestamp for a file.
        
        Uses smbclient.stat() to query file metadata from the SMB share
        and extracts the last modified timestamp.
        
        Args:
            remote_path: Full path to file on network share (e.g., "/folder/file.pdf")
            
        Returns:
            datetime object with last modified time, or None if query fails
        """
        if not self._connected:
            logger.error("Cannot get file modified time: not connected to network drive")
            return None
        
        try:
            # Construct full SMB path
            smb_path = f"\\\\{self.host}\\{self.share}\\{remote_path.lstrip('/').replace('/', '\\')}"
            
            logger.debug("Getting modified time for %s", smb_path)
            
            # Get file statistics using smbclient.stat()
            stat_info = smbclient.stat(smb_path)
            
            # Extract modified time from stat result
            # stat_info.st_mtime is a Unix timestamp (float)
            modified_time = datetime.fromtimestamp(stat_info.st_mtime)
            
            logger.debug("File %s last modified: %s", remote_path, modified_time.isoformat())
            return modified_time
            
        except FileNotFoundError as e:
            logger.error("File not found on network share %s: %s", remote_path, e)
            return None
        except PermissionError as e:
            logger.error("Permission denied accessing %s: %s", remote_path, e)
            return None
        except OSError as e:
            # Handles network interruptions and other OS-level errors
            logger.error("OS error getting modified time for %s: %s", remote_path, e)
            return None
        except Exception as e:
            logger.error("Unexpected error getting modified time for %s: %s", remote_path, e)
            return None
    
    def list_files(
        self,
        remote_path: str = "/",
        extensions: Optional[set[str]] = None
    ) -> list[str]:
        """
        Recursively list all files matching specified extensions.
        
        Traverses the network share starting from remote_path and discovers
        all files with extensions matching the filter. Handles access denied
        errors gracefully by logging and continuing with accessible directories.
        
        Args:
            remote_path: Starting path on the share (default: "/")
            extensions: Set of file extensions to include (default: {.pdf, .docx, .xlsx, .txt, .csv})
            
        Returns:
            List of full SMB paths for matching files
            
        Raises:
            RuntimeError: If called without an active connection
        """
        if not self._connected:
            raise RuntimeError("Cannot list files: not connected. Call connect() first.")
        
        # Default to supported document extensions
        if extensions is None:
            extensions = {".pdf", ".docx", ".xlsx", ".txt", ".csv"}
        
        # Normalize extensions to lowercase for case-insensitive matching
        extensions = {ext.lower() for ext in extensions}
        
        # Build the full UNC path
        # Convert forward slashes to backslashes for Windows paths
        normalized_path = remote_path.replace("/", "\\")
        if not normalized_path.startswith("\\"):
            normalized_path = "\\" + normalized_path
        
        base_path = f"\\\\{self.host}\\{self.share}{normalized_path}"
        
        logger.info("Starting file discovery from %s", base_path)
        logger.debug("Filtering for extensions: %s", extensions)
        
        matching_files = []
        
        # Use a stack for iterative traversal to avoid deep recursion
        dirs_to_process = [base_path]
        
        while dirs_to_process:
            current_dir = dirs_to_process.pop()
            
            try:
                # List all entries in the current directory
                entries = smbclient.listdir(current_dir)
                
                for entry in entries:
                    # Skip special directories
                    if entry in (".", ".."):
                        continue
                    
                    entry_path = os.path.join(current_dir, entry)
                    
                    try:
                        # Check if this is a directory or file
                        # Use scandir for more efficient stat operations
                        is_dir = smbclient.path.isdir(entry_path)
                        
                        if is_dir:
                            # Add directory to processing stack
                            dirs_to_process.append(entry_path)
                            logger.debug("Found directory: %s", entry_path)
                        else:
                            # Check if file extension matches filter
                            file_ext = os.path.splitext(entry)[1].lower()
                            if file_ext in extensions:
                                matching_files.append(entry_path)
                                logger.debug("Found matching file: %s", entry_path)
                    
                    except PermissionError as pe:
                        # Access denied to specific file/directory - log and continue
                        logger.warning(
                            "Access denied to %s: %s - skipping",
                            entry_path, pe
                        )
                        continue
                    
                    except Exception as e:
                        # Other errors for specific entries - log and continue
                        logger.warning(
                            "Error accessing %s: %s - skipping",
                            entry_path, e
                        )
                        continue
            
            except PermissionError as pe:
                # Access denied to entire directory - log and continue
                logger.warning(
                    "Access denied to directory %s: %s - skipping entire directory",
                    current_dir, pe
                )
                continue
            
            except Exception as e:
                # Other errors for directory listing - log and continue
                logger.error(
                    "Error listing directory %s: %s - skipping",
                    current_dir, e
                )
                continue
        
        logger.info(
            "File discovery complete: found %d matching files",
            len(matching_files)
        )
        
        return matching_files
