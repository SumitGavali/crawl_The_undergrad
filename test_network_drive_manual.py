#!/usr/bin/env python3
"""
Manual test script for NetworkDriveConnector.list_files() method.

This script demonstrates the usage of the list_files() method and can be used
for manual testing when a real SMB network share is available.

Usage:
    python test_network_drive_manual.py
"""

import logging
import os
from connectors.network_drive import NetworkDriveConnector

# Set up logging to see debug messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def test_list_files():
    """Test the list_files method with mock or real credentials."""
    
    # These would normally come from environment variables
    # For testing, you can set them here or use environment variables
    host = os.getenv("NETWORK_DRIVE_HOST", "test-server")
    share = os.getenv("NETWORK_DRIVE_SHARE", "test-share")
    username = os.getenv("NETWORK_DRIVE_USERNAME", "test-user")
    password = os.getenv("NETWORK_DRIVE_PASSWORD", "test-pass")
    domain = os.getenv("NETWORK_DRIVE_DOMAIN", "")
    
    print("=" * 80)
    print("NetworkDriveConnector.list_files() Manual Test")
    print("=" * 80)
    print(f"Host: {host}")
    print(f"Share: {share}")
    print(f"Username: {username}")
    print(f"Domain: {domain or '(none)'}")
    print("=" * 80)
    
    # Create connector
    connector = NetworkDriveConnector(
        host=host,
        share=share,
        username=username,
        password=password,
        domain=domain
    )
    
    # Test 1: Verify connection required
    print("\nTest 1: Calling list_files() without connection (should raise RuntimeError)")
    try:
        files = connector.list_files()
        print("❌ FAILED: Expected RuntimeError but got result")
    except RuntimeError as e:
        print(f"✅ PASSED: Got expected RuntimeError: {e}")
    
    # Test 2: Connect and list files
    print("\nTest 2: Connect and list files with default extensions")
    if connector.connect():
        print("✅ Connection successful")
        
        try:
            files = connector.list_files(remote_path="/")
            print(f"✅ Found {len(files)} files with default extensions")
            
            # Show first 5 files as sample
            if files:
                print("\nSample files (first 5):")
                for i, file_path in enumerate(files[:5], 1):
                    print(f"  {i}. {file_path}")
            else:
                print("  (no files found)")
        except Exception as e:
            print(f"❌ FAILED: {e}")
        
        # Test 3: List files with custom extensions
        print("\nTest 3: List files with custom extensions (.txt, .pdf)")
        try:
            files = connector.list_files(
                remote_path="/",
                extensions={".txt", ".pdf"}
            )
            print(f"✅ Found {len(files)} files with .txt and .pdf extensions")
            
            # Show first 5 files as sample
            if files:
                print("\nSample files (first 5):")
                for i, file_path in enumerate(files[:5], 1):
                    print(f"  {i}. {file_path}")
            else:
                print("  (no files found)")
        except Exception as e:
            print(f"❌ FAILED: {e}")
        
        # Disconnect
        connector.disconnect()
        print("\n✅ Disconnected")
    else:
        print("❌ Connection failed - cannot proceed with tests")
        print("   (This is expected if no real SMB server is configured)")
    
    print("\n" + "=" * 80)
    print("Manual test complete")
    print("=" * 80)

if __name__ == "__main__":
    test_list_files()
