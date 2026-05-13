# Requirements Document

## Introduction

This document specifies the requirements for adding SMB network drive connector functionality to KnowledgeOS. The system will connect to Windows network shares (SMB protocol), discover and index supported document files, track modification times to avoid redundant re-indexing, and provide API endpoints for triggering indexing operations and checking connector status.

## Glossary

- **Network_Drive_Connector**: The component responsible for establishing SMB connections and managing file operations on network shares
- **Indexing_Service**: The component that orchestrates the document discovery, download, text extraction, chunking, embedding, and vector storage pipeline
- **Vector_Store**: The Qdrant database that stores document embeddings and metadata
- **Search_Service**: The component that manages the embedding model and provides search functionality
- **Index_State**: A JSON file tracking the last indexed timestamp for each file to enable incremental indexing
- **SMB**: Server Message Block protocol used for network file sharing in Windows environments
- **Supported_File**: A file with extension .pdf, .docx, .xlsx, .txt, or .csv
- **Source_Tag**: A metadata field identifying the origin of indexed content (e.g., "local", "network_drive")

## Requirements

### Requirement 1: SMB Connection Management

**User Story:** As a system administrator, I want to establish secure connections to network drives, so that the system can access shared documents for indexing.

#### Acceptance Criteria

1. WHEN the Network_Drive_Connector is initialized with host, share, username, password, and optional domain parameters, THE Network_Drive_Connector SHALL store the credentials without attempting connection
2. WHEN the connect method is invoked, THE Network_Drive_Connector SHALL attempt to establish an SMB connection to the specified host and share
3. WHEN the SMB connection succeeds, THE connect method SHALL return True
4. IF the SMB connection fails, THEN THE Network_Drive_Connector SHALL log the error details and return False
5. WHEN the disconnect method is invoked, THE Network_Drive_Connector SHALL close the active SMB connection

### Requirement 2: File Discovery

**User Story:** As a system administrator, I want to discover all supported documents on the network drive, so that they can be indexed for search.

#### Acceptance Criteria

1. WHEN the list_files method is invoked with a remote_path parameter, THE Network_Drive_Connector SHALL recursively traverse all subdirectories starting from that path
2. WHERE extensions parameter is provided, THE Network_Drive_Connector SHALL filter files to match only the specified extensions
3. WHERE extensions parameter is not provided, THE Network_Drive_Connector SHALL default to extensions {.pdf, .docx, .xlsx, .txt, .csv}
4. WHEN file discovery completes, THE Network_Drive_Connector SHALL return a list of full SMB paths for all matching files
5. IF file discovery encounters an access error for a directory, THEN THE Network_Drive_Connector SHALL log the error and continue traversing accessible directories

### Requirement 3: File Download

**User Story:** As a system administrator, I want to download files from the network drive to local cache, so that they can be processed by the text extraction pipeline.

#### Acceptance Criteria

1. WHEN the download_file method is invoked with a remote_path and local_cache_dir, THE Network_Drive_Connector SHALL download the file from the network share to the local cache directory
2. WHEN the download succeeds, THE download_file method SHALL return the local file path
3. IF the download fails, THEN THE Network_Drive_Connector SHALL log the error and return None
4. WHEN downloading a file, THE Network_Drive_Connector SHALL preserve the original filename in the local cache directory

### Requirement 4: File Modification Tracking

**User Story:** As a system administrator, I want to track file modification times, so that the system only re-indexes files that have changed since the last indexing operation.

#### Acceptance Criteria

1. WHEN the get_file_modified_time method is invoked with a remote_path, THE Network_Drive_Connector SHALL query the SMB share for the file's last modified timestamp
2. WHEN the timestamp query succeeds, THE get_file_modified_time method SHALL return a datetime object
3. IF the timestamp query fails, THEN THE Network_Drive_Connector SHALL log the error and return None

### Requirement 5: Incremental Indexing

**User Story:** As a system administrator, I want the system to perform incremental indexing, so that only new or modified files are processed and indexing operations complete faster.

#### Acceptance Criteria

1. WHEN the Indexing_Service begins indexing, THE Indexing_Service SHALL load the Index_State from the JSON file at cache_dir/index_state.json
2. WHERE the Index_State file does not exist, THE Indexing_Service SHALL treat all discovered files as new
3. WHEN comparing a discovered file against Index_State, THE Indexing_Service SHALL download and process the file only if the file's modified timestamp is newer than the last indexed timestamp
4. WHEN a file is successfully indexed, THE Indexing_Service SHALL update the Index_State with the current timestamp for that file
5. WHEN indexing completes, THE Indexing_Service SHALL persist the updated Index_State to the JSON file

### Requirement 6: Document Processing Pipeline

**User Story:** As a system administrator, I want network drive files to be processed through the existing text extraction pipeline, so that they are indexed consistently with local files.

#### Acceptance Criteria

1. WHEN a file is downloaded from the network drive, THE Indexing_Service SHALL invoke the extract_text function to extract text content
2. WHEN text extraction completes, THE Indexing_Service SHALL invoke the make_chunks function to create overlapping text chunks
3. WHEN chunks are created, THE Indexing_Service SHALL set the source metadata field to "network_drive"
4. WHEN chunks are embedded, THE Indexing_Service SHALL use the Search_Service embedding model to generate vector representations
5. WHEN vectors are generated, THE Indexing_Service SHALL upsert the chunks and vectors into the Vector_Store

### Requirement 7: Vector Store Management

**User Story:** As a system administrator, I want old vectors to be removed when files are re-indexed, so that the vector store does not contain duplicate or stale content.

#### Acceptance Criteria

1. WHEN a file is being re-indexed, THE Indexing_Service SHALL invoke delete_by_source on the Vector_Store with a file-specific filter before upserting new vectors
2. WHEN deletion completes, THE Indexing_Service SHALL proceed with upserting the new vectors for the file

### Requirement 8: Indexing Summary

**User Story:** As a system administrator, I want to receive a summary of indexing operations, so that I can verify the results and troubleshoot issues.

#### Acceptance Criteria

1. WHEN indexing completes, THE Indexing_Service SHALL return a summary dictionary containing the count of indexed files
2. WHEN indexing completes, THE Indexing_Service SHALL return a summary dictionary containing the count of skipped files
3. WHEN indexing completes, THE Indexing_Service SHALL return a summary dictionary containing the count of failed files
4. WHEN indexing completes, THE Indexing_Service SHALL return a summary dictionary containing the total count of discovered files

### Requirement 9: Environment Configuration

**User Story:** As a system administrator, I want to configure network drive credentials through environment variables, so that sensitive information is not hardcoded in the application.

#### Acceptance Criteria

1. THE system SHALL read the NETWORK_DRIVE_HOST environment variable to determine the SMB server address
2. THE system SHALL read the NETWORK_DRIVE_SHARE environment variable to determine the share name
3. THE system SHALL read the NETWORK_DRIVE_USERNAME environment variable to determine the authentication username
4. THE system SHALL read the NETWORK_DRIVE_PASSWORD environment variable to determine the authentication password
5. THE system SHALL read the NETWORK_DRIVE_DOMAIN environment variable to determine the authentication domain
6. WHERE NETWORK_DRIVE_DOMAIN is not provided, THE system SHALL use an empty string as the domain
7. THE system SHALL read the NETWORK_DRIVE_ENABLED environment variable to determine if the network drive connector is enabled

### Requirement 10: API Endpoint for Triggering Indexing

**User Story:** As a system administrator, I want to trigger network drive indexing through an API endpoint, so that I can initiate indexing operations programmatically or through the UI.

#### Acceptance Criteria

1. THE system SHALL provide a POST endpoint at /api/index/network-drive
2. WHEN the endpoint receives a request, THE system SHALL check the NETWORK_DRIVE_ENABLED environment variable
3. IF NETWORK_DRIVE_ENABLED is false or not set, THEN THE system SHALL return a 400 status code with an error message
4. WHEN NETWORK_DRIVE_ENABLED is true, THE system SHALL initiate the indexing operation as a background task
5. WHEN the background task is created, THE system SHALL return a task_id for polling the operation status
6. WHEN the background task is created, THE system SHALL return a 200 status code

### Requirement 11: API Endpoint for Connector Status

**User Story:** As a system administrator, I want to check the status of connectors through an API endpoint, so that I can monitor which connectors are enabled and when they last indexed content.

#### Acceptance Criteria

1. THE system SHALL provide a GET endpoint at /api/connectors/status
2. WHEN the endpoint receives a request, THE system SHALL return a JSON object containing connector status information
3. WHEN the endpoint receives a request, THE system SHALL include the enabled status for the network_drive connector
4. WHERE the network_drive connector has completed at least one indexing operation, THE system SHALL include the last indexed timestamp in the response
5. WHERE the network_drive connector has not completed any indexing operations, THE system SHALL indicate that no indexing has occurred

### Requirement 12: Error Handling and Resilience

**User Story:** As a system administrator, I want the system to handle network errors gracefully, so that temporary connectivity issues do not crash the application.

#### Acceptance Criteria

1. IF the network drive is unreachable during connection, THEN THE system SHALL log the error and return a clear error message without crashing
2. IF the network drive becomes unreachable during file listing, THEN THE system SHALL log the error and return a partial list of discovered files
3. IF a file download fails, THEN THE system SHALL log the error, increment the failed count, and continue processing remaining files
4. IF text extraction fails for a file, THEN THE system SHALL log the error, increment the failed count, and continue processing remaining files
5. WHEN any error occurs during indexing, THE system SHALL ensure the Index_State file remains in a consistent state

### Requirement 13: Cache Directory Management

**User Story:** As a system administrator, I want the system to manage the local cache directory automatically, so that I do not need to manually create directories before indexing.

#### Acceptance Criteria

1. WHEN the Indexing_Service begins indexing, THE Indexing_Service SHALL check if the cache directory exists
2. WHERE the cache directory does not exist, THE Indexing_Service SHALL create the cache directory and all necessary parent directories
3. WHEN the cache directory is created, THE Indexing_Service SHALL proceed with the indexing operation
