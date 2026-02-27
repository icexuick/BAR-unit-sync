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
    """Simple rate limiter to stay under Webflow API limits."""
    
    def __init__(self, max_requests_per_minute: int = 60):
        self.max_requests = max_requests_per_minute
        self.requests = []
    
    def wait_if_needed(self):
        """Wait if we're approaching rate limit."""
        import time
        now = time.time()
        
        # Remove requests older than 60 seconds
        self.requests = [ts for ts in self.requests if now - ts < 60]
        
        if len(self.requests) >= self.max_requests:
            # Wait until oldest request expires
            sleep_time = 60 - (now - self.requests[0]) + 0.1
            if sleep_time > 0:
                print(f"  ⏱️  Rate limit - waiting {sleep_time:.1f}s...")
                time.sleep(sleep_time)
                self.requests = []
        
        self.requests.append(now)


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
    
    def get_all_items(self) -> List[Dict]:
        """Fetch all items from the collection."""
        items = []
        offset = 0
        limit = 100
        
        while True:
            try:
                self._rate_limit()
                
                url = f"{self.base_url}/collections/{self.collection_id}/items"
                params = {"offset": offset, "limit": limit}
                
                response = requests.get(url, headers=self.headers, params=params)
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
            self._rate_limit()
            
            url = f"{self.base_url}/collections/{self.collection_id}/items"
            
            payload = {
                "fieldData": field_data,
                "isDraft": is_draft
            }
            
            response = requests.post(url, headers=self.headers, json=payload)
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
            self._rate_limit()
            
            url = f"{self.base_url}/collections/{self.collection_id}/items/{item_id}"
            
            payload = {
                "fieldData": field_data
            }
            
            response = requests.patch(url, headers=self.headers, json=payload)
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
            self._rate_limit()
            
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
            print(f"  ❌ Error publishing item {item_id}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"     Response: {e.response.text}")
            return False


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
            wcp_match = re.search(r'\bcustomparams\s*=\s*\{', wblock, re.IGNORECASE)
            if wcp_match:
                wcp_block = LuaParser.extract_balanced_braces(wblock, wcp_match.end() - 1)
                if wcp_block:
                    # Bogus flag
                    if re.search(r'\bbogus\s*=\s*1', wcp_block, re.IGNORECASE):
                        is_bogus = True
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
                
                # Damage stats
                'dps': 0,  # Calculated later
                'damage_default': damage['default'],
                'damage_commanders': damage['commanders'],
                'damage_vtol': damage['vtol'],
                'damage_subs': damage['subs'],
                
                # Target capabilities (detected from damage types)
                'can_target_surface': damage['default'] > 0,  # Has default damage
                'can_target_air': damage['vtol'] > 0,  # Has VTOL damage
                'can_target_subs': damage['subs'] > 0,  # Has submarine damage
                
                # Weapon stats
                'reload_time': round(_val(wblock, 'reloadtime', float) or 0, 5),
                'range': int(_val(wblock, 'range', float) or 0),
                'accuracy': int(_val(wblock, 'accuracy', float) or 0),
                'area_of_effect': int(_val(wblock, 'areaofeffect', float) or 0),
                'edge_effectiveness': round(_val(wblock, 'edgeeffectiveness', float) or 1.0, 2),
                'impulse': round(_val(wblock, 'impulsefactor', float) or 0, 2),
                
                # Projectile stats
                'projectiles': int(_val(wblock, 'projectiles', float) or 1),
                'velocity': int(_val(wblock, 'weaponvelocity', float) or 0),
                'burst': int(_val(wblock, 'burst', float) or 1),
                
                # Cost stats
                'energy_per_shot': int(_val(wblock, 'energypershot', float) or 0),
                'metal_per_shot': int(_val(wblock, 'metalpershot', float) or 0),
                
                # Stockpile
                'stockpile': _bool(wblock, 'stockpile'),
                'stockpile_limit': stockpile_limit,
                'stockpile_time': int(_val(wblock, 'stockpiletime', float) or 0),
                
                # Special properties
                'impact_only': _bool(wblock, 'impactonly'),
                'paralyzer': _bool(wblock, 'paralyzer'),
                'paralyze_duration': int(_val(wblock, 'paralyzetime', float) or 0),
                'homing': _bool(wblock, 'tracks'),
                'turn_rate': int(_val(wblock, 'turnrate', float) or 0),
                'water_weapon': _bool(wblock, 'waterweapon'),
                
                # Visual
                'color': WeaponParser.parse_rgb_color(wblock) or '#ffffff',
                
                # For DPS calculation
                '_salvosize': int(_val(wblock, 'salvosize', float) or 1),
            }
            
            weapondefs_dict[weapondef_key.upper()] = weapon_data
        
        # Now parse weapons = {} to find which weapondefs are actually used and count them
        weapon_counts = {}
        w_match = re.search(r'\bweapons\s*=\s*\{', unit_block, re.IGNORECASE)
        if w_match:
            w_block = LuaParser.extract_balanced_braces(unit_block, w_match.end() - 1)
            if w_block:
                for dm in re.finditer(r'\bdef\s*=\s*["\']?(\w+)["\']?', w_block, re.IGNORECASE):
                    weapondef_name = dm.group(1).upper()
                    weapon_counts[weapondef_name] = weapon_counts.get(weapondef_name, 0) + 1
        
        # Build final weapons list with counts and DPS
        for weapondef_key, count in weapon_counts.items():
            if weapondef_key not in weapondefs_dict:
                continue
            
            weapon = weapondefs_dict[weapondef_key].copy()
            weapon['weapon_count'] = count
            
            # Skip DPS/DOT calculation for paralyzer weapons (EMP)
            # They do paralysis, not real damage
            if weapon.get('paralyzer', False):
                weapon['dps'] = 0
                weapon['dot'] = 0
                # Continue to include weapon in list (but with 0 DPS)
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
                
                # Calculate main projectile DPS (without DOT)
                if dmg > 0 and weapon['reload_time'] > 0:
                    main_dps = (dmg * (1.0 / weapon['reload_time'])) * weapon['_salvosize'] * weapon['burst'] * weapon['projectiles']
                    weapon['dps'] = int(round(main_dps))
                    weapon['dot'] = int(round(dot_dps)) if dot_dps > 0 else 0
                else:
                    weapon['dps'] = 0
                    weapon['dot'] = 0
            
            # Skip weapons with no damage at all (default, vtol, subs, commanders all zero)
            total_damage = weapon['damage_default'] + weapon['damage_vtol'] + weapon['damage_subs'] + weapon['damage_commanders']
            if total_damage <= 0:
                continue
            
            # Skip if bogus flag is set OR weapon name contains 'bogus' or 'mine'
            if weapon.get('_is_bogus', False):
                continue
            if 'bogus' in weapon['weapondef_key'].lower() or 'mine' in weapon['weapondef_key'].lower():
                continue
            
            weapons.append(weapon)
        
        return weapons


# ══════════════════════════════════════════════════════════════════════════════
# WEAPON CATEGORY DETECTION
# ══════════════════════════════════════════════════════════════════════════════

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
        
        # Anti-Nuke detection (CHECK FIRST - highest priority)
        # Multiple indicators: interceptor flag, name indicators
        is_antinuke = False
        if weapon.get('_interceptor', False):
            is_antinuke = True
        if 'interceptor' in full_name or 'intercepting' in full_name:
            is_antinuke = True
        
        if is_antinuke:
            return self.category_map.get('anti-nuke')
        
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
        # Name "sniper"/"snipe" in weapondef key or full_name is an extra signal
        is_sniper = False
        if weapon.get('impact_only', False) and weapon.get('velocity', 0) > 2500 and weapon.get('damage_default', 0) >= 2000:
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
        
        # BeamLaser (both continuous and regular go to beam-laser)
        # This must come AFTER heat ray check!
        if wtype == 'BeamLaser':
            return self.category_map.get('beam-laser')
        
        # Missiles
        if wtype == 'MissileLauncher':
            return self.category_map.get('missile-launcher')
        
        # Vertical Rocket Launcher (StarburstLauncher)
        # This comes AFTER anti-nuke check!
        if wtype == 'StarburstLauncher':
            return self.category_map.get('vertical-rocket-launcher')
        
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
        
        # LaserCannon (regular - after railgun and sea laser checks)
        if wtype == 'LaserCannon':
            return self.category_map.get('laser-cannon')
        
        # LightningCannon
        if wtype == 'LightningCannon':
            return self.category_map.get('lightning-cannon')
        
        # TorpedoLauncher
        if wtype == 'TorpedoLauncher':
            return self.category_map.get('torpedo-launcher')
        
        # Melee
        if wtype == 'Melee':
            return self.category_map.get('melee')
        
        # AircraftBomb
        if wtype == 'AircraftBomb':
            return self.category_map.get('aircraft-bomb')
        
        # D-Gun (special weapon)
        if 'disintegrator' in weapondef_key:
            return self.category_map.get('d-gun')
        
        # Default: None (no category)
        return None


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
    
    def fetch_unit_file(self, unit_name: str) -> Optional[str]:
        """Fetch a single unit .lua file from GitHub by searching recursively."""
        try:
            github_token = os.environ.get("GITHUB_TOKEN")
            headers = {}
            if github_token:
                headers['Authorization'] = f'token {github_token}'
            
            # First try: direct path (most common)
            direct_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/refs/heads/{GITHUB_BRANCH}/units/{unit_name}.lua"
            response = requests.get(direct_url, headers=headers)
            if response.status_code == 200:
                return response.text
            
            # Second try: search through units directory using GitHub API
            print(f"  🔍 Searching for {unit_name}.lua in repository...")
            search_url = f"https://api.github.com/repos/{GITHUB_REPO}/git/trees/{GITHUB_BRANCH}?recursive=1"
            response = requests.get(search_url, headers=headers)
            
            if response.status_code == 200:
                tree = response.json()
                # Find the file in the tree
                for item in tree.get('tree', []):
                    if item['path'].endswith(f'/{unit_name}.lua') or item['path'] == f'units/{unit_name}.lua':
                        # Found it! Fetch the file
                        file_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/refs/heads/{GITHUB_BRANCH}/{item['path']}"
                        file_response = requests.get(file_url, headers=headers)
                        if file_response.status_code == 200:
                            print(f"  ✅ Found at: {item['path']}")
                            return file_response.text
            
            print(f"  ⚠️  Could not find unit file for {unit_name}")
            return None
            
        except Exception as e:
            print(f"  ❌ Error fetching unit file: {e}")
            return None
    
    def sync_weapons_for_unit(self, unit_name: str, dry_run: bool = False, publish: bool = False) -> Tuple[List[str], List[Dict]]:
        """
        Sync all weapons for a single unit.
        Returns tuple of (weapon_ids, weapons_data).
        
        Args:
            unit_name: Name of the unit to sync weapons for
            dry_run: If True, preview changes without making them
            publish: If True, publish weapons immediately (default: draft)
        """
        print(f"\nProcessing unit: {unit_name}")
        
        # Fetch unit file
        unit_content = self.fetch_unit_file(unit_name)
        if not unit_content:
            return [], []
        
        # Parse weapons
        weapons = WeaponParser.parse_weapondefs(unit_content, unit_name)
        
        if not weapons:
            print(f"  ℹ️  No weapons found")
            return [], []
        
        print(f"  📊 Found {len(weapons)} weapons")
        
        # Fetch existing weapons from Webflow
        existing_weapons = self.weapons_api.get_all_items()
        existing_lookup = {
            item.get('fieldData', {}).get('name', ''): item
            for item in existing_weapons
        }
        
        weapon_ids = []
        
        for weapon in weapons:
            weapon_name = weapon['name']
            
            # Show DOT info if present
            dot_info = ""
            if weapon.get('dot', 0) > 0:
                dot_info = f" [DOT: {weapon['dot']}]"
            
            print(f"    🔫 {weapon_name} (count: {weapon['weapon_count']}, DPS: {weapon['dps']}{dot_info})")
            
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
                
                # Damage
                'damage-default': weapon['damage_default'],
                'damage-commanders': weapon['damage_commanders'],
                'damage-vtol': weapon['damage_vtol'],
                'damage-submarines': weapon['damage_subs'],  # NOT damage-subs
                
                # Cost
                'energy-per-shot': weapon['energy_per_shot'],
                'metal-per-shot': weapon['metal_per_shot'],
                
                # Stockpile
                'stockpile': weapon['stockpile'],
                'stockpile-time': weapon['stockpile_time'],
                
                # Special
                'paralyzer': weapon['paralyzer'],
                'paralyse-duration': weapon['paralyze_duration'],  # NOT paralyze-duration (British spelling!)
                'homing': weapon['homing'],
                'turnrate': weapon['turn_rate'],  # NOT turn-rate (no dash)
                'waterweapon': weapon['water_weapon'],  # NOT water-weapon (no dash)
                
                # Target capabilities
                'can-target-surface': weapon['can_target_surface'],
                'can-target-air': weapon['can_target_air'],
                'can-target-subs': weapon['can_target_subs'],
                
                # Visual
                'color': weapon['color'],
            }
            
            # Add category if detected
            if category_id:
                field_data['weapon-category'] = category_id
            
            # Add stockpile limit if present
            if weapon['stockpile_limit'] is not None:
                field_data['stockpile-limit'] = weapon['stockpile_limit']
            
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
            else:
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
    
    def link_weapons_to_unit(self, unit_name: str, weapon_ids: List[str], weapons_data: List[Dict], dry_run: bool = False):
        """
        Link weapons to their parent unit via multi-reference field.
        Also sets unit-level target capabilities based on weapons.
        """
        if not weapon_ids:
            return
        
        # Aggregate unit-level capabilities from weapons
        has_anti_surface = any(w.get('can_target_surface', False) for w in weapons_data)
        has_anti_air = any(w.get('can_target_air', False) for w in weapons_data)
        has_anti_sub = any(w.get('can_target_subs', False) for w in weapons_data)
        
        # Fetch unit from Webflow
        units = self.units_api.get_all_items()
        unit = None
        for u in units:
            if u.get('fieldData', {}).get('name', '') == unit_name:
                unit = u
                break
        
        if not unit:
            print(f"  ⚠️  Unit {unit_name} not found in Webflow - cannot link weapons")
            return
        
        if dry_run:
            print(f"  🔍 Would link {len(weapon_ids)} weapons to unit {unit_name}")
            print(f"     Anti-Surface: {has_anti_surface}, Anti-Air: {has_anti_air}, Anti-Sub: {has_anti_sub}")
        else:
            # Update unit with weapons reference AND target capabilities
            update_data = {
                'attached-unit-weapons': weapon_ids,
                'can-target-surface': has_anti_surface,
                'can-target-air': has_anti_air,
                'can-target-subs': has_anti_sub,
            }
            success = self.units_api.update_item(unit['id'], update_data)
            if success:
                caps = []
                if has_anti_surface:
                    caps.append("Surface")
                if has_anti_air:
                    caps.append("Air")
                if has_anti_sub:
                    caps.append("Sub")
                print(f"  ✅ Linked {len(weapon_ids)} weapons to unit")
                print(f"     Can target: {', '.join(caps) if caps else 'None'}")
    
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
        if not args.unit and not args.all:
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
        sync_service.link_weapons_to_unit(args.unit, weapon_ids, weapons_data, dry_run=args.dry_run)
    elif args.all:
        # All units mode - fetch from Webflow and sync each
        print("📦 Fetching all units from Webflow...")
        all_units = sync_service.units_api.get_all_items()
        
        # Filter to non-archived units only
        active_units = [u for u in all_units if not u.get('isArchived', False)]
        
        # Note: This syncs ALL active units in Webflow
        # If you only want build tree units, the main unit sync should have already
        # archived non-buildable units, so active units = build tree units
        
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
                    publish=args.publish
                )
                
                if weapon_ids:
                    sync_service.link_weapons_to_unit(unit_name, weapon_ids, weapons_data, dry_run=args.dry_run)
                    success_count += 1
                else:
                    # No weapons found (builders, eco, etc.)
                    skip_count += 1
                
            except Exception as e:
                print(f"  ❌ Error: {e}")
                error_count += 1
            
            print()  # Blank line between units
        
        # Summary
        print("=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"Total units: {len(active_units)}")
        print(f"✅ Synced: {success_count}")
        print(f"⏭️  Skipped (no weapons): {skip_count}")
        if error_count > 0:
            print(f"❌ Errors: {error_count}")
    else:
        print("ℹ️  No action specified. Use --unit, --all, or --cleanup")
        print("   Examples:")
        print("   python sync_weapons_to_webflow.py --unit armcom")
        print("   python sync_weapons_to_webflow.py --all")
        print("   python sync_weapons_to_webflow.py --all --publish")
        print("   python sync_weapons_to_webflow.py --cleanup")
    
    print()
    print("=" * 80)
    print("Done!")
    print("=" * 80)


if __name__ == "__main__":
    main()
