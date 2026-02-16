#!/usr/bin/env python3
"""
Beyond All Reason - GitHub to Webflow Unit Data Sync Script
============================================================

This script syncs unit data from the Beyond All Reason GitHub repository
to the Webflow CMS Units collection.

It fetches .lua files from GitHub, parses the unit definitions, and updates
the corresponding items in Webflow.

Author: Generated for BAR project
Date: 2026-02-12
"""

import os
import sys
import requests
import re
import json
from typing import Dict, List, Optional, Any
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration
GITHUB_REPO = "beyond-all-reason/Beyond-All-Reason"
GITHUB_BRANCH = "master"
GITHUB_UNITS_PATH = "units"
WEBFLOW_SITE_ID = "5c68622246b367adf6f3041d"
WEBFLOW_COLLECTION_ID = "6564c6553676389f8ba45a9e"  # Units collection
WEBFLOW_API_TOKEN = os.environ.get("WEBFLOW_API_TOKEN", "")

# Field mapping: GitHub lua field → Webflow CMS field slug
# Only includes fields that exist in the Webflow Units collection
FIELD_MAPPING = {
    "energycost": "energy-cost",
    "metalcost": "metal-cost",
    "buildtime": "build-cost",
    "energymake": "energy-make",
    "workertime": "buildpower",
    "health": "health",
    "speed": "speed",
    "sightdistance": "sightrange",
    "radardistance": "radarrange",
    "sonardistance": "metal-make",  # Note: Sonarrange field has slug "metal-make"
    "jammerdistance": "jammerrange",
    # Note: The following fields don't exist in Webflow yet:
    # "mass", "paralyzemultiplier", "cloakcost"
    # Add them to Webflow CMS first if you want to sync them
}

# Fields to skip (weapon-related, managed manually)
SKIP_FIELDS = ["weapons", "dps", "weaponrange"]


class GitHubUnitFetcher:
    """Fetches unit .lua files from GitHub repository."""
    
    def __init__(self, repo: str, branch: str, github_token: Optional[str] = None):
        self.repo = repo
        self.branch = branch
        self.base_url = f"https://api.github.com/repos/{repo}"
        self.raw_url = f"https://raw.githubusercontent.com/{repo}/refs/heads/{branch}"
        self.github_token = github_token
        
        # Setup headers with auth if token provided
        self.headers = {}
        if self.github_token:
            self.headers['Authorization'] = f'token {self.github_token}'
        
    def get_unit_files(self, path: str) -> List[Dict[str, str]]:
        """
        Recursively get all .lua files from the units directory.
        Returns list of dicts with 'name' and 'path' keys.
        """
        unit_files = []
        
        try:
            url = f"{self.base_url}/contents/{path}?ref={self.branch}"
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            items = response.json()
            
            for item in items:
                if item['type'] == 'file' and item['name'].endswith('.lua'):
                    # Extract unit name from filename (remove .lua extension)
                    unit_name = item['name'].replace('.lua', '')
                    unit_files.append({
                        'name': unit_name,
                        'path': item['path'],
                        'download_url': item['download_url']
                    })
                elif item['type'] == 'dir':
                    # Recursively search subdirectories
                    unit_files.extend(self.get_unit_files(item['path']))
                    
        except Exception as e:
            print(f"Error fetching unit files from {path}: {e}")
            
        return unit_files
    
    def fetch_unit_data(self, file_path: str) -> Optional[str]:
        """Fetch the content of a unit .lua file."""
        try:
            url = f"{self.raw_url}/{file_path}"
            response = requests.get(url)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"Error fetching {file_path}: {e}")
            return None


class LuaParser:
    """Parses Lua unit definition files."""
    
    @staticmethod
    def parse_unit_file(content: str, unit_name: str) -> Optional[Dict[str, Any]]:
        """
        Parse a Lua unit definition file and extract relevant data.
        Returns a dict with the unit data.
        """
        try:
            # The file should contain: return { unitname = { ... } }
            # We need to extract the values from the table
            
            unit_data = {}
            
            # Find the unit definition block
            pattern = rf"{unit_name}\s*=\s*\{{(.*?)\}}(?:,|\s*\}})"
            match = re.search(pattern, content, re.DOTALL)
            
            if not match:
                print(f"Could not find unit definition for {unit_name}")
                return None
            
            unit_block = match.group(1)
            
            # Extract key-value pairs
            # Pattern for simple key = value pairs
            kv_pattern = r'(\w+)\s*=\s*([^,\n]+)'
            
            for match in re.finditer(kv_pattern, unit_block):
                key = match.group(1).strip()
                value = match.group(2).strip()
                
                # Clean up the value
                value = value.rstrip(',')
                
                # Remove quotes from strings
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                # Convert numbers
                elif value.replace('.', '', 1).replace('-', '', 1).isdigit():
                    if '.' in value:
                        value = float(value)
                    else:
                        value = int(value)
                # Convert booleans
                elif value.lower() == 'true':
                    value = True
                elif value.lower() == 'false':
                    value = False
                
                unit_data[key] = value
            
            return unit_data
            
        except Exception as e:
            print(f"Error parsing unit file for {unit_name}: {e}")
            return None


class WebflowAPI:
    """Handles Webflow API interactions."""
    
    def __init__(self, api_token: str, site_id: str, collection_id: str):
        self.api_token = api_token
        self.site_id = site_id
        self.collection_id = collection_id
        self.base_url = "https://api.webflow.com/v2"
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "accept": "application/json"
        }
    
    def get_all_items(self) -> List[Dict]:
        """Fetch all items from the Units collection."""
        items = []
        offset = 0
        limit = 100
        
        while True:
            try:
                url = f"{self.base_url}/collections/{self.collection_id}/items"
                params = {"offset": offset, "limit": limit}
                
                response = requests.get(url, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                
                items.extend(data.get('items', []))
                
                # Check if there are more items
                total = data.get('pagination', {}).get('total', 0)
                if offset + limit >= total:
                    break
                    
                offset += limit
                
            except Exception as e:
                print(f"Error fetching Webflow items: {e}")
                break
        
        return items
    
    def update_item(self, item_id: str, field_data: Dict) -> bool:
        """Update a single item in the collection."""
        try:
            url = f"{self.base_url}/collections/{self.collection_id}/items/{item_id}"
            
            payload = {
                "fieldData": field_data
            }
            
            response = requests.patch(url, headers=self.headers, json=payload)
            response.raise_for_status()
            
            return True
            
        except Exception as e:
            print(f"Error updating item {item_id}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            return False
    
    def publish_item(self, item_id: str) -> bool:
        """Publish a single item."""
        try:
            url = f"{self.base_url}/collections/{self.collection_id}/items/publish"
            
            payload = {
                "itemIds": [item_id]
            }
            
            response = requests.post(url, headers=self.headers, json=payload)
            response.raise_for_status()
            
            return True
            
        except Exception as e:
            print(f"Error publishing item {item_id}: {e}")
            return False


class UnitSyncService:
    """Main service to sync units from GitHub to Webflow."""
    
    def __init__(self, github_fetcher: GitHubUnitFetcher, webflow_api: WebflowAPI):
        self.github = github_fetcher
        self.webflow = webflow_api
        self.parser = LuaParser()
    
    def map_github_to_webflow_fields(self, github_data: Dict) -> Dict:
        """Map GitHub unit data to Webflow field structure."""
        webflow_fields = {}
        
        for github_key, webflow_key in FIELD_MAPPING.items():
            if github_key in github_data:
                value = github_data[github_key]
                
                # Ensure numeric values are properly typed
                if webflow_key in ["energy-cost", "metal-cost", "build-cost", "energy-make", 
                                   "buildpower", "health", "speed", "sightrange", "radarrange", 
                                   "metal-make", "jammerrange"]:
                    try:
                        # Convert to int if it's a number
                        if isinstance(value, (int, float)):
                            value = int(value)
                    except:
                        pass
                
                webflow_fields[webflow_key] = value
        
        return webflow_fields
    
    def sync_all_units(self, dry_run: bool = False, auto_publish: bool = False):
        """
        Sync all units from GitHub to Webflow.
        
        Args:
            dry_run: If True, show what would be updated without making changes
            auto_publish: If True, automatically publish updated items
        """
        print("=" * 80)
        print("Beyond All Reason - Unit Data Sync")
        print("=" * 80)
        print()
        
        # Step 1: Fetch all unit files from GitHub
        print("Step 1: Fetching unit files from GitHub...")
        unit_files = self.github.get_unit_files(GITHUB_UNITS_PATH)
        print(f"Found {len(unit_files)} unit files")
        print()
        
        # Step 2: Fetch all items from Webflow
        print("Step 2: Fetching items from Webflow...")
        webflow_items = self.webflow.get_all_items()
        print(f"Found {len(webflow_items)} items in Webflow")
        
        # Create a lookup dict by unit name
        webflow_lookup = {}
        for item in webflow_items:
            name = item.get('fieldData', {}).get('name', '')
            if name:
                webflow_lookup[name] = item
        
        print()
        
        # Step 3: Process each unit
        print("Step 3: Processing units...")
        print()
        
        stats = {
            'processed': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'not_found': 0
        }
        
        for unit_file in unit_files:
            unit_name = unit_file['name']
            file_path = unit_file['path']
            
            print(f"Processing: {unit_name} ({file_path})")
            
            # Check if unit exists in Webflow
            if unit_name not in webflow_lookup:
                print(f"  ⚠️  Unit '{unit_name}' not found in Webflow - skipping")
                stats['not_found'] += 1
                print()
                continue
            
            # Fetch unit data from GitHub
            lua_content = self.github.fetch_unit_data(file_path)
            if not lua_content:
                print(f"  ❌ Failed to fetch file")
                stats['errors'] += 1
                print()
                continue
            
            # Parse the Lua file
            github_data = self.parser.parse_unit_file(lua_content, unit_name)
            if not github_data:
                print(f"  ❌ Failed to parse file")
                stats['errors'] += 1
                print()
                continue
            
            # Map to Webflow fields
            webflow_fields = self.map_github_to_webflow_fields(github_data)
            
            if not webflow_fields:
                print(f"  ⚠️  No fields to update")
                stats['skipped'] += 1
                print()
                continue
            
            # Get current Webflow data
            webflow_item = webflow_lookup[unit_name]
            current_data = webflow_item.get('fieldData', {})
            
            # Check what's changed
            changes = {}
            for key, new_value in webflow_fields.items():
                current_value = current_data.get(key)
                if current_value != new_value:
                    changes[key] = {
                        'old': current_value,
                        'new': new_value
                    }
            
            if not changes:
                print(f"  ✓ No changes needed")
                stats['skipped'] += 1
                print()
                continue
            
            # Show changes
            print(f"  📝 Changes detected:")
            for key, change in changes.items():
                print(f"     {key}: {change['old']} → {change['new']}")
            
            # Update in Webflow (unless dry run)
            if not dry_run:
                item_id = webflow_item['id']
                success = self.webflow.update_item(item_id, webflow_fields)
                
                if success:
                    print(f"  ✅ Updated successfully")
                    stats['updated'] += 1
                    
                    # Publish if requested
                    if auto_publish:
                        if self.webflow.publish_item(item_id):
                            print(f"  📤 Published successfully")
                        else:
                            print(f"  ⚠️  Failed to publish")
                else:
                    print(f"  ❌ Update failed")
                    stats['errors'] += 1
            else:
                print(f"  ℹ️  Dry run - no changes made")
                stats['updated'] += 1
            
            stats['processed'] += 1
            print()
        
        # Print summary
        print("=" * 80)
        print("Sync Summary")
        print("=" * 80)
        print(f"Total units processed: {stats['processed']}")
        print(f"Updated: {stats['updated']}")
        print(f"Skipped (no changes): {stats['skipped']}")
        print(f"Not found in Webflow: {stats['not_found']}")
        print(f"Errors: {stats['errors']}")
        print()
        
        if dry_run:
            print("ℹ️  This was a DRY RUN - no actual changes were made")
            print("   Run without --dry-run to apply changes")
        print()


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Sync unit data from GitHub to Webflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run (show what would be updated)
  python sync_units_github_to_webflow.py --dry-run
  
  # Sync and update (but don't publish)
  python sync_units_github_to_webflow.py
  
  # Sync, update, and auto-publish
  python sync_units_github_to_webflow.py --publish
  
  # Sync a specific unit
  python sync_units_github_to_webflow.py --unit armfast
        """
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be updated without making changes'
    )
    
    parser.add_argument(
        '--publish',
        action='store_true',
        help='Automatically publish updated items'
    )
    
    parser.add_argument(
        '--unit',
        type=str,
        help='Sync only a specific unit (by name)'
    )
    
    parser.add_argument(
        '--token',
        type=str,
        help='Webflow API token (or set WEBFLOW_API_TOKEN env var)'
    )
    
    args = parser.parse_args()
    
    # Get API token
    api_token = args.token or WEBFLOW_API_TOKEN
    if not api_token:
        print("Error: Webflow API token required")
        print("Set WEBFLOW_API_TOKEN environment variable or use --token")
        sys.exit(1)
    
    # Initialize services
    github_fetcher = GitHubUnitFetcher(GITHUB_REPO, GITHUB_BRANCH, github_token=os.environ.get("GITHUB_TOKEN"))
    webflow_api = WebflowAPI(api_token, WEBFLOW_SITE_ID, WEBFLOW_COLLECTION_ID)
    sync_service = UnitSyncService(github_fetcher, webflow_api)
    
    # Run sync
    sync_service.sync_all_units(
        dry_run=args.dry_run,
        auto_publish=args.publish
    )


if __name__ == "__main__":
    main()
