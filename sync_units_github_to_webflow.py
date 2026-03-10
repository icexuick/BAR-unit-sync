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
import math
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
    "metalmake": "metal-create",
    "metalstorage": "metal-storage",
    "energystorage": "energy-storage",
    "workertime": "buildpower",
    "health": "health",
    "speed": "speed",
    "sightdistance": "sightrange",
    "radardistance": "radarrange",
    "sonardistance": "metal-make",  # Note: Field was renamed, slug is still "metal-make"
    "seismicdistance": "seismic-detector-range",
    "jammerdistance": "jammerrange",
    "mass": "mass",
    "cloakcost": "cloak-cost",
    "cloakcostmoving": "cloak-cost-moving",
    # Special fields handled separately:
    # - paralyzemultiplier (in customparams)
    # - techlevel (in customparams)
    # - amphibious (derived from movement type)
    # - energyconv_capacity (in customparams) → converter-metal-make
    # - energyconv_efficiency (in customparams) → converter-efficiency
}

# Fields to skip (weapon-related, managed manually)
SKIP_FIELDS = ["weapons", "dps", "weaponrange"]


class RateLimiter:
    """Simple rate limiter to stay under Webflow API limits."""
    
    def __init__(self, max_requests_per_minute: int = 60):
        """
        Initialize rate limiter.
        
        Args:
            max_requests_per_minute: Max requests allowed (default 60 for safety margin)
                                     Webflow Business: 120/min, we use 60 to allow concurrent syncs
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


def resolve_target_categories(onlytargetcategory: str) -> dict:
    """
    Convert onlytargetcategory string to surface/air/subs booleans.
    
    Known values:
      NOTSUB       → surface=True,  air=True,  subs=False
      NOTAIR       → surface=True,  air=False, subs=True
      VTOL         → surface=False, air=True,  subs=False
      SURFACE      → surface=True,  air=False, subs=False
      EMPABLE      → surface=True,  air=False, subs=False  (same as SURFACE)
      CANBEUWUNDERWATER / UNDERWATER → surface=False, air=False, subs=True
      GROUNDSCOUT  → surface=True,  air=False, subs=False
      None / ''    → surface=False, air=False, subs=False  (no target info)
    """
    FALSE_ALL = {'can_target_surface': False, 'can_target_air': False, 'can_target_subs': False}
    if not onlytargetcategory:
        return FALSE_ALL
    
    otc = onlytargetcategory.upper().replace(' ', '')
    
    mapping = {
        'NOTSUB':              {'can_target_surface': True,  'can_target_air': True,  'can_target_subs': False},
        'NOTAIR':              {'can_target_surface': True,  'can_target_air': False, 'can_target_subs': True},
        'VTOL':                {'can_target_surface': False, 'can_target_air': True,  'can_target_subs': False},
        'SURFACE':             {'can_target_surface': True,  'can_target_air': False, 'can_target_subs': False},
        'EMPABLE':             {'can_target_surface': True,  'can_target_air': False, 'can_target_subs': False},
        'CANBEUWUNDERWATER':   {'can_target_surface': False, 'can_target_air': False, 'can_target_subs': True},
        'UNDERWATER':          {'can_target_surface': False, 'can_target_air': False, 'can_target_subs': True},
        'NOTHOVER':            {'can_target_surface': False, 'can_target_air': False, 'can_target_subs': True},
        'GROUNDSCOUT':         {'can_target_surface': True,  'can_target_air': False, 'can_target_subs': False},
    }
    
    return mapping.get(otc, FALSE_ALL)


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
    def parse_weapons(unit_block: str, unit_paths_map: dict = None) -> Dict:
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
            'dot':           0,  # Damage Over Time (cluster/napalm)
            'pps':           0,  # Paralyse Per Second (EMP weapons)
            'weaponrange':   0,
            'weapons_text':  '',
            'has_weapondefs': False,
            'has_damage':    False,
            'can_target_surface': False,
            'can_target_air': False,
            'can_target_subs': False,
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

            # Parse damage { default = X, vtol = Y, subs = Z, commanders = W }
            dmg_default = 0.0
            dmg_vtol    = 0.0
            dmg_subs    = 0.0
            dmg_commanders = 0.0
            dmg_match   = re.search(r'\bdamage\s*=\s*\{', wblock, re.IGNORECASE)
            if dmg_match:
                dmg_block = LuaParser.extract_balanced_braces(wblock, dmg_match.end() - 1)
                if dmg_block:
                    d = _val(dmg_block, 'default', float)
                    v = _val(dmg_block, 'vtol',    float)
                    s = _val(dmg_block, 'subs',    float)
                    c = _val(dmg_block, 'commanders', float)
                    dmg_default = d or 0.0
                    dmg_vtol    = v or 0.0
                    dmg_subs    = s or 0.0
                    dmg_commanders = c or 0.0

            is_paralyzer = bool(re.search(r'\bparalyzer\s*=\s*true', wblock, re.IGNORECASE))

            # Check customparams { bogus = 1, stockpilelimit = X, cluster_number, cluster_def, area_onhit, spark } inside this weapondef
            is_bogus_cp = False
            smart_backup = False  # Alternative fire mode flag
            interceptor  = False  # Anti-nuke interceptor flag
            stockpile_limit = None
            cluster_number = None
            cluster_def = None
            area_onhit_damage = None
            area_onhit_time = None
            spark_forkdamage = None  # Lightning - ONLY in customparams
            spark_maxunits = None    # Lightning - ONLY in customparams
            
            # Try root level first for cluster info
            cn_match_root = re.search(r'\bcluster_number\s*=\s*([0-9]+)', wblock, re.IGNORECASE)
            if cn_match_root:
                cluster_number = int(cn_match_root.group(1))
            
            cd_match_root = re.search(r'\bcluster_def\s*=\s*["\']?(\w+)["\']?', wblock, re.IGNORECASE)
            if cd_match_root:
                cluster_def = cd_match_root.group(1)
            
            sweepfire = 1  # Sweepfire beam multiplier (default 1 = no sweep)
            drone_carried_unit = None  # Drone carrier: name of carried unit
            drone_maxunits = None      # Drone carrier: max number of drones
            wcp_match = re.search(r'\bcustomparams\s*=\s*\{', wblock, re.IGNORECASE)
            if wcp_match:
                wcp_block = LuaParser.extract_balanced_braces(wblock, wcp_match.end() - 1)
                if wcp_block:
                    if re.search(r'\bbogus\s*=\s*1', wcp_block, re.IGNORECASE):
                        is_bogus_cp = True
                    # Smart_backup flag (alternative fire mode - don't count in unit DPS)
                    if re.search(r'\bsmart_backup\s*=\s*true', wcp_block, re.IGNORECASE):
                        smart_backup = True
                    # Extract stockpilelimit
                    sl_match = re.search(r'\bstockpilelimit\s*=\s*([0-9]+)', wcp_block, re.IGNORECASE)
                    if sl_match:
                        stockpile_limit = int(sl_match.group(1))
                    # Extract cluster info from customparams if not found in root
                    if not cluster_number:
                        cn_match = re.search(r'\bcluster_number\s*=\s*([0-9]+)', wcp_block, re.IGNORECASE)
                        if cn_match:
                            cluster_number = int(cn_match.group(1))
                    if not cluster_def:
                        cd_match = re.search(r'\bcluster_def\s*=\s*["\']?(\w+)["\']?', wcp_block, re.IGNORECASE)
                        if cd_match:
                            cluster_def = cd_match.group(1)
                    # Extract napalm DOT info
                    aod_match = re.search(r'\barea_onhit_damage\s*=\s*([0-9.]+)', wcp_block, re.IGNORECASE)
                    if aod_match:
                        area_onhit_damage = float(aod_match.group(1))
                    aot_match = re.search(r'\barea_onhit_time\s*=\s*([0-9.]+)', wcp_block, re.IGNORECASE)
                    if aot_match:
                        area_onhit_time = float(aot_match.group(1))
                    # Extract lightning spark info from customparams if not found in root
                    if not spark_forkdamage:
                        sf_match_cp = re.search(r'\bspark_forkdamage\s*=\s*["\']?([0-9.]+)["\']?', wcp_block, re.IGNORECASE)
                        if sf_match_cp:
                            spark_forkdamage = float(sf_match_cp.group(1))
                    if not spark_maxunits:
                        sm_match_cp = re.search(r'\bspark_maxunits\s*=\s*["\']?([0-9]+)["\']?', wcp_block, re.IGNORECASE)
                        if sm_match_cp:
                            spark_maxunits = int(sm_match_cp.group(1))
                    # Sweepfire multiplier
                    sw_match = re.search(r'\bsweepfire\s*=\s*([0-9]+)', wcp_block, re.IGNORECASE)
                    if sw_match:
                        sweepfire = int(sw_match.group(1))
                    # Drone carrier
                    cu_m = re.search(r'\bcarried_unit\s*=\s*["\']([\w]+)["\']', wcp_block, re.IGNORECASE)
                    if cu_m:
                        drone_carried_unit = cu_m.group(1).lower()
                    mu_m = re.search(r'\bmaxunits\s*=\s*([0-9]+)', wcp_block, re.IGNORECASE)
                    if mu_m:
                        drone_maxunits = int(mu_m.group(1))

            # Parse interceptor at root level (anti-nuke indicator)
            int_m = re.search(r'^\s{0,8}interceptor\s*=\s*([0-9]+)', wblock, re.IGNORECASE | re.MULTILINE)
            if int_m and int(int_m.group(1)) == 1:
                interceptor = True

            weapondefs[wname.upper()] = {
                'def_name':      (_val(wblock, 'name') or '').lower(),
                'weapontype':    _val(wblock, 'weapontype') or '',
                'reloadtime':    _val(wblock, 'reloadtime', float) or 1.0,
                'salvosize':     _val(wblock, 'salvosize',  int)   or 1,
                'burst':         _val(wblock, 'burst',      int)   or 1,
                'projectiles':   _val(wblock, 'projectiles',int)   or 1,
                'range':         _val(wblock, 'range',      float) or 0.0,
                'paralyzer':     is_paralyzer,
                'is_bogus':      is_bogus_cp,
                'smart_backup':  smart_backup,  # Alternative fire mode flag
                'stockpile_limit': stockpile_limit,
                'impulsefactor': _val(wblock, 'impulsefactor', float) or 0.0,
                'areaofeffect':  _val(wblock, 'areaofeffect',  float) or 0.0,
                'dmg_default':   dmg_default,
                'dmg_vtol':      dmg_vtol,
                'dmg_subs':      dmg_subs,
                'dmg_commanders': dmg_commanders,
                'cluster_number': cluster_number,
                'cluster_def':    cluster_def,
                'area_onhit_damage': area_onhit_damage,
                'area_onhit_time': area_onhit_time,
                'spark_forkdamage': spark_forkdamage,
                'spark_maxunits': spark_maxunits,
                'sweepfire':          sweepfire,
                'drone_carried_unit': drone_carried_unit,
                'drone_maxunits':     drone_maxunits,
                'interceptor':        interceptor,
            }

        # ── Step 2: parse weapons = { } to find which weapondefs are used ─────
        used_defs: List[str] = []
        weapon_onlytarget: dict = {}  # def_name_upper -> onlytargetcategory string or None
        w_match = re.search(r'\bweapons\s*=\s*\{', unit_block, re.IGNORECASE)
        if w_match:
            w_block = LuaParser.extract_balanced_braces(unit_block, w_match.end() - 1)
            if w_block:
                # Parse each numbered weapon entry [N] = { ... }
                for entry_match in re.finditer(r'\[\d+\]\s*=\s*\{', w_block):
                    entry_block = LuaParser.extract_balanced_braces(w_block, entry_match.end() - 1)
                    if not entry_block:
                        continue
                    def_m = re.search(r'\bdef\s*=\s*["\']?(\w+)["\']?', entry_block, re.IGNORECASE)
                    if not def_m:
                        continue
                    def_name = def_m.group(1).upper()
                    used_defs.append(def_name)
                    # Read onlytargetcategory for this entry
                    otc_m = re.search(r'\bonlytargetcategory\s*=\s*["\']?(\w+)["\']?', entry_block, re.IGNORECASE)
                    weapon_onlytarget[def_name] = otc_m.group(1).upper() if otc_m else None

        # If there's no weapons = {} block at all, nothing is actually equipped
        if not used_defs:
            return result

        # ── Step 3: apply DPS formula ─────────────────────────────────────────
        dps          = 0.0
        weapon_range = 0.0
        weapon_table: Dict[str, int] = {}
        stockpile_limit = None  # Track highest stockpile limit found
        max_impulsefactor = 0.0  # Track highest impulsefactor (only from weapons with damage)
        max_areaofeffect = 0.0   # Track highest areaofeffect (only from weapons with damage)
        has_non_paralyzer = False  # Track if unit has any real damage weapons

        for def_key in used_defs:
            wd = weapondefs.get(def_key)
            if not wd:
                continue

            # Skip weapons with 'bogus' or 'mine' in their name — always placeholders
            # Also skip if is_bogus flag is set in customparams
            # Also skip 'detonator' weapons — contact-trigger weapons on crawling bombs (range=1, not real DPS)
            if 'bogus' in wd['def_name'] or 'mine' in wd['def_name'] or wd.get('is_bogus', False):
                continue
            if 'detonator' in def_key.lower():
                continue

            # Track stockpile limit (typically only one weapon has this)
            if wd['stockpile_limit'] is not None:
                if stockpile_limit is None or wd['stockpile_limit'] > stockpile_limit:
                    stockpile_limit = wd['stockpile_limit']

            wtype = wd['weapontype']

            # Handle paralyzer weapons (EMP) - they don't do real damage
            # But they SHOULD appear in weapon list with EMP prefix
            if wd.get('paralyzer', False):
                # Track range even for paralyzer weapons
                if wd['range'] > weapon_range:
                    weapon_range = wd['range']
                
                # Calculate PPS (Paralyse Per Second)
                # PPS = damage / reload (how much paralysis damage per second)
                dmg = wd['dmg_vtol'] if wd['dmg_vtol'] > wd['dmg_default'] else wd['dmg_default']
                if dmg > 0:
                    reload = wd['reloadtime'] or 1.0
                    paralyze_dps = (dmg * (1.0 / reload)) * wd['salvosize'] * wd['burst'] * wd['projectiles']
                    result['pps'] += paralyze_dps
                
                # Add to weapon_table with EMP prefix
                emp_map = {
                    'BeamLaser':          'EMP-BeamLaser',
                    'AircraftBomb':       'EMP-AircraftBomb',
                    'StarburstLauncher':  'EMP-StarburstLauncher',
                    'LightningCannon':    'EMP-LightningCannon',
                }
                emp_wtype = emp_map.get(wtype, f'EMP-{wtype}')
                weapon_table[emp_wtype] = weapon_table.get(emp_wtype, 0) + 1
                
                # Skip normal DPS calculation
                continue
            
            # Mark that we have at least one non-paralyzer weapon
            has_non_paralyzer = True

            # Skip smart_backup weapons (alternative fire modes) entirely
            if wd.get('smart_backup', False):
                continue

            # Anti-nuke interceptors: contribute 0 DPS (they intercept, not damage)
            if wd.get('interceptor', False):
                weapon_table[wtype] = weapon_table.get(wtype, 0) + 1
                continue

            # Pick higher damage tier (vtol vs default) — mirrors Lua logic
            dmg = wd['dmg_vtol'] if wd['dmg_vtol'] > wd['dmg_default'] else wd['dmg_default']
            weapon_dot_dps = 0
            
            # Check if this is a cluster weapon - DOT only (not main DPS)
            if wd.get('cluster_number') and wd.get('cluster_def'):
                cluster_def_key = wd['cluster_def'].upper()
                cluster_wd = weapondefs.get(cluster_def_key)
                if cluster_wd:
                    # Cluster DOT = (cluster_number × cluster_damage) / reload
                    cluster_dmg = cluster_wd['dmg_default']
                    cluster_total = wd['cluster_number'] * cluster_dmg
                    weapon_dot_dps = (cluster_total / (wd['reloadtime'] or 1.0)) * wd['salvosize'] * wd['burst'] * wd['projectiles']

            # ── Drone carrier weapon: get DPS from carried unit's weapons ──
            print(f"     🔍 weapon {wd['def_name']}: interceptor={wd.get('interceptor')}, drone_carried_unit={wd.get('drone_carried_unit')}, dmg={wd.get('dmg_default')}")
            if wd.get('drone_carried_unit'):
                drone_name = wd['drone_carried_unit']
                maxunits   = wd.get('drone_maxunits') or 1
                weapon_table['DroneCarrier'] = weapon_table.get('DroneCarrier', 0) + 1
                if wd['range'] > weapon_range:
                    weapon_range = wd['range']
                # Fetch and parse the drone unit file
                github_token = os.environ.get('GITHUB_TOKEN')
                headers = {'Authorization': f'token {github_token}'} if github_token else {}
                # Look up exact path from unit_paths_map, fall back to flat units/ path
                _paths = unit_paths_map or {}
                drone_rel = _paths.get(drone_name, f'units/{drone_name}.lua')
                drone_url = f'https://raw.githubusercontent.com/{GITHUB_REPO}/refs/heads/{GITHUB_BRANCH}/{drone_rel}'
                drone_resp = requests.get(drone_url, headers=headers, timeout=10)
                if drone_resp.status_code == 200:
                    drone_data = LuaParser.parse_weapons(drone_resp.text)
                    drone_dps = drone_data.get('dps', 0.0)
                    print(f"     🚁 Drone {drone_name} ({drone_rel}): dps={drone_dps} x {maxunits} = {drone_dps * maxunits}")
                    if drone_dps > 0:
                        dps += drone_dps * maxunits
                        result['has_damage'] = True
                else:
                    print(f"     ⚠️  Could not fetch drone unit {drone_name} ({drone_rel}): HTTP {drone_resp.status_code}")
                continue  # Skip normal DPS calculation for this weapon

            # Skip if no main damage (regardless of paralyzer or bogus flag)
            if dmg <= 0:
                continue

            # Apply sweepfire multiplier to damage (display + DPS)
            if wd.get('sweepfire', 1) > 1:
                dmg = dmg * wd['sweepfire']

            # Calculate main projectile DPS (WITHOUT cluster/napalm)
            reload = wd['reloadtime'] or 1.0
            main_dps = (dmg * (1.0 / reload)) * wd['salvosize'] * wd['burst'] * wd['projectiles']
            
            # Add napalm DOT if present
            if wd.get('area_onhit_damage') and wd.get('area_onhit_time'):
                # Napalm DOT = (area_damage × area_time) / reload
                napalm_total = wd['area_onhit_damage'] * wd['area_onhit_time']
                napalm_dot = (napalm_total / reload) * wd['salvosize'] * wd['burst'] * wd['projectiles']
                weapon_dot_dps += napalm_dot
            
            # Add lightning chain damage if present
            if wd.get('spark_forkdamage') and wd.get('spark_maxunits'):
                # Lightning DOT = (default_damage × burst × spark_forkdamage × spark_maxunits) / reload
                base_dmg = wd['dmg_default']
                fork_dmg_per_target = base_dmg * wd['burst'] * wd['spark_forkdamage']
                total_fork_dmg = fork_dmg_per_target * wd['spark_maxunits']
                lightning_dot = (total_fork_dmg / reload) * wd['salvosize'] * wd['projectiles']
                weapon_dot_dps += lightning_dot
            
            # Accumulate separately
            dps += main_dps
            result['dot'] += weapon_dot_dps
            result['has_damage'] = True

            # Track max range
            if wd['range'] > weapon_range:
                weapon_range = wd['range']
            
            # Track max impulsefactor (only from weapons with damage > 0)
            if wd['impulsefactor'] > max_impulsefactor:
                max_impulsefactor = wd['impulsefactor']
            
            # Track max areaofeffect (only from weapons with damage > 0)
            if wd['areaofeffect'] > max_areaofeffect:
                max_areaofeffect = wd['areaofeffect']
            
            # Detect target capabilities from onlytargetcategory
            # Bogus/dummy weapons → all False
            is_bogus_weapon = (
                wd.get('is_bogus', False) or
                'bogus' in def_key.lower() or
                'dummy' in (wd.get('def_name', '') or '').lower()
            )
            has_damage = (wd['dmg_default'] > 0 or wd['dmg_vtol'] > 0 or wd['dmg_subs'] > 0)
            
            if not is_bogus_weapon and has_damage:
                otc = weapon_onlytarget.get(def_key)
                targets = resolve_target_categories(otc)
                if targets['can_target_surface']:
                    result['can_target_surface'] = True
                if targets['can_target_air']:
                    result['can_target_air'] = True
                if targets['can_target_subs']:
                    result['can_target_subs'] = True

            # Add to weapon_table (paralyzer weapons already added above with EMP prefix)
            weapon_table[wtype] = weapon_table.get(wtype, 0) + 1

        # ── Step 4: build weapons text ────────────────────────────────────────
        parts = []
        for wname, count in weapon_table.items():
            parts.append(f"{count}x {wname}" if count > 1 else wname)

        print(f"  🔍 DEBUG weapon_table: {weapon_table}")
        print(f"  🔍 DEBUG has_non_paralyzer: {has_non_paralyzer}")
        
        # If unit only has paralyzer weapons, force DPS/DOT to 0
        if not has_non_paralyzer:
            dps = 0
            result['dot'] = 0
        
        # If no weapons remain after filtering (only bogus/mine), clear weapon fields
        if not weapon_table:
            print(f"  ⚠️  No weapons in weapon_table - clearing all weapon fields")
            result['dps'] = 0
            result['dot'] = 0
            result['weaponrange'] = 0
            result['weapons_text'] = ''
            result['has_weapondefs'] = False
        else:
            result['dps'] = round(dps)
            result['weaponrange'] = round(weapon_range)
            result['weapons_text'] = ", ".join(parts)
        
        result['stockpile_limit'] = stockpile_limit  # None if no weapon has it
        result['max_impulsefactor'] = round(max_impulsefactor, 2) if max_impulsefactor > 0 else None
        result['max_areaofeffect']  = round(max_areaofeffect) if max_areaofeffect > 0 else None

        return result

    @staticmethod
    def parse_unit_file(content: str, unit_name: str, unit_paths_map: dict = None) -> Optional[Dict[str, Any]]:
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

                    # Detect shield fields in customparams
                    for shield_key in ('shield_power', 'shield_radius'):
                        sm = re.search(rf'{shield_key}\s*=\s*([0-9.]+)', customparams_block, re.IGNORECASE)
                        if sm:
                            try:
                                unit_data[shield_key] = float(sm.group(1))
                            except ValueError:
                                pass

            # Detect buildoptions block presence
            bo_match = re.search(r'buildoptions\s*=\s*\{', unit_block, re.IGNORECASE)
            if bo_match:
                bo_block = LuaParser.extract_balanced_braces(unit_block, bo_match.end() - 1)
                # Only count as having buildoptions if there's at least one entry
                if bo_block and re.search(r'"(\w+)"', bo_block):
                    unit_data['_has_buildoptions'] = True

            # Parse weapons: DPS, range, weapon type list, stockpile limit, impulse, aoe, targets
            weapon_result = LuaParser.parse_weapons(unit_block, unit_paths_map=unit_paths_map)
            unit_data['_has_weapondefs'] = weapon_result['has_weapondefs']
            unit_data['_has_damage']     = weapon_result['has_damage']
            if weapon_result['has_weapondefs']:
                unit_data['_dps']              = weapon_result['dps']
                unit_data['_dot']              = weapon_result['dot']  # Damage Over Time
                unit_data['_pps']              = weapon_result['pps']  # Paralyse Per Second
                unit_data['_weaponrange']      = weapon_result['weaponrange']
                unit_data['_weapons_text']     = weapon_result['weapons_text']
                unit_data['_stockpile_limit']  = weapon_result['stockpile_limit']
                unit_data['_max_impulsefactor'] = weapon_result['max_impulsefactor']
                unit_data['_max_areaofeffect'] = weapon_result['max_areaofeffect']
                unit_data['_can_target_surface'] = weapon_result['can_target_surface']
                unit_data['_can_target_air'] = weapon_result['can_target_air']
                unit_data['_can_target_subs'] = weapon_result['can_target_subs']
            
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
            
            # Parse customparams for energyconv (metal makers)
            cp_match = re.search(r'\bcustomparams\s*=\s*\{', unit_block, re.IGNORECASE)
            if cp_match:
                cp_block = LuaParser.extract_balanced_braces(unit_block, cp_match.end() - 1)
                if cp_block:
                    # Energy conversion capacity (metal make rate)
                    ecc_match = re.search(r'\benergyconv_capacity\s*=\s*([0-9.]+)', cp_block, re.IGNORECASE)
                    if ecc_match:
                        unit_data['energyconv_capacity'] = float(ecc_match.group(1))
                    
                    # Energy conversion efficiency
                    ece_match = re.search(r'\benergyconv_efficiency\s*=\s*([0-9.]+)', cp_block, re.IGNORECASE)
                    if ece_match:
                        unit_data['energyconv_efficiency'] = float(ece_match.group(1))
            
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

    def create_item(self, field_data: Dict, is_draft: bool = True) -> Optional[str]:
        """
        Create a new item in the collection.
        Returns the new item's ID on success, or None on failure.
        Items are created as draft by default.
        """
        try:
            self._rate_limit()

            url = f"{self.base_url}/collections/{self.collection_id}/items"

            payload = {
                "fieldData": field_data,
                "isDraft":   is_draft,
            }

            response = requests.post(url, headers=self.headers, json=payload)
            response.raise_for_status()

            data = response.json()
            return data.get('id')

        except Exception as e:
            print(f"Error creating item: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            return None
    
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

    def unpublish_item(self, item_id: str) -> bool:
        """
        Unpublish a single CMS item using Webflow's V2 API.
        Archives the item to fully remove it from the published site.
        (Using isDraft only creates 'changes in draft', isArchived fully unpublishes)
        """
        try:
            self._rate_limit()  # Rate limiting
            
            url = f"{self.base_url}/collections/{self.collection_id}/items/{item_id}"
            
            # Use isArchived to fully unpublish (not just "changes in draft")
            payload = {
                "isArchived": True
            }
            
            response = requests.patch(url, headers=self.headers, json=payload)
            response.raise_for_status()
            
            return True
            
        except Exception as e:
            print(f"  ⚠️  Error archiving item {item_id}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"     Response: {e.response.text}")
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


class MoveDefsParser:
    """Parses movedefs.lua to detect all-terrain movement classes."""
    
    _cache = None  # Cache parsed data
    
    @staticmethod
    def fetch_movedefs(repo: str, branch: str, github_token: Optional[str] = None) -> Optional[str]:
        """Fetch movedefs.lua from GitHub."""
        try:
            url = f"https://raw.githubusercontent.com/{repo}/refs/heads/{branch}/gamedata/movedefs.lua"
            headers = {}
            if github_token:
                headers['Authorization'] = f'token {github_token}'
            
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"Warning: Could not fetch movedefs.lua: {e}")
            return None
    
    @staticmethod
    def parse_allterrain_classes(content: str) -> set:
        """
        Parse movedefs.lua and find all-terrain movement classes.
        
        All-terrain = movedef has no maxslope OR maxslope = SLOPE.MAXIMUM
        
        Returns set of uppercase movement class names.
        """
        allterrain = set()
        
        try:
            # Find the Spring.moveCtrl.loadMoveCtrlDefs block
            block_match = re.search(r'Spring\.moveCtrl\.loadMoveCtrlDefs\s*\(\s*\{', content)
            if not block_match:
                return allterrain
            
            movedefs_block = LuaParser.extract_balanced_braces(content, block_match.end() - 1)
            if not movedefs_block:
                return allterrain
            
            # Find each movedef: CLASSNAME = { ... }
            for movedef_match in re.finditer(r'(\w+)\s*=\s*\{', movedefs_block):
                class_name = movedef_match.group(1).upper()
                movedef_block = LuaParser.extract_balanced_braces(movedefs_block, movedef_match.end() - 1)
                
                if not movedef_block:
                    continue
                
                # Check if maxslope is absent or = SLOPE.MAXIMUM
                maxslope_match = re.search(r'maxslope\s*=\s*([^\n,]+)', movedef_block, re.IGNORECASE)
                
                if not maxslope_match:
                    # No maxslope = all-terrain
                    allterrain.add(class_name)
                else:
                    # Check if maxslope = SLOPE.MAXIMUM (or variants)
                    maxslope_value = maxslope_match.group(1).strip().rstrip(',')
                    if 'SLOPE.MAXIMUM' in maxslope_value.upper() or 'SLOPE_MAXIMUM' in maxslope_value.upper():
                        allterrain.add(class_name)
            
            return allterrain
            
        except Exception as e:
            print(f"Warning: Error parsing movedefs.lua: {e}")
            return allterrain
    
    @classmethod
    def get_allterrain_classes(cls, repo: str, branch: str, github_token: Optional[str] = None) -> set:
        """
        Get all-terrain movement classes from movedefs.lua.
        Uses cache to avoid repeated fetches.
        """
        if cls._cache is not None:
            return cls._cache
        
        content = cls.fetch_movedefs(repo, branch, github_token)
        
        if content:
            cls._cache = cls.parse_allterrain_classes(content)
            
            if cls._cache:
                print(f"  📖 Loaded {len(cls._cache)} all-terrain movement classes from movedefs.lua")
                # Show first few for debugging
                sample = sorted(list(cls._cache))[:5]
                print(f"     Examples: {', '.join(sample)}")
                return cls._cache
        
        # Fallback to hardcoded list if fetch/parse fails
        print("  ⚠️  Could not parse movedefs.lua, using hardcoded all-terrain list")
        cls._cache = {'TBOT3', 'HTBOT6'}  # Known all-terrain classes
        return cls._cache


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
    def dds_to_webp(dds_data: bytes, quality: int = 80) -> Optional[bytes]:
        """
        Convert DDS to WebP with specified quality.
        Pillow 10+ supports reading DDS natively.

        Args:
            dds_data: DDS image as bytes
            quality:  WebP quality (1-100), default 80

        Returns:
            WebP image as bytes, or None on failure
        """
        try:
            img = Image.open(io.BytesIO(dds_data))

            # DDS can come in various modes; normalise to RGBA for WebP
            if img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGBA')

            output = io.BytesIO()
            img.save(output, format='WEBP', quality=quality, method=6)
            return output.getvalue()

        except Exception as e:
            print(f"Error converting DDS to WebP: {e}")
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
        self.buildpics_dir = "buildpics"

    def _upload_file(self, file_data: bytes, repo_path: str, commit_msg: str) -> tuple[Optional[str], bool]:
        """
        Internal helper: commit a file to the GitHub repo and return its raw URL.
        Creates or updates the file depending on whether it already exists.
        Skips upload if file already exists with same size (optimization).
        
        Returns: (raw_url, was_uploaded)
            - raw_url: Public GitHub raw URL or None on error
            - was_uploaded: True if file was actually uploaded, False if skipped
        """
        try:
            import base64

            check_url = f"{self.base_url}/repos/{self.repo_owner}/{self.repo_name}/contents/{repo_path}"
            params    = {"ref": self.branch}

            sha = None
            existing_size = None
            check_response = requests.get(check_url, headers=self.headers, params=params)
            
            if check_response.status_code == 200:
                existing = check_response.json()
                sha = existing.get('sha')
                existing_size = existing.get('size')
                
                # Compare file sizes - if same, skip upload
                new_size = len(file_data)
                if existing_size == new_size:
                    print(f"     File exists with same size ({existing_size} bytes) — skipping upload")
                    # Return the existing URL with was_uploaded=False
                    raw_url = (
                        f"https://raw.githubusercontent.com/"
                        f"{self.repo_owner}/{self.repo_name}/{self.branch}/{repo_path}"
                    )
                    return (raw_url, False)
                else:
                    print(f"     File exists but size changed ({existing_size} → {new_size} bytes), updating...")
                    
            elif check_response.status_code == 404:
                print(f"     Creating new file...")
            else:
                print(f"  ⚠️  Unexpected response checking file: {check_response.status_code}")

            content_base64 = base64.b64encode(file_data).decode('utf-8')
            payload = {
                "message": commit_msg,
                "content": content_base64,
                "branch":  self.branch,
            }
            if sha:
                payload["sha"] = sha

            response = requests.put(check_url, headers=self.headers, json=payload)
            response.raise_for_status()

            raw_url = (
                f"https://raw.githubusercontent.com/"
                f"{self.repo_owner}/{self.repo_name}/{self.branch}/{repo_path}"
            )
            return (raw_url, True)

        except Exception as e:
            print(f"  ❌ Error uploading to GitHub: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"     Response: {e.response.text}")
            return (None, False)
    
    def upload_icon(self, file_data: bytes, filename: str) -> Optional[str]:
        """Upload strategic icon WebP to icons/ folder. Returns public raw URL."""
        repo_path = f"{self.icons_dir}/{filename}"
        url, was_uploaded = self._upload_file(
            file_data, repo_path,
            commit_msg=f"🎨 Add/update strategic icon: {filename}"
        )
        if url:
            if was_uploaded:
                print(f"  ✅ Committed to GitHub: {filename}")
            else:
                print(f"  ✓ Already up-to-date in GitHub: {filename}")
            print(f"     URL: {url}")
        return url

    def upload_buildpic(self, file_data: bytes, filename: str) -> Optional[str]:
        """Upload buildpic WebP to buildpics/ folder. Returns public raw URL."""
        repo_path = f"{self.buildpics_dir}/{filename}"
        url, was_uploaded = self._upload_file(
            file_data, repo_path,
            commit_msg=f"🖼️  Add/update buildpic: {filename}"
        )
        if url:
            if was_uploaded:
                print(f"  ✅ Committed to GitHub: {filename}")
            else:
                print(f"  ✓ Already up-to-date in GitHub: {filename}")
            print(f"     URL: {url}")
        return url


class UnitSyncService:
    """Main service to sync units from GitHub to Webflow."""
    
    def __init__(self, github_fetcher: GitHubUnitFetcher, webflow_api: WebflowAPI):
        self.github = github_fetcher
        self.webflow = webflow_api
        self.parser = LuaParser()
        self.movement_lists    = {}   # Loaded from alldefs_post.lua
        self.allterrain_classes = set()  # Loaded from movedefs.lua
        self.language_data     = {}   # Loaded from language/en/units.json
        self._buildoptions_map  = {}  # { unit_name: [units_it_can_build] } — from cache/archive
        self._carried_units_map = {}  # { unit_name: [drone_unit_names] }   — from cache/archive
        self._unit_paths_map    = {}  # { unit_name: 'units/path/unit.lua' } — from cache/archive
        self._webflow_id_map    = {}  # { unit_name: webflow_item_id }       — built from Webflow items
    
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

    def detect_specials(self, unit_data: Dict) -> str:
        """
        Build a comma-separated string of special abilities for a unit.

        Rules (all based on parsed unit_data fields):
          Cloakable        — cloakcost > 0
          Stealth          — stealth = true OR sonarstealth = true
          Radar            — radardistance > 0
          Sonar            — sonardistance > 0
          Jammer           — radardistancejam > 0
          Shield           — shield_power > 0 OR shield_radius > 0  (in customparams)
          Resurrector      — canresurrect = true
          Capturer         — cancapture = true
          Transport        — transportsize > 0
          Stealth Detector — seismicdistance > 0
          All-terrain      — movementclass has no maxslope or maxslope = SLOPE.MAXIMUM
        """
        specials = []

        def _num(key):
            try:
                return float(unit_data.get(key, 0) or 0)
            except (ValueError, TypeError):
                return 0.0

        def _bool(key):
            v = unit_data.get(key)
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.lower() == 'true'
            return False

        if _num('cloakcost') > 0:
            specials.append('Cloakable')
        if _bool('stealth') or _bool('sonarstealth'):
            specials.append('Stealth')
        if _num('radardistance') > 0:
            specials.append('Radar')
        if _num('sonardistance') > 0:
            specials.append('Sonar')
        if _num('radardistancejam') > 0:
            specials.append('Jammer')
        if _num('shield_power') > 0 or _num('shield_radius') > 0:
            specials.append('Shield')
        if _bool('canresurrect'):
            specials.append('Resurrector')
        if _bool('cancapture'):
            specials.append('Capturer')
        if _num('transportsize') > 0:
            specials.append('Transport')
        if _num('seismicdistance') > 0:
            specials.append('Stealth Detector')
        
        # All-terrain detection based on movementclass
        movementclass = unit_data.get('movementclass', '').upper()
        if movementclass and movementclass in self.allterrain_classes:
            specials.append('All-terrain')

        return ', '.join(specials)

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
                                   "metal-make", "jammerrange", "mass", "cloak-cost", 
                                   "metal-storage", "energy-storage", "seismic-detector-range"]:
                    try:
                        # Convert to int if it's a number
                        if isinstance(value, (int, float)):
                            value = int(value)
                    except:
                        pass
                
                # Decimal fields
                elif webflow_key in ["metal-create", "converter-metal-make", "converter-efficiency"]:
                    try:
                        if isinstance(value, (int, float)):
                            value = float(value)
                    except:
                        pass
                
                webflow_fields[webflow_key] = value
        
        # Handle seismicdistance: only send if > 0
        # (overrides any 0 value that may have been set by the loop above)
        if 'seismicdistance' in github_data:
            seismic_val = github_data['seismicdistance']
            try:
                seismic_val = int(float(seismic_val))
            except:
                seismic_val = 0
            if seismic_val > 0:
                webflow_fields['seismic-detector-range'] = seismic_val
            else:
                webflow_fields.pop('seismic-detector-range', None)
        
        # Handle paralyzemultiplier separately (from customparams, decimal field)
        if 'paralyzemultiplier' in github_data:
            try:
                webflow_fields['paralyze-multiplier'] = float(github_data['paralyzemultiplier'])
            except:
                pass
        
        # Handle techlevel (from customparams, integer field)
        # Default to 1 if not found (most units without explicit techlevel are T1)
        try:
            webflow_fields['techlevel'] = int(github_data.get('techlevel', 1) or 1)
        except:
            webflow_fields['techlevel'] = 1
        
        # Handle energyconv_capacity (from customparams, decimal field)
        if 'energyconv_capacity' in github_data:
            try:
                webflow_fields['converter-metal-make'] = float(github_data['energyconv_capacity'])
            except:
                pass
        
        # Handle energyconv_efficiency (from customparams, decimal field)
        if 'energyconv_efficiency' in github_data:
            try:
                webflow_fields['converter-efficiency'] = float(github_data['energyconv_efficiency'])
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

        # Handle weapon fields (DPS, range, weapon type list, stockpile, impulse, aoe)
        # ALWAYS process these fields to allow clearing old values
        dps = github_data.get('_dps', 0)
        webflow_fields['dps'] = int(dps) if dps else 0
        
        dot = github_data.get('_dot', 0)
        webflow_fields['dot'] = int(dot) if dot else 0
        
        pps = github_data.get('_pps', 0)
        webflow_fields['pps'] = int(pps) if pps else 0
        
        # Always sync weaponrange (even if 0) to clear old values
        wr = github_data.get('_weaponrange', 0)
        webflow_fields['weaponrange'] = int(wr) if wr else 0
        
        # Always sync weapons text (even if empty) to clear old values
        wt = github_data.get('_weapons_text', '')
        webflow_fields['weapons'] = wt if wt else ''
        
        # Debug: show what we're sending for weapon fields
        print(f"  🔧 DEBUG Webflow fields:")
        print(f"     dps: {webflow_fields.get('dps', 'NOT SET')}")
        print(f"     dot: {webflow_fields.get('dot', 'NOT SET')}")
        print(f"     pps: {webflow_fields.get('pps', 'NOT SET')}")
        print(f"     weaponrange: {webflow_fields.get('weaponrange', 'NOT SET')}")
        print(f"     weapons: '{webflow_fields.get('weapons', 'NOT SET')}'")
        
        # Only process additional weapon stats if unit has real weapons
        if github_data.get('_has_weapondefs'):
            sl = github_data.get('_stockpile_limit')
            if sl is not None:
                webflow_fields['stockpile-limit'] = int(sl)
            # Impulsefactor (max across all damage-dealing weapons, 2 decimals)
            imp = github_data.get('_max_impulsefactor')
            if imp is not None:
                webflow_fields['weapon-max-impulse'] = float(imp)
            # Area of effect (max across all damage-dealing weapons, integer)
            aoe = github_data.get('_max_areaofeffect')
            if aoe is not None:
                webflow_fields['weapon-area-of-effect'] = int(aoe)
            # Target capabilities (booleans)
            webflow_fields['can-target-surface'] = github_data.get('_can_target_surface', False)
            webflow_fields['can-target-air'] = github_data.get('_can_target_air', False)
            webflow_fields['can-target-subs'] = github_data.get('_can_target_subs', False)

        # Handle buildoptions multi-reference field
        # Look up what this unit can build, then resolve each to a Webflow item ID
        # Also includes carried_units (drones) from drone carrier weapons
        if unit_name and self._buildoptions_map and self._webflow_id_map:
            can_build = list(self._buildoptions_map.get(unit_name.lower(), []))
            # Add carried_units (drones) — drone carriers don't have real buildoptions
            carried = getattr(self, '_carried_units_map', {}).get(unit_name.lower(), [])
            for drone in carried:
                if drone not in can_build:
                    can_build.append(drone)
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

        # Handle transportable-by multi-reference field
        # Maps which transport units can carry this unit (based on mass + footprintX checks)
        # ALWAYS send this field (even as empty list) to clear old incorrect values
        if unit_name and hasattr(self, '_transportable_by_map') and self._webflow_id_map:
            can_be_transported_by = self._transportable_by_map.get(unit_name.lower(), [])
            transport_ids = []
            transport_unresolved = []
            for transport_name in can_be_transported_by:
                wf_id = self._webflow_id_map.get(transport_name)
                if wf_id:
                    transport_ids.append(wf_id)
                else:
                    transport_unresolved.append(transport_name)
            webflow_fields['transportable-by'] = transport_ids
            if transport_unresolved:
                github_data['_transport_unresolved'] = transport_unresolved

        # Handle specials (comma-separated string of special abilities)
        specials = self.detect_specials(github_data)
        if specials:
            webflow_fields['specials'] = specials

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

    def sync_unit_buildpic(self, unit_name: str, dds_filename: str,
                           github_uploader: GitHubIconUploader,
                           current_buildpic_url: Optional[str] = None,
                           dry_run: bool = False) -> Optional[str]:
        """
        Sync a unit's in-game buildpic.

        Reads the DDS file from BAR's unitpics/ folder, converts it to WebP (80%
        quality), commits it to the icon repo under buildpics/, and returns the
        public raw GitHub URL.

        Args:
            unit_name:            Unit name (used for the output filename)
            dds_filename:         Filename as found in buildpic field (e.g. "armflea.dds")
            github_uploader:      GitHubIconUploader instance
            current_buildpic_url: Current URL already stored in Webflow (skip if unchanged)
            dry_run:              If True, don't actually upload

        Returns:
            Public GitHub raw URL on success, None on failure
        """
        try:
            # BAR stores buildpics in unitpics/<n>.dds
            # Strip path prefix, force lowercase (GitHub is case-sensitive)
            dds_basename = dds_filename.split('/')[-1].split('\\')[-1].lower()
            if not dds_basename.endswith('.dds'):
                dds_basename += '.dds'
            dds_path = f"unitpics/{dds_basename}"

            dds_url = (
                f"https://raw.githubusercontent.com/{GITHUB_REPO}"
                f"/refs/heads/{GITHUB_BRANCH}/{dds_path}"
            )

            headers = {}
            github_token = os.environ.get("GITHUB_TOKEN")
            if github_token:
                headers['Authorization'] = f'token {github_token}'

            print(f"    📥 Downloading buildpic: {dds_path}")
            response = requests.get(dds_url, headers=headers)
            response.raise_for_status()
            dds_data = response.content

            # Convert DDS → WebP (80% quality)
            print(f"    🔄 Converting DDS → WebP (80% quality)")
            webp_data = ImageConverter.dds_to_webp(dds_data, quality=80)

            if not webp_data:
                print(f"    ❌ Failed to convert DDS image")
                return None

            webp_filename = f"{unit_name}.webp"

            if dry_run:
                print(f"    ℹ️  Would commit: buildpics/{webp_filename} ({len(webp_data):,} bytes)")
                return "https://raw.githubusercontent.com/example/repo/main/buildpics/dry-run.webp"

            print(f"    📤 Committing to GitHub: buildpics/{webp_filename}")
            public_url = github_uploader.upload_buildpic(webp_data, webp_filename)
            return public_url

        except Exception as e:
            print(f"    ❌ Error syncing buildpic: {e}")
            return None

    def _build_buildable_set_from_archive(self) -> set:
        """
        Download the entire repository as a zip archive and scan all unit .lua
        files for buildoptions in a single pass.

        Builds a RECURSIVE build tree starting from commanders (armcom, corcom, legcom).
        Only units reachable from commanders are considered buildable.

        Builds two indexes and caches both to .buildable_cache.json:
          - buildable      : set of unit names reachable from commanders
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
                    self._buildoptions_map    = cache_data.get('buildoptions_map', {})
                    self._carried_units_map   = cache_data.get('carried_units_map', {})
                    self._unit_paths_map      = cache_data.get('unit_paths_map', {})
                    self._transportable_by_map = cache_data.get('transportable_by_map', {})
                    print(f"  💾 Loaded {len(buildable)} buildable units from cache"
                          f" ({len(self._buildoptions_map)} units have buildoptions,"
                          f" {len(self._transportable_by_map)} transportable)"
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
            buildoptions_map  = {}  # { unit_name: [units_it_can_build] }
            carried_units_map = {}  # { unit_name: [drone_unit_names] }
            unit_paths_map    = {}  # { unit_name: 'units/path/to/unit.lua' }
            transport_defs    = {}  # { unit_name: { transportsize, transportmass } }
            unit_transport_data = {}  # { unit_name: { mass, footprintx, cantbetransported } }
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
                    # Store relative path (strip repo-branch prefix)
                    rel_path = '/'.join(parts[1:])  # e.g. units/Legion/Air/legdrone.lua
                    unit_paths_map[unit_name] = rel_path

                    options = LuaParser.parse_buildoptions(content, unit_name)
                    if options:
                        with_options += 1
                        normalized = [o.lower() for o in options]
                        all_buildable.update(normalized)
                        buildoptions_map[unit_name] = normalized

                    # Also scan for drone carrier weapons (customparams.carried_unit)
                    clean = re.sub(r'--[^\n]*', '', content)
                    for cu_m in re.finditer(r'\bcarried_unit\s*=\s*["\']([\w]+)["\']', clean, re.IGNORECASE):
                        drone_name = cu_m.group(1).lower()
                        if unit_name not in carried_units_map:
                            carried_units_map[unit_name] = []
                        if drone_name not in carried_units_map[unit_name]:
                            carried_units_map[unit_name].append(drone_name)

                    # --- Extract transport-relevant fields for ALL units ---
                    # (reuses 'clean' = content with Lua comments stripped)

                    # Check if this unit IS a transport (has transportsize > 0)
                    ts_match = re.search(r'\btransportsize\s*=\s*(\d+)', clean, re.IGNORECASE)
                    tm_match = re.search(r'\btransportmass\s*=\s*([0-9.]+)', clean, re.IGNORECASE)
                    if ts_match and int(ts_match.group(1)) > 0:
                        transport_defs[unit_name] = {
                            'transportsize': int(ts_match.group(1)),
                            # Default 100000.0 matches engine default when not specified
                            'transportmass': float(tm_match.group(1)) if tm_match else 100000.0,
                        }

                    # For every unit: store mass, footprintx, cantbetransported
                    # ENGINE DEFAULT (UnitDef.cpp):
                    #   metal = max(1.0, buildCostMetal)
                    #   mass  = clamp(GetFloat("mass", metal), MINIMUM_MASS, MAXIMUM_MASS)
                    # So if mass is not set, the engine uses metalcost as mass.
                    #
                    # cantBeTransported ENGINE DEFAULT (UnitDef.cpp):
                    #   cantBeTransported = GetBool("cantBeTransported", !RequireMoveDef())
                    #   → structures (no movementclass, not canfly) default to TRUE (not transportable)
                    #   → mobile units default to FALSE (transportable)
                    #   Some structures like armllt explicitly set cantbetransported = false
                    mass_m  = re.search(r'\bmass\s*=\s*([0-9.]+)', clean, re.IGNORECASE)
                    mc_m    = re.search(r'\bmetalcost\s*=\s*([0-9.]+)', clean, re.IGNORECASE)
                    fp_m    = re.search(r'\bfootprintx\s*=\s*(\d+)', clean, re.IGNORECASE)
                    cbt_true  = re.search(r'\bcantbetransported\s*=\s*true', clean, re.IGNORECASE)
                    cbt_false = re.search(r'\bcantbetransported\s*=\s*false', clean, re.IGNORECASE)
                    has_moveclass = re.search(r'\bmovementclass\s*=', clean, re.IGNORECASE)
                    has_canfly   = re.search(r'\bcanfly\s*=\s*true', clean, re.IGNORECASE)

                    if mass_m:
                        effective_mass = float(mass_m.group(1))
                    elif mc_m:
                        effective_mass = max(1.0, float(mc_m.group(1)))
                    else:
                        effective_mass = 1.0

                    # Determine cantbetransported:
                    # Engine rules (UnitDef.cpp + Unit.cpp):
                    #   aircraft (canfly = true) → ALWAYS not transportable (BAR has transportAir disabled)
                    #   structures (no movementclass) → not transportable by default
                    #   mobile ground units → transportable by default
                    # Explicit cantbetransported = true/false overrides the default for ground units
                    # and structures, but NOT for aircraft — those are never transportable in BAR.
                    is_aircraft = bool(has_canfly)
                    if is_aircraft:
                        cant_transport = True
                    elif cbt_true:
                        cant_transport = True
                    elif cbt_false:
                        cant_transport = False
                    else:
                        # No explicit setting: structures default to not transportable
                        is_structure = not has_moveclass
                        cant_transport = is_structure

                    unit_transport_data[unit_name] = {
                        'mass':                effective_mass,
                        'footprintx':          int(fp_m.group(1)) if fp_m else 1,
                        'cantbetransported':   cant_transport,
                    }

            print(f"  Scanned {scanned} unit files, {with_options} have buildoptions")

            # --- Compute transport compatibility ---
            # For each unit, determine which transport units can carry it.
            # Only match transports from the SAME faction (arm↔arm, cor↔cor, leg↔leg).
            def _faction_prefix(name):
                """Return faction prefix for a unit name, or '' if unknown."""
                for prefix in sorted(FACTION_MAP.keys(), key=len, reverse=True):
                    if name.startswith(prefix):
                        return prefix
                return ''

            transportable_by_map = {}  # { unit_name: [transport_unit_names] }
            for uname, udata in unit_transport_data.items():
                if udata['cantbetransported']:
                    continue
                unit_faction = _faction_prefix(uname)
                compatible = []
                for tname, tdata in transport_defs.items():
                    # Same-faction check
                    if _faction_prefix(tname) != unit_faction:
                        continue
                    if (udata['footprintx'] <= tdata['transportsize'] and
                            udata['mass'] <= tdata['transportmass']):
                        compatible.append(tname)
                if compatible:
                    # Sort by transportmass (smallest capacity first)
                    compatible.sort(key=lambda t: transport_defs[t]['transportmass'])
                    transportable_by_map[uname] = compatible

            print(f"  🚁 Found {len(transport_defs)} transport units: {', '.join(sorted(transport_defs.keys()))}")
            print(f"  📦 Computed transport compatibility for {len(transportable_by_map)} transportable units")

            # Store on instance for use during sync
            self._buildoptions_map    = buildoptions_map
            self._carried_units_map   = carried_units_map
            self._unit_paths_map      = unit_paths_map
            self._transportable_by_map = transportable_by_map

            # --- Build recursive tree from commanders ---
            print(f"  🌳 Building recursive build tree from commanders...")
            COMMANDERS = {'armcom', 'corcom', 'legcom'}
            buildable_from_commanders = set()
            commander_stats = {}  # Track per-commander statistics
            
            def collect_buildable_recursive(unit_name: str, visited: set):
                """Recursively collect all units buildable from this unit."""
                if unit_name in visited:
                    return
                visited.add(unit_name)
                buildable_from_commanders.add(unit_name)

                # Get what this unit can build via buildoptions
                can_build = buildoptions_map.get(unit_name.lower(), [])
                for child_unit in can_build:
                    collect_buildable_recursive(child_unit, visited)

                # Also include drones carried by this unit
                for drone_unit in carried_units_map.get(unit_name.lower(), []):
                    collect_buildable_recursive(drone_unit, visited)
            
            # Start from each commander and recursively collect their build trees
            for commander in COMMANDERS:
                visited_for_commander = set()
                collect_buildable_recursive(commander, visited_for_commander)
                commander_stats[commander] = len(visited_for_commander)
            
            print(f"  ✅ Found {len(buildable_from_commanders)} units buildable from commanders")
            print(f"     (was {len(all_buildable)} if we counted ALL units in any buildoptions)")
            print(f"     Breakdown per faction:")
            for commander, count in sorted(commander_stats.items()):
                faction = commander[:3].upper()
                print(f"       {faction}: {count} units from {commander}")
            
            # Show excluded units for debugging
            excluded = all_buildable - buildable_from_commanders
            if excluded:
                print(f"     Excluded {len(excluded)} units not in commander trees")
                # Show first 10 as examples
                sample = sorted(list(excluded))[:10]
                print(f"       Examples: {', '.join(sample)}")
                if len(excluded) > 10:
                    print(f"       ... and {len(excluded) - 10} more")

            # --- Save to cache ---
            try:
                with open(BUILDABLE_CACHE_FILE, 'w') as f:
                    json.dump({
                        'repo':               GITHUB_REPO,
                        'branch':             GITHUB_BRANCH,
                        'buildable':          sorted(buildable_from_commanders),
                        'buildoptions_map':   buildoptions_map,
                        'carried_units_map':  carried_units_map,
                        'unit_paths_map':     unit_paths_map,
                        'transportable_by_map': transportable_by_map,
                    }, f, indent=2)
                print(f"  💾 Saved buildable index + buildoptions map to {BUILDABLE_CACHE_FILE}")
            except Exception as e:
                print(f"  ⚠️  Could not save buildable cache: {e}")

            return buildable_from_commanders

        except Exception as e:
            print(f"  ❌ Archive download failed: {e}")
            self._buildoptions_map    = {}
            self._carried_units_map   = {}
            self._unit_paths_map      = {}
            self._transportable_by_map = {}
            return set()

    def sync_all_units(self, dry_run: bool = False, auto_publish: bool = False,
                       sync_icons: bool = False, unit_filter: Optional[str] = None,
                       faction_filter: Optional[str] = None, force: bool = False):
        """
        Sync all units from GitHub to Webflow.

        Args:
            dry_run:     If True, show what would be updated without making changes
            auto_publish: If True, automatically publish updated items
            sync_icons:  If True, also sync strategic icons from icontypes.lua
            unit_filter: If set, only sync this single unit (by name, e.g. 'armzeus')
            faction_filter: If set, only sync units whose name starts with this prefix (e.g. 'arm')
            force:       If True, overwrite all units even if unchanged
        """
        print("=" * 80)
        print("Beyond All Reason - Unit Data Sync")
        print("=" * 80)
        print()
        
        # Step 0: Load definitions from BAR repository
        print("Step 0: Loading definitions from BAR repository...")
        github_token = os.environ.get("GITHUB_TOKEN")
        
        self.movement_lists = AllDefsParser.get_movement_lists(GITHUB_REPO, GITHUB_BRANCH, github_token)
        self.allterrain_classes = MoveDefsParser.get_allterrain_classes(GITHUB_REPO, GITHUB_BRANCH, github_token)
        self.language_data = LanguageParser.fetch_and_parse(GITHUB_REPO, GITHUB_BRANCH, github_token)
        print()
        
        # Step 1: Set up GitHub uploader (needed for buildpics + optional icon sync)
        icon_map = {}
        github_uploader = None

        icon_repo_owner = os.environ.get("ICON_REPO_OWNER", "icexuick")
        icon_repo_name  = os.environ.get("ICON_REPO_NAME",  "bar-unit-sync")
        icon_branch     = os.environ.get("ICON_BRANCH",     "main")

        if github_token:
            github_uploader = GitHubIconUploader(
                icon_repo_owner, icon_repo_name, github_token, icon_branch
            )
            print(f"Step 1: GitHub uploader ready → {icon_repo_owner}/{icon_repo_name} (branch: {icon_branch})")
        else:
            print("Step 1: ⚠️  GITHUB_TOKEN not set — buildpic sync disabled")
            print("   Set GITHUB_TOKEN in .env to enable buildpic and icon uploads")
        print()

        if sync_icons:
            print("Step 1a: Fetching icontypes.lua for icon paths...")

            if not github_token:
                print("⚠️  GITHUB_TOKEN not found - icon sync requires GitHub token")
                sync_icons = False
            else:
                icontypes_content = IconTypesParser.fetch_icontypes(GITHUB_REPO, GITHUB_BRANCH, github_token)

                if icontypes_content:
                    icon_map = IconTypesParser.parse_icontypes(icontypes_content)
                    print(f"Found {len(icon_map)} unit icons in icontypes.lua")
                    print(f"Will commit icons to: {icon_repo_owner}/{icon_repo_name} (branch: {icon_branch})")
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
                  f" skipping {len(unbuildable_files)} unbuildable")
            unit_files = buildable_files
        else:
            print("  ⚠️  Could not determine buildable units — syncing all units")
        print()

        # Apply faction filter if requested (--faction arm)
        if faction_filter:
            prefix = faction_filter.strip().lower()
            unit_files = [uf for uf in unit_files if uf['name'].lower().startswith(prefix)]
            print(f"  🎯 Faction filter: syncing only '{prefix}*' units ({len(unit_files)} found)")
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
        
        # Step 2b: Unpublish items not in buildable tree
        print("Step 2b: Checking for published items not in commander build tree...")
        unpublish_count = 0
        unpublished_examples = []
        
        for unit_name, item in webflow_lookup.items():
            # Check if this item is already archived
            is_archived = item.get('isArchived', False)
            
            # Skip if already archived
            if is_archived:
                continue
            
            # Check if unit is NOT in buildable tree
            if unit_name not in all_buildable:
                unpublish_count += 1
                unpublished_examples.append(unit_name)
                
                if dry_run:
                    print(f"  🔍 Would archive (unpublish): {unit_name}")
                else:
                    print(f"  📦 Archiving: {unit_name}")
                    # Archive the item (fully unpublish)
                    success = self.webflow.unpublish_item(item['id'])
                    if not success:
                        print(f"     ⚠️  Failed to archive {unit_name}")
        
        if unpublish_count == 0:
            print("  ✅ All published items are in the commander build tree")
        else:
            action = "Would archive" if dry_run else "Archived"
            print(f"  ✅ {action} {unpublish_count} items not in build tree")
            if unpublished_examples[:5]:
                print(f"     Examples: {', '.join(unpublished_examples[:5])}")
        
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
            'specials':            'Specials         ',
            'buildpic-in-game':    'BuildPic In-game ',
            'stockpile-limit':     'Stockpile Limit  ',
            'weapon-max-impulse':  'Max Impulse      ',
            'weapon-area-of-effect': 'Max Area of Effect',
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
            'cloak-cost-moving':   'Cloak Cost Moving',
            'paralyze-multiplier': 'Paralyze Mult    ',
            'transportable-by':   'Transportable By ',
        }
        
        stats = {
            'processed': 0,
            'updated':   0,
            'created':   0,
            'skipped':   0,
            'errors':    0,
        }
        
        for unit_file in unit_files:
            unit_name = unit_file['name']
            file_path = unit_file['path']
            
            print(f"Processing: {unit_name} ({file_path})")
            
            is_new_unit = unit_name not in webflow_lookup
            
            # Fetch unit data from GitHub
            lua_content = self.github.fetch_unit_data(file_path)
            if not lua_content:
                print(f"  ❌ Failed to fetch file")
                stats['errors'] += 1
                print()
                continue
            
            # Parse the Lua file
            github_data = self.parser.parse_unit_file(lua_content, unit_name, unit_paths_map=self._unit_paths_map)
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
                    elif field_key == 'transportable-by':
                        # Show transport unit names (resolved from IDs) for readability
                        id_to_name = {v: k for k, v in self._webflow_id_map.items()}
                        names = [id_to_name.get(wf_id, wf_id) for wf_id in value]
                        print(f"     {label}: [{', '.join(names)}]  ({len(names)} transports)")
                        unresolved = github_data.get('_transport_unresolved', [])
                        if unresolved:
                            print(f"     {'':19}  ⚠️  {len(unresolved)} not in Webflow (skipped): {', '.join(unresolved)}")
                    else:
                        print(f"     {label}: {value}")
                else:
                    print(f"     {label}: —")
            print()

            # ── Buildpic sync (always, for both new and existing units) ───────
            dds_filename = github_data.get('buildpic', '')
            if dds_filename and github_uploader:
                current_bp_url = (
                    webflow_lookup.get(unit_name, {}).get('fieldData', {}).get('buildpic-in-game')
                    if not is_new_unit else None
                )
                print(f"  🖼️  Syncing buildpic ({dds_filename})...")
                bp_url = self.sync_unit_buildpic(
                    unit_name, dds_filename, github_uploader,
                    current_bp_url, dry_run
                )
                if bp_url:
                    webflow_fields['buildpic-in-game'] = bp_url

            # ── Icon sync (when --sync-icons enabled) ─────────────────────────
            if sync_icons and unit_name in icon_map and github_uploader:
                current_icon_url = (
                    webflow_lookup.get(unit_name, {}).get('fieldData', {}).get('icon')
                    if not is_new_unit else None
                )
                icon_path = icon_map[unit_name]
                print(f"  🎨 Syncing strategic icon...")
                icon_url = self.sync_unit_icon(
                    unit_name, icon_path, github_uploader,
                    current_icon_url, dry_run
                )
                if icon_url:
                    webflow_fields['icon'] = icon_url

            # ── New unit: skip change-detection, go straight to create ────────
            if is_new_unit:
                print(f"  🆕 New unit — will be created as draft in Webflow")
            else:
                # ── Existing unit: detect what changed ─────────────────────────
                # Get current Webflow data
                webflow_item = webflow_lookup[unit_name]
                current_data = webflow_item.get('fieldData', {})
            
            # Check what's changed (only for existing units)
            changes = {}
            if not is_new_unit:
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
                
                if not changes and not force:
                    print(f"  ✓ Already up-to-date — no changes needed")
                    stats['skipped'] += 1
                    print()
                    continue
            
            # Show changes with readable labels (existing units only)
            if not is_new_unit and changes:
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
                    elif key == 'transportable-by':
                        id_to_name = {v: k for k, v in self._webflow_id_map.items()}
                        if isinstance(old_val, list):
                            old_val = f"[{', '.join(id_to_name.get(i, i) for i in old_val)}] ({len(old_val)})"
                        if isinstance(new_val, list):
                            new_val = f"[{', '.join(id_to_name.get(i, i) for i in new_val)}] ({len(new_val)})"
                    print(f"     {label}: {old_val}  →  {new_val}")
            
            # Update in Webflow (unless dry run)
            if dry_run:
                if is_new_unit:
                    print(f"  🔍 DRY RUN — would create as draft in Webflow")
                else:
                    print(f"  🔍 DRY RUN — no changes written to Webflow")
                stats['skipped'] += 1
                print()
                continue

            if is_new_unit:
                # ── CREATE new item as draft ──────────────────────────────────
                # name and slug are required by Webflow
                webflow_fields['name'] = unit_name
                webflow_fields['slug'] = unit_name

                new_id = self.webflow.create_item(webflow_fields, is_draft=True)
                if new_id:
                    print(f"  ✅ Created as draft (id: {new_id})")
                    stats['created'] += 1
                    # Add to webflow_lookup + id_map so buildoptions of later
                    # units in this same run can resolve this new item
                    self._webflow_id_map[unit_name] = new_id
                    webflow_lookup[unit_name] = {'id': new_id, 'fieldData': webflow_fields}
                else:
                    print(f"  ❌ Create failed")
                    stats['errors'] += 1
            else:
                # ── UPDATE existing item ──────────────────────────────────────
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
        print(f"Total units processed : {stats['processed']}")
        print(f"Created (draft)       : {stats['created']}")
        print(f"Updated               : {stats['updated']}")
        print(f"Skipped (no changes)  : {stats['skipped']}")
        print(f"Errors                : {stats['errors']}")
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
        '--faction',
        type=str,
        help='Sync only units from a specific faction (e.g. arm, cor, leg, raptor)'
    )

    parser.add_argument(
        '--force',
        action='store_true',
        help='Overwrite all units in Webflow, even if unchanged'
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
        unit_filter=args.unit,
        faction_filter=args.faction,
        force=args.force
    )


if __name__ == "__main__":
    main()
