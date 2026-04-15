#!/usr/bin/env python3
"""
Beyond All Reason - Weapon Sync to Webflow

Syncs individual weapon data from BAR GitHub repository to Webflow CMS.
Each weapon gets its own item in the "Unit Weapons" collection with detailed stats.

Usage:
    python sync_weapons_to_webflow.py [--dry-run] [--unit armcom]

License: BAR Only - See LICENSE file
"""

import os
import re
import json
import time
import requests
import argparse
from typing import Dict, List, Optional, Any, Tuple
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

GITHUB_REPO = "beyond-all-reason/Beyond-All-Reason"
GITHUB_BRANCH = "master"
GITHUB_UNITS_PATH = "units"

WEBFLOW_SITE_ID = "5c68622246b367adf6f3041d"
UNITS_COLLECTION_ID = "6564c6553676389f8ba45a9e"
WEAPONS_COLLECTION_ID = "699446edb237b8c196b4c683"
WEAPON_CATEGORIES_COLLECTION_ID = "6998dad0bb861bb8fa17d237"

# Weapon category mapping (weapontype → category rules)
# We'll fetch actual IDs from Webflow at runtime
WEAPON_CATEGORY_MAP = {}  # Populated during init

# Kamikaze units: unit_name → weapondef_key
# These are suicide units whose weapon is a one-time explosion (trigger-explosive).
# DPS is set to full alpha damage instead of damage/reload.
KAMIKAZE_WEAPONS = {
    "legkam": "martyrbomb",
}

# Category overrides: (unit_name, weapondef_key) → category_slug
# For weapons that use a different weapontype in-game for special mechanics
CATEGORY_OVERRIDES = {
    ("armmship", "rocket"): "vertical-rocket-launcher",
    ("cormship", "rocket"): "vertical-rocket-launcher",
    ("leganavymissileship", "leg_salvo_vertical_rocket"): "vertical-rocket-launcher",
}

# Weapontype overrides: (unit_name, weapondef_key) → weapontype string for Webflow
WEAPONTYPE_OVERRIDES = {
    ("armmship", "rocket"): "StarburstLauncher",
    ("cormship", "rocket"): "StarburstLauncher",
    ("leganavymissileship", "leg_salvo_vertical_rocket"): "StarburstLauncher",
}


# ══════════════════════════════════════════════════════════════════════════════
# SHARED UTILITIES (imported from main sync)
# ══════════════════════════════════════════════════════════════════════════════

class LuaParser:
    """Lua parsing utilities."""
    
    @staticmethod
    def extract_balanced_braces(text: str, start_pos: int) -> Optional[str]:
        """
        Extract a balanced { ... } block starting from start_pos.
        Returns the content between braces (excluding the outer braces).
        """
        if start_pos >= len(text) or text[start_pos] != '{':
            return None
        
        depth = 0
        i = start_pos
        
        while i < len(text):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start_pos + 1:i]
            i += 1
        
        return None


class RateLimiter:
    """Rate limiter with per-request delay to avoid Webflow 429 burst detection."""

    def __init__(self, max_requests_per_minute: int = 50, min_delay: float = 1.5):
        self.max_requests = max_requests_per_minute
        self.min_delay = min_delay  # minimum seconds between any two requests
        self.requests = []
        self.last_request = 0

    def wait_if_needed(self):
        """Wait to respect both burst delay and per-minute cap."""
        now = time.time()

        # Always wait at least min_delay since last request (anti-burst)
        since_last = now - self.last_request
        if since_last < self.min_delay:
            time.sleep(self.min_delay - since_last)

        # Per-minute cap
        now = time.time()
        self.requests = [ts for ts in self.requests if now - ts < 60]

        if len(self.requests) >= self.max_requests:
            sleep_time = 60 - (now - self.requests[0]) + 1.0
            if sleep_time > 0:
                print(f"  ⏱️  Rate limit cap — waiting {sleep_time:.1f}s...")
                time.sleep(sleep_time)
                self.requests = []

        self.last_request = time.time()
        self.requests.append(self.last_request)


# ══════════════════════════════════════════════════════════════════════════════
# WEBFLOW API CLIENT
# ══════════════════════════════════════════════════════════════════════════════

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
        self.rate_limiter = rate_limiter or RateLimiter()
    
    def _rate_limit(self):
        """Apply rate limiting before API calls."""
        if self.rate_limiter:
            self.rate_limiter.wait_if_needed()

    def _request_with_retry(self, method, url, max_retries=3, **kwargs):
        """Make an API request with automatic retry on 429 Too Many Requests."""
        for attempt in range(max_retries):
            self._rate_limit()
            response = method(url, headers=self.headers, **kwargs)
            if response.status_code == 429:
                wait = 15 * (attempt + 1)
                print(f"  ⏱️  429 Too Many Requests — waiting {wait}s (attempt {attempt+1}/{max_retries})...")
                time.sleep(wait)
                continue
            return response
        return response  # return last response even if still 429

    def get_all_items(self) -> List[Dict]:
        """Fetch all items from the collection."""
        items = []
        offset = 0
        limit = 100
        
        while True:
            try:
                url = f"{self.base_url}/collections/{self.collection_id}/items"
                params = {"offset": offset, "limit": limit}

                response = self._request_with_retry(requests.get, url, params=params)
                response.raise_for_status()
                data = response.json()
                
                items.extend(data.get('items', []))
                
                total = data.get('pagination', {}).get('total', 0)
                if offset + limit >= total:
                    break
                    
                offset += limit
                
            except Exception as e:
                print(f"Error fetching items: {e}")
                break
        
        return items
    
    def create_item(self, field_data: Dict, is_draft: bool = True) -> Optional[str]:
        """Create a new item. Returns the item ID on success."""
        try:
            url = f"{self.base_url}/collections/{self.collection_id}/items"

            payload = {
                "fieldData": field_data,
                "isDraft": is_draft
            }

            response = self._request_with_retry(requests.post, url, json=payload)
            response.raise_for_status()
            
            data = response.json()
            return data.get('id')
            
        except Exception as e:
            print(f"  ❌ Error creating item: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"     Response: {e.response.text}")
            return None
    
    def update_item(self, item_id: str, field_data: Dict) -> bool:
        """Update an existing item."""
        try:
            url = f"{self.base_url}/collections/{self.collection_id}/items/{item_id}"

            payload = {
                "fieldData": field_data
            }

            response = self._request_with_retry(requests.patch, url, json=payload)
            response.raise_for_status()
            
            return True
            
        except Exception as e:
            print(f"  ❌ Error updating item {item_id}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"     Response: {e.response.text}")
            return False
    
    def publish_item(self, item_id: str) -> bool:
        """Publish a single item."""
        try:
            url = f"{self.base_url}/collections/{self.collection_id}/items/publish"

            payload = {
                "itemIds": [item_id]
            }

            response = self._request_with_retry(requests.post, url, json=payload)
            response.raise_for_status()
            
            # Check response for errors
            data = response.json()
            if data.get('errors'):
                print(f"  Warning: Publish had errors: {data['errors']}")
                return False
            
            return True
            
        except Exception as e:
            print(f"  ❌ Error publishing item {item_id}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"     Response: {e.response.text}")
            return False

    # ── Bulk operations (max 100 items per request) ──────────────────────────

    def bulk_create_items(self, items_field_data: List[Dict], is_draft: bool = False) -> List[str]:
        """
        Create multiple items in one API call.
        items_field_data: list of fieldData dicts (max 100).
        Returns list of created item IDs.
        """
        if not items_field_data:
            return []
        try:
            url = f"{self.base_url}/collections/{self.collection_id}/items"
            # Webflow v2 bulk create expects { items: [{fieldData, isDraft}, ...] }
            payload = {
                "items": [
                    {"fieldData": fd, "isDraft": is_draft}
                    for fd in items_field_data
                ]
            }
            response = self._request_with_retry(requests.post, url, json=payload)
            response.raise_for_status()
            data = response.json()
            # Response: {items: [{id, fieldData, ...}, ...]}
            if isinstance(data, dict):
                items = data.get('items', [])
                if items:
                    return [item.get('id') for item in items if item.get('id')]
                if data.get('id'):
                    return [data['id']]
            elif isinstance(data, list):
                return [item.get('id') for item in data if item.get('id')]
            return []
        except Exception as e:
            print(f"  ❌ Bulk create error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"     Response: {e.response.text}")
            return []

    def bulk_update_items(self, items: List[Dict]) -> int:
        """
        Update multiple items in one API call.
        items: list of {id, fieldData} dicts (max 100).
        Returns number of successfully updated items.
        """
        if not items:
            return 0
        try:
            url = f"{self.base_url}/collections/{self.collection_id}/items"
            payload = {"items": items}
            response = self._request_with_retry(requests.patch, url, json=payload)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return len(data.get('items', items))
            return len(items)
        except Exception as e:
            print(f"  ❌ Bulk update error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"     Response: {e.response.text}")
            return 0

    def bulk_publish_items(self, item_ids: List[str]) -> bool:
        """
        Publish multiple items in one API call.
        item_ids: list of item IDs (max 100).
        """
        if not item_ids:
            return True
        try:
            url = f"{self.base_url}/collections/{self.collection_id}/items/publish"
            payload = {"itemIds": item_ids}
            response = self._request_with_retry(requests.post, url, json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get('errors'):
                print(f"  ⚠️  Bulk publish had errors: {data['errors']}")
                return False
            return True
        except Exception as e:
            print(f"  ❌ Bulk publish error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"     Response: {e.response.text}")
            return False


# ══════════════════════════════════════════════════════════════════════════════
# TARGET CATEGORY RESOLUTION
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# WEAPON PARSING
# ══════════════════════════════════════════════════════════════════════════════

class WeaponParser:
    """Parse weapon data from BAR unit Lua files."""
    
    @staticmethod
    def parse_rgb_color(wblock: str) -> Optional[str]:
        """
        Parse rgbcolor field which can be in two formats:
        1. Simple: rgbcolor = "1 0.5 0"
        2. Table: rgbcolor = { [1] = 1, [2] = 0.33, [3] = 0.7 }
        
        Returns hex color string: "#ff8000" or None
        """
        # Try simple format first
        simple_match = re.search(r'rgbcolor\s*=\s*["\']([^"\']+)["\']', wblock, re.IGNORECASE)
        if simple_match:
            rgb_str = simple_match.group(1).strip()
            try:
                parts = rgb_str.split()
                if len(parts) >= 3:
                    r, g, b = float(parts[0]), float(parts[1]), float(parts[2])
                    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
            except:
                pass
        
        # Try table format
        table_match = re.search(r'rgbcolor\s*=\s*\{', wblock, re.IGNORECASE)
        if table_match:
            rgb_block = LuaParser.extract_balanced_braces(wblock, table_match.end() - 1)
            if rgb_block:
                try:
                    # Extract [1], [2], [3] values
                    r_match = re.search(r'\[1\]\s*=\s*([0-9.]+)', rgb_block)
                    g_match = re.search(r'\[2\]\s*=\s*([0-9.]+)', rgb_block)
                    b_match = re.search(r'\[3\]\s*=\s*([0-9.]+)', rgb_block)
                    
                    if r_match and g_match and b_match:
                        r = float(r_match.group(1))
                        g = float(g_match.group(1))
                        b = float(b_match.group(1))
                        return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
                except:
                    pass
        
        return None
    
    @staticmethod
    def parse_damage_block(wblock: str) -> Dict[str, int]:
        """
        Parse damage = { default = X, commanders = Y, vtol = Z, subs = W }
        Returns dict with all damage types.
        """
        damage = {
            'default': 0,
            'commanders': 0,
            'vtol': 0,
            'subs': 0
        }
        
        dmg_match = re.search(r'\bdamage\s*=\s*\{', wblock, re.IGNORECASE)
        if not dmg_match:
            return damage
        
        dmg_block = LuaParser.extract_balanced_braces(wblock, dmg_match.end() - 1)
        if not dmg_block:
            return damage
        
        # Strip Lua comments BEFORE parsing — otherwise commented-out values
        # like --vtol = 400 would still be matched by the regex
        dmg_block = re.sub(r'--[^\n]*', '', dmg_block)
        
        # Extract each damage type
        for dtype in ['default', 'commanders', 'vtol', 'subs']:
            m = re.search(rf'\b{dtype}\s*=\s*([0-9.]+)', dmg_block, re.IGNORECASE)
            if m:
                try:
                    damage[dtype] = int(float(m.group(1)))
                except:
                    pass
        
        return damage
    
    @staticmethod
    def parse_weapondefs(unit_block: str, unit_name: str) -> List[Dict[str, Any]]:
        """
        Parse all weapondefs from a unit file.
        Returns list of weapon dicts with all 31+ fields.
        """
        weapons = []
        
        # Find weapondefs block
        wd_match = re.search(r'\bweapondefs\s*=\s*\{', unit_block, re.IGNORECASE)
        if not wd_match:
            return weapons
        
        wd_block = LuaParser.extract_balanced_braces(unit_block, wd_match.end() - 1)
        if not wd_block:
            return weapons
        
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
        
        def _bool(block, key):
            """Extract boolean value."""
            m = re.search(rf'\b{key}\s*=\s*(true|false)', block, re.IGNORECASE)
            if m:
                return m.group(1).lower() == 'true'
            return False
        
        # Parse each weapondef
        weapondefs_dict = {}
        for wm in re.finditer(r'(\w+)\s*=\s*\{', wd_block):
            weapondef_key = wm.group(1)  # e.g. "armcomlaser"
            wblock = LuaParser.extract_balanced_braces(wd_block, wm.end() - 1)
            if not wblock:
                continue
            # Strip Lua line comments so commented-out values (e.g. -- burst = 3) are ignored
            wblock = re.sub(r'--[^\n]*', '', wblock)
            
            # Parse damage block
            damage = WeaponParser.parse_damage_block(wblock)
            
            # Parse interceptor flag (anti-nuke indicator)
            interceptor = False
            int_match = re.search(r'\binterceptor\s*=\s*([0-9]+)', wblock, re.IGNORECASE)
            if int_match:
                interceptor = int(int_match.group(1)) == 1
            
            # Parse cluster info from weapondef root level (FIRST)
            cluster_number = None
            cluster_def = None
            
            # Try root level
            cn_match_root = re.search(r'\bcluster_number\s*=\s*([0-9]+)', wblock, re.IGNORECASE)
            if cn_match_root:
                cluster_number = int(cn_match_root.group(1))
            
            cd_match_root = re.search(r'\bcluster_def\s*=\s*["\']?(\w+)["\']?', wblock, re.IGNORECASE)
            if cd_match_root:
                cluster_def = cd_match_root.group(1)
            else:
                # Try alternative format: cluster_def = [[name]]
                cd_match_alt = re.search(r'\bcluster_def\s*=\s*\[\[(\w+)\]\]', wblock, re.IGNORECASE)
                if cd_match_alt:
                    cluster_def = cd_match_alt.group(1)
            
            # Lightning spark info - ONLY in customparams (initialize here)
            spark_forkdamage = None
            spark_maxunits = None
            
            # Parse customparams for stockpilelimit, overpenetrate, and cluster info (override if found)
            stockpile_limit = None
            overpenetrate = False
            area_onhit_damage = None
            area_onhit_time = None
            is_bogus = False  # Parse bogus flag
            is_nuclear = False  # Nuclear missile flag (customparams)
            is_juno = False    # Juno Surge flag (customparams)
            # Drone carrier fields (from customparams)
            drone_carried_unit = None
            drone_spawnrate = None
            drone_maxunits = None
            drone_energycost = None
            drone_metalcost = None
            nofire = False  # Parse nofire flag (crush/stomp indicator)
            smart_backup = False  # Parse smart_backup (alternative fire mode)
            sweepfire = 1  # Sweepfire multiplier (default 1 = no sweep)
            wcp_match = re.search(r'\bcustomparams\s*=\s*\{', wblock, re.IGNORECASE)
            if wcp_match:
                wcp_block = LuaParser.extract_balanced_braces(wblock, wcp_match.end() - 1)
                if wcp_block:
                    # Bogus flag
                    if re.search(r'\bbogus\s*=\s*1', wcp_block, re.IGNORECASE):
                        is_bogus = True
                    if re.search(r'\bnuclear\s*=\s*1', wcp_block, re.IGNORECASE):
                        is_nuclear = True
                    if re.search(r'\bjunotype\s*=', wcp_block, re.IGNORECASE):
                        is_juno = True
                    # Drone carrier detection
                    cu_m = re.search(r'\bcarried_unit\s*=\s*["\']([\w]+)["\']', wcp_block, re.IGNORECASE)
                    if cu_m:
                        drone_carried_unit = cu_m.group(1).lower()
                        sr_m = re.search(r'\bspawnrate\s*=\s*([0-9.]+)', wcp_block, re.IGNORECASE)
                        if sr_m: drone_spawnrate = int(float(sr_m.group(1)))
                        mu_m = re.search(r'\bmaxunits\s*=\s*([0-9]+)', wcp_block, re.IGNORECASE)
                        if mu_m: drone_maxunits = int(mu_m.group(1))
                        ec_m = re.search(r'\benergycos[t]\s*=\s*([0-9.]+)', wcp_block, re.IGNORECASE)
                        if ec_m: drone_energycost = int(float(ec_m.group(1)))
                        mc_m = re.search(r'\bmetalcos[t]\s*=\s*([0-9.]+)', wcp_block, re.IGNORECASE)
                        if mc_m: drone_metalcost = int(float(mc_m.group(1)))
                    # Nofire flag (crush/stomp melee indicator)
                    if re.search(r'\bnofire\s*=\s*true', wcp_block, re.IGNORECASE):
                        nofire = True
                    # Smart_backup flag (alternative fire mode - don't count in unit DPS)
                    if re.search(r'\bsmart_backup\s*=\s*true', wcp_block, re.IGNORECASE):
                        smart_backup = True
                    # Stockpile limit
                    sl_match = re.search(r'\bstockpilelimit\s*=\s*([0-9]+)', wcp_block, re.IGNORECASE)
                    if sl_match:
                        stockpile_limit = int(sl_match.group(1))
                    # Overpenetrate (railgun indicator)
                    op_match = re.search(r'\boverpenetrate\s*=\s*(true|false)', wcp_block, re.IGNORECASE)
                    if op_match:
                        overpenetrate = op_match.group(1).lower() == 'true'
                    # Area onhit damage (napalm indicator)
                    aod_match = re.search(r'\barea_onhit_damage\s*=\s*([0-9.]+)', wcp_block, re.IGNORECASE)
                    if aod_match:
                        area_onhit_damage = float(aod_match.group(1))
                    # Area onhit time (napalm duration)
                    aot_match = re.search(r'\barea_onhit_time\s*=\s*([0-9.]+)', wcp_block, re.IGNORECASE)
                    if aot_match:
                        area_onhit_time = float(aot_match.group(1))
                    # Lightning spark info (check customparams too)
                    if not spark_forkdamage:
                        sf_match_cp = re.search(r'\bspark_forkdamage\s*=\s*["\']?([0-9.]+)["\']?', wcp_block, re.IGNORECASE)
                        if sf_match_cp:
                            spark_forkdamage = float(sf_match_cp.group(1))
                    if not spark_maxunits:
                        sm_match_cp = re.search(r'\bspark_maxunits\s*=\s*["\']?([0-9]+)["\']?', wcp_block, re.IGNORECASE)
                        if sm_match_cp:
                            spark_maxunits = int(sm_match_cp.group(1))
                    # Cluster number (cluster plasma indicator) - check customparams too
                    cn_match = re.search(r'\bcluster_number\s*=\s*([0-9]+)', wcp_block, re.IGNORECASE)
                    if cn_match and not cluster_number:
                        cluster_number = int(cn_match.group(1))
                    
                    # Sweepfire multiplier (weapon fires N beams simultaneously)
                    sw_match = re.search(r'\bsweepfire\s*=\s*([0-9.]+)', wcp_block, re.IGNORECASE)
                    if sw_match:
                        sweepfire = float(sw_match.group(1))

                    # Cluster def (name of child weapondef)
                    cd_match = re.search(r'\bcluster_def\s*=\s*["\']?(\w+)["\']?', wcp_block, re.IGNORECASE)
                    if cd_match and not cluster_def:
                        cluster_def = cd_match.group(1)
                    
                    # Try alternative format in customparams too
                    if not cluster_def:
                        cd_match_alt = re.search(r'\bcluster_def\s*=\s*\[\[(\w+)\]\]', wcp_block, re.IGNORECASE)
                        if cd_match_alt:
                            cluster_def = cd_match_alt.group(1)
            
            # Build weapon data dict with all fields
            weapon_data = {
                'weapondef_key': weapondef_key,  # Internal key for tracking
                'name': f"{unit_name}-{weapondef_key}",  # armcom-armcomlaser
                'full_name': _val(wblock, 'name') or weapondef_key,  # "Laser"
                'weapon_type': _val(wblock, 'weapontype') or 'Unknown',
                
                # Special flags for category detection
                '_overpenetrate': overpenetrate,  # Railgun indicator
                '_interceptor': interceptor,  # Anti-nuke indicator
                '_cluster_number': cluster_number,  # Cluster plasma indicator
                '_cluster_def': cluster_def,  # Name of cluster child weapondef
                '_area_onhit_damage': area_onhit_damage,  # Napalm DOT damage
                '_area_onhit_time': area_onhit_time,  # Napalm DOT duration
                '_spark_forkdamage': spark_forkdamage,  # Lightning chain damage multiplier
                '_spark_maxunits': spark_maxunits,  # Lightning chain max targets
                '_is_bogus': is_bogus,  # Bogus/dummy weapon flag
                '_is_nuclear': is_nuclear,  # Nuclear missile flag
                '_is_juno': is_juno,        # Juno Surge flag
                '_sweepfire': sweepfire,  # Sweepfire beam multiplier
                # Drone carrier
                '_drone_carried_unit': drone_carried_unit,
                '_drone_spawnrate':    drone_spawnrate,
                '_drone_maxunits':     drone_maxunits,
                '_drone_energycost':   drone_energycost,
                '_drone_metalcost':    drone_metalcost,
                '_nofire': nofire,  # Nofire flag (crush/stomp melee indicator)
                '_smart_backup': smart_backup,  # Smart backup (alternative fire mode)
                
                # Damage stats
                'dps': 0,  # Calculated later
                'damage_default': damage['default'],
                'damage_commanders': damage['commanders'],
                'damage_vtol': damage['vtol'],
                'damage_subs': damage['subs'],
                
                # Target capabilities (detected from damage types)
                # Target capabilities: set to False here, resolved later from
                # onlytargetcategory in the weapons = {} block (see below)
                'can_target_surface': False,
                'can_target_air': False,
                'can_target_subs': False,
                
                # Weapon stats
                'reload_time': round(_val(wblock, 'reloadtime', float) or 0, 5),
                'range': int(_val(wblock, 'range', float) or 0),
                'accuracy': int(_val(wblock, 'accuracy', float) or _val(wblock, 'movingaccuracy', float) or 0),
                'area_of_effect': int(_val(wblock, 'areaofeffect', float) or 0),
                'edge_effectiveness': round(_val(wblock, 'edgeeffectiveness', float) or 1.0, 2),
                'impulse': round(_val(wblock, 'impulsefactor', float) or 0, 2),
                
                # Projectile stats
                'projectiles': int(_val(wblock, 'projectiles', float) or 1),
                'velocity': int(_val(wblock, 'weaponvelocity', float) or 0),
                'burst': int(_val(wblock, 'burst', float) or 1),
                'burst_rate': round(_val(wblock, 'burstrate', float) or 0, 5),
                
                # Cost stats
                'energy_per_shot': int(_val(wblock, 'energypershot', float) or 0),
                'metal_per_shot': int(_val(wblock, 'metalpershot', float) or 0),
                
                # Stockpile
                'stockpile': _bool(wblock, 'stockpile'),
                'stockpile_limit': stockpile_limit,
                'stockpile_time': int(_val(wblock, 'stockpiletime', float) or 0),
                
                # Special properties
                'impact_only': _bool(wblock, 'impactonly'),
                'commandfire': _bool(wblock, 'commandfire'),
                'paralyzer': _bool(wblock, 'paralyzer'),
                'paralyze_duration': int(_val(wblock, 'paralyzetime', float) or 0),
                'homing': _bool(wblock, 'tracks'),
                'turn_rate': int(_val(wblock, 'turnrate', float) or 0),
                'water_weapon': _bool(wblock, 'waterweapon'),
                'beamtime': round(_val(wblock, 'beamtime', float) or 0, 5),
                'large_beam_laser': _bool(wblock, 'largebeamlaser'),
                
                # Shield properties (for weapontype = Shield)
                # Parse from shield = { } sub-block
                'shield_power': 0,
                'shield_power_regen': 0,
                'shield_power_regen_energy': 0,
                'shield_radius': 0,
                
                # Visual
                'color': WeaponParser.parse_rgb_color(wblock) or '#ffffff',
                
                # For DPS calculation
                '_salvosize': int(_val(wblock, 'salvosize', float) or 1),

                # Sound (fallback: soundhitdry if no soundstart)
                'sound_start': _val(wblock, 'soundstart') or _val(wblock, 'soundhitdry') or None,

                # Velocity
                'start_velocity': int(_val(wblock, 'startvelocity', float) or 0),
                'weapon_acceleration': int(_val(wblock, 'weaponacceleration', float) or 0),
            }
            
            # Parse shield = { } sub-block if weapontype is Shield
            if weapon_data['weapon_type'] == 'Shield':
                shield_match = re.search(r'\bshield\s*=\s*\{', wblock, re.IGNORECASE)
                if shield_match:
                    shield_block = LuaParser.extract_balanced_braces(wblock, shield_match.end() - 1)
                    if shield_block:
                        # Parse power, powerregen, powerregenenergy, radius
                        power_match = re.search(r'\bpower\s*=\s*([0-9.]+)', shield_block, re.IGNORECASE)
                        if power_match:
                            weapon_data['shield_power'] = int(float(power_match.group(1)))
                        
                        regen_match = re.search(r'\bpowerregen\s*=\s*([0-9.]+)', shield_block, re.IGNORECASE)
                        if regen_match:
                            weapon_data['shield_power_regen'] = int(float(regen_match.group(1)))
                        
                        regen_energy_match = re.search(r'\bpowerregenenergy\s*=\s*([0-9.]+)', shield_block, re.IGNORECASE)
                        if regen_energy_match:
                            weapon_data['shield_power_regen_energy'] = int(float(regen_energy_match.group(1)))
                        
                        radius_match = re.search(r'\bradius\s*=\s*([0-9.]+)', shield_block, re.IGNORECASE)
                        if radius_match:
                            weapon_data['shield_radius'] = int(float(radius_match.group(1)))
            
            # Apply weapontype override if configured
            override_key = (unit_name, weapondef_key)
            if override_key in WEAPONTYPE_OVERRIDES:
                weapon_data['weapon_type'] = WEAPONTYPE_OVERRIDES[override_key]

            weapondefs_dict[weapondef_key.upper()] = weapon_data

        # Sound fallback: if a weapon has no sound, borrow from a sibling weapondef
        # (e.g. legphoenix skybeam has no sound, but legphsound has soundhitdry)
        all_sounds = [wd['sound_start'] for wd in weapondefs_dict.values() if wd.get('sound_start')]
        if all_sounds:
            fallback_sound = all_sounds[0]
            for wd in weapondefs_dict.values():
                if not wd.get('sound_start'):
                    wd['sound_start'] = fallback_sound

        # Now parse weapons = {} to find which weapondefs are actually used and count them
        # Also read onlytargetcategory per weapon entry
        weapon_counts = {}
        weapon_onlytarget = {}  # weapondef_name -> onlytargetcategory string
        w_match = re.search(r'\bweapons\s*=\s*\{', unit_block, re.IGNORECASE)
        if w_match:
            w_block = LuaParser.extract_balanced_braces(unit_block, w_match.end() - 1)
            if w_block:
                # Parse each numbered weapon entry [1] = { ... }
                for entry_match in re.finditer(r'\[\d+\]\s*=\s*\{', w_block):
                    entry_block = LuaParser.extract_balanced_braces(w_block, entry_match.end() - 1)
                    if not entry_block:
                        continue
                    def_m = re.search(r'\bdef\s*=\s*["\']?(\w+)["\']?', entry_block, re.IGNORECASE)
                    if not def_m:
                        continue
                    weapondef_name = def_m.group(1).upper()
                    weapon_counts[weapondef_name] = weapon_counts.get(weapondef_name, 0) + 1
                    # Read onlytargetcategory for this entry
                    otc_m = re.search(r'\bonlytargetcategory\s*=\s*["\']?(\w+)["\']?', entry_block, re.IGNORECASE)
                    if otc_m:
                        weapon_onlytarget[weapondef_name] = otc_m.group(1).upper()
                    else:
                        weapon_onlytarget[weapondef_name] = None
        
        # Build final weapons list with counts and DPS
        for weapondef_key, count in weapon_counts.items():
            if weapondef_key not in weapondefs_dict:
                continue
            
            weapon = weapondefs_dict[weapondef_key].copy()
            weapon['weapon_count'] = count
            
            # Resolve target capabilities from onlytargetcategory
            # Bogus/dummy weapons get everything False regardless
            is_bogus = (
                weapon.get('_is_bogus', False) or
                'bogus' in weapondef_key.lower() or
                'dummy' in weapon.get('full_name', '').lower()
            )
            has_damage = weapon.get('damage_default', 0) > 0 or weapon.get('damage_vtol', 0) > 0 or weapon.get('damage_subs', 0) > 0
            
            if is_bogus or not has_damage:
                # No real weapon - all targets False
                targets = {'can_target_surface': False, 'can_target_air': False, 'can_target_subs': False}
            else:
                otc = weapon_onlytarget.get(weapondef_key)
                targets = resolve_target_categories(otc)
            
            weapon.update(targets)
            
            # Apply sweepfire multiplier to damage_default (display value in Webflow).
            # Only for pulsed beams (reload > beamtime); continuous beams sweep
            # visually but don't multiply single-target DPS.
            if weapon.get('_sweepfire', 1) > 1 and weapon['reload_time'] > weapon.get('beamtime', 0):
                weapon['damage_default'] = weapon['damage_default'] * weapon['_sweepfire']

            # Skip DPS for Anti-Nuke weapons — they intercept, not damage
            if weapon.get('_interceptor', False):
                weapon['dps'] = 0
                weapon['dot'] = 0
                weapon['pps'] = 0
                # Continue to include weapon (damage shown, but DPS = 0)

            # Skip DPS/DOT/PPS calculation for Shield weapons
            # Shields have no damage - they only provide protection
            elif weapon.get('weapon_type') == 'Shield':
                weapon['dps'] = 0
                weapon['dot'] = 0
                weapon['pps'] = 0
                # Continue to include weapon in list (shields are valid weapons)

            # Skip DPS/DOT calculation for paralyzer weapons (EMP)
            # They do paralysis, not real damage
            elif weapon.get('paralyzer', False):
                weapon['dps'] = 0
                weapon['dot'] = 0
                
                # Calculate PPS (Paralyse Per Second) for paralyzer weapons
                # PPS = damage / reload (how much paralysis damage per second)
                dmg = max(weapon['damage_vtol'], weapon['damage_default'])
                if dmg > 0 and weapon['reload_time'] > 0:
                    pps = (dmg * (1.0 / weapon['reload_time'])) * weapon['_salvosize'] * weapon['burst'] * weapon['projectiles']
                    weapon['pps'] = int(round(pps))
                else:
                    weapon['pps'] = 0
                
                # Continue to include weapon in list (but with 0 DPS, non-zero PPS)
            else:
                # Calculate DPS (only main projectile damage)
                dmg = max(weapon['damage_vtol'], weapon['damage_default'])
                dot_dps = 0  # Damage Over Time from cluster/napalm/lightning
                
                # Check if this is a cluster weapon
                if weapon.get('_cluster_number') and weapon.get('_cluster_def'):
                    cluster_num = weapon['_cluster_number']
                    cluster_def_key = weapon['_cluster_def'].upper()
                    
                    # Try to find cluster def weapondef (not in weapons array!)
                    if cluster_def_key in weapondefs_dict:
                        cluster_def_data = weapondefs_dict[cluster_def_key]
                        cluster_dmg = cluster_def_data.get('damage_default', 0)
                        
                        # Cluster DOT = (cluster_number × cluster_damage) / reload
                        # This is the extra damage from cluster projectiles AFTER main impact
                        cluster_total = cluster_num * cluster_dmg
                        dot_dps = (cluster_total / weapon['reload_time']) * weapon['_salvosize'] * weapon['burst'] * weapon['projectiles']
                        
                        # Store cluster info
                        weapon['_has_cluster'] = True
                        weapon['_cluster_child_damage'] = cluster_dmg
                    else:
                        weapon['_has_cluster'] = False
                
                # Check for napalm DOT
                if weapon.get('_area_onhit_damage') and weapon.get('_area_onhit_time'):
                    # Napalm DOT = (area_damage × area_time) / reload
                    # This is the extra damage from burning AFTER main impact
                    napalm_total = weapon['_area_onhit_damage'] * weapon['_area_onhit_time']
                    napalm_dot = (napalm_total / weapon['reload_time']) * weapon['_salvosize'] * weapon['burst'] * weapon['projectiles']
                    
                    # Add to DOT (could have both cluster AND napalm theoretically)
                    dot_dps += napalm_dot
                    weapon['_has_napalm'] = True
                
                # Check for lightning chain damage
                if weapon.get('_spark_forkdamage') and weapon.get('_spark_maxunits'):
                    # Lightning DOT = (default_damage × burst × spark_forkdamage × spark_maxunits) / reload
                    # This is the chain lightning damage to secondary targets
                    base_dmg = weapon['damage_default']
                    fork_dmg_per_target = base_dmg * weapon['burst'] * weapon['_spark_forkdamage']
                    total_fork_dmg = fork_dmg_per_target * weapon['_spark_maxunits']
                    lightning_dot = (total_fork_dmg / weapon['reload_time']) * weapon['_salvosize'] * weapon['projectiles']
                    
                    # Add to DOT
                    dot_dps += lightning_dot
                    weapon['_has_lightning_chain'] = True
                
                # Kamikaze weapons: DPS = full alpha damage (unit dies on firing)
                wep_unit = weapon['name'].split('-')[0] if '-' in weapon['name'] else ''
                is_kamikaze = (wep_unit in KAMIKAZE_WEAPONS and
                               weapon['weapondef_key'] == KAMIKAZE_WEAPONS[wep_unit])
                if is_kamikaze and dmg > 0:
                    weapon['dps'] = dmg
                    weapon['dot'] = 0
                    weapon['pps'] = 0
                # Calculate main projectile DPS (without DOT)
                elif dmg > 0 and weapon['reload_time'] > 0:
                    main_dps = (dmg * (1.0 / weapon['reload_time'])) * weapon['_salvosize'] * weapon['burst'] * weapon['projectiles']
                    weapon['dps'] = int(round(main_dps))
                    weapon['dot'] = int(round(dot_dps)) if dot_dps > 0 else 0
                    weapon['pps'] = 0  # Non-paralyzer weapons have 0 PPS
                else:
                    weapon['dps'] = 0
                    weapon['dot'] = 0
                    weapon['pps'] = 0
            
            # Skip weapons with no damage at all (default, vtol, subs, commanders all zero)
            # EXCEPTION: Shield weapons (weapontype = Shield) have no damage but are valid
            total_damage = weapon['damage_default'] + weapon['damage_vtol'] + weapon['damage_subs'] + weapon['damage_commanders']
            is_shield = weapon['weapon_type'] == 'Shield'
            
            is_drone_carrier = weapon.get('_drone_carried_unit') is not None
            if total_damage <= 0 and not is_shield and not is_drone_carrier:
                print(f"  ⏭️  Skipping {weapon['weapondef_key']}: no damage (total=0)")
                continue
            
            # Skip smart_backup weapons (alternative fire modes)
            # These are just alternative modes of the main weapon
            if weapon.get('_smart_backup', False):
                print(f"  ⏭️  Skipping {weapon['weapondef_key']}: smart_backup=true")
                continue
            
            # Check if this is a crush/stomp weapon BEFORE filtering bogus
            # Crush/stomp: Cannon + range < 60 + nofire = true
            is_crush_stomp = (weapon['weapon_type'] == 'Cannon' and 
                            weapon['range'] < 60 and 
                            weapon.get('_nofire', False))
            
            # Skip if bogus flag is set OR weapon name contains 'bogus' or 'mine'
            # EXCEPTION: Don't skip crush/stomp weapons even if bogus
            if not is_crush_stomp:
                if weapon.get('_is_bogus', False):
                    print(f"  ⏭️  Skipping {weapon['weapondef_key']}: bogus=1 (not crush/stomp)")
                    continue
                if 'bogus' in weapon['weapondef_key'].lower() or 'mine' in weapon['weapondef_key'].lower():
                    print(f"  ⏭️  Skipping {weapon['weapondef_key']}: 'bogus' or 'mine' in name")
                    continue
            
            weapons.append(weapon)
        
        return weapons


# ══════════════════════════════════════════════════════════════════════════════
# WEAPON CATEGORY DETECTION
# ══════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def parse_mine_weapondef(file_content: str, unit_name: str, explodeas: str) -> Optional[Dict]:
        """
        Parse a mine explosion weapondef from a standalone weapons/xxx.lua file.
        
        These files have the weapondef at root level (not inside weapondefs = {}):
            WeaponDefs = {
                mine_light = {
                    weapontype = "Cannon",
                    ...
                }
            }
        or sometimes just:
            mine_light = {
                weapontype = "Cannon",
                ...
            }
        
        Returns a weapon dict compatible with the normal weapon format.
        """
        if not file_content:
            return None

        # Strip Lua comments before parsing
        content = re.sub(r'--[^\n]*', '', file_content)

        weapondef_key = explodeas.lower()

        def _val(block, key, cast=str):
            m = re.search(rf'\b{key}\s*=\s*([^\n,}}]+)', block, re.IGNORECASE)
            if not m:
                return None
            v = m.group(1).strip().rstrip(',').strip('"\'')
            try:
                return cast(v)
            except (ValueError, TypeError):
                return None

        def _bool(block, key):
            m = re.search(rf'\b{key}\s*=\s*(true|false|1|0)\b', block, re.IGNORECASE)
            if m:
                v = m.group(1).lower()
                return v in ('true', '1')
            return False

        # Find the weapondef block — try WeaponDefs = { name = { ... } } first
        wblock = None
        wd_match = re.search(r'\bWeaponDefs\s*=\s*\{', content, re.IGNORECASE)
        if wd_match:
            wd_outer = LuaParser.extract_balanced_braces(content, wd_match.end() - 1)
            if wd_outer:
                # Find the named weapondef inside
                inner_match = re.search(rf'\b{re.escape(weapondef_key)}\s*=\s*\{{', wd_outer, re.IGNORECASE)
                if inner_match:
                    wblock = LuaParser.extract_balanced_braces(wd_outer, inner_match.end() - 1)

        # Fallback: try root-level named block
        if not wblock:
            root_match = re.search(rf'\b{re.escape(weapondef_key)}\s*=\s*\{{', content, re.IGNORECASE)
            if root_match:
                wblock = LuaParser.extract_balanced_braces(content, root_match.end() - 1)

        if not wblock:
            print(f"  ⚠️  Could not find weapondef block '{weapondef_key}' in mine weapon file")
            return None

        # Strip Lua line comments so commented-out values are ignored
        wblock = re.sub(r'--[^\n]*', '', wblock)

        # Parse damage block
        damage = WeaponParser.parse_damage_block(wblock)

        # Parse customparams (bogus, nofire, etc.)
        is_bogus = False
        wcp_match = re.search(r'\bcustomparams\s*=\s*\{', wblock, re.IGNORECASE)
        if wcp_match:
            wcp_block = LuaParser.extract_balanced_braces(wblock, wcp_match.end() - 1)
            if wcp_block and re.search(r'\bbogus\s*=\s*1', wcp_block, re.IGNORECASE):
                is_bogus = True

        # Build weapon dict — same structure as normal weapons
        weapon_data = {
            'weapondef_key':    weapondef_key,
            'name':             f"{unit_name}-{weapondef_key}",
            'full_name':        _val(wblock, 'name') or weapondef_key,
            'weapon_type':      _val(wblock, 'weapontype') or 'Unknown',

            # Special flags
            '_overpenetrate':   False,
            '_interceptor':     False,
            '_cluster_number':  None,
            '_cluster_def':     None,
            '_area_onhit_damage': None,
            '_area_onhit_time': None,
            '_spark_forkdamage': None,
            '_spark_maxunits':  None,
            '_is_bogus':        is_bogus,
            '_nofire':          False,
            '_smart_backup':    False,

            # Damage
            'dps':              0,
            'dot':              0,
            'pps':              0,
            'damage_default':   damage['default'],
            'damage_commanders': damage['commanders'],
            'damage_vtol':      damage['vtol'],
            'damage_subs':      damage['subs'],

            # Target capabilities — resolved below from onlytargetcategory
            'can_target_surface': False,
            'can_target_air':   False,
            'can_target_subs':  False,

            # Weapon stats
            'reload_time':      round(_val(wblock, 'reloadtime', float) or 0, 5),
            'range':            int(_val(wblock, 'range', float) or 0),
            'accuracy':         int(_val(wblock, 'accuracy', float) or 0),
            'area_of_effect':   int(_val(wblock, 'areaofeffect', float) or 0),
            'edge_effectiveness': round(_val(wblock, 'edgeeffectiveness', float) or 1.0, 2),
            'impulse':          round(_val(wblock, 'impulsefactor', float) or 0, 2),

            # Projectile stats
            'projectiles':      int(_val(wblock, 'projectiles', float) or 1),
            'velocity':         int(_val(wblock, 'weaponvelocity', float) or 0),
            'burst':            int(_val(wblock, 'burst', float) or 1),
            'burst_rate':       round(_val(wblock, 'burstrate', float) or 0, 5),

            # Cost
            'energy_per_shot':  int(_val(wblock, 'energypershot', float) or 0),
            'metal_per_shot':   int(_val(wblock, 'metalpershot', float) or 0),

            # Stockpile
            'stockpile':        _bool(wblock, 'stockpile'),
            'stockpile_limit':  None,
            'stockpile_time':   int(_val(wblock, 'stockpiletime', float) or 0),

            # Special properties
            'impact_only':      _bool(wblock, 'impactonly'),
            'commandfire':      _bool(wblock, 'commandfire'),
            'paralyzer':        _bool(wblock, 'paralyzer'),
            'paralyze_duration': int(_val(wblock, 'paralyzetime', float) or 0),
            'homing':           _bool(wblock, 'tracks'),
            'turn_rate':        int(_val(wblock, 'turnrate', float) or 0),
            'water_weapon':     _bool(wblock, 'waterweapon'),
            'beamtime':         round(_val(wblock, 'beamtime', float) or 0, 5),
            'large_beam_laser': _bool(wblock, 'largebeamlaser'),

            # Shield (mines won't have this but keep structure consistent)
            'shield_power':     0,
            'shield_power_regen': 0,
            'shield_power_regen_energy': 0,
            'shield_radius':    0,

            # Visual
            'color':            WeaponParser.parse_rgb_color(wblock) or '#ffffff',

            # DPS calculation helper
            '_salvosize':       int(_val(wblock, 'salvosize', float) or 1),

            # Mine weapon has count 1
            'weapon_count':     1,
            '_is_mine':         True,   # Mark as mine explosion weapon

            # Sound
            'sound_start':      _val(wblock, 'soundstart') or None,

            # Velocity
            'start_velocity':   int(_val(wblock, 'startvelocity', float) or 0),
            'weapon_acceleration': int(_val(wblock, 'weaponacceleration', float) or 0),
        }

        # Resolve onlytargetcategory — mine weapons don't have a weapons = {} block,
        # so we read it directly from the weapondef block itself
        otc_m = re.search(r'\bonlytargetcategory\s*=\s*["\']?(\w+)["\']?', wblock, re.IGNORECASE)
        otc = otc_m.group(1).upper() if otc_m else None
        targets = resolve_target_categories(otc)
        weapon_data.update(targets)

        # Calculate DPS or PPS
        has_damage = damage['default'] > 0 or damage['vtol'] > 0 or damage['subs'] > 0
        if has_damage and not is_bogus:
            reload = weapon_data['reload_time'] or 1.0
            dmg = damage['vtol'] if damage['vtol'] > damage['default'] else damage['default']
            if dmg > 0:
                value = round(
                    (dmg * (1.0 / reload)) * weapon_data['_salvosize'] * weapon_data['burst'] * weapon_data['projectiles'],
                    1
                )
                if weapon_data['paralyzer']:
                    weapon_data['pps'] = value
                else:
                    weapon_data['dps'] = value

        stat_label = f"PPS={weapon_data['pps']}" if weapon_data['paralyzer'] else f"DPS={weapon_data['dps']}"
        print(f"  💣 Mine weapon: {weapon_data['full_name']} | type={weapon_data['weapon_type']} | {stat_label} | AOE={weapon_data['area_of_effect']}")
        return weapon_data
    
class WeaponCategoryDetector:
    """Detect weapon category based on weapon stats."""
    
    def __init__(self, category_map: Dict[str, str]):
        """
        category_map: {category_slug: webflow_item_id}
        e.g. {'beam': '123abc', 'missile': '456def'}
        """
        self.category_map = category_map
    
    def detect_category(self, weapon: Dict) -> Optional[str]:
        """
        Detect weapon category and return Webflow category ID.
        
        Rules (expandable):
        - BeamLaser + reload < 0.1 → beam-laser (continuous effect)
        - BeamLaser → beam-laser
        - MissileLauncher/StarburstLauncher → missile-launcher or vertical-rocket-launcher
        - Cannon → cannon
        - etc.
        """
        wtype = weapon.get('weapon_type', '')
        reload = weapon.get('reload_time', 0)
        is_water_weapon = weapon.get('water_weapon', False)
        weapondef_key = weapon.get('weapondef_key', '').lower()
        full_name = weapon.get('full_name', '').lower()
        
        # Explicit category overrides (CHECK FIRST - absolute priority)
        weapon_name = weapon.get('name', '')  # e.g. "armmship-plasmacannon"
        unit_name = weapon_name.split('-')[0] if '-' in weapon_name else ''
        override_key = (unit_name, weapondef_key)
        if override_key in CATEGORY_OVERRIDES:
            return self.category_map.get(CATEGORY_OVERRIDES[override_key])

        # Drone Carrier
        if weapon.get('_drone_carried_unit'):
            return self.category_map.get('drone-controller')

        # Mine / Trigger Explosive or Trigger EMP (CHECK FIRST - absolute priority)
        # These are explosion weapons from external weapons/ files
        if weapon.get('_is_mine', False):
            if weapon.get('paralyzer', False):
                return self.category_map.get('trigger-emp')
            return self.category_map.get('trigger-explosive')
        
        # Juno Surge (customparams.junotype present — any weapon type)
        if weapon.get('_is_juno', False):
            return self.category_map.get('juno-surge')

        # Anti-Nuke detection (CHECK FIRST - highest priority)
        # Multiple indicators: interceptor flag, name indicators
        is_antinuke = False
        if weapon.get('_interceptor', False):
            is_antinuke = True
        if 'interceptor' in full_name or 'intercepting' in full_name:
            is_antinuke = True
        
        if is_antinuke:
            return self.category_map.get('anti-nuke')
        
        # Crush / Stomp detection (CHECK EARLY - very specific melee)
        # Criteria: weapontype=Cannon + range < 60 + nofire=true in customparams
        is_crush = False
        if (wtype == 'Cannon' and 
            weapon.get('range', 0) < 60 and 
            weapon.get('_nofire', False)):
            is_crush = True
        
        # Debug for potential crush weapons
        if wtype == 'Cannon' and weapon.get('range', 0) < 100:
            print(f"  🔍 DEBUG Potential crush weapon: {weapondef_key}")
            print(f"     weapontype: {wtype}")
            print(f"     range: {weapon.get('range', 0)}")
            print(f"     _nofire: {weapon.get('_nofire', False)}")
            print(f"     is_crush: {is_crush}")
        
        if is_crush:
            return self.category_map.get('crush-stomp')
        
        # Napalm Launcher detection (CHECK BEFORE CLUSTER)
        # Indicators: weapontype Cannon + area_onhit_damage + area_onhit_time
        is_napalm = False
        if weapon.get('_area_onhit_damage') and weapon.get('_area_onhit_time'):
            is_napalm = True
        
        if is_napalm and wtype == 'Cannon':
            return self.category_map.get('napalm-launcher')
        
        # Cluster Plasma detection (AFTER NAPALM)
        # Primary indicator: cluster_number in customparams (ONLY reliable check)
        is_cluster = False
        if weapon.get('_cluster_number'):
            is_cluster = True
        
        if is_cluster:
            return self.category_map.get('cluster-plasma-cannon')
        
        # Sniper detection (CHECK BEFORE GENERAL WEAPON TYPES)
        # Indicators: impactonly=true + weaponvelocity > 2500 + damage_default >= 2000
        # Weapontype MUST be Cannon or LaserCannon
        # Name "sniper"/"snipe" in weapondef key or full_name is an extra signal
        is_sniper = False
        weapon_type = weapon.get('weapon_type', '')
        if (weapon.get('impact_only', False) and 
            weapon.get('velocity', 0) > 2500 and 
            weapon.get('damage_default', 0) >= 2000 and
            weapon_type in ['Cannon', 'LaserCannon']):
            is_sniper = True

        if is_sniper:
            return self.category_map.get('sniper')

        # Railgun detection (MUST CHECK FIRST before LaserCannon)
        # Multiple indicators: weapondef name, overpenetrate, LaserCannon type
        is_railgun = False
        if 'rail' in weapondef_key or 'railgun' in weapondef_key or 'rail_accelerator' in weapondef_key:
            is_railgun = True
        # Also check if it has overpenetrate flag (stored in weapon data)
        if weapon.get('_overpenetrate', False):
            is_railgun = True
        
        if is_railgun and wtype == 'LaserCannon':
            return self.category_map.get('railgun')
        
        # Thermal Ordnance Generator (e.g. corkorg "Eradicator Heat Ray")
        # Must check BEFORE heat-ray since the name also contains "heat ray"
        if 'eradicator heat ray' in full_name.lower():
            return self.category_map.get('thermal-ordnance-generator')

        # Heat Ray detection (MUST CHECK BEFORE general BeamLaser)
        # Multiple indicators: weapondef name, full name, BeamLaser type
        is_heatray = False
        if 'heatray' in weapondef_key or 'heat_ray' in weapondef_key:
            is_heatray = True
        if 'heat ray' in full_name or 'heatray' in full_name:
            is_heatray = True
        
        if is_heatray and wtype == 'BeamLaser':
            return self.category_map.get('heat-ray')
        
        # Sea Laser (LaserCannon + waterweapon)
        if wtype == 'LaserCannon' and is_water_weapon:
            return self.category_map.get('sea-laser-cannon')
        
        # Tachyon Laser (CHECK BEFORE general BeamLaser)
        # Criteria: BeamLaser + largebeamlaser = true + beamtime >= 0.3
        if (wtype == 'BeamLaser' and
                weapon.get('large_beam_laser', False) and
                weapon.get('beamtime', 0) >= 0.3):
            return self.category_map.get('tachyon-laser-beam')
        
        # BeamLaser (both continuous and regular go to beam-laser)
        # This must come AFTER heat ray and tachyon laser checks!
        if wtype == 'BeamLaser':
            return self.category_map.get('beam-laser')
        
        # Missiles: homing (tracks=true) → missile-launcher, non-homing → rocket-launcher
        if wtype == 'MissileLauncher':
            if weapon.get('homing', False):
                return self.category_map.get('missile-launcher')
            else:
                return self.category_map.get('rocket-launcher')
        
        # Nuclear Missile / Tactical Missile (CHECK BEFORE general StarburstLauncher)
        # Criteria: StarburstLauncher + targetable=1 + commandfire=true
        # Then split by damage: >= 8000 → nuclear-missile, < 8000 → tactical-missile
        if (wtype == 'StarburstLauncher' and
                weapon.get('_is_nuclear', False) and
                weapon.get('commandfire', False)):
            if weapon.get('damage_default', 0) >= 8000:
                return self.category_map.get('nuclear-missile')
            else:
                return self.category_map.get('tactical-missile')

        # Vertical Rocket Launcher (StarburstLauncher - general)
        # This comes AFTER anti-nuke and nuclear missile checks!
        if wtype == 'StarburstLauncher':
            return self.category_map.get('vertical-rocket-launcher')
        
        # Flak Cannon (CHECK BEFORE general Cannon and Plasma Repeater)
        # Primary: Cannon + VTOL-only target + flak color (#ff54b2 = rgbcolor {1, 0.33, 0.7})
        # Fallback: 'flak' in weapon name
        # Supporting signal: aoe between 35-200
        is_flak = False
        if wtype == 'Cannon' and weapon.get('can_target_air', False):
            flak_color = weapon.get('color', '').lower()
            aoe = weapon.get('area_of_effect', 0)
            has_flak_color = (flak_color == '#ff54b2')
            has_flak_name  = ('flak' in full_name or 'flak' in weapondef_key)
            has_flak_aoe   = (35 <= aoe <= 200)
            # Match if color+aoe, or name alone is enough as fallback
            if (has_flak_color and has_flak_aoe) or has_flak_name:
                is_flak = True
        
        if is_flak:
            return self.category_map.get('flak-cannon')
        
        # Plasma Repeater (CHECK BEFORE general Cannon)
        # Criteria: Cannon + burst >= 3 + reloadtime <= 0.7
        if (wtype == 'Cannon' and
                weapon.get('burst', 1) >= 3 and
                weapon.get('reload_time', 999) <= 0.7):
            return self.category_map.get('plasma-repeater')
        
        # Plasma Shotgun (Cannon with 3+ projectiles)
        if wtype == 'Cannon' and weapon.get('projectiles', 1) >= 3:
            return self.category_map.get('plasma-shotgun')

        # Plasma Blast (Cannon with impulsefactor >= 0.5)
        if wtype == 'Cannon' and weapon.get('impulse', 0) >= 0.5:
            return self.category_map.get('plasma-blast')

        # Cannons
        if wtype == 'Cannon':
            return self.category_map.get('cannon')
        
        # EMG (if it exists as category)
        if wtype == 'EmgCannon':
            return self.category_map.get('emg-cannon')
        
        # Plasma
        if wtype == 'Plasma':
            return self.category_map.get('plasma')
        
        # Flamethrower
        if wtype == 'Flame':
            return self.category_map.get('flamethrower')
        
        # Gatling Gun (CHECK BEFORE general LaserCannon)
        # Criteria: LaserCannon + reloadtime < 0.5 + burst >= 3
        if (wtype == 'LaserCannon' and
                weapon.get('reload_time', 999) < 0.5 and
                weapon.get('burst', 1) >= 3):
            return self.category_map.get('gatling-gun')
        
        # Shotgun Cannon (LaserCannon with 3+ projectiles)
        if wtype == 'LaserCannon' and weapon.get('projectiles', 1) >= 3:
            return self.category_map.get('shotgun-cannon')

        # LaserCannon (regular - after railgun, sea laser and gatling gun checks)
        if wtype == 'LaserCannon':
            return self.category_map.get('laser-cannon')
        
        # LightningCannon
        if wtype == 'LightningCannon':
            return self.category_map.get('lightning-cannon')
        
        # TorpedoLauncher — homing → torpedo-launcher, dumb-fire → dumb-fire-torpedo-launcher
        if wtype == 'TorpedoLauncher':
            if weapon.get('homing', False):
                return self.category_map.get('torpedo-launcher')
            else:
                return self.category_map.get('dumb-fire-torpedo-launcher')
        
        # Melee
        if wtype == 'Melee':
            return self.category_map.get('melee')
        
        # Kamikaze units (explicit list) — categorized as trigger-explosive
        if unit_name in KAMIKAZE_WEAPONS and weapondef_key == KAMIKAZE_WEAPONS[unit_name]:
            return self.category_map.get('trigger-explosive')

        # Aircraft EMP Bomb (CHECK FIRST - more specific than regular AircraftBomb)
        # Criteria: AircraftBomb + paralyzer = true
        if wtype == 'AircraftBomb' and weapon.get('paralyzer', False):
            return self.category_map.get('aircraft-emp-bomb')

        # AircraftBomb
        if wtype == 'AircraftBomb':
            return self.category_map.get('aircraft-bomb')
        
        # D-Gun (special weapon) — disintegrator check goes first
        if 'disintegrator' in weapondef_key:
            return self.category_map.get('d-gun')

        # Disintegrator Cannon (DGun weapontype, but not a disintegrator beam)
        # e.g. corjugg's juggernaut_fire (fireball/gauss variant)
        if wtype == 'DGun':
            return self.category_map.get('disintegrator-cannon')

        # Shield (weapontype = Shield)
        if wtype == 'Shield':
            return self.category_map.get('shield')
        
        # Default: None (no category)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# SOUND HELPERS
# ══════════════════════════════════════════════════════════════════════════════

import base64
import subprocess
import tempfile

# Cached index: soundname (lowercase, no ext) → raw GitHub URL
_sounds_index_cache: Optional[Dict[str, str]] = None

SOUNDS_REPO_DIR = "weapon-sounds"  # Folder in unit-sync repo for MP3s


def _build_sounds_index(gh_headers: dict) -> Dict[str, str]:
    """Build a one-time index mapping sound name (lowercase, no ext) → raw GitHub URL."""
    global _sounds_index_cache
    if _sounds_index_cache is not None:
        return _sounds_index_cache

    print("  🔍 Building sounds index from BAR repo (one-time)...")
    tree_url = (
        f"https://api.github.com/repos/{GITHUB_REPO}"
        f"/git/trees/{GITHUB_BRANCH}?recursive=1"
    )
    try:
        resp = requests.get(tree_url, headers=gh_headers, timeout=20)
        resp.raise_for_status()
        tree = resp.json()
    except Exception as e:
        print(f"  ❌ Could not fetch GitHub tree for sounds: {e}")
        _sounds_index_cache = {}
        return {}

    index = {}
    for item in tree.get("tree", []):
        path = item.get("path", "")
        if path.startswith("sounds/") and path.lower().endswith(".wav"):
            name = os.path.splitext(os.path.basename(path))[0].lower()
            raw_url = (
                f"https://raw.githubusercontent.com/{GITHUB_REPO}"
                f"/refs/heads/{GITHUB_BRANCH}/{path}"
            )
            index[name] = raw_url

    print(f"     ✅ Sound index built: {len(index)} WAV files")
    _sounds_index_cache = index
    return index


def resolve_and_upload_sound(
    sound_name: str,
    gh_headers: dict,
    sync_gh_owner: str,
    sync_gh_repo: str,
    sync_gh_branch: str,
    sync_gh_token: str,
) -> Optional[str]:
    """
    Given a soundstart name (e.g. "lasrfir1"), find the WAV in the BAR repo,
    convert to mono 128kbps MP3, upload to our unit-sync GitHub repo,
    and return a jsDelivr CDN URL (works as external URL in Webflow File fields).
    Returns None if the sound can't be found or converted.
    """
    if not sound_name:
        return None

    index = _build_sounds_index(gh_headers)
    wav_url = index.get(sound_name.lower())
    if not wav_url:
        print(f"     ⚠️  Sound not found in BAR repo: {sound_name}")
        return None

    # Download WAV
    try:
        r = requests.get(wav_url, headers=gh_headers, timeout=20)
        r.raise_for_status()
        wav_bytes = r.content
    except Exception as e:
        print(f"     ❌ Could not download WAV {sound_name}: {e}")
        return None

    # Convert WAV → mono 128kbps MP3 using ffmpeg
    mp3_filename = f"{sound_name.lower()}.mp3"
    tmp_wav_path = tmp_mp3_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
            tmp_wav.write(wav_bytes)
            tmp_wav_path = tmp_wav.name

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
            tmp_mp3_path = tmp_mp3.name

        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_wav_path, "-ac", "1", "-ab", "128k", "-f", "mp3", tmp_mp3_path],
            capture_output=True,
            check=True,
        )
        with open(tmp_mp3_path, "rb") as f:
            mp3_bytes = f.read()
    except subprocess.CalledProcessError as e:
        print(f"     ❌ ffmpeg failed for {sound_name}: {e.stderr.decode()[:200]}")
        return None
    except Exception as e:
        print(f"     ❌ Sound conversion error for {sound_name}: {e}")
        return None
    finally:
        for p in (tmp_wav_path, tmp_mp3_path):
            if p:
                try:
                    os.unlink(p)
                except Exception:
                    pass

    # Upload MP3 to unit-sync GitHub repo
    repo_path = f"weapon-sounds/{mp3_filename}"
    api_url = (
        f"https://api.github.com/repos/{sync_gh_owner}/{sync_gh_repo}"
        f"/contents/{repo_path}"
    )
    upload_headers = {
        "Authorization": f"token {sync_gh_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    sha = None
    existing = requests.get(api_url, headers=upload_headers, params={"ref": sync_gh_branch})
    if existing.status_code == 200:
        existing_data = existing.json()
        sha = existing_data.get("sha")
        if existing_data.get("size") == len(mp3_bytes):
            jsdelivr_url = (
                f"https://cdn.jsdelivr.net/gh/{sync_gh_owner}/{sync_gh_repo}"
                f"@{sync_gh_branch}/{repo_path}"
            )
            print(f"     Sound already up-to-date: {mp3_filename}")
            return jsdelivr_url

    payload = {
        "message": f"Add/update weapon sound: {mp3_filename}",
        "content": base64.b64encode(mp3_bytes).decode(),
        "branch": sync_gh_branch,
    }
    if sha:
        payload["sha"] = sha

    try:
        resp = requests.put(api_url, headers=upload_headers, json=payload)
        resp.raise_for_status()
    except Exception as e:
        print(f"     ❌ GitHub upload failed for {mp3_filename}: {e}")
        return None

    jsdelivr_url = (
        f"https://cdn.jsdelivr.net/gh/{sync_gh_owner}/{sync_gh_repo}"
        f"@{sync_gh_branch}/{repo_path}"
    )
    print(f"     🔊 Sound uploaded: {mp3_filename} ({len(mp3_bytes):,} bytes)")
    return jsdelivr_url


# ══════════════════════════════════════════════════════════════════════════════
# WEAPON SYNC SERVICE
# ══════════════════════════════════════════════════════════════════════════════

class WeaponSyncService:
    """Main service to sync weapons from GitHub to Webflow."""

    def __init__(self, webflow_weapons: WebflowAPI, webflow_categories: WebflowAPI,
                 webflow_units: WebflowAPI):
        self.weapons_api = webflow_weapons
        self.categories_api = webflow_categories
        self.units_api = webflow_units
        self.category_detector = None  # Initialized after loading categories

        # Cache: sound_name → Webflow hosted URL (avoid re-uploading same sound in one run)
        self._sound_url_cache: Dict[str, Optional[str]] = {}

        # Caches (populated by prefetch_caches, avoids re-fetching per unit)
        self._existing_weapons_cache = None   # {weapon_name: item_dict}
        self._units_by_name_cache = None      # {unit_name_lower: webflow_id}
        self._all_webflow_units_cache = None  # [unit_dicts]

        # Bulk operation queues
        self._pending_creates = []      # [{fieldData}]
        self._pending_updates = []      # [{id, fieldData}]
        self._pending_publish_ids = []  # [item_id]
        # Track unit link updates to flush in bulk
        self._pending_unit_updates = []  # [{id, fieldData}]
        self._pending_unit_publish_ids = []  # [unit_id]
        # Map weapon name→id for weapons that already existed or will be created
        self._weapon_name_to_id = {}
        # Track create order so we can assign IDs from bulk create response
        self._create_names_order = []   # [weapon_name] in same order as _pending_creates
    
    def load_weapon_categories(self):
        """Load weapon categories from Webflow to build category map."""
        print("Loading weapon categories from Webflow...")
        categories = self.categories_api.get_all_items()
        
        category_map = {}
        for cat in categories:
            slug = cat.get('fieldData', {}).get('slug', '')
            cat_id = cat.get('id')
            if slug and cat_id:
                category_map[slug] = cat_id
        
        print(f"  ✅ Loaded {len(category_map)} weapon categories")
        print(f"     Categories: {', '.join(category_map.keys())}")
        
        self.category_detector = WeaponCategoryDetector(category_map)
        return category_map

    def prefetch_caches(self):
        """Pre-fetch existing weapons and units from Webflow (call once before bulk sync)."""
        print("📦 Pre-fetching existing weapons from Webflow...")
        existing_weapons = self.weapons_api.get_all_items()
        self._existing_weapons_cache = {
            item.get('fieldData', {}).get('name', ''): item
            for item in existing_weapons
        }
        print(f"  ✅ Cached {len(self._existing_weapons_cache)} existing weapons")

        print("📦 Pre-fetching all units from Webflow...")
        self._all_webflow_units_cache = self.units_api.get_all_items()
        self._units_by_name_cache = {
            u.get('fieldData', {}).get('name', '').lower(): u['id']
            for u in self._all_webflow_units_cache if u.get('id')
        }
        print(f"  ✅ Cached {len(self._units_by_name_cache)} units")
        print()

    def _get_existing_weapons_lookup(self) -> Dict[str, Dict]:
        """Return cached existing weapons lookup, or fetch if not cached."""
        if self._existing_weapons_cache is not None:
            return self._existing_weapons_cache
        existing_weapons = self.weapons_api.get_all_items()
        return {
            item.get('fieldData', {}).get('name', ''): item
            for item in existing_weapons
        }

    def _get_units_by_name(self) -> Dict[str, str]:
        """Return cached units-by-name lookup, or fetch if not cached."""
        if self._units_by_name_cache is not None:
            return self._units_by_name_cache
        all_units = self.units_api.get_all_items()
        return {
            u.get('fieldData', {}).get('name', '').lower(): u['id']
            for u in all_units if u.get('id')
        }

    def flush_bulk_operations(self, publish: bool = False) -> Tuple[int, int, int]:
        """
        Send all pending bulk operations to Webflow.
        Returns (created_count, updated_count, published_count).
        """
        created = 0
        updated = 0
        published = 0

        # ── Bulk create weapons (batches of 100) ─────────────────────────
        if self._pending_creates:
            print(f"\n📤 Bulk creating {len(self._pending_creates)} weapons...")
            for i in range(0, len(self._pending_creates), 100):
                batch = self._pending_creates[i:i+100]
                batch_names = self._create_names_order[i:i+100]
                ids = self.weapons_api.bulk_create_items(batch, is_draft=(not publish))
                if ids:
                    created += len(ids)
                    # Map names to new IDs
                    for name, wid in zip(batch_names, ids):
                        self._weapon_name_to_id[name] = wid
                    self._pending_publish_ids.extend(ids)
                    print(f"  ✅ Created batch {i//100 + 1}: {len(ids)} weapons")
                else:
                    print(f"  ❌ Batch {i//100 + 1} failed")

        # ── Bulk update weapons (batches of 100) ─────────────────────────
        if self._pending_updates:
            print(f"\n📤 Bulk updating {len(self._pending_updates)} weapons...")
            for i in range(0, len(self._pending_updates), 100):
                batch = self._pending_updates[i:i+100]
                count = self.weapons_api.bulk_update_items(batch)
                updated += count
                # Collect IDs for publish
                self._pending_publish_ids.extend(item['id'] for item in batch)
                print(f"  ✅ Updated batch {i//100 + 1}: {count} weapons")

        # ── Bulk publish weapons ─────────────────────────────────────────
        if publish and self._pending_publish_ids:
            unique_ids = list(dict.fromkeys(self._pending_publish_ids))  # deduplicate, keep order
            print(f"\n📢 Bulk publishing {len(unique_ids)} weapons...")
            for i in range(0, len(unique_ids), 100):
                batch = unique_ids[i:i+100]
                ok = self.weapons_api.bulk_publish_items(batch)
                if ok:
                    published += len(batch)
                    print(f"  ✅ Published batch {i//100 + 1}: {len(batch)} weapons")
                else:
                    print(f"  ⚠️  Publish batch {i//100 + 1} had issues")

        # ── Resolve pending-create-xxx IDs in unit updates ────────────────
        if self._pending_unit_updates:
            for unit_update in self._pending_unit_updates:
                weapons_ref = unit_update['fieldData'].get('attached-unit-weapons', [])
                resolved = []
                for wid in weapons_ref:
                    if isinstance(wid, str) and wid.startswith("pending-create-"):
                        wname = wid.replace("pending-create-", "")
                        actual_id = self._weapon_name_to_id.get(wname)
                        if actual_id:
                            resolved.append(actual_id)
                        # else: weapon create failed, skip
                    else:
                        resolved.append(wid)
                unit_update['fieldData']['attached-unit-weapons'] = resolved

        # ── Bulk update units (link weapons + target caps) ───────────────
        if self._pending_unit_updates:
            print(f"\n📤 Bulk updating {len(self._pending_unit_updates)} units (weapon links)...")
            for i in range(0, len(self._pending_unit_updates), 100):
                batch = self._pending_unit_updates[i:i+100]
                count = self.units_api.bulk_update_items(batch)
                print(f"  ✅ Updated batch {i//100 + 1}: {count} units")

        # ── Bulk publish units ───────────────────────────────────────────
        if publish and self._pending_unit_publish_ids:
            unique_ids = list(dict.fromkeys(self._pending_unit_publish_ids))
            print(f"\n📢 Bulk publishing {len(unique_ids)} units...")
            for i in range(0, len(unique_ids), 100):
                batch = unique_ids[i:i+100]
                ok = self.units_api.bulk_publish_items(batch)
                if ok:
                    print(f"  ✅ Published batch {i//100 + 1}: {len(batch)} units")
                else:
                    print(f"  ⚠️  Publish batch {i//100 + 1} had issues")

        # Clear queues
        total_creates = len(self._pending_creates)
        total_updates = len(self._pending_updates)
        self._pending_creates.clear()
        self._pending_updates.clear()
        self._pending_publish_ids.clear()
        self._create_names_order.clear()
        self._pending_unit_updates.clear()
        self._pending_unit_publish_ids.clear()

        return created, updated, published

    def fetch_unit_file(self, unit_name: str) -> Optional[str]:
        """Fetch a single unit .lua file from GitHub by searching recursively.

        Returns the file content on success, None if the unit genuinely does
        not exist, and raises RuntimeError on transient errors (rate limit,
        network failure) so the caller can skip instead of wiping data.
        """
        github_token = os.environ.get("GITHUB_TOKEN")
        headers = {}
        if github_token:
            headers['Authorization'] = f'token {github_token}'

        # First try: direct path (most common)
        direct_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/refs/heads/{GITHUB_BRANCH}/units/{unit_name}.lua"
        try:
            response = requests.get(direct_url, headers=headers)
        except Exception as e:
            raise RuntimeError(f"Network error fetching {unit_name}.lua: {e}")
        if response.status_code == 200:
            return response.text
        if response.status_code not in (404,):
            raise RuntimeError(f"HTTP {response.status_code} fetching {unit_name}.lua direct")

        # Second try: search through units directory using GitHub API
        print(f"  🔍 Searching for {unit_name}.lua in repository...")
        search_url = f"https://api.github.com/repos/{GITHUB_REPO}/git/trees/{GITHUB_BRANCH}?recursive=1"
        try:
            response = requests.get(search_url, headers=headers)
        except Exception as e:
            raise RuntimeError(f"Network error searching tree for {unit_name}: {e}")

        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code} fetching git tree (rate limit?) for {unit_name}")

        tree = response.json()
        for item in tree.get('tree', []):
            if item['path'].startswith('units/') and item['path'].endswith(f'/{unit_name}.lua'):
                file_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/refs/heads/{GITHUB_BRANCH}/{item['path']}"
                try:
                    file_response = requests.get(file_url, headers=headers)
                except Exception as e:
                    raise RuntimeError(f"Network error fetching {item['path']}: {e}")
                if file_response.status_code == 200:
                    print(f"  ✅ Found at: {item['path']}")
                    return file_response.text
                raise RuntimeError(f"HTTP {file_response.status_code} fetching {item['path']}")

        print(f"  ⚠️  Unit file genuinely not found for {unit_name}")
        return None

    def _get_github_headers(self) -> dict:
        """Return GitHub auth headers if token is available."""
        token = os.environ.get("GITHUB_TOKEN")
        return {'Authorization': f'token {token}'} if token else {}

    def build_weapons_folder_index(self) -> Dict[str, str]:
        """
        Build a one-time index of all weapondef keys in the weapons/ folder.
        Scans every .lua file under weapons/ on GitHub and maps each weapondef
        key (lowercase) to the file path it lives in.
        e.g. { "crawl_blastsml": "weapons/crawl_blast.lua", ... }
        Cached in self._weapons_file_index so GitHub is only hit once per run.
        """
        if hasattr(self, '_weapons_file_index'):
            return self._weapons_file_index

        print("  \U0001f50d Building weapons/ folder index (one-time scan)...")
        headers = self._get_github_headers()
        index: Dict[str, str] = {}

        # Get list of all .lua files in weapons/ via GitHub tree API
        tree_url = (
            f"https://api.github.com/repos/{GITHUB_REPO}"
            f"/git/trees/{GITHUB_BRANCH}?recursive=1"
        )
        try:
            resp = requests.get(tree_url, headers=headers, timeout=15)
            resp.raise_for_status()
            tree = resp.json()
        except Exception as e:
            print(f"  \u274c Could not fetch GitHub tree: {e}")
            self._weapons_file_index = index
            return index

        weapon_files = [
            item['path'] for item in tree.get('tree', [])
            if item['path'].startswith('weapons/') and item['path'].endswith('.lua')
        ]
        print(f"     Found {len(weapon_files)} .lua files in weapons/")

        # Fetch each file and scan for top-level weapondef keys
        key_pattern = re.compile(r'^\s{0,4}(\w+)\s*=\s*\{', re.MULTILINE)

        for path in weapon_files:
            url = (
                f"https://raw.githubusercontent.com/{GITHUB_REPO}"
                f"/refs/heads/{GITHUB_BRANCH}/{path}"
            )
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code != 200:
                    continue
                file_content = re.sub(r'--[^\n]*', '', r.text)

                # Prefer scanning inside WeaponDefs block if present
                wd_match = re.search(r'\bWeaponDefs\s*=\s*\{', file_content, re.IGNORECASE)
                if wd_match:
                    wd_block = LuaParser.extract_balanced_braces(file_content, wd_match.end() - 1)
                    scan_text = wd_block or file_content
                else:
                    scan_text = file_content

                for m in key_pattern.finditer(scan_text):
                    key = m.group(1).lower()
                    if key in ('weapondefs', 'return', 'local', 'damage'):
                        continue
                    if key not in index:
                        index[key] = path

            except Exception as e:
                print(f"     \u26a0\ufe0f  Error scanning {path}: {e}")

        print(f"     \u2705 Index built: {len(index)} weapondef keys across {len(weapon_files)} files")
        self._weapons_file_index = index
        return index

    def fetch_mine_weapon_file(self, explodeas: str) -> Optional[str]:
        """
        Fetch the .lua file containing the given explodeas weapondef.
        First tries direct filename match, then consults weapons/ folder index.
        """
        headers = self._get_github_headers()
        key = explodeas.lower()

        # Fast path: file named exactly after the key
        filename = key + ".lua"
        url = (
            f"https://raw.githubusercontent.com/{GITHUB_REPO}"
            f"/refs/heads/{GITHUB_BRANCH}/weapons/{filename}"
        )
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                print(f"  \u2705 Found mine weapon file: weapons/{filename}")
                return r.text
        except Exception:
            pass

        # Slow path: consult the index
        index = self.build_weapons_folder_index()
        if key in index:
            found_path = index[key]
            url = (
                f"https://raw.githubusercontent.com/{GITHUB_REPO}"
                f"/refs/heads/{GITHUB_BRANCH}/{found_path}"
            )
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 200:
                    print(f"  \u2705 Found mine weapon file via index: {found_path}")
                    return r.text
            except Exception as e:
                print(f"  \u274c Error fetching {found_path}: {e}")
        else:
            print(f"  \u26a0\ufe0f  '{key}' not found in weapons/ index")

        return None

    def sync_weapons_for_unit(self, unit_name: str, dry_run: bool = False, publish: bool = False, bulk: bool = False) -> Tuple[List[str], List[Dict]]:
        """
        Sync all weapons for a single unit.
        For mine units (mine = true in customparams), fetches the explosion
        weapon from weapons/explodeas.lua instead of the unit's weapondefs.

        When bulk=True, operations are queued instead of executed immediately.
        Call flush_bulk_operations() to send them all at once.

        Returns tuple of (weapon_ids, weapons_data).
        """
        print(f"\nProcessing unit: {unit_name}")

        # Fetch unit file. A transient error (rate limit, network) raises
        # RuntimeError — we return (None, None) so the caller skips the link
        # update and preserves existing weapon references in Webflow.
        try:
            unit_content = self.fetch_unit_file(unit_name)
        except RuntimeError as e:
            print(f"  ⚠️  Fetch failed for {unit_name}: {e} — preserving existing links")
            return None, None
        if not unit_content:
            # Unit genuinely does not exist in GitHub — return empty lists so
            # the link update clears weapons (unit is probably being removed).
            return [], []
        
        # Strip Lua comments before parsing
        unit_content_clean = re.sub(r'--[^\n]*', '', unit_content)

        # ── Parse selfdestructcountdown ───────────────────────────────────────
        sdc_match = re.search(r'\bselfdestructcountdown\s*=\s*(\d+)', unit_content_clean, re.IGNORECASE)
        sdc_value = int(sdc_match.group(1)) if sdc_match else None  # None = not specified (default 5)

        # ── Parse selfdestructas / explodeas ──────────────────────────────────
        sda_match = re.search(r'\bselfdestructas\s*=\s*["\']+([\w]+)["\']+', unit_content_clean, re.IGNORECASE)
        if not sda_match:
            sda_match = re.search(r'\bselfdestructas\s*=\s*(\w+)', unit_content_clean, re.IGNORECASE)
        ea_match = re.search(r'\bexplodeas\s*=\s*["\']+([\w]+)["\']+', unit_content_clean, re.IGNORECASE)
        if not ea_match:
            ea_match = re.search(r'\bexplodeas\s*=\s*(\w+)', unit_content_clean, re.IGNORECASE)

        selfdestructas = sda_match.group(1) if sda_match else None
        explodeas_name = ea_match.group(1) if ea_match else None

        # ── Detect if this is a mine, crawling bomb, or spy unit ─────────────
        # All types use an external selfdestructas/explodeas weapon — unitdef weapons are ignored.
        #
        # Mine:          customparams { mine = true }
        # Crawling bomb: selfdestructcountdown = 0
        #                customparams { unitgroup = "explo", instantselfd = true }
        # Spy bomb:      selfdestructcountdown = 0
        #                customparams { unitgroup = "buildert2" }

        is_explode_unit = False   # True for mines, crawling bombs AND spy units
        explodeas = None

        cp_match = re.search(r'\bcustomparams\s*=\s*\{', unit_content_clean, re.IGNORECASE)
        cp_block = None
        if cp_match:
            cp_block = LuaParser.extract_balanced_braces(unit_content_clean, cp_match.end() - 1)

        # Mine check
        if cp_block and re.search(r'\bmine\s*=\s*true', cp_block, re.IGNORECASE):
            is_explode_unit = True
            print(f"  💣 Mine unit detected")

        # Crawling bomb check
        if not is_explode_unit:
            has_sdc0    = bool(sdc_value == 0)
            has_explo   = bool(cp_block and re.search(r'\bunitgroup\s*=\s*["\']+explo["\']+', cp_block, re.IGNORECASE))
            has_instant = bool(cp_block and re.search(r'\binstantselfd\s*=\s*true', cp_block, re.IGNORECASE))
            if has_sdc0 and has_explo and has_instant:
                is_explode_unit = True
                print(f"  💥 Crawling bomb detected")

        # Spy bomb check (paralyze self-destruct)
        if not is_explode_unit:
            has_sdc0      = bool(sdc_value == 0)
            has_buildert2 = bool(cp_block and re.search(r'\bunitgroup\s*=\s*["\']+buildert2["\']+', cp_block, re.IGNORECASE))
            if has_sdc0 and has_buildert2:
                is_explode_unit = True
                print(f"  🕵️  Spy bomb detected")

        # EMP building explosion (selfdestructas = "empblast")
        # e.g. armamex — explodes with EMP blast when destroyed
        if not is_explode_unit:
            if selfdestructas and selfdestructas.lower() == 'empblast':
                is_explode_unit = True
                print(f"  💥 EMP building detected (empblast)")

        if is_explode_unit:
            # Prefer selfdestructas, fall back to explodeas
            if selfdestructas:
                explodeas = selfdestructas
                print(f"     selfdestructas = {explodeas} (preferred over explodeas)")
            elif explodeas_name:
                explodeas = explodeas_name
                print(f"     explodeas = {explodeas}")
            else:
                print(f"  ⚠️  Explode unit but no selfdestructas/explodeas found — skipping weapon sync")
                return [], []

        # ── Detect units with non-standard selfdestruct timer ────────────────
        # Default is 5s. sdc=0 units are already handled above (mines/bombs).
        # Units like corpyro (sdc=1), corkorg (sdc=10), armbanth (sdc=10) have
        # a significant self-destruct explosion that should be synced as an
        # ADDITIONAL weapon alongside their regular weapons.
        #
        # Skip trivial explosions (walls, mex, scouts, beacons) — these are
        # cosmetic and not meaningful as weapons.
        _TRIVIAL_SELFDESTRUCT = {
            'wallexplosionmetal', 'wallexplosionconcrete', 'wallexplosionwater',
            'wallexplosionmetalxl', 'wallexplosionconcretexl',
            'smallmex', 'smallbuildingexplosiongeneric', 'smallbuildingexplosiongenericselfd',
            'mediumbuildingexplosiongeneric', 'mediumbuildingexplosiongenericselfd',
            'tinyexplosiongeneric', 'tinyexplosiongenericselfd',
            'advmetalmaker',
        }
        has_selfdestruct_weapon = False
        selfdestruct_weapon_name = None
        if not is_explode_unit and sdc_value is not None and sdc_value != 0 and sdc_value != 5:
            if selfdestructas and selfdestructas.lower() not in _TRIVIAL_SELFDESTRUCT:
                selfdestruct_weapon_name = selfdestructas
                has_selfdestruct_weapon = True
                print(f"  💥 Non-standard self-destruct timer: {sdc_value}s (selfdestructas = {selfdestructas})")

        # ── Parse weapons ──────────────────────────────────────────────────────
        if is_explode_unit and explodeas:
            # Mine / crawling bomb: use ONLY the external selfdestructas weapon (preferred)
            # or explodeas as fallback. All weapondefs inside the unitdef are ignored.
            mine_content = self.fetch_mine_weapon_file(explodeas)
            if not mine_content:
                print(f"  ⚠️  Could not fetch weapon file for {explodeas}")
                return [], []
            mine_weapon = WeaponParser.parse_mine_weapondef(mine_content, unit_name, explodeas)
            if not mine_weapon:
                return [], []
            weapons = [mine_weapon]
        else:
            # Normal unit: parse weapondefs from the unit file
            weapons = WeaponParser.parse_weapondefs(unit_content_clean, unit_name)

            # Add self-destruct weapon as extra if non-standard timer
            if has_selfdestruct_weapon and selfdestruct_weapon_name:
                sd_content = self.fetch_mine_weapon_file(selfdestruct_weapon_name)
                if sd_content:
                    sd_weapon = WeaponParser.parse_mine_weapondef(sd_content, unit_name, selfdestruct_weapon_name)
                    if sd_weapon:
                        print(f"     ➕ Added self-destruct weapon: {sd_weapon['name']}")
                        weapons.append(sd_weapon)
                    else:
                        print(f"     ⚠️  Could not parse self-destruct weapon: {selfdestruct_weapon_name}")
                else:
                    print(f"     ⚠️  Could not fetch self-destruct weapon file: {selfdestruct_weapon_name}")

        if not weapons:
            print(f"  ℹ️  No weapons found")
            return [], []
        
        print(f"  📊 Found {len(weapons)} weapons")
        
        # Use cached lookups (or fetch if not cached)
        existing_lookup = self._get_existing_weapons_lookup()
        _units_by_name = self._get_units_by_name()

        weapon_ids = []
        
        for weapon in weapons:
            weapon_name = weapon['name']
            
            # Show DOT info if present
            dot_info = ""
            if weapon.get('dot', 0) > 0:
                dot_info = f" [DOT: {weapon['dot']}]"
            
            # Show PPS info if paralyzer
            pps_info = ""
            if weapon.get('pps', 0) > 0:
                pps_info = f" [PPS: {weapon['pps']}]"
            
            # Show shield info
            shield_info = ""
            if weapon.get('weapon_type') == 'Shield':
                shield_power = weapon.get('shield_power', 0)
                shield_regen = weapon.get('shield_power_regen', 0)
                shield_info = f" [Shield: {shield_power}, Regen: {shield_regen}/s]"
            
            print(f"    🔫 {weapon_name} (count: {weapon['weapon_count']}, DPS: {weapon['dps']}{dot_info}{pps_info}{shield_info})")
            
            # Detect category
            category_id = self.category_detector.detect_category(weapon) if self.category_detector else None
            
            # Build Webflow field data (using exact field slugs from Webflow)
            field_data = {
                'name': weapon['name'],
                'full-name': weapon['full_name'],
                'number-of-weapons-on-unit': weapon['weapon_count'],  # NOT weapon-count
                'weapon-type': weapon['weapon_type'],
                
                # Stats
                'dps': weapon['dps'],
                'dot': weapon.get('dot', 0),  # Damage Over Time (cluster/napalm)
                'pps': weapon.get('pps', 0),  # Paralyse Per Second (EMP weapons)
                'reload-time': weapon['reload_time'],
                'weapon-range': weapon['range'],  # NOT range
                'accuracy': weapon['accuracy'],
                'area-of-effect': weapon['area_of_effect'],
                'edge-effectiveness': weapon['edge_effectiveness'],
                'impulse': weapon['impulse'],
                
                # Projectile
                'projectiles': weapon['projectiles'],
                'velocity': weapon['velocity'],
                'burst': weapon['burst'],
                'burst-rate': weapon.get('burst_rate', 0),
                
                # Damage
                'damage-default': weapon['damage_default'],
                'damage-commanders': weapon['damage_commanders'],
                'damage-vtol': weapon['damage_vtol'],
                'damage-submarines': weapon['damage_subs'],  # NOT damage-subs
                
                # Cost (per salvo: shot × salvosize × burst; continuous beams: shot × 30)
                'energy-per-shot': (
                    weapon['energy_per_shot'] * 30
                    if weapon.get('reload_time', 1) <= weapon.get('beamtime', 0) and weapon.get('beamtime', 0) > 0
                    else weapon['energy_per_shot'] * weapon['_salvosize'] * weapon['burst']
                ),
                'metal-per-shot': (
                    weapon['metal_per_shot'] * 30
                    if weapon.get('reload_time', 1) <= weapon.get('beamtime', 0) and weapon.get('beamtime', 0) > 0
                    else weapon['metal_per_shot'] * weapon['_salvosize'] * weapon['burst']
                ),
                
                # Stockpile
                'stockpile': weapon['stockpile'],
                'stockpile-time': weapon['stockpile_time'],
                
                # Special
                'paralyzer': weapon['paralyzer'],
                'paralyse-duration': weapon['paralyze_duration'],  # NOT paralyze-duration (British spelling!)
                'homing': weapon['homing'],
                'turnrate': weapon['turn_rate'],  # NOT turn-rate (no dash)
                'waterweapon': weapon['water_weapon'],  # NOT water-weapon (no dash)
                'large-beam-laser': weapon['large_beam_laser'],
                
                # Shield properties (for weapontype = Shield)
                'shield-power': weapon.get('shield_power', 0),
                'shield-regen': weapon.get('shield_power_regen', 0),
                'shield-regen-energy-cost': weapon.get('shield_power_regen_energy', 0),
                'shield-radius': weapon.get('shield_radius', 0),
                
                # Target capabilities
                'can-target-surface': weapon['can_target_surface'],
                'can-target-air': weapon['can_target_air'],
                'can-target-subs': weapon['can_target_subs'],
                
                # Visual
                'color': weapon['color'],

                # Velocity (only meaningful for accelerating projectiles)
                'start-velocity': weapon.get('start_velocity', 0),
                'weapon-acceleration': weapon.get('weapon_acceleration', 0),
            }

            # Add category if detected
            if category_id:
                field_data['weapon-category'] = category_id

            # Add stockpile limit if present
            if weapon['stockpile_limit'] is not None:
                field_data['stockpile-limit'] = weapon['stockpile_limit']

            # Add beamtime only if > 0 (most weapons don't have this)
            if weapon.get('beamtime', 0) > 0:
                field_data['beamtime'] = weapon['beamtime']

            # Add sound if present — upload WAV→MP3 to Webflow Assets, set file field
            sound_name = weapon.get('sound_start')
            if sound_name:
                if sound_name not in self._sound_url_cache:
                    gh_headers = self._get_github_headers()
                    sync_gh_owner  = os.environ.get("ICON_REPO_OWNER", "icexuick")
                    sync_gh_repo   = os.environ.get("ICON_REPO_NAME", "bar-unit-sync")
                    sync_gh_branch = os.environ.get("ICON_BRANCH", "main")
                    sync_gh_token  = os.environ.get("GITHUB_TOKEN", "")
                    url = resolve_and_upload_sound(
                        sound_name,
                        gh_headers,
                        sync_gh_owner,
                        sync_gh_repo,
                        sync_gh_branch,
                        sync_gh_token,
                    )
                    self._sound_url_cache[sound_name] = url
                sound_url = self._sound_url_cache[sound_name]
                if sound_url:
                    field_data['sound-url'] = sound_url

            # Add countdown timer for self-destruct / explode weapons
            if weapon.get('_is_mine'):
                # Determine the countdown value:
                # - sdc_value from the unitdef (None = default 5, 0 = instant)
                # - For mines with sdc=0 the explosion is on contact, timer = 0
                timer = sdc_value if sdc_value is not None else 5
                field_data['countdown-timer'] = timer

            # ── Drone carrier: override stats from carried_unit weapons ─────
            if weapon.get('_drone_carried_unit'):
                carried_name = weapon['_drone_carried_unit']
                print(f"     🚁 Drone carrier — fetching stats from {carried_name}")

                # Override cost/spawn fields from customparams
                if weapon['_drone_spawnrate'] is not None:
                    field_data['stockpile-time'] = weapon['_drone_spawnrate']
                if weapon['_drone_maxunits'] is not None:
                    field_data['number-of-weapons-on-unit'] = weapon['_drone_maxunits']
                if weapon['_drone_energycost'] is not None:
                    field_data['energy-per-shot'] = weapon['_drone_energycost']
                if weapon['_drone_metalcost'] is not None:
                    field_data['metal-per-shot'] = weapon['_drone_metalcost']

                # Fetch drone unit file and parse its weapons for DPS/damage stats
                try:
                    drone_content = self.fetch_unit_file(carried_name)
                except RuntimeError as e:
                    print(f"     ⚠️  Could not fetch drone unit {carried_name}: {e}")
                    drone_content = None
                if drone_content:
                    drone_content = re.sub(r'--[^\n]*', '', drone_content)
                    drone_weapons = WeaponParser.parse_weapondefs(drone_content, carried_name)
                    if drone_weapons:
                        # Use first real damage-dealing weapon from drone
                        drone_w = next((w for w in drone_weapons
                                        if w.get('damage_default', 0) > 0 or w.get('damage_vtol', 0) > 0), None)
                        if drone_w:
                            field_data['dps']              = drone_w.get('dps', 0)
                            field_data['dot']              = drone_w.get('dot', 0)
                            field_data['pps']              = drone_w.get('pps', 0)
                            field_data['damage-default']   = drone_w.get('damage_default', 0)
                            field_data['damage-commanders']= drone_w.get('damage_commanders', 0)
                            field_data['damage-vtol']      = drone_w.get('damage_vtol', 0)
                            field_data['damage-submarines']= drone_w.get('damage_subs', 0)
                            field_data['reload-time']      = drone_w.get('reload_time', 0)
                            field_data['accuracy']         = drone_w.get('accuracy', 0)
                            field_data['area-of-effect']   = drone_w.get('area_of_effect', 0)
                            field_data['projectiles']      = drone_w.get('projectiles', 1)
                            field_data['burst']            = drone_w.get('burst', 1)
                            field_data['homing']           = drone_w.get('homing', False)
                            print(f"       ✅ Drone DPS={drone_w.get('dps', 0)} from {drone_w['weapondef_key']}")
                        else:
                            print(f"       ⚠️  No damage-dealing weapon found in {carried_name}")
                    else:
                        print(f"       ⚠️  No weapondefs found in {carried_name}")
                else:
                    print(f"       ⚠️  Could not fetch drone unit file: {carried_name}")

                # Resolve carried_unit → Webflow item ID for multi-reference (cached)
                carried_id = _units_by_name.get(carried_name)
                if carried_id:
                    field_data['carried-units'] = [carried_id]
                    print(f"       🔗 Linked carried unit: {carried_name} ({carried_id})")
                else:
                    print(f"       ⚠️  Carried unit '{carried_name}' not found in Webflow (not yet synced?)")

            # Check if weapon exists
            existing = existing_lookup.get(weapon_name)

            if dry_run:
                # Resolve category slug name for display
                category_slug = None
                if category_id and self.category_detector:
                    for slug, cid in self.category_detector.category_map.items():
                        if cid == category_id:
                            category_slug = slug
                            break
                cat_label = f"category={category_slug}" if category_slug else "category=None"
                if existing:
                    print(f"       🔍 Would update existing weapon [{cat_label}]")
                    if publish:
                        print(f"       🔍 Would publish")
                else:
                    print(f"       🔍 Would create new weapon [{cat_label}]")
                    if publish:
                        print(f"       🔍 Would publish")
                weapon_ids.append("dry-run-id")
            elif bulk:
                # ── Bulk mode: queue operations for later ────────────────
                if existing:
                    self._pending_updates.append({
                        'id': existing['id'],
                        'fieldData': field_data,
                    })
                    weapon_ids.append(existing['id'])
                    self._weapon_name_to_id[weapon_name] = existing['id']
                    print(f"       📋 Queued update")
                else:
                    self._pending_creates.append(field_data)
                    self._create_names_order.append(weapon_name)
                    weapon_ids.append(f"pending-create-{weapon_name}")
                    print(f"       📋 Queued create")
            else:
                # ── Single mode: execute immediately ─────────────────────
                if existing:
                    # Update
                    success = self.weapons_api.update_item(existing['id'], field_data)
                    if success:
                        status = "Updated"
                        weapon_ids.append(existing['id'])

                        # Publish if requested
                        if publish:
                            pub_success = self.weapons_api.publish_item(existing['id'])
                            if pub_success:
                                status += " & Published"
                            else:
                                status += " (publish failed)"

                        print(f"       ✅ {status}")
                else:
                    # Create
                    weapon_id = self.weapons_api.create_item(field_data, is_draft=True)
                    if weapon_id:
                        status = "Created"
                        weapon_ids.append(weapon_id)

                        # Publish if requested
                        if publish:
                            pub_success = self.weapons_api.publish_item(weapon_id)
                            if pub_success:
                                status += " & Published"
                            else:
                                status += " (publish failed)"
                        else:
                            status += " (draft)"

                        print(f"       ✅ {status}")

        return weapon_ids, weapons
    
    def link_weapons_to_unit(self, unit_name: str, weapon_ids, weapons_data,
                             dry_run: bool = False, publish: bool = False, bulk: bool = False):
        """
        Link weapons to their parent unit via multi-reference field.
        Also sets unit-level target capabilities based on weapons.
        When bulk=True, queues the update for later batch execution.

        If weapon_ids is None, skip the link update entirely (signals a fetch
        failure upstream — we must not wipe existing links).
        """
        if weapon_ids is None:
            print(f"  ⏭️  Skipping link update for {unit_name} (fetch failed — existing links preserved)")
            return

        # Aggregate unit-level capabilities from weapons
        if weapon_ids:
            has_anti_surface = any(w.get('can_target_surface', False) for w in weapons_data)
            has_anti_air = any(w.get('can_target_air', False) for w in weapons_data)
            has_anti_sub = any(w.get('can_target_subs', False) for w in weapons_data)
        else:
            has_anti_surface = False
            has_anti_air = False
            has_anti_sub = False

        # Find unit in Webflow (use cache if available)
        if self._all_webflow_units_cache is not None:
            all_units = self._all_webflow_units_cache
        else:
            all_units = self.units_api.get_all_items()
        unit = None
        for u in all_units:
            if u.get('fieldData', {}).get('name', '') == unit_name:
                unit = u
                break

        if not unit:
            print(f"  ⚠️  Unit {unit_name} not found in Webflow - cannot link weapons")
            return

        # In bulk mode, resolve pending-create-xxx placeholders to actual IDs
        resolved_ids = []
        for wid in weapon_ids:
            if wid.startswith("pending-create-"):
                wname = wid.replace("pending-create-", "")
                actual_id = self._weapon_name_to_id.get(wname)
                if actual_id:
                    resolved_ids.append(actual_id)
                else:
                    # Will be resolved after flush; skip for now
                    resolved_ids.append(wid)
            else:
                resolved_ids.append(wid)

        caps = []
        if has_anti_surface:
            caps.append("Surface")
        if has_anti_air:
            caps.append("Air")
        if has_anti_sub:
            caps.append("Sub")

        if dry_run:
            print(f"  🔍 Would link {len(weapon_ids)} weapons to unit {unit_name}")
            print(f"     Anti-Surface: {has_anti_surface}, Anti-Air: {has_anti_air}, Anti-Sub: {has_anti_sub}")
            if publish:
                print(f"     📢 Would publish unit")
        elif bulk:
            update_data = {
                'attached-unit-weapons': resolved_ids,
                'can-target-surface': has_anti_surface,
                'can-target-air': has_anti_air,
                'can-target-subs': has_anti_sub,
            }
            self._pending_unit_updates.append({
                'id': unit['id'],
                'fieldData': update_data,
            })
            if publish:
                self._pending_unit_publish_ids.append(unit['id'])
            print(f"  📋 Queued link {len(resolved_ids)} weapons to unit")
            print(f"     Can target: {', '.join(caps) if caps else 'None'}")
        else:
            # Update unit with weapons reference AND target capabilities
            update_data = {
                'attached-unit-weapons': resolved_ids,
                'can-target-surface': has_anti_surface,
                'can-target-air': has_anti_air,
                'can-target-subs': has_anti_sub,
            }
            success = self.units_api.update_item(unit['id'], update_data)
            if success:
                print(f"  ✅ Linked {len(resolved_ids)} weapons to unit")
                print(f"     Can target: {', '.join(caps) if caps else 'None'}")
                if publish:
                    pub_ok = self.units_api.publish_item(unit['id'])
                    if pub_ok:
                        print(f"     📢 Unit published")
                    else:
                        print(f"     ⚠️  Unit publish failed")
    
    def cleanup_zero_damage_weapons(self, dry_run: bool = False):
        """
        Archive all weapons in Webflow that have zero damage in all categories.
        This cleans up weapons that were created before the zero-damage filter was added.
        """
        print("\n🧹 Cleaning up zero-damage weapons...")
        
        # Fetch all weapons from Webflow
        all_weapons = self.weapons_api.get_all_items()
        
        zero_damage_weapons = []
        for weapon in all_weapons:
            field_data = weapon.get('fieldData', {})
            
            # Check if all damage types are zero
            dmg_default = field_data.get('damage-default', 0)
            dmg_vtol = field_data.get('damage-vtol', 0)
            dmg_subs = field_data.get('damage-submarines', 0)
            dmg_commanders = field_data.get('damage-commanders', 0)
            
            total_damage = dmg_default + dmg_vtol + dmg_subs + dmg_commanders
            
            if total_damage == 0 and not weapon.get('isArchived', False):
                zero_damage_weapons.append(weapon)
        
        if not zero_damage_weapons:
            print("  ✅ No zero-damage weapons found")
            return
        
        print(f"  📦 Found {len(zero_damage_weapons)} zero-damage weapons to archive")
        
        if dry_run:
            for weapon in zero_damage_weapons:
                name = weapon.get('fieldData', {}).get('name', 'unknown')
                print(f"     🔍 Would archive: {name}")
        else:
            archived_count = 0
            for weapon in zero_damage_weapons:
                name = weapon.get('fieldData', {}).get('name', 'unknown')
                weapon_id = weapon.get('id')
                
                # Archive by setting isArchived = true
                try:
                    self.weapons_api._rate_limit()
                    url = f"{self.weapons_api.base_url}/collections/{self.weapons_api.collection_id}/items/{weapon_id}"
                    payload = {"isArchived": True}
                    response = requests.patch(url, headers=self.weapons_api.headers, json=payload)
                    response.raise_for_status()
                    
                    print(f"     📦 Archived: {name}")
                    archived_count += 1
                except Exception as e:
                    print(f"     ❌ Failed to archive {name}: {e}")
            
            print(f"  ✅ Archived {archived_count} zero-damage weapons")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Sync BAR weapons to Webflow')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without writing')
    parser.add_argument('--unit', type=str, help='Sync weapons for specific unit only')
    parser.add_argument('--all', action='store_true', help='Sync weapons for all units in Webflow')
    parser.add_argument('--faction', type=str, help='Sync weapons for all units of a faction (e.g. arm, cor, leg)')
    parser.add_argument('--mines', action='store_true', help='Sync mine units only (mine=true in customparams)')
    parser.add_argument('--publish', action='store_true', help='Publish weapons immediately (default: draft)')
    parser.add_argument('--cleanup', action='store_true', help='Archive zero-damage weapons from previous syncs')
    args = parser.parse_args()
    
    # Check for API token
    api_token = os.environ.get("WEBFLOW_API_TOKEN")
    if not api_token:
        print("❌ Error: WEBFLOW_API_TOKEN not found in environment")
        print("   Set it in .env file or export WEBFLOW_API_TOKEN=your-token")
        return
    
    print("=" * 80)
    print("BAR Weapon Sync to Webflow")
    print("=" * 80)
    print()
    
    if args.dry_run:
        print("🔍 DRY RUN MODE - No changes will be made to Webflow")
        print()
    
    if args.publish:
        print("📢 PUBLISH MODE - Weapons will be published immediately")
        print()
    
    # Initialize APIs
    rate_limiter = RateLimiter()
    weapons_api = WebflowAPI(api_token, WEBFLOW_SITE_ID, WEAPONS_COLLECTION_ID, rate_limiter)
    categories_api = WebflowAPI(api_token, WEBFLOW_SITE_ID, WEAPON_CATEGORIES_COLLECTION_ID, rate_limiter)
    units_api = WebflowAPI(api_token, WEBFLOW_SITE_ID, UNITS_COLLECTION_ID, rate_limiter)
    
    # Initialize sync service
    sync_service = WeaponSyncService(weapons_api, categories_api, units_api)
    
    # Load weapon categories
    sync_service.load_weapon_categories()
    print()
    
    # Run cleanup if requested
    if args.cleanup:
        sync_service.cleanup_zero_damage_weapons(dry_run=args.dry_run)
        if not args.unit and not args.all and not args.faction:
            # If only cleanup was requested, we're done
            print()
            print("=" * 80)
            print("Done!")
            print("=" * 80)
            return
        print()
    
    # Sync weapons
    if args.unit:
        # Single unit mode
        weapon_ids, weapons_data = sync_service.sync_weapons_for_unit(args.unit, dry_run=args.dry_run, publish=args.publish)
        sync_service.link_weapons_to_unit(args.unit, weapon_ids, weapons_data, dry_run=args.dry_run, publish=args.publish)
    elif args.faction:
        # Faction filter mode - reuse --all logic but filtered by prefix
        prefix = args.faction.strip().lower()

        # Pre-fetch caches for bulk mode
        sync_service.prefetch_caches()

        if sync_service._all_webflow_units_cache:
            active_units = [u for u in sync_service._all_webflow_units_cache if not u.get('isArchived', False)]
        else:
            all_units = sync_service.units_api.get_all_items()
            active_units = [u for u in all_units if not u.get('isArchived', False)]
        active_units = [u for u in active_units if u.get('fieldData', {}).get('name', '').lower().startswith(prefix)]
        print(f"  Found {len(active_units)} active '{prefix}*' units")
        print()

        success_count = 0
        skip_count = 0
        error_count = 0

        for idx, unit in enumerate(active_units, 1):
            unit_name = unit.get('fieldData', {}).get('name', '')
            if not unit_name:
                skip_count += 1
                continue
            print(f"[{idx}/{len(active_units)}] {unit_name}")
            try:
                weapon_ids, weapons_data = sync_service.sync_weapons_for_unit(
                    unit_name, dry_run=args.dry_run, publish=args.publish, bulk=True
                )
                sync_service.link_weapons_to_unit(
                    unit_name, weapon_ids, weapons_data,
                    dry_run=args.dry_run, publish=args.publish, bulk=True
                )
                if weapon_ids:
                    success_count += 1
                else:
                    skip_count += 1
            except Exception as e:
                print(f"  Error: {e}")
                error_count += 1
            print()

        # Flush all queued bulk operations
        if not args.dry_run:
            created, updated, published = sync_service.flush_bulk_operations(publish=args.publish)

        print("=" * 80)
        print(f"SUMMARY ({prefix.upper()})")
        print("=" * 80)
        print(f"Total units: {len(active_units)}")
        print(f"Synced        : {success_count}")
        print(f"Skipped (no weapons): {skip_count}")
        if error_count > 0:
            print(f"Errors        : {error_count}")
        if not args.dry_run:
            print(f"Bulk created  : {created}")
            print(f"Bulk updated  : {updated}")
            if args.publish:
                print(f"Bulk published: {published}")
    elif args.mines:
        # Mines-only mode: scan all active Webflow units, detect mines via GitHub file check
        print("💣 MINES + CRAWLING BOMBS MODE")
        print()

        # Pre-fetch caches for bulk mode
        sync_service.prefetch_caches()

        if sync_service._all_webflow_units_cache:
            active_units = [u for u in sync_service._all_webflow_units_cache if not u.get('isArchived', False)]
        else:
            all_units = sync_service.units_api.get_all_items()
            active_units = [u for u in all_units if not u.get('isArchived', False)]
        print(f"  Found {len(active_units)} active units — scanning for mines and crawling bombs...")
        print()

        github_token = os.environ.get("GITHUB_TOKEN")
        gh_headers = {'Authorization': f'token {github_token}'} if github_token else {}

        mine_units = []
        for unit in active_units:
            unit_name = unit.get('fieldData', {}).get('name', '')
            if not unit_name:
                continue
            url = (
                f"https://raw.githubusercontent.com/{GITHUB_REPO}"
                f"/refs/heads/{GITHUB_BRANCH}/units/{unit_name}.lua"
            )
            try:
                r = requests.get(url, headers=gh_headers, timeout=10)
                if r.status_code != 200:
                    continue
                clean = re.sub(r'--[^\n]*', '', r.text)
                cp_m = re.search(r'\bcustomparams\s*=\s*\{', clean, re.IGNORECASE)
                cp_block = None
                if cp_m:
                    cp_block = LuaParser.extract_balanced_braces(clean, cp_m.end() - 1)

                # Mine check
                if cp_block and re.search(r'\bmine\s*=\s*true', cp_block, re.IGNORECASE):
                    mine_units.append(unit_name)
                    print(f"  💣 Mine: {unit_name}")
                    continue

                # Crawling bomb check
                has_sdc0    = bool(re.search(r'\bselfdestructcountdown\s*=\s*0\b', clean, re.IGNORECASE))
                has_explo   = bool(cp_block and re.search(r'\bunitgroup\s*=\s*["\']+explo["\']+', cp_block, re.IGNORECASE))
                has_instant = bool(cp_block and re.search(r'\binstantselfd\s*=\s*true', cp_block, re.IGNORECASE))
                if has_sdc0 and has_explo and has_instant:
                    mine_units.append(unit_name)
                    print(f"  💥 Crawling bomb: {unit_name}")
                    continue

                # Spy bomb check (paralyze self-destruct)
                has_buildert2 = bool(cp_block and re.search(r'\bunitgroup\s*=\s*["\']+buildert2["\']+', cp_block, re.IGNORECASE))
                if has_sdc0 and has_buildert2:
                    mine_units.append(unit_name)
                    print(f"  🕵️  Spy bomb: {unit_name}")
                    continue

                # EMP building explosion (selfdestructas = "empblast")
                has_empblast = bool(re.search(r'\bselfdestructas\s*=\s*["\']?empblast["\']?', clean, re.IGNORECASE))
                if has_empblast:
                    mine_units.append(unit_name)
                    print(f"  💥 EMP building: {unit_name}")

            except Exception as e:
                print(f"  ⚠️  Could not check {unit_name}: {e}")

        print()
        print(f"  ✅ Found {len(mine_units)} explode units (mines + crawling bombs)")
        print()

        success_count = 0
        skip_count = 0
        error_count = 0

        for idx, unit_name in enumerate(mine_units, 1):
            print(f"[{idx}/{len(mine_units)}] {unit_name}")
            try:
                weapon_ids, weapons_data = sync_service.sync_weapons_for_unit(
                    unit_name, dry_run=args.dry_run, publish=args.publish, bulk=True
                )
                sync_service.link_weapons_to_unit(
                    unit_name, weapon_ids, weapons_data,
                    dry_run=args.dry_run, publish=args.publish, bulk=True
                )
                if weapon_ids:
                    success_count += 1
                else:
                    skip_count += 1
            except Exception as e:
                print(f"  ❌ Error: {e}")
                error_count += 1
            print()

        # Flush all queued bulk operations
        if not args.dry_run:
            created, updated, published = sync_service.flush_bulk_operations(publish=args.publish)

        print("=" * 80)
        print("MINES SUMMARY")
        print("=" * 80)
        print(f"Explode units found: {len(mine_units)}")
        print(f"✅ Synced        : {success_count}")
        print(f"⏭️  Skipped       : {skip_count}")
        if error_count > 0:
            print(f"❌ Errors        : {error_count}")
        if not args.dry_run:
            print(f"Bulk created  : {created}")
            print(f"Bulk updated  : {updated}")
            if args.publish:
                print(f"Bulk published: {published}")
    elif args.all:
        # All units mode - fetch from Webflow and sync each (bulk mode)

        # Pre-fetch caches for bulk mode (avoids re-fetching per unit)
        sync_service.prefetch_caches()

        if sync_service._all_webflow_units_cache:
            active_units = [u for u in sync_service._all_webflow_units_cache if not u.get('isArchived', False)]
        else:
            all_units = sync_service.units_api.get_all_items()
            active_units = [u for u in all_units if not u.get('isArchived', False)]

        print(f"  ✅ Found {len(active_units)} active units")
        print(f"  ℹ️  Tip: Run main unit sync first to ensure only build tree units are active")
        print()

        success_count = 0
        skip_count = 0
        error_count = 0

        for idx, unit in enumerate(active_units, 1):
            unit_name = unit.get('fieldData', {}).get('name', '')
            if not unit_name:
                skip_count += 1
                continue

            print(f"[{idx}/{len(active_units)}] {unit_name}")

            try:
                weapon_ids, weapons_data = sync_service.sync_weapons_for_unit(
                    unit_name,
                    dry_run=args.dry_run,
                    publish=args.publish,
                    bulk=True
                )

                sync_service.link_weapons_to_unit(
                    unit_name, weapon_ids, weapons_data,
                    dry_run=args.dry_run, publish=args.publish, bulk=True
                )
                if weapon_ids:
                    success_count += 1
                else:
                    skip_count += 1

            except Exception as e:
                print(f"  ❌ Error: {e}")
                error_count += 1

            print()  # Blank line between units

        # Flush all queued bulk operations
        if not args.dry_run:
            created, updated, published = sync_service.flush_bulk_operations(publish=args.publish)

        # Summary
        print("=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"Total units: {len(active_units)}")
        print(f"✅ Synced: {success_count}")
        print(f"⏭️  Skipped (no weapons): {skip_count}")
        if error_count > 0:
            print(f"❌ Errors: {error_count}")
        if not args.dry_run:
            print(f"Bulk created  : {created}")
            print(f"Bulk updated  : {updated}")
            if args.publish:
                print(f"Bulk published: {published}")
    else:
        print("ℹ️  No action specified. Use --unit, --all, --mines, or --cleanup")
        print("   Examples:")
        print("   python sync_weapons_to_webflow.py --unit armcom")
        print("   python sync_weapons_to_webflow.py --all")
        print("   python sync_weapons_to_webflow.py --all --publish")
        print("   python sync_weapons_to_webflow.py --mines")
        print("   python sync_weapons_to_webflow.py --mines --dry-run")
        print("   python sync_weapons_to_webflow.py --cleanup")
    
    print()
    print("=" * 80)
    print("Done!")
    print("=" * 80)


if __name__ == "__main__":
    main()
