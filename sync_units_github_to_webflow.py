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
import hashlib
import io
from typing import Dict, List, Optional, Any
from pathlib import Path
from dotenv import load_dotenv
from PIL import Image

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
    "mass": "mass",
    "cloakcost": "cloak-cost",
    # paralyzemultiplier is in customparams, handled separately
}

# Fields to skip (weapon-related, managed manually)
SKIP_FIELDS = ["weapons", "dps", "weaponrange"]


class GitHubUnitFetcher:
    """Fetches unit .lua files from GitHub repository."""
    
    def __init__(self, repo: str, branch: str, github_token: Optional[str] = None, cache_file: str = ".unit_cache.json"):
        self.repo = repo
        self.branch = branch
        self.base_url = f"https://api.github.com/repos/{repo}"
        self.raw_url = f"https://raw.githubusercontent.com/{repo}/refs/heads/{branch}"
        self.github_token = github_token
        self._file_cache = None  # In-memory cache for unit files
        self.cache_file = cache_file  # Persistent cache file
        
        # Setup headers with auth if token provided
        self.headers = {}
        if self.github_token:
            self.headers['Authorization'] = f'token {self.github_token}'
        
        # Load cache from file if it exists
        self._load_cache_from_file()
        # Load cache from file if it exists
        self._load_cache_from_file()
        
    def _load_cache_from_file(self):
        """Load cached unit files from JSON file if it exists."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    cache_data = json.load(f)
                    # Verify cache is for the same repo and branch
                    if cache_data.get('repo') == self.repo and cache_data.get('branch') == self.branch:
                        self._file_cache = cache_data.get('files', [])
                        print(f"  💾 Loaded {len(self._file_cache)} unit files from cache")
                    else:
                        print(f"  ⚠️  Cache is for different repo/branch - will rebuild")
            except Exception as e:
                print(f"  ⚠️  Failed to load cache: {e}")
    
    def _save_cache_to_file(self):
        """Save cached unit files to JSON file."""
        try:
            cache_data = {
                'repo': self.repo,
                'branch': self.branch,
                'files': self._file_cache
            }
            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            print(f"  💾 Saved {len(self._file_cache)} unit files to cache")
        except Exception as e:
            print(f"  ⚠️  Failed to save cache: {e}")
    
    def clear_cache(self):
        """Clear both in-memory and file cache."""
        self._file_cache = None
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)
            print(f"  🗑️  Cache cleared")
        
    def get_unit_files(self, path: str) -> List[Dict[str, str]]:
        """
        Recursively get all .lua files from the units directory.
        Returns list of dicts with 'name' and 'path' keys.
        Uses persistent caching to avoid repeated API calls.
        """
        # Return cached result if available
        if self._file_cache is not None:
            return self._file_cache
        
        print("  📥 Fetching unit file list from GitHub (this may take a moment)...")
        unit_files = self._get_unit_files_recursive(path)
        
        # Cache the result in memory and save to file
        self._file_cache = unit_files
        self._save_cache_to_file()
        print(f"  ✅ Found {len(unit_files)} unit files (cached for future use)")
        
        return unit_files
    
    def _get_unit_files_recursive(self, path: str) -> List[Dict[str, str]]:
        """Internal recursive method to fetch unit files."""
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
                    unit_files.extend(self._get_unit_files_recursive(item['path']))
                    
        except Exception as e:
            print(f"Error fetching unit files from {path}: {e}")
            
        return unit_files
    
    def find_unit_file(self, unit_name: str, path: str = "units") -> Optional[Dict[str, str]]:
        """
        Fast search for a specific unit file.
        First checks cache, then does targeted search.
        Returns dict with 'name', 'path', 'download_url' or None if not found.
        """
        # Check cache first
        if self._file_cache:
            for file_info in self._file_cache:
                if file_info['name'] == unit_name:
                    return file_info
        
        # If not in cache, do a fresh search
        print(f"  🔍 Searching for {unit_name}.lua in GitHub...")
        all_files = self.get_unit_files(path)
        
        for file_info in all_files:
            if file_info['name'] == unit_name:
                return file_info
        
        return None
    
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
    def extract_balanced_braces(text: str, start_pos: int) -> Optional[str]:
        """
        Extract content between balanced braces starting at start_pos.
        Handles nested tables correctly.
        """
        depth = 0
        start = None
        for i in range(start_pos, len(text)):
            if text[i] == '{':
                if depth == 0:
                    start = i + 1
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i]
        return None
    
    @staticmethod
    def parse_unit_file(content: str, unit_name: str) -> Optional[Dict[str, Any]]:
        """
        Parse a Lua unit definition file and extract relevant data.
        Returns a dict with the unit data, including customparams.
        """
        try:
            unit_data = {}
            
            # Find the unit definition start using balanced brace extraction
            unit_pattern = rf"{unit_name}\s*=\s*\{{"
            match = re.search(unit_pattern, content)
            
            if not match:
                print(f"Could not find unit definition for {unit_name}")
                return None
            
            # Extract the full unit block with proper brace balancing
            unit_block = LuaParser.extract_balanced_braces(content, match.end() - 1)
            
            if not unit_block:
                print(f"Could not extract unit block for {unit_name}")
                return None
            
            # Extract customparams block first (if it exists)
            customparams_pattern = r'customparams\s*=\s*\{'
            customparams_match = re.search(customparams_pattern, unit_block, re.IGNORECASE)
            
            if customparams_match:
                customparams_block = LuaParser.extract_balanced_braces(unit_block, customparams_match.end() - 1)
                if customparams_block:
                    # Extract paralyzemultiplier from customparams
                    param_pattern = r'paralyzemultiplier\s*=\s*([0-9.]+)'
                    param_match = re.search(param_pattern, customparams_block, re.IGNORECASE)
                    if param_match:
                        unit_data['paralyzemultiplier'] = float(param_match.group(1))
            
            # Extract key-value pairs (excluding nested tables like customparams)
            # Pattern for simple key = value pairs (not followed by {)
            kv_pattern = r'(\w+)\s*=\s*([^,\n{]+)'
            
            for match in re.finditer(kv_pattern, unit_block):
                key = match.group(1).strip()
                value = match.group(2).strip()
                
                # Skip if this is the start of a nested table
                if key.lower() == 'customparams':
                    continue
                
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
        """
        Publish a single CMS item using Webflow's V2 API.
        This publishes ONLY this specific item, not the entire site.
        
        Uses the /items/publish endpoint with itemIds in the request body.
        """
        try:
            url = f"{self.base_url}/collections/{self.collection_id}/items/publish"
            
            payload = {
                "itemIds": [item_id]
            }
            
            response = requests.post(url, headers=self.headers, json=payload)
            response.raise_for_status()
            
            # Check response for errors
            data = response.json()
            if data.get('errors'):
                print(f"  Warning: Publish had errors: {data['errors']}")
                return False
            
            return True
            
        except Exception as e:
            print(f"Error publishing item {item_id}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            return False


class IconTypesParser:
    """Parses icontypes.lua to get icon paths for units."""
    
    @staticmethod
    def fetch_icontypes(repo: str, branch: str, github_token: Optional[str] = None) -> Optional[str]:
        """Fetch icontypes.lua from GitHub."""
        try:
            url = f"https://raw.githubusercontent.com/{repo}/refs/heads/{branch}/gamedata/icontypes.lua"
            headers = {}
            if github_token:
                headers['Authorization'] = f'token {github_token}'
            
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"Error fetching icontypes.lua: {e}")
            return None
    
    @staticmethod
    def parse_icontypes(content: str) -> Dict[str, str]:
        """
        Parse icontypes.lua and return dict of unit_name -> icon_path.
        
        Example format:
        local icontypes = {
            armepoch = {
                bitmap = "icons/ship_t2_flagship.png",
                size = 3.46499968
            },
        }
        """
        icon_map = {}
        
        try:
            # Find the icontypes table
            pattern = r'local\s+icontypes\s*=\s*\{'
            match = re.search(pattern, content)
            if not match:
                return icon_map
            
            # Extract content after "local icontypes = {"
            start_pos = match.end() - 1
            icontypes_block = IconTypesParser._extract_balanced_braces(content, start_pos)
            
            if not icontypes_block:
                return icon_map
            
            # Pattern to match: unitname = { bitmap = "path", ... }
            unit_pattern = r'(\w+)\s*=\s*\{'
            
            for unit_match in re.finditer(unit_pattern, icontypes_block):
                unit_name = unit_match.group(1)
                
                # Extract the unit's block
                unit_block_start = unit_match.end() - 1
                unit_block = IconTypesParser._extract_balanced_braces(icontypes_block, unit_block_start)
                
                if unit_block:
                    # Extract bitmap path
                    bitmap_pattern = r'bitmap\s*=\s*"([^"]+)"'
                    bitmap_match = re.search(bitmap_pattern, unit_block)
                    
                    if bitmap_match:
                        icon_path = bitmap_match.group(1)
                        icon_map[unit_name] = icon_path
            
            return icon_map
            
        except Exception as e:
            print(f"Error parsing icontypes.lua: {e}")
            return icon_map
    
    @staticmethod
    def _extract_balanced_braces(text: str, start_pos: int) -> Optional[str]:
        """Extract content between balanced braces."""
        depth = 0
        start = None
        for i in range(start_pos, len(text)):
            if text[i] == '{':
                if depth == 0:
                    start = i + 1
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i]
        return None


class ImageConverter:
    """Handles image conversion from PNG to WebP."""
    
    @staticmethod
    def png_to_webp(png_data: bytes, quality: int = 80) -> bytes:
        """
        Convert PNG to WebP with specified quality.
        
        Args:
            png_data: PNG image as bytes
            quality: WebP quality (1-100), default 80
            
        Returns:
            WebP image as bytes
        """
        try:
            # Open PNG from bytes
            img = Image.open(io.BytesIO(png_data))
            
            # Convert to RGB if necessary (WebP doesn't support P mode)
            if img.mode in ('P', 'PA'):
                img = img.convert('RGBA')
            
            # Save as WebP to bytes
            output = io.BytesIO()
            img.save(output, format='WEBP', quality=quality, method=6)
            
            return output.getvalue()
            
        except Exception as e:
            print(f"Error converting PNG to WebP: {e}")
            return None
    
    @staticmethod
    def generate_md5_hash(data: bytes) -> str:
        """Generate MD5 hash from bytes."""
        return hashlib.md5(data).hexdigest()


class WebflowAssetsAPI:
    """Handles Webflow Assets API operations."""
    
    def __init__(self, api_token: str, site_id: str):
        self.api_token = api_token
        self.site_id = site_id
        self.base_url = "https://api.webflow.com/v2"
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "accept": "application/json"
        }
    
    def upload_asset(self, file_data: bytes, filename: str, parent_folder: Optional[str] = None) -> Optional[str]:
        """
        Upload an asset to Webflow (2-step process).
        
        Args:
            file_data: Asset file as bytes
            filename: Filename including extension
            parent_folder: Optional folder ID to organize asset
            
        Returns:
            Asset ID if successful, None otherwise
        """
        try:
            # Step 1: Create asset metadata
            file_hash = ImageConverter.generate_md5_hash(file_data)
            
            payload = {
                "fileName": filename,
                "fileHash": file_hash
            }
            
            if parent_folder:
                payload["parentFolder"] = parent_folder
            
            url = f"{self.base_url}/sites/{self.site_id}/assets"
            response = requests.post(url, headers=self.headers, json=payload)
            response.raise_for_status()
            
            result = response.json()
            upload_url = result.get('uploadUrl')
            upload_details = result.get('uploadDetails')
            asset_id = result.get('id')
            
            if not upload_url or not upload_details:
                print(f"  ❌ Failed to get upload URL")
                return None
            
            # Step 2: Upload file to S3
            s3_headers = upload_details
            s3_response = requests.post(upload_url, headers=s3_headers, data=file_data)
            s3_response.raise_for_status()
            
            print(f"  ✅ Uploaded asset: {filename} (ID: {asset_id})")
            return asset_id
            
        except Exception as e:
            print(f"  ❌ Error uploading asset {filename}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"     Response: {e.response.text}")
            return None


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
                # Integer fields
                if webflow_key in ["energy-cost", "metal-cost", "build-cost", "energy-make", 
                                   "buildpower", "health", "speed", "sightrange", "radarrange", 
                                   "metal-make", "jammerrange", "mass", "cloak-cost"]:
                    try:
                        # Convert to int if it's a number
                        if isinstance(value, (int, float)):
                            value = int(value)
                    except:
                        pass
                
                webflow_fields[webflow_key] = value
        
        # Handle paralyzemultiplier separately (from customparams, decimal field)
        if 'paralyzemultiplier' in github_data:
            try:
                webflow_fields['paralyze-multiplier'] = float(github_data['paralyzemultiplier'])
            except:
                pass
        
        return webflow_fields
    
    def sync_unit_icon(self, unit_name: str, icon_path: str, webflow_assets: WebflowAssetsAPI, 
                       current_icon_id: Optional[str] = None, dry_run: bool = False) -> Optional[str]:
        """
        Sync a unit's strategic icon.
        
        Args:
            unit_name: Name of the unit
            icon_path: Path to icon PNG in GitHub (e.g., "icons/ship_t2_flagship.png")
            webflow_assets: WebflowAssetsAPI instance
            current_icon_id: Current icon asset ID in Webflow (if any)
            dry_run: If True, don't actually upload
            
        Returns:
            Asset ID if successful, None otherwise
        """
        try:
            # Download PNG from GitHub
            png_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/refs/heads/{GITHUB_BRANCH}/{icon_path}"
            
            headers = {}
            github_token = os.environ.get("GITHUB_TOKEN")
            if github_token:
                headers['Authorization'] = f'token {github_token}'
            
            print(f"    📥 Downloading icon: {icon_path}")
            response = requests.get(png_url, headers=headers)
            response.raise_for_status()
            png_data = response.content
            
            # Convert PNG to WebP (80% quality)
            print(f"    🔄 Converting PNG → WebP (80% quality)")
            webp_data = ImageConverter.png_to_webp(png_data, quality=80)
            
            if not webp_data:
                print(f"    ❌ Failed to convert image")
                return None
            
            # Generate filename
            webp_filename = f"{unit_name}.webp"
            
            if dry_run:
                print(f"    ℹ️  Would upload: {webp_filename} ({len(webp_data)} bytes)")
                return "dry-run-asset-id"
            
            # Upload to Webflow Assets
            print(f"    📤 Uploading to Webflow Assets: {webp_filename}")
            asset_id = webflow_assets.upload_asset(webp_data, webp_filename)
            
            return asset_id
            
        except Exception as e:
            print(f"    ❌ Error syncing icon: {e}")
            return None
    
    def sync_all_units(self, dry_run: bool = False, auto_publish: bool = False, sync_icons: bool = False):
        """
        Sync all units from GitHub to Webflow.
        
        Args:
            dry_run: If True, show what would be updated without making changes
            auto_publish: If True, automatically publish updated items
            sync_icons: If True, also sync strategic icons from icontypes.lua
        """
        print("=" * 80)
        print("Beyond All Reason - Unit Data Sync")
        print("=" * 80)
        print()
        
        # Step 1: Fetch icontypes if icon sync enabled
        icon_map = {}
        webflow_assets = None
        
        if sync_icons:
            print("Step 1a: Fetching icontypes.lua for icon paths...")
            github_token = os.environ.get("GITHUB_TOKEN")
            icontypes_content = IconTypesParser.fetch_icontypes(GITHUB_REPO, GITHUB_BRANCH, github_token)
            
            if icontypes_content:
                icon_map = IconTypesParser.parse_icontypes(icontypes_content)
                print(f"Found {len(icon_map)} unit icons in icontypes.lua")
                
                # Initialize Webflow Assets API
                webflow_assets = WebflowAssetsAPI(self.webflow.api_token, self.webflow.site_id)
            else:
                print("⚠️  Failed to fetch icontypes.lua - icon sync disabled")
                sync_icons = False
            print()
        
        # Step 1b: Fetch all unit files from GitHub
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
                    
                    # Sync icon if enabled and icon exists for this unit
                    if sync_icons and unit_name in icon_map and webflow_assets:
                        icon_path = icon_map[unit_name]
                        current_icon = current_data.get('icon')
                        
                        print(f"  🎨 Syncing strategic icon...")
                        asset_id = self.sync_unit_icon(
                            unit_name, 
                            icon_path, 
                            webflow_assets,
                            current_icon,
                            dry_run
                        )
                        
                        if asset_id:
                            # Update icon field
                            icon_update = {"icon": asset_id}
                            if self.webflow.update_item(item_id, icon_update):
                                print(f"  ✅ Icon updated")
                            else:
                                print(f"  ⚠️  Failed to link icon")
                    
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
    
    parser.add_argument(
        '--clear-cache',
        action='store_true',
        help='Clear the unit file cache before syncing'
    )
    
    parser.add_argument(
        '--sync-icons',
        action='store_true',
        help='Also sync strategic icons from icontypes.lua (PNG → WebP conversion)'
    )
    
    args = parser.parse_args()
    
    # Get API token
    api_token = args.token or WEBFLOW_API_TOKEN
    if not api_token:
        print("Error: Webflow API token required")
        print("Set WEBFLOW_API_TOKEN environment variable or use --token")
        sys.exit(1)
    
    # Check for auto-publish setting from environment if --publish not explicitly set
    auto_publish = args.publish
    if not auto_publish:
        env_auto_publish = os.environ.get("AUTO_PUBLISH", "false").lower()
        auto_publish = env_auto_publish in ("true", "1", "yes")
    
    # Initialize services
    github_fetcher = GitHubUnitFetcher(GITHUB_REPO, GITHUB_BRANCH, github_token=os.environ.get("GITHUB_TOKEN"))
    
    # Clear cache if requested
    if args.clear_cache:
        github_fetcher.clear_cache()
        print()
    
    webflow_api = WebflowAPI(api_token, WEBFLOW_SITE_ID, WEBFLOW_COLLECTION_ID)
    sync_service = UnitSyncService(github_fetcher, webflow_api)
    
    # Run sync
    sync_service.sync_all_units(
        dry_run=args.dry_run,
        auto_publish=auto_publish,
        sync_icons=args.sync_icons
    )


if __name__ == "__main__":
    main()
