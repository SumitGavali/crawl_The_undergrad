# Implementation Plan: Network Drive Connector

## Overview

This implementation plan breaks down the network drive connector feature into discrete coding tasks. The feature adds SMB network drive connectivity to KnowledgeOS, enabling secure connections to Windows network shares, file discovery, incremental indexing, and API endpoints for triggering and monitoring indexing operations.

The implementation follows the existing patterns in `app.py` for background task management and reuses the text extraction pipeline (`extract_text`, `make_chunks`), Qdrant vector store (`QdrantVectorStore`), and embedding model managed by `SearchService`.

## Tasks

- [x] 1. Set up project structure and dependencies
  - Create `connectors/` directory in project root
  - Create `connectors/__init__.py` file
  - Add `smbprotocol` to `requirements.txt`
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [x] 2. Implement NetworkDriveConnector class
  - [x] 2.1 Create NetworkDriveConnector class with initialization and connection methods
    - Create `connectors/network_drive.py` file
    - Implement `__init__` method to store connection parameters (host, share, username, password, domain)
    - Implement `connect()` method using `smbclient.register_session()` to establish SMB connection
    - Implement `disconnect()` method to close SMB connection
    - Add comprehensive error handling and logging for connection operations
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 2.2 Implement file discovery methods
    - Implement `list_files()` method to recursively traverse network share using `smbclient.listdir()`
    - Add filtering logic for supported file extensions (.pdf, .docx, .xlsx, .txt, .csv)
    - Handle access denied errors gracefully by logging and continuing with accessible directories
    - Return list of full SMB paths for matching files
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 2.3 Implement file download and metadata methods
    - Implement `download_file()` method using `smbclient.open()` to download files to local cache
    - Implement `get_file_modified_time()` method using `smbclient.stat()` to get file timestamps
    - Add error handling for file download failures (locked files, network interruptions)
    - Preserve original filename in local cache directory
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3_

- [ ] 3. Checkpoint - Verify NetworkDriveConnector implementation
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Implement indexing service module
  - [~] 4.1 Create index state management functions
    - Create `connectors/indexing_service.py` file
    - Implement `load_index_state()` function to read JSON state file
    - Implement `save_index_state()` function to write JSON state file
    - Handle missing state file by returning empty dictionary
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [~] 4.2 Implement main indexing orchestration function
    - Implement `index_network_drive()` function with setup phase (create cache directory, load state, initialize counters)
    - Add connection establishment logic with error handling
    - Implement file discovery phase with logging
    - _Requirements: 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 6.5, 13.1, 13.2, 13.3_

  - [~] 4.3 Implement incremental processing loop
    - Add loop to process discovered files with modification time comparison
    - Implement file download, text extraction, and chunking logic
    - Add source metadata field ("network_drive") to all chunks
    - Implement vector deletion for re-indexed files using Qdrant filter on file_path
    - Add embedding generation and vector storage using SearchService
    - Update index state after successful processing
    - _Requirements: 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 7.1, 7.2_

  - [~] 4.4 Implement cleanup and summary generation
    - Save updated index state to JSON file
    - Disconnect from network drive
    - Return summary dictionary with counts (total_discovered, indexed, skipped, failed, total_chunks, elapsed_seconds)
    - Add comprehensive error handling for all stages with logging
    - _Requirements: 5.5, 8.1, 8.2, 8.3, 8.4, 12.1, 12.2, 12.3, 12.4, 12.5_

- [~] 5. Checkpoint - Verify indexing service implementation
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Add environment variables and configuration
  - [~] 6.1 Update .env file with network drive configuration
    - Add NETWORK_DRIVE_ENABLED variable (default: false)
    - Add NETWORK_DRIVE_HOST variable for SMB server address
    - Add NETWORK_DRIVE_SHARE variable for share name
    - Add NETWORK_DRIVE_USERNAME variable for authentication
    - Add NETWORK_DRIVE_PASSWORD variable for authentication
    - Add NETWORK_DRIVE_DOMAIN variable (optional, default: empty string)
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_

  - [~] 6.2 Add configuration loading to app.py
    - Add network drive configuration variables after existing config loading section
    - Load NETWORK_DRIVE_ENABLED, NETWORK_DRIVE_HOST, NETWORK_DRIVE_SHARE, NETWORK_DRIVE_USERNAME, NETWORK_DRIVE_PASSWORD, NETWORK_DRIVE_DOMAIN
    - Add NETWORK_DRIVE_CACHE_DIR variable pointing to DATA_DIR/network_drive_cache
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_

- [ ] 7. Implement API endpoints in app.py
  - [~] 7.1 Implement background task function for network drive indexing
    - Create `background_index_network_drive()` function following the pattern of `background_crawl()`
    - Import NetworkDriveConnector and index_network_drive from connectors modules
    - Update task status to "running" and set initial message
    - Create NetworkDriveConnector instance with credentials from parameters
    - Call index_network_drive() with connector, search service, and cache directory
    - Update task status with summary results on completion
    - Handle exceptions and set task status to "error" with error message
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 12.1_

  - [~] 7.2 Implement POST /api/index/network-drive endpoint
    - Create endpoint function with BackgroundTasks parameter
    - Check NETWORK_DRIVE_ENABLED environment variable and return 400 error if disabled
    - Validate required environment variables (host, share, username, password)
    - Generate unique task_id using uuid.uuid4()
    - Initialize task in crawl_tasks dictionary with status "queued" and counters
    - Add background_index_network_drive to background tasks
    - Return task_id and status "queued"
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [~] 7.3 Implement GET /api/connectors/status endpoint
    - Create endpoint function to return connector status information
    - Query Qdrant for local source statistics (file count, chunk count)
    - Check NETWORK_DRIVE_ENABLED and return appropriate status
    - Query Qdrant for network_drive source statistics if enabled
    - Load index state JSON to get last indexed timestamp
    - Return JSON response with local and network_drive connector status
    - Add error handling for Qdrant query failures
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

- [~] 8. Checkpoint - Verify API endpoints implementation
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Integration and final wiring
  - [~] 9.1 Update existing code to support source field
    - Verify that `make_chunks()` function in app.py includes "source": "local" for local files
    - Ensure SearchService.update() method properly handles source metadata
    - Verify Qdrant collection schema supports source field as keyword
    - _Requirements: 6.3, 7.1, 7.2_

  - [~] 9.2 Add import statements and integrate components
    - Add necessary imports at top of app.py (datetime for timestamp handling)
    - Ensure connectors module is importable
    - Verify all environment variables are loaded correctly
    - Test that background task can import and use connector modules
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_

- [~] 10. Final checkpoint - End-to-end verification
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- All tasks reference specific requirements for traceability
- Checkpoints ensure incremental validation at key milestones
- Error handling is integrated throughout the implementation
- The implementation reuses existing components (extract_text, make_chunks, SearchService, QdrantVectorStore)
- Background task pattern follows existing crawl_tasks implementation
- No property-based tests are included as this is primarily infrastructure and integration code
- The existing GET /api/crawl/status/{task_id} endpoint is reused for polling network drive indexing progress

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3"] },
    { "id": 3, "tasks": ["4.1"] },
    { "id": 4, "tasks": ["4.2"] },
    { "id": 5, "tasks": ["4.3"] },
    { "id": 6, "tasks": ["4.4"] },
    { "id": 7, "tasks": ["6.1", "6.2"] },
    { "id": 8, "tasks": ["7.1"] },
    { "id": 9, "tasks": ["7.2", "7.3"] },
    { "id": 10, "tasks": ["9.1", "9.2"] }
  ]
}
```
