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
import time
from typing import Dict, List, Optional, Any
from pathlib import Path
from dotenv import load_dotenv
from PIL import Image

# Load environment variables from .env file
load_dotenv()

# Rate limiting for Webflow API (120 req/min = 0.5 sec between requests)
WEBFLOW_RATE_LIMIT_DELAY = 0.5
last_webflow_request_time = 0

def rate_limit_webflow():
    """Enforce rate limit for Webflow API calls (120 req/min = 1 req per 0.5 sec)."""
    global last_webflow_request_time
    current_time = time.time()
    time_since_last_request = current_time - last_webflow_request_time
    
    if time_since_last_request < WEBFLOW_RATE_LIMIT_DELAY:
        time.sleep(WEBFLOW_RATE_LIMIT_DELAY - time_since_last_request)
    
    last_webflow_request_time = time.time()

# Configuration
GITHUB_REPO = "beyond-all-reason/Beyond-All-Reason"
GITHUB_BRANCH = "master"
GITHUB_UNITS_PATH = "units"
WEBFLOW_SITE_ID = "5c68622246b367adf6f3041d"
WEBFLOW_COLLECTION_ID = "6564c6553676389f8ba45a9e"  # Units collection
WEBFLOW_FACTIONS_COLLECTION_ID = "6564c6553676389f8ba45a9f"  # Factions collection
WEBFLOW_API_TOKEN = os.environ.get("WEBFLOW_API_TOKEN", "")

# Faction mapping: unit name prefix → Webflow Faction item ID
# Item IDs fetched from the Factions CMS collection
FACTION_MAP = {
    "arm":    {"id": "6564c6553676389f8ba46320", "name": "Armada"},
    "cor":    {"id": "6564c6553676389f8ba46321", "name": "Cortex"},
    "leg":    {"id": "684fc7cd5025549eba34c053", "name": "Legion"},
    "raptor": {"id": "6564c6553676389f8ba4631f", "name": "CHICKS"},
}

# Units that are always synced regardless of buildoptions
# (e.g. commanders that are spawned directly, not built)
SYNC_WHITELIST = {
    "armcom",
    "corcom",
    "legcom",
}

# UnitType reference field mapping: type slug → Webflow item ID
# Item IDs fetched from the "Unit Types" CMS collection (6564c6553676389f8ba45aa0)
UNIT_TYPE_MAP = {
    "aircraft":   {"id": "6564c6553676389f8ba45aee", "name": "Aircraft"},
    "hovercraft": {"id": "6564c6553676389f8ba45ad4", "name": "Hovercraft"},
    "bot":        {"id": "6564c6553676389f8ba45b2e", "name": "Bots"},
    "vehicle":    {"id": "6564c6553676389f8ba45b03", "name": "Vehicles"},
    "ship":       {"id": "6564c6553676389f8ba45b19", "name": "Ships"},
    "building":   {"id": "6564c6553676389f8ba45fa6", "name": "Buildings"},
    "defense":    {"id": "6564c6553676389f8ba45fa7", "name": "Defenses"},
    "factory":    {"id": "6564c6553676389f8ba45fa8", "name": "Factories"},
    "chicken":    {"id": "6564c6553676389f8ba46085", "name": "Chicken"},
}

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
    # Special fields handled separately:
    # - paralyzemultiplier (in customparams)
    # - techlevel (in customparams)
    # - amphibious (derived from movement type)
}

# Fields to skip (weapon-related, managed manually)
SKIP_FIELDS = ["weapons", "dps", "weaponrange"]


class RateLimiter:
    """Simple rate limiter to stay under Webflow API limits."""
    
    def __init__(self, max_requests_per_minute: int = 110):
        """
        Initialize rate limiter.
        
        Args:
            max_requests_per_minute: Max requests allowed (default 110 for safety margin)
                                     Webflow Business: 120/min, we use 110 for buffer
        """
        self.max_requests = max_requests_per_minute
        self.requests = []
    
    def wait_if_needed(self):
        """Wait if we're approaching rate limit."""
        now = time.time()
        
        # Remove requests older than 1 minute
        self.requests = [req_time for req_time in self.requests if now - req_time < 60]
        
        # If we're at the limit, wait
        if len(self.requests) >= self.max_requests:
            oldest_request = min(self.requests)
            wait_time = 60 - (now - oldest_request) + 0.1  # Extra 100ms buffer
            if wait_time > 0:
                print(f"  ⏸️  Rate limit: waiting {wait_time:.1f}s...")
                time.sleep(wait_time)
                self.requests = []
        
        # Record this request
        self.requests.append(time.time())


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
        """Clear unit file cache and buildable index cache."""
        self._file_cache = None
        cleared = []

        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)
            cleared.append(self.cache_file)

        buildable_cache = ".buildable_cache.json"
        if os.path.exists(buildable_cache):
            os.remove(buildable_cache)
            cleared.append(buildable_cache)

        if cleared:
            for f in cleared:
                print(f"  🗑️  Deleted cache: {f}")
        else:
            print(f"  ℹ️  No cache files found to clear")
        
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
        """Fetch the content of a unit .lua file. Uses in-memory cache to avoid double downloads."""
        if not hasattr(self, '_content_cache'):
            self._content_cache = {}
        if file_path in self._content_cache:
            return self._content_cache[file_path]
        try:
            url = f"{self.raw_url}/{file_path}"
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            content = response.text
            self._content_cache[file_path] = content
            return content
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
    def parse_weapons(unit_block: str) -> Dict:
        """
        Parse weapondefs + weapons blocks from a unit block to compute:
          - dps          : int  (0 if no weapons or only EMP/paralyzer)
          - weaponrange  : int  (highest range across all non-bogus weapons)
          - weapons_text : str  (e.g. "LaserCannon, 2x MissileLauncher, EMP-BeamLaser")
          - has_weapondefs : bool
          - has_damage     : bool (at least one weapon with damage.default > 0)

        Mirrors the DPS formula used in BAR's Lua export scripts:
          dps += (max(dmg_vtol, dmg_default) * (1/reload)) * salvosize * projectiles
        Paralyzer weapons contribute to weapon list (as EMP-*) but not to DPS.
        Weapons with 'bogus' or 'mine' in their name are skipped entirely.
        """
        result = {
            'dps':           0,
            'weaponrange':   0,
            'weapons_text':  '',
            'has_weapondefs': False,
            'has_damage':    False,
        }

        # ── Step 1: parse all weapondefs into a dict keyed by UPPERCASE name ──
        weapondefs: Dict[str, Dict] = {}
        wd_match = re.search(r'\bweapondefs\s*=\s*\{', unit_block, re.IGNORECASE)
        if not wd_match:
            return result

        wd_block = LuaParser.extract_balanced_braces(unit_block, wd_match.end() - 1)
        if not wd_block:
            return result

        result['has_weapondefs'] = True

        def _val(block, key, cast=str):
            """Extract a simple key = value from a block."""
            m = re.search(rf'\b{key}\s*=\s*([^\n,}}]+)', block, re.IGNORECASE)
            if not m:
                return None
            v = m.group(1).strip().rstrip(',').strip('"\'')
            try:
                return cast(v)
            except (ValueError, TypeError):
                return None

        for wm in re.finditer(r'(\w+)\s*=\s*\{', wd_block):
            wname  = wm.group(1)
            wblock = LuaParser.extract_balanced_braces(wd_block, wm.end() - 1)
            if not wblock:
                continue

            # Parse damage { default = X, vtol = Y }
            dmg_default = 0.0
            dmg_vtol    = 0.0
            dmg_match   = re.search(r'\bdamage\s*=\s*\{', wblock, re.IGNORECASE)
            if dmg_match:
                dmg_block = LuaParser.extract_balanced_braces(wblock, dmg_match.end() - 1)
                if dmg_block:
                    d = _val(dmg_block, 'default', float)
                    v = _val(dmg_block, 'vtol',    float)
                    dmg_default = d or 0.0
                    dmg_vtol    = v or 0.0

            is_paralyzer = bool(re.search(r'\bparalyzer\s*=\s*true', wblock, re.IGNORECASE))

            # Check customparams { bogus = 1 } inside this weapondef
            is_bogus_cp = False
            wcp_match = re.search(r'\bcustomparams\s*=\s*\{', wblock, re.IGNORECASE)
            if wcp_match:
                wcp_block = LuaParser.extract_balanced_braces(wblock, wcp_match.end() - 1)
                if wcp_block and re.search(r'\bbogus\s*=\s*1', wcp_block, re.IGNORECASE):
                    is_bogus_cp = True

            weapondefs[wname.upper()] = {
                'def_name':   (_val(wblock, 'name') or '').lower(),
                'weapontype': _val(wblock, 'weapontype') or '',
                'reloadtime': _val(wblock, 'reloadtime', float) or 1.0,
                'salvosize':  _val(wblock, 'salvosize',  int)   or 1,
                'burst':      _val(wblock, 'burst',      int)   or 1,
                'projectiles':_val(wblock, 'projectiles',int)   or 1,
                'range':      _val(wblock, 'range',      float) or 0.0,
                'paralyzer':  is_paralyzer,
                'is_bogus':   is_bogus_cp,
                'dmg_default':dmg_default,
                'dmg_vtol':   dmg_vtol,
            }

        # ── Step 2: parse weapons = { } to find which weapondefs are used ─────
        used_defs: List[str] = []
        w_match = re.search(r'\bweapons\s*=\s*\{', unit_block, re.IGNORECASE)
        if w_match:
            w_block = LuaParser.extract_balanced_braces(unit_block, w_match.end() - 1)
            if w_block:
                for dm in re.finditer(r'\bdef\s*=\s*["\']?(\w+)["\']?', w_block, re.IGNORECASE):
                    used_defs.append(dm.group(1).upper())

        # If there's no weapons = {} block at all, nothing is actually equipped
        if not used_defs:
            return result

        # ── Step 3: apply DPS formula ─────────────────────────────────────────
        dps          = 0.0
        weapon_range = 0.0
        weapon_table: Dict[str, int] = {}

        for def_key in used_defs:
            wd = weapondefs.get(def_key)
            if not wd:
                continue

            # Skip bogus/mine weapons (by name or customparams.bogus = 1)
            if 'bogus' in wd['def_name'] or 'mine' in wd['def_name'] or wd['is_bogus']:
                continue

            wtype = wd['weapontype']

            if wd['paralyzer']:
                # EMP — contributes to weapon list but not DPS
                emp_map = {
                    'BeamLaser':          'EMP-BeamLaser',
                    'AircraftBomb':       'EMP-AircraftBomb',
                    'StarburstLauncher':  'EMP-StarburstLauncher',
                }
                wtype = emp_map.get(wtype, f'EMP-{wtype}')
                # Track range for paralyzer weapons too
                if wd['range'] > weapon_range:
                    weapon_range = wd['range']
                weapon_table[wtype] = weapon_table.get(wtype, 0) + 1
            else:
                # Pick higher damage tier (vtol vs default) — mirrors Lua logic
                dmg = wd['dmg_vtol'] if wd['dmg_vtol'] > wd['dmg_default'] else wd['dmg_default']

                # Skip weapons with zero damage — don't count in list or DPS
                if dmg <= 0:
                    continue

                reload = wd['reloadtime'] or 1.0
                dps += (dmg * (1.0 / reload)) * wd['salvosize'] * wd['burst'] * wd['projectiles']
                result['has_damage'] = True

                # Track max range
                if wd['range'] > weapon_range:
                    weapon_range = wd['range']

                weapon_table[wtype] = weapon_table.get(wtype, 0) + 1

        # ── Step 4: build weapons text ────────────────────────────────────────
        parts = []
        for wname, count in weapon_table.items():
            parts.append(f"{count}x {wname}" if count > 1 else wname)

        result['dps']          = round(dps)
        result['weaponrange']  = round(weapon_range)
        result['weapons_text'] = ", ".join(parts)

        return result

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
                    
                    # Extract techlevel from customparams
                    techlevel_pattern = r'techlevel\s*=\s*([0-9]+)'
                    techlevel_match = re.search(techlevel_pattern, customparams_block, re.IGNORECASE)
                    if techlevel_match:
                        unit_data['techlevel'] = int(techlevel_match.group(1))

                    # Detect mine = true in customparams
                    mine_match = re.search(r'mine\s*=\s*true', customparams_block, re.IGNORECASE)
                    if mine_match:
                        unit_data['_is_mine'] = True

            # Detect buildoptions block presence
            bo_match = re.search(r'buildoptions\s*=\s*\{', unit_block, re.IGNORECASE)
            if bo_match:
                bo_block = LuaParser.extract_balanced_braces(unit_block, bo_match.end() - 1)
                # Only count as having buildoptions if there's at least one entry
                if bo_block and re.search(r'"(\w+)"', bo_block):
                    unit_data['_has_buildoptions'] = True

            # Parse weapons: DPS, range, weapon type list
            weapon_result = LuaParser.parse_weapons(unit_block)
            unit_data['_has_weapondefs'] = weapon_result['has_weapondefs']
            unit_data['_has_damage']     = weapon_result['has_damage']
            if weapon_result['has_weapondefs']:
                unit_data['_dps']          = weapon_result['dps']
                unit_data['_weaponrange']  = weapon_result['weaponrange']
                unit_data['_weapons_text'] = weapon_result['weapons_text']
            
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

    @staticmethod
    def parse_buildoptions(content: str, unit_name: str) -> List[str]:
        """
        Parse buildoptions from a unit .lua file.
        Returns list of unit names that this unit can build.

        Example in .lua:
            buildoptions = {
                "armpw",
                "armck",
            },
        """
        try:
            unit_pattern = rf"{unit_name}\s*=\s*\{{"
            match = re.search(unit_pattern, content, re.IGNORECASE)
            if not match:
                return []

            unit_block = LuaParser.extract_balanced_braces(content, match.end() - 1)
            if not unit_block:
                return []

            bo_match = re.search(r'buildoptions\s*=\s*\{', unit_block, re.IGNORECASE)
            if not bo_match:
                return []

            bo_block = LuaParser.extract_balanced_braces(unit_block, bo_match.end() - 1)
            if not bo_block:
                return []

            return re.findall(r'"(\w+)"', bo_block)

        except Exception:
            return []


class WebflowAPI:
    """Handles Webflow API interactions."""
    
    def __init__(self, api_token: str, site_id: str, collection_id: str, rate_limiter: Optional[RateLimiter] = None):
        self.api_token = api_token
        self.site_id = site_id
        self.collection_id = collection_id
        self.base_url = "https://api.webflow.com/v2"
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "accept": "application/json"
        }
        self.rate_limiter = rate_limiter or RateLimiter()  # Default to 110 req/min
    
    def _rate_limit(self):
        """Apply rate limiting before API calls."""
        if self.rate_limiter:
            self.rate_limiter.wait_if_needed()
    
    def get_all_items(self) -> List[Dict]:
        """Fetch all items from the Units collection."""
        items = []
        offset = 0
        limit = 100
        
        while True:
            try:
                self._rate_limit()  # Rate limiting
                
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
            self._rate_limit()  # Rate limiting
            
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
            self._rate_limit()  # Rate limiting
            
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


class LanguageParser:
    """Parses language/en/units.json to get unit names and tooltips."""
    
    _cache = None  # Cache parsed data
    UNITS_JSON_PATH = "language/en/units.json"
    
    @classmethod
    def fetch_and_parse(cls, repo: str, branch: str, github_token: Optional[str] = None) -> Dict[str, Dict]:
        """
        Fetch units.json and return dict with names and descriptions per unit.
        Uses cache to avoid repeated fetches.
        
        Returns: {
            "armcom": {"name": "Armada Commander", "tooltip": "..."},
            ...
        }
        """
        if cls._cache is not None:
            return cls._cache
        
        try:
            url = f"https://raw.githubusercontent.com/{repo}/refs/heads/{branch}/{cls.UNITS_JSON_PATH}"
            headers = {}
            if github_token:
                headers['Authorization'] = f'token {github_token}'
            
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            cls._cache = cls.parse(data)
            
            total = len(cls._cache)
            with_tooltip = sum(1 for v in cls._cache.values() if v.get('tooltip'))
            print(f"  📖 Loaded {total} unit names from units.json ({with_tooltip} with tooltips)")
            return cls._cache
            
        except Exception as e:
            print(f"  ⚠️  Could not fetch units.json: {e}")
            cls._cache = {}
            return cls._cache
    
    @staticmethod
    def parse(data: Dict) -> Dict[str, Dict]:
        """
        Parse units.json structure into a flat lookup dict.
        
        Structure:
        {
          "units": {
            "names": { "armcom": "Armada Commander", ... },
            "descriptions": { "armcom": "Builds T1...", ... }  (if present)
          }
        }
        """
        result = {}
        
        units = data.get('units', {})
        names = units.get('names', {})
        descriptions = units.get('descriptions', {})
        
        # Collect all unit keys from both sections
        all_keys = set(names.keys()) | set(descriptions.keys())
        
        for unit_key in all_keys:
            result[unit_key.lower()] = {
                'name': names.get(unit_key, ''),
                'tooltip': descriptions.get(unit_key, '')
            }
        
        return result


class AllDefsParser:
    """Parses alldefs_post.lua to get movement class categorizations."""
    
    _cache = None  # Cache parsed data
    
    @staticmethod
    def fetch_alldefs(repo: str, branch: str, github_token: Optional[str] = None) -> Optional[str]:
        """Fetch alldefs_post.lua from GitHub."""
        try:
            url = f"https://raw.githubusercontent.com/{repo}/refs/heads/{branch}/gamedata/alldefs_post.lua"
            headers = {}
            if github_token:
                headers['Authorization'] = f'token {github_token}'
            
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"Warning: Could not fetch alldefs_post.lua: {e}")
            return None
    
    @staticmethod
    def parse_movement_lists(content: str) -> Dict[str, set]:
        """
        Parse alldefs_post.lua and extract movement class lists.
        
        Returns dict with keys: 'hover', 'ship', 'sub', 'amphib', 'commander'
        """
        lists = {
            'hover': set(),
            'ship': set(),
            'sub': set(),
            'amphib': set(),
            'commander': set()
        }
        
        try:
            # Pattern to find each list: local listName = { ... }
            list_names = {
                'hoverList': 'hover',
                'shipList': 'ship',
                'subList': 'sub',
                'amphibList': 'amphib',
                'commanderList': 'commander'
            }
            
            for lua_name, key in list_names.items():
                # Find the list definition
                pattern = rf'local\s+{lua_name}\s*=\s*\{{'
                match = re.search(pattern, content)
                
                if match:
                    # Extract the list block
                    block = LuaParser.extract_balanced_braces(content, match.end() - 1)
                    
                    if block:
                        # Extract all class names (pattern: CLASSNAME = true)
                        class_pattern = r'(\w+)\s*=\s*true'
                        for class_match in re.finditer(class_pattern, block):
                            class_name = class_match.group(1).upper()
                            lists[key].add(class_name)
            
            return lists
            
        except Exception as e:
            print(f"Warning: Error parsing alldefs_post.lua: {e}")
            return lists
    
    @classmethod
    def get_movement_lists(cls, repo: str, branch: str, github_token: Optional[str] = None) -> Dict[str, set]:
        """
        Get movement class lists from alldefs_post.lua.
        Uses cache to avoid repeated fetches.
        """
        if cls._cache is not None:
            return cls._cache
        
        content = cls.fetch_alldefs(repo, branch, github_token)
        
        if content:
            cls._cache = cls.parse_movement_lists(content)
            
            total = sum(len(s) for s in cls._cache.values())
            if total > 0:
                print(f"  📖 Loaded {total} movement classes from alldefs_post.lua")
                print(f"     Hover: {len(cls._cache['hover'])}, Ship: {len(cls._cache['ship'])}, "
                      f"Sub: {len(cls._cache['sub'])}, Amphib: {len(cls._cache['amphib'])}")
                return cls._cache
        
        # Fallback to hardcoded lists if fetch/parse fails
        print("  ⚠️  Using fallback movement class lists from code")
        cls._cache = {
            'hover': {'HOVER2', 'HOVER3', 'HHOVER4', 'AHOVER2'},
            'ship': {'BOAT3', 'BOAT4', 'BOAT5', 'BOAT9', 'EPICSHIP'},
            'sub': {'UBOAT4', 'EPICSUBMARINE'},
            'amphib': {'VBOT6', 'COMMANDERBOT', 'SCAVCOMMANDERBOT', 
                      'ATANK3', 'ABOT3', 'HABOT5', 'ABOTBOMB2', 
                      'EPICBOT', 'EPICALLTERRAIN'},
            'commander': {'COMMANDERBOT', 'SCAVCOMMANDERBOT'}
        }
        return cls._cache


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


class GitHubIconUploader:
    """Handles uploading icons to GitHub repository for public hosting."""
    
    def __init__(self, repo_owner: str, repo_name: str, github_token: str, branch: str = "main"):
        """
        Initialize GitHub icon uploader.
        
        Args:
            repo_owner: GitHub username or org (e.g., 'icexuick')
            repo_name: Repository name (e.g., 'bar-unit-sync')
            github_token: GitHub personal access token
            branch: Branch to commit to (default: 'main')
        """
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.github_token = github_token
        self.branch = branch
        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        self.icons_dir = "icons"
    
    def upload_icon(self, file_data: bytes, filename: str) -> Optional[str]:
        """
        Upload icon to GitHub repository and return public raw URL.
        
        This commits the file directly to the repo, making it publicly accessible
        via GitHub's raw content URL.
        
        Args:
            file_data: WebP file as bytes
            filename: Filename (e.g., "armclaw.webp")
            
        Returns:
            Public raw GitHub URL if successful, None otherwise
        """
        try:
            import base64
            
            # Path in repo
            file_path = f"{self.icons_dir}/{filename}"
            
            # Check if file already exists (to get SHA for update)
            sha = None
            check_url = f"{self.base_url}/repos/{self.repo_owner}/{self.repo_name}/contents/{file_path}"
            params = {"ref": self.branch}
            
            check_response = requests.get(check_url, headers=self.headers, params=params)
            
            if check_response.status_code == 200:
                existing = check_response.json()
                sha = existing.get('sha')
                print(f"     File exists, updating...")
            elif check_response.status_code == 404:
                print(f"     Creating new file...")
            else:
                print(f"  ⚠️  Unexpected response checking file: {check_response.status_code}")
            
            # Encode file as base64
            content_base64 = base64.b64encode(file_data).decode('utf-8')
            
            # Create/update file via GitHub API
            payload = {
                "message": f"🎨 Add/update strategic icon: {filename}",
                "content": content_base64,
                "branch": self.branch
            }
            
            if sha:
                payload["sha"] = sha  # Required for updates
            
            response = requests.put(check_url, headers=self.headers, json=payload)
            response.raise_for_status()
            
            # Construct raw GitHub URL
            raw_url = f"https://raw.githubusercontent.com/{self.repo_owner}/{self.repo_name}/{self.branch}/{file_path}"
            
            print(f"  ✅ Committed to GitHub: {filename}")
            print(f"     URL: {raw_url}")
            
            return raw_url
            
        except Exception as e:
            print(f"  ❌ Error uploading to GitHub: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"     Response: {e.response.text}")
            return None


class UnitSyncService:
    """Main service to sync units from GitHub to Webflow."""
    
    def __init__(self, github_fetcher: GitHubUnitFetcher, webflow_api: WebflowAPI):
        self.github = github_fetcher
        self.webflow = webflow_api
        self.parser = LuaParser()
        self.movement_lists   = {}   # Loaded from alldefs_post.lua
        self.language_data    = {}   # Loaded from language/en/units.json
        self._buildoptions_map = {}  # { unit_name: [units_it_can_build] } — from cache/archive
        self._webflow_id_map   = {}  # { unit_name: webflow_item_id }       — built from Webflow items
    
    def detect_faction(self, unit_name: str) -> Optional[Dict]:
        """
        Detect faction based on unit name prefix.
        Returns dict with 'id' and 'name', or None if unknown.
        
        Prefixes:
          arm    → Armada
          cor    → Cortex
          leg    → Legion
          raptor → CHICKS
        """
        name = unit_name.lower()
        # Check longest prefix first (raptor before any 3-letter prefix)
        for prefix in sorted(FACTION_MAP.keys(), key=len, reverse=True):
            if name.startswith(prefix):
                return FACTION_MAP[prefix]
        return None

    def detect_amphibious(self, unit_data: Dict) -> bool:
        """
        Detect if a unit is amphibious based on movement class and properties.
        
        Based on alldefs_post.lua logic + hover units:
        - Hover units (can go land + water) are amphibious
        - Units in amphibList are amphibious
        - Units with cansubmerge that are NOT submarines are amphibious
        
        Amphibious = can move on BOTH land AND water
        """
        # Get movementclass
        movementclass = unit_data.get('movementclass', '').upper()
        maxwaterdepth = unit_data.get('maxwaterdepth')
        
        # Use loaded lists from alldefs_post.lua
        hover_list = self.movement_lists.get('hover', set())
        amphib_list = self.movement_lists.get('amphib', set())
        
        # HOVER units are amphibious (can go land + water)
        # categories["HOVER"] = hoverList[movementclass] and (maxwaterdepth == nil or maxwaterdepth < 1)
        if movementclass in hover_list and (maxwaterdepth is None or maxwaterdepth < 1):
            return True
        
        # Check if in amphibList
        if movementclass in amphib_list:
            return True
        
        # Check if CANBEUW (can be underwater) but NOT UNDERWATER (submarine)
        can_be_uw = unit_data.get('cansubmerge') == True
        
        if can_be_uw:
            # Check if UNDERWATER (submarine)
            minwaterdepth = unit_data.get('minwaterdepth')
            waterline = unit_data.get('waterline')
            speed = unit_data.get('speed', 0)
            
            is_underwater = False
            if minwaterdepth is not None:
                if waterline is None:
                    is_underwater = True
                elif waterline > minwaterdepth and speed > 0:
                    is_underwater = True
            
            # Amphibious if can submerge but is NOT a submarine
            if not is_underwater:
                return True
        
        return False
    
    def detect_unit_type(self, unit_data: Dict) -> Optional[str]:
        """
        Detect unit type based on movementclass and unit properties.

        Detection order (first match wins):

        MOBILE TYPES (have speed > 0 and a movementclass):
          1. canfly = true                                     → aircraft
          2. movementclass = AHOVER2                          → bot (special: Platypus)
          3. movementclass = HOVER7                           → ship (special)
          4. *HOVER* + maxwaterdepth < 1                      → hovercraft
          5. *HOVER* + maxwaterdepth >= 1                     → ship
          6. *BOAT* / *SHIP* / *UBOAT* / *SUB*               → ship
          7. *RAPTOR*                                         → chicken
          8. *COMMANDER* / *BOT* / *KBOT*                     → bot
          9. *TANK* / *VEH*                                   → vehicle

        STATIONARY TYPES (speed == 0 or no movementclass):
         10. builder=true + buildoptions + workertime > 0     → factory
         11. weapondefs with damage.default > 0               → defense
         12. everything else stationary                       → building

        FALLBACK:
         13. mobile with unknown movementclass                → bot

        Returns a key from UNIT_TYPE_MAP, or None if undetermined.
        """
        mc = unit_data.get('movementclass', '').upper().strip()
        speed = unit_data.get('speed', 0) or 0
        maxwaterdepth = unit_data.get('maxwaterdepth')

        # ── MOBILE TYPES ──────────────────────────────────────────────────────

        # 1. Aircraft
        if unit_data.get('canfly') is True:
            return "aircraft"

        # 2–9 only apply to units with a movementclass
        if mc:
            # 2. Special overrides
            if mc == 'AHOVER2':
                return "bot"
            if mc == 'HOVER7':
                return "ship"

            # 3–4. Hover
            if 'HOVER' in mc:
                if maxwaterdepth is None or maxwaterdepth < 1:
                    return "hovercraft"
                else:
                    return "ship"

            # 5. Naval
            if any(kw in mc for kw in ('BOAT', 'SHIP', 'UBOAT', 'SUB')):
                return "ship"

            # 6. Chicken
            if 'RAPTOR' in mc:
                return "chicken"

            # 7. Bots
            if any(kw in mc for kw in ('COMMANDER', 'BOT', 'KBOT')):
                return "bot"

            # 8. Vehicles
            if any(kw in mc for kw in ('TANK', 'VEH')):
                return "vehicle"

        # ── STATIONARY TYPES ──────────────────────────────────────────────────
        # Applies when speed == 0 OR there's no movementclass at all

        if speed == 0 or not mc:
            is_builder    = unit_data.get('builder') is True
            has_buildopts = unit_data.get('_has_buildoptions') is True
            workertime    = unit_data.get('workertime', 0) or 0
            has_damage    = unit_data.get('_has_damage') is True
            is_mine       = unit_data.get('_is_mine') is True

            # 10. Factory: builder + buildoptions + workertime > 0
            if is_builder and has_buildopts and workertime > 0:
                return "factory"

            # 11. Defense: has weapondefs with real damage, OR is a mine
            if has_damage or is_mine:
                return "defense"

            # 12. Building: stationary, not a factory, no damaging weapons
            return "building"

        # ── FALLBACK ──────────────────────────────────────────────────────────
        # Mobile unit with unrecognised movementclass
        return "bot"

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
        
        # Handle techlevel (from customparams, integer field)
        if 'techlevel' in github_data:
            try:
                webflow_fields['techlevel'] = int(github_data['techlevel'])
            except:
                pass
        
        # Handle amphibious (boolean, derived from movement type)
        if 'amphibious' in github_data:
            webflow_fields['amphibious'] = bool(github_data['amphibious'])
        
        # Handle unit name from language file (plain text field)
        if 'unitname' in github_data and github_data['unitname']:
            webflow_fields['unitname'] = github_data['unitname']
        
        # Handle tooltip from language file (plain text field)
        if 'tooltip' in github_data and github_data['tooltip']:
            webflow_fields['tooltip'] = github_data['tooltip']
        
        # Handle faction reference field
        unit_name = github_data.get('_unit_name', '')
        if unit_name:
            faction = self.detect_faction(unit_name)
            if faction:
                webflow_fields['faction-ref'] = faction['id']

        # Handle unit type reference field
        unit_type_key = github_data.get('_unit_type')
        if unit_type_key and unit_type_key in UNIT_TYPE_MAP:
            webflow_fields['unittype'] = UNIT_TYPE_MAP[unit_type_key]['id']

        # Handle weapon fields (DPS, range, weapon type list)
        if github_data.get('_has_weapondefs'):
            dps = github_data.get('_dps', 0)
            if dps and dps > 0:
                webflow_fields['dps'] = int(dps)
            wr = github_data.get('_weaponrange', 0)
            if wr and wr > 0:
                webflow_fields['weaponrange'] = int(wr)
            wt = github_data.get('_weapons_text', '')
            if wt:
                webflow_fields['weapons'] = wt

        # Handle buildoptions multi-reference field
        # Look up what this unit can build, then resolve each to a Webflow item ID
        if unit_name and self._buildoptions_map and self._webflow_id_map:
            can_build = self._buildoptions_map.get(unit_name.lower(), [])
            resolved_ids = []
            unresolved   = []
            for bo_unit in can_build:
                wf_id = self._webflow_id_map.get(bo_unit)
                if wf_id:
                    resolved_ids.append(wf_id)
                else:
                    unresolved.append(bo_unit)
            if resolved_ids:
                webflow_fields['buildoptions-ref'] = resolved_ids
            if unresolved:
                # Store for display only — not sent to Webflow
                github_data['_buildoptions_unresolved'] = unresolved

        return webflow_fields
    
    def sync_unit_icon(self, unit_name: str, icon_path: str, github_uploader: GitHubIconUploader, 
                       current_icon_url: Optional[str] = None, dry_run: bool = False) -> Optional[str]:
        """
        Sync a unit's strategic icon.
        
        Args:
            unit_name: Name of the unit
            icon_path: Path to icon PNG in BAR GitHub (e.g., "icons/ship_t2_flagship.png")
            github_uploader: GitHubIconUploader instance
            current_icon_url: Current icon URL in Webflow (if any)
            dry_run: If True, don't actually upload
            
        Returns:
            Public GitHub raw URL if successful, None otherwise
        """
        try:
            # Download PNG from BAR GitHub repo
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
                print(f"    ℹ️  Would commit: {webp_filename} ({len(webp_data)} bytes)")
                return "https://raw.githubusercontent.com/example/repo/main/icons/dry-run.webp"
            
            # Upload to your GitHub repository
            print(f"    📤 Committing to GitHub: {webp_filename}")
            public_url = github_uploader.upload_icon(webp_data, webp_filename)
            
            return public_url
            
        except Exception as e:
            print(f"    ❌ Error syncing icon: {e}")
            return None

    def _build_buildable_set_from_archive(self) -> set:
        """
        Download the entire repository as a zip archive and scan all unit .lua
        files for buildoptions in a single pass.

        Builds two indexes and caches both to .buildable_cache.json:
          - buildable      : set of all unit names that appear in any buildoptions
          - buildoptions_map : { unit_name: [list_of_units_it_can_build] }

        Subsequent runs load from cache. Use --clear-cache to force a refresh.
        """
        import zipfile
        import io

        BUILDABLE_CACHE_FILE = ".buildable_cache.json"

        # --- Try loading from cache first ---
        if os.path.exists(BUILDABLE_CACHE_FILE):
            try:
                with open(BUILDABLE_CACHE_FILE, 'r') as f:
                    cache_data = json.load(f)
                if (cache_data.get('repo') == GITHUB_REPO and
                        cache_data.get('branch') == GITHUB_BRANCH):
                    buildable = set(cache_data.get('buildable', []))
                    # Also restore buildoptions_map into instance variable
                    self._buildoptions_map = cache_data.get('buildoptions_map', {})
                    print(f"  💾 Loaded {len(buildable)} buildable units from cache"
                          f" ({len(self._buildoptions_map)} units have buildoptions)"
                          f" — use --clear-cache to refresh")
                    return buildable
                else:
                    print(f"  ⚠️  Buildable cache is for different repo/branch — rebuilding")
            except Exception as e:
                print(f"  ⚠️  Could not read buildable cache: {e} — rebuilding")

        # --- Cache miss: download and scan the repo archive ---
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/zipball/{GITHUB_BRANCH}"
            headers = {}
            github_token = os.environ.get("GITHUB_TOKEN")
            if github_token:
                headers['Authorization'] = f'token {github_token}'

            print(f"  📦 Downloading repo archive from GitHub (this happens once)...")
            response = requests.get(url, headers=headers, stream=True)
            response.raise_for_status()

            raw = response.content
            print(f"  ✅ Downloaded {len(raw) / 1024 / 1024:.1f} MB — scanning unit files...")

            all_buildable    = set()
            buildoptions_map = {}   # { unit_name: [units_it_can_build] }
            scanned          = 0
            with_options     = 0

            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for name in zf.namelist():
                    parts = name.split('/')
                    if len(parts) < 3:
                        continue
                    if parts[1] != 'units' or not name.endswith('.lua'):
                        continue

                    unit_name = parts[-1].replace('.lua', '').lower()
                    content = zf.read(name).decode('utf-8', errors='replace')
                    scanned += 1

                    options = LuaParser.parse_buildoptions(content, unit_name)
                    if options:
                        with_options += 1
                        normalized = [o.lower() for o in options]
                        all_buildable.update(normalized)
                        buildoptions_map[unit_name] = normalized

            print(f"  Scanned {scanned} unit files, {with_options} have buildoptions")

            # Store on instance for use during sync
            self._buildoptions_map = buildoptions_map

            # --- Save to cache ---
            try:
                with open(BUILDABLE_CACHE_FILE, 'w') as f:
                    json.dump({
                        'repo':             GITHUB_REPO,
                        'branch':           GITHUB_BRANCH,
                        'buildable':        sorted(all_buildable),
                        'buildoptions_map': buildoptions_map,
                    }, f, indent=2)
                print(f"  💾 Saved buildable index + buildoptions map to {BUILDABLE_CACHE_FILE}")
            except Exception as e:
                print(f"  ⚠️  Could not save buildable cache: {e}")

            return all_buildable

        except Exception as e:
            print(f"  ❌ Archive download failed: {e}")
            self._buildoptions_map = {}
            return set()

    def sync_all_units(self, dry_run: bool = False, auto_publish: bool = False,
                       sync_icons: bool = False, unit_filter: Optional[str] = None):
        """
        Sync all units from GitHub to Webflow.
        
        Args:
            dry_run:     If True, show what would be updated without making changes
            auto_publish: If True, automatically publish updated items
            sync_icons:  If True, also sync strategic icons from icontypes.lua
            unit_filter: If set, only sync this single unit (by name, e.g. 'armzeus')
        """
        print("=" * 80)
        print("Beyond All Reason - Unit Data Sync")
        print("=" * 80)
        print()
        
        # Step 0: Load definitions from BAR repository
        print("Step 0: Loading definitions from BAR repository...")
        github_token = os.environ.get("GITHUB_TOKEN")
        
        self.movement_lists = AllDefsParser.get_movement_lists(GITHUB_REPO, GITHUB_BRANCH, github_token)
        self.language_data = LanguageParser.fetch_and_parse(GITHUB_REPO, GITHUB_BRANCH, github_token)
        print()
        
        # Step 1: Fetch icontypes if icon sync enabled
        icon_map = {}
        github_uploader = None
        
        if sync_icons:
            print("Step 1a: Fetching icontypes.lua for icon paths...")
            github_token = os.environ.get("GITHUB_TOKEN")
            
            if not github_token:
                print("⚠️  GITHUB_TOKEN not found - icon sync requires GitHub token")
                print("   Set GITHUB_TOKEN in .env file for icon uploads")
                sync_icons = False
            else:
                icontypes_content = IconTypesParser.fetch_icontypes(GITHUB_REPO, GITHUB_BRANCH, github_token)
                
                if icontypes_content:
                    icon_map = IconTypesParser.parse_icontypes(icontypes_content)
                    print(f"Found {len(icon_map)} unit icons in icontypes.lua")
                    
                    # Get repo info from environment or use defaults
                    icon_repo_owner = os.environ.get("ICON_REPO_OWNER", "icexuick")
                    icon_repo_name = os.environ.get("ICON_REPO_NAME", "bar-unit-sync")
                    icon_branch = os.environ.get("ICON_BRANCH", "main")
                    
                    print(f"Will commit icons to: {icon_repo_owner}/{icon_repo_name} (branch: {icon_branch})")
                    
                    # Initialize GitHub uploader
                    github_uploader = GitHubIconUploader(
                        icon_repo_owner,
                        icon_repo_name,
                        github_token,
                        icon_branch
                    )
                else:
                    print("⚠️  Failed to fetch icontypes.lua - icon sync disabled")
                    sync_icons = False
            print()
        
        # Step 1b: Fetch all unit files from GitHub
        print("Step 1: Fetching unit files from GitHub...")
        unit_files = self.github.get_unit_files(GITHUB_UNITS_PATH)
        print(f"Found {len(unit_files)} unit files")
        print()

        # Step 1b: Build the set of all buildable units by downloading
        # the entire units/ folder in one request (GitHub tarball/zipball API).
        # This is MUCH faster than fetching each file individually.
        print("Step 1b: Building buildable-units index (downloading repo archive)...")
        all_buildable = self._build_buildable_set_from_archive()
        if all_buildable:
            # Always include whitelisted units even if not in any buildoptions
            whitelisted_present = [uf for uf in unit_files if uf['name'] in SYNC_WHITELIST]
            buildable_files    = [uf for uf in unit_files
                                  if uf['name'] in all_buildable or uf['name'] in SYNC_WHITELIST]
            unbuildable_files  = [uf for uf in unit_files
                                  if uf['name'] not in all_buildable and uf['name'] not in SYNC_WHITELIST]

            print(f"  {len(all_buildable)} unique buildable units found")
            if whitelisted_present:
                print(f"  ⭐ Whitelist exceptions always included:")
                for uf in sorted(whitelisted_present, key=lambda x: x['name']):
                    in_buildable = "already in buildoptions" if uf['name'] in all_buildable else "not in buildoptions"
                    print(f"     ✅ {uf['name']}  ({in_buildable})")
            print(f"  Keeping {len(buildable_files)} units,"
                  f" skipping {len(unbuildable_files)} unbuildable:")
            for uf in sorted(unbuildable_files, key=lambda x: x['name']):
                print(f"    ⏭️  {uf['name']}  (not in any buildoptions)")
            unit_files = buildable_files
        else:
            print("  ⚠️  Could not determine buildable units — syncing all units")
        print()

        # Apply single-unit filter if requested (--unit armzeus)
        if unit_filter:
            unit_filter_lower = unit_filter.strip().lower()
            matched = [uf for uf in unit_files if uf['name'].lower() == unit_filter_lower]
            if not matched:
                # Also search in ALL unit_files (in case it was filtered as unbuildable)
                all_files_again = self.github.get_unit_files(GITHUB_UNITS_PATH)
                matched = [uf for uf in all_files_again if uf['name'].lower() == unit_filter_lower]
                if matched:
                    print(f"  ℹ️  '{unit_filter}' was filtered out (not buildable/whitelisted) — forcing inclusion for test")
                else:
                    print(f"  ❌ Unit '{unit_filter}' not found in GitHub repository")
                    return
            unit_files = matched
            print(f"  🎯 Single-unit mode: syncing only '{unit_filter}'")
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

        # Build unit_name → Webflow item ID map (used for buildoptions multi-ref)
        self._webflow_id_map = {
            name: item['id']
            for name, item in webflow_lookup.items()
        }
        print(f"  🗺️  Built Webflow ID map for {len(self._webflow_id_map)} units")
        
        print()
        
        # Step 3: Process each unit
        print("Step 3: Processing units...")
        print()
        
        # Readable labels for all Webflow fields (used in output)
        FIELD_LABELS = {
            'unitname':            'UnitName         ',
            'tooltip':             'Tooltip          ',
            'faction-ref':         'Faction          ',
            'unittype':            'Unit Type        ',
            'techlevel':           'Techlevel        ',
            'amphibious':          'Amphibious       ',
            'buildoptions-ref':    'Buildoptions     ',
            'dps':                 'DPS              ',
            'weaponrange':         'Weapon Range     ',
            'weapons':             'Weapons          ',
            'metal-cost':          'Metal Cost       ',
            'energy-cost':         'Energy Cost      ',
            'build-cost':          'Build Cost       ',
            'health':              'Health           ',
            'speed':               'Speed            ',
            'mass':                'Mass             ',
            'sightrange':          'Sight Range      ',
            'radarrange':          'Radar Range      ',
            'metal-make':          'Sonar Range      ',
            'jammerrange':         'Jammer Range     ',
            'energy-make':         'Energy Make      ',
            'buildpower':          'Build Power      ',
            'cloak-cost':          'Cloak Cost       ',
            'paralyze-multiplier': 'Paralyze Mult    ',
        }
        
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
                print(f"  ⏭️  Not in Webflow CMS — skipping (unit may not have been imported yet)")
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
            
            # Store unit_name inside github_data so map_github_to_webflow_fields can use it
            github_data['_unit_name'] = unit_name
            
            # Detect amphibious status using loaded movement classes
            github_data['amphibious'] = self.detect_amphibious(github_data)

            # Detect unit type (bot, vehicle, ship, aircraft, etc.)
            github_data['_unit_type'] = self.detect_unit_type(github_data)
            
            # Add name and tooltip from language file
            lang = self.language_data.get(unit_name.lower(), {})
            if lang.get('name'):
                github_data['unitname'] = lang['name']
            if lang.get('tooltip'):
                github_data['tooltip'] = lang['tooltip']
            
            # Map to Webflow fields
            webflow_fields = self.map_github_to_webflow_fields(github_data)
            
            if not webflow_fields:
                print(f"  ⚠️  No fields to update")
                stats['skipped'] += 1
                print()
                continue
            
            # Show all extracted values
            print(f"  📋 Extracted values:")
            for field_key, label in FIELD_LABELS.items():
                value = webflow_fields.get(field_key)
                if value is not None:
                    if field_key == 'faction-ref':
                        display = next(
                            (f['name'] for f in FACTION_MAP.values() if f['id'] == value),
                            value
                        )
                        print(f"     {label}: {display}  (id: {value})")
                    elif field_key == 'unittype':
                        display = next(
                            (t['name'] for t in UNIT_TYPE_MAP.values() if t['id'] == value),
                            value
                        )
                        print(f"     {label}: {display}  (id: {value})")
                    elif field_key == 'buildoptions-ref':
                        # Show unit names (resolved from IDs) for readability
                        id_to_name = {v: k for k, v in self._webflow_id_map.items()}
                        names = [id_to_name.get(wf_id, wf_id) for wf_id in value]
                        print(f"     {label}: [{', '.join(names)}]  ({len(names)} units)")
                        # Warn about any units from buildoptions not found in Webflow
                        unresolved = github_data.get('_buildoptions_unresolved', [])
                        if unresolved:
                            print(f"     {'':19}  ⚠️  {len(unresolved)} not in Webflow (skipped): {', '.join(unresolved)}")
                    else:
                        print(f"     {label}: {value}")
                else:
                    print(f"     {label}: —")
            print()
            
            # Get current Webflow data
            webflow_item = webflow_lookup[unit_name]
            current_data = webflow_item.get('fieldData', {})
            
            # Check what's changed
            changes = {}
            for key, new_value in webflow_fields.items():
                current_value = current_data.get(key)
                # For multi-reference fields (lists), compare sorted to ignore order
                if isinstance(new_value, list) or isinstance(current_value, list):
                    old_sorted = sorted(current_value) if isinstance(current_value, list) else []
                    new_sorted = sorted(new_value)     if isinstance(new_value, list)     else []
                    if old_sorted != new_sorted:
                        changes[key] = {'old': current_value, 'new': new_value}
                elif current_value != new_value:
                    changes[key] = {'old': current_value, 'new': new_value}
            
            # Sync icon if enabled (BEFORE checking for data changes)
            icon_synced = False
            if sync_icons and unit_name in icon_map and github_uploader:
                icon_path = icon_map[unit_name]
                current_icon_url = current_data.get('icon')
                
                print(f"  🎨 Syncing strategic icon...")
                asset_url = self.sync_unit_icon(
                    unit_name, 
                    icon_path, 
                    github_uploader,
                    current_icon_url,
                    dry_run
                )
                
                if asset_url:
                    # Check if icon URL changed
                    if current_icon_url != asset_url:
                        print(f"  📝 Icon URL changed")
                        # Add icon to changes or update it separately
                        if not changes:
                            # No data changes, but icon changed - update only icon
                            if not dry_run:
                                item_id = webflow_item['id']
                                icon_update = {
                                    "icon": asset_url,
                                    "name": current_data.get('name'),
                                    "slug": current_data.get('slug')
                                }
                                if self.webflow.update_item(item_id, icon_update):
                                    print(f"  ✅ Icon updated")
                                    icon_synced = True
                                    stats['updated'] += 1
                                else:
                                    print(f"  ⚠️  Failed to link icon")
                        else:
                            # Will be updated together with data changes below
                            changes['icon'] = {'old': current_icon_url, 'new': asset_url}
                    else:
                        print(f"  ✓ Icon already up-to-date")
            
            if not changes:
                if icon_synced:
                    # Icon was updated, publish if requested
                    if auto_publish and not dry_run:
                        item_id = webflow_item['id']
                        if self.webflow.publish_item(item_id):
                            print(f"  📤 Published successfully")
                        else:
                            print(f"  ⚠️  Failed to publish")
                else:
                    print(f"  ✓ Already up-to-date — no changes needed")
                    stats['skipped'] += 1
                print()
                continue
            
            # Show changes with readable labels
            print(f"  🔄 Changes detected ({len(changes)} field(s)):")
            for key, change in changes.items():
                label = FIELD_LABELS.get(key, key)
                old_val = change['old'] if change['old'] is not None else '—'
                new_val = change['new'] if change['new'] is not None else '—'
                if key == 'faction-ref':
                    old_val = next((f['name'] for f in FACTION_MAP.values() if f['id'] == old_val), old_val)
                    new_val = next((f['name'] for f in FACTION_MAP.values() if f['id'] == new_val), new_val)
                elif key == 'unittype':
                    old_val = next((t['name'] for t in UNIT_TYPE_MAP.values() if t['id'] == old_val), old_val)
                    new_val = next((t['name'] for t in UNIT_TYPE_MAP.values() if t['id'] == new_val), new_val)
                elif key == 'buildoptions-ref':
                    id_to_name = {v: k for k, v in self._webflow_id_map.items()}
                    if isinstance(old_val, list):
                        old_val = f"[{', '.join(id_to_name.get(i, i) for i in old_val)}] ({len(old_val)})"
                    if isinstance(new_val, list):
                        new_val = f"[{', '.join(id_to_name.get(i, i) for i in new_val)}] ({len(new_val)})"
                print(f"     {label}: {old_val}  →  {new_val}")
            
            # Update in Webflow (unless dry run)
            if dry_run:
                print(f"  🔍 DRY RUN — no changes written to Webflow")
                stats['skipped'] += 1
                print()
                continue

            # Apply changes to Webflow
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
        help='Clear all local caches (.unit_cache.json, .buildable_cache.json) and re-fetch from GitHub'
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
        sync_icons=args.sync_icons,
        unit_filter=args.unit
    )


if __name__ == "__main__":
    main()
