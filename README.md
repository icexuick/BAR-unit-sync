# Beyond All Reason — GitHub to Webflow Unit Sync 🔄

[![License: BAR Only](https://img.shields.io/badge/License-BAR%20Only-red.svg)](LICENSE)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)

Automatically sync unit and weapon data from the [Beyond All Reason GitHub repository](https://github.com/beyond-all-reason/Beyond-All-Reason) to your Webflow CMS collections.

---

## 🎯 Features

### Unit Sync (`sync_units_github_to_webflow.py`)
- ✅ Syncs **42 fields** total: 22 direct stats + 14 computed + 4 references + 2 images
- ✅ **Auto-creates new units** as drafts in Webflow when they don't exist yet
- ✅ Only syncs **buildable units** (recursive tree from commanders: armcom, corcom, legcom)
- ✅ **Scavenger mode** — sync all 88 unique Scavenger units separately (`--scavengers`)
- ✅ Scavenger units are **protected from archiving** during regular syncs
- ✅ **Auto-archives** units not in commander tree or Scavenger set
- ✅ Calculates **DPS** using BAR's official formula (damage × reload × salvo × burst × projectiles)
- ✅ Detects **weapon types** and filters out zero-damage/unequipped/detonator weapons
- ✅ Detects **special abilities** (Radar, Stealth, Shield, Transport, Resurrector, etc.)
- ✅ Detects **faction** (Armada, Cortex, Legion, CHICKS, SCAV) from filename prefix or path
- ✅ Detects **unit type** (Aircraft, Bot, Vehicle, Ship, Hovercraft, Building, Defense, Factory, Chicken)
- ✅ Detects **amphibious** units using BAR's official `alldefs_post.lua` logic
- ✅ Computes **transport compatibility** — ships and subs are never transportable, hovercraft are
- ✅ Resolves **buildoptions** to Webflow item IDs (multi-reference field)
- ✅ Syncs **unit names and tooltips** from `language/en/units.json`
- ✅ Syncs **strategic icons** (PNG → WebP) and **buildpics** (DDS → WebP) via GitHub hosting
- ✅ Updates **only fields that have changed** — skips untouched units
- ✅ `--force` mode to overwrite all units regardless of changes
- ✅ **Lua comment stripping** — `--` commented values are correctly ignored everywhere
- ✅ Default `techlevel = 1` for units without an explicit tech level

### Weapon Sync (`sync_weapons_to_webflow.py`)
- ✅ Syncs each weapon as a separate CMS item with full stats and auto-detected category
- ✅ **37 weapon categories** auto-detected (see [WEAPONS_README.md](WEAPONS_README.md))
- ✅ Detects **mines, crawling bombs, spy bombs, and EMP buildings** from external weapon files
- ✅ Prefers `selfdestructas` over `explodeas` for correct alpha damage
- ✅ Splits **missiles** into homing (Missile Launcher) vs unguided (Rocket Launcher)
- ✅ Splits **torpedoes** into homing (Torpedo Launcher) vs unguided (Dumb-fire Torpedo)
- ✅ Detects **Trigger EMP** (paralyzer explosions) vs **Trigger Explosive** (damage explosions)
- ✅ Detects **Disintegrator Cannon** (DGun weapontype, non-disintegrator beam)
- ✅ Special detection for **Eradicator Heat Ray** → Thermal Ordnance Generator category
- ✅ Calculates **DPS, PPS** (paralyze per second), and **DOT** (damage over time)
- ✅ Supports **drone carriers** — fetches drone DPS and links carried unit

---

## 📊 Synced Unit Fields

### Direct fields (from `.lua` unit file)

| Lua field | Webflow slug | Type |
|---|---|---|
| `energycost` | `energy-cost` | Number |
| `metalcost` | `metal-cost` | Number |
| `buildtime` | `build-cost` | Number |
| `energymake` | `energy-make` | Number |
| `workertime` | `buildpower` | Number |
| `health` | `health` | Number |
| `maxvelocity` | `speed` | Number |
| `sightdistance` | `sightrange` | Number |
| `radardistance` | `radarrange` | Number |
| `sonardistance` | `metal-make` | Number (Sonarrange) |
| `jammerdistance` | `jammerrange` | Number |
| `mass` | `mass` | Number |
| `metalmake` | `metal-create` | Number (passive metal income) |
| `metalstorage` | `metal-storage` | Number |
| `energystorage` | `energy-storage` | Number |
| `seismicdistance` | `seismic-detector-range` | Number (stealth detector radius) |
| `cloakcost` | `cloak-cost` | Number |
| `cloakcostmoving` | `cloak-cost-moving` | Number |
| `customparams.paralyzemultiplier` | `paralyze-multiplier` | Number |
| `customparams.techlevel` | `techlevel` | Number (defaults to 1) |
| `customparams.energyconv_capacity` | `converter-metal-make` | Number (metal maker output) |
| `customparams.energyconv_efficiency` | `converter-efficiency` | Number (metal maker efficiency) |

### Derived / computed fields

| Field | Webflow slug | Type | How it's computed |
|---|---|---|---|
| Unit display name | `unitname` | PlainText | From `language/en/units.json` |
| Tooltip | `tooltip` | PlainText | From `language/en/units.json` |
| Faction | `faction-ref` | Reference | Filename prefix or Scavengers path → SCAV |
| Unit Type | `unittype` | Reference | Detected from `movementclass`, `canfly`, speed, builder flags, weapondefs |
| Amphibious | `amphibious` | Switch | Based on BAR's `alldefs_post.lua` movement class lists |
| Is Scavenger | `is-scavenger` | Switch | `true` if unit file is in `units/Scavengers/` |
| Buildoptions | `buildoptions-ref` | MultiReference | Units this unit can build, resolved to Webflow item IDs |
| Transportable By | `transportable-by` | MultiReference | Same-faction transports that can carry this unit |
| DPS | `dps` | Number | `(max(dmg_vtol, dmg_default) × (1/reload)) × salvosize × burst × projectiles` |
| Weapon Range | `weaponrange` | Number | Highest range across all equipped non-bogus weapons |
| Weapons | `weapons` | PlainText | e.g. `LaserCannon, 2x MissileLauncher, EMP-BeamLaser` |
| Stockpile Limit | `stockpile-limit` | Number | From `weapondefs.<n>.customparams.stockpilelimit` |
| Max Impulse | `weapon-max-impulse` | Number | Highest `impulsefactor` from damage-dealing weapons |
| Max Area of Effect | `weapon-area-of-effect` | Number | Highest `areaofeffect` from damage-dealing weapons |
| Specials | `specials` | PlainText | Comma-separated special abilities (see below) |

### Images (synced via GitHub hosting)

| Field | Webflow slug | Type | Source | Format |
|---|---|---|---|---|
| Strategic Icon | `icon` | Image | `icons/*.png` from BAR repo | PNG → WebP (80%) |
| BuildPic In-game | `buildpic-in-game` | Image | `unitpics/*.dds` from BAR repo | DDS → WebP (80%) |

### Specials detection

| Special | Condition |
|---|---|
| Cloakable | `cloakcost > 0` |
| Stealth | `stealth = true` OR `sonarstealth = true` |
| Radar | `radardistance > 0` |
| Sonar | `sonardistance > 0` |
| Jammer | `radardistancejam > 0` |
| Shield | `customparams.shield_power > 0` OR `customparams.shield_radius > 0` |
| Resurrector | `canresurrect = true` |
| Capturer | `cancapture = true` |
| Transport | `transportsize > 0` |
| Stealth Detector | `seismicdistance > 0` (passive underground detection radius) |

---

## 🚀 Quick Start

### Prerequisites

- Python 3.7 or higher
- Webflow API token with write access to the CMS
- GitHub token (for buildpic and optional icon sync)
- Internet connection

### Installation

1. **Download and unzip** this repository, or clone it:
   ```bash
   git clone https://github.com/your-username/bar-unit-sync.git
   cd bar-unit-sync
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set your API tokens:**
   ```bash
   # Option A: environment variables
   export WEBFLOW_API_TOKEN="your-webflow-token"
   export GITHUB_TOKEN="your-github-token"

   # Option B: .env file (recommended)
   cp .env.example .env
   # Edit .env and fill in your tokens
   ```

### Usage

**Dry run — preview changes without writing anything (recommended first):**
```bash
python sync_units_github_to_webflow.py --dry-run
```

**Test a single unit:**
```bash
python sync_units_github_to_webflow.py --unit armzeus --dry-run
python sync_units_github_to_webflow.py --unit armzeus --publish
```

**Full sync (includes buildpics automatically):**
```bash
python sync_units_github_to_webflow.py --publish
```

**Sync with strategic icons:**
```bash
python sync_units_github_to_webflow.py --sync-icons --publish
```

**Sync only one faction:**
```bash
python sync_units_github_to_webflow.py --faction arm --publish
```

**Sync Scavenger units:**
```bash
python sync_units_github_to_webflow.py --scavengers --publish
python sync_units_github_to_webflow.py --scavengers --sync-icons --publish
```

**Force overwrite all units (skip change detection):**
```bash
python sync_units_github_to_webflow.py --force --publish
```

**Clear cache and force a full re-fetch from GitHub:**
```bash
python sync_units_github_to_webflow.py --clear-cache
```

**Sync weapons for all units:**
```bash
python sync_weapons_to_webflow.py --all --publish
```

**Sync weapons for a single unit:**
```bash
python sync_weapons_to_webflow.py --unit armcom --publish
```

---

## 📖 Command Line Reference — Units sync

| Flag | Description |
|---|---|
| `--dry-run` | Preview changes without updating Webflow |
| `--unit NAME` | Sync only one specific unit, e.g. `--unit armzeus` |
| `--faction NAME` | Sync only units from one faction, e.g. `--faction arm` |
| `--scavengers` | Sync Scavenger units (`units/Scavengers/`) instead of regular buildable units |
| `--force` | Overwrite all units in Webflow even if unchanged |
| `--publish` | Automatically publish updated items after sync |
| `--sync-icons` | Also sync strategic icons (PNG → WebP, requires icontypes.lua parsing) |
| `--clear-cache` | Delete local cache files and re-fetch everything from GitHub |
| `--token TOKEN` | Provide Webflow API token via command line |
| `--help` | Show help message |

## 📖 Command Line Reference — Weapons sync

| Flag | Description |
|---|---|
| `--dry-run` | Preview changes without updating Webflow |
| `--unit NAME` | Sync weapons for one specific unit, e.g. `--unit armcom` |
| `--all` | Sync weapons for all units in Webflow |
| `--mines` | Sync mine/crawling bomb/spy/EMP-building units only |
| `--publish` | Automatically publish weapons immediately after sync |
| `--cleanup` | Archive zero-damage weapons from previous syncs |
| `--help` | Show help message |

---

## 💾 Cache System

The script uses two local cache files to avoid re-downloading data on every run.

### `.unit_cache.json`
Caches individual unit `.lua` file contents fetched from GitHub.
- **First run**: downloads each file on demand
- **Subsequent runs**: loads from cache instantly ⚡

### `.buildable_cache.json`
Built once by downloading the entire BAR repository as a ZIP archive and scanning all unit files. Stores:

- `buildable` — set of unit names **reachable from commanders** via recursive build tree
- `buildoptions_map` — maps each unit name to the list of units it can build
- `transportable_by_map` — maps each unit to compatible same-faction transports

> The script only syncs **buildable units** — those reachable from the three faction commanders (armcom, corcom, legcom) through their build chains. Scavenger units are synced separately with `--scavengers`. Units not in either set are automatically excluded.

**When to clear cache:**
```bash
# After BAR updates that add new units or change buildoptions
python sync_units_github_to_webflow.py --clear-cache
```

Cache is automatically invalidated if the repository or branch changes.

---

## 🔫 Weapon Parsing

Weapons are read directly from the `weapondefs` and `weapons` blocks in each unit's `.lua` file. Only weapons listed in `weapons = { [1] = { def = "..." }, ... }` are processed — unused or backup definitions in `weapondefs` that are not assigned are ignored.

**DPS formula** (mirrors BAR's in-game Lua scripts):
```
dps += (max(dmg_vtol, dmg_default) × (1 / reloadtime)) × salvosize × burst × projectiles
```

The formula uses `dmg_vtol` when it is higher than `dmg_default`, matching the game's own logic for anti-air optimised weapons.

**Sweepfire multiplier:** if `customparams.sweepfire = N` is set on a weapondef, `dmg_default` is multiplied by N before DPS is calculated. This represents weapons that fire N simultaneous beams.

**A weapon is skipped if:**
- Its `name` field contains `bogus` or `mine` (always placeholders)
- Both `dmg_default` and `dmg_vtol` are zero or absent
- `customparams.bogus = 1` (bogus flag in customparams)
- `customparams.smart_backup = true` (alternative fire mode — excluded from unit DPS total)
- Its weapondef key contains `detonator` (contact-trigger on crawling bombs, `range = 1`)

**EMP / paralyzer weapons** (`paralyzer = true`) appear in the `weapons` field with an `EMP-` prefix (e.g. `EMP-BeamLaser`) but do **not** contribute to the DPS value — they paralyse, not damage.

**Commented-out Lua values** (e.g. `-- burst = 3`) are stripped before parsing across both scripts, so they are correctly ignored in all calculations.

---

## 🚢 Transport Compatibility

The sync automatically computes which transports can carry which units based on BAR's engine rules:

- **Ships and submarines** (movementclass contains `BOAT`, `SHIP`, `UBOAT`, or `SUB`) → **never transportable**
- **Aircraft** (`canfly = true`) → **never transportable** (BAR has `transportAir` disabled)
- **Hovercraft** → **transportable** (treated as ground units)
- **Structures** → **not transportable** by default (unless `cantbetransported = false`)
- **Mobile ground units** → **transportable** by default

Only same-faction transports are matched (arm↔arm, cor↔cor, leg↔leg). The result is stored in the `transportable-by` multi-reference field.

---

## 🎨 Image Sync (Icons + Buildpics)

The script syncs two types of images by converting them to WebP and committing them to your GitHub repository for public hosting.

### Buildpics (always enabled)
Buildpics are **always** synced when `GITHUB_TOKEN` is set — no extra flag needed.

**How it works:**
1. Reads `buildpic = "armflea.dds"` from unit lua file
2. Downloads DDS from BAR's `unitpics/` folder (case-insensitive, always uses lowercase)
3. Converts DDS → WebP at 80% quality
4. Commits to your repo at `buildpics/<unitname>.webp`
5. Sets the raw GitHub URL in Webflow's `buildpic-in-game` field

### Strategic Icons (opt-in via --sync-icons)
Icons require the `--sync-icons` flag and parse `icontypes.lua` from the BAR repository.

**How it works:**
1. Parses `icontypes.lua` from BAR repo to find each unit's icon path
2. Downloads PNG from the BAR GitHub repo
3. Converts PNG → WebP at 80% quality (transparency preserved)
4. Commits the WebP to your repo at `icons/<unitname>.webp`
5. Sets the raw GitHub URL in Webflow's `icon` field

### Optimization

The sync automatically **skips re-uploading** files that already exist with the same size:
- Compares file size on GitHub vs new WebP file
- Skips commit if sizes match (no changes)
- Only uploads when size differs or file is new

### Setup — add to `.env`:
```
GITHUB_TOKEN=ghp_your_token_here
ICON_REPO_OWNER=your-github-username
ICON_REPO_NAME=bar-unit-sync
ICON_BRANCH=main
```

> The GitHub token needs `repo` scope (full control). Create one at [github.com/settings/tokens](https://github.com/settings/tokens).

---

## 🔄 Auto-Archive Non-Buildable Units

The sync automatically manages published status based on the commander build tree:

- Scans all Webflow items for units **not** in the commander tree and **not** in the Scavenger set
- If found: automatically archives the item
- Scavenger units are always **protected** from archiving, even during regular syncs

> In `--scavengers` mode, the archive step is skipped entirely.

---

## 🆕 Creating New Units

When the script encounters a buildable unit that doesn't exist in Webflow yet, it automatically creates it:

- ✅ Creates as **draft** (not published)
- ✅ Fills in all available fields
- ✅ Adds to `_webflow_id_map` so buildoptions work in the same run

---

## 🤖 Automation

### GitHub Actions

Five pre-configured workflows are included in `.github/workflows/`:

| Workflow | Trigger | Description |
|---|---|---|
| `sync-units-changed.yml` | Weekly (Mon 3:00 UTC) + manual | Units sync — only new/changed |
| `sync-units-overwrite.yml` | Manual only | Units sync — force overwrite all (`--force`) |
| `sync-units-icons.yml` | Manual only | Units + strategic icons — force overwrite |
| `sync-weapons-changed.yml` | Weekly (Mon 4:00 UTC) + manual | Weapons sync — all units |
| `sync-weapons-overwrite.yml` | Manual only | Weapons sync — all units |

Run any workflow from **GitHub → Actions tab → select workflow → Run workflow**.

All workflows support a `dry_run` toggle (preview without writing) and a `publish` toggle.

Add `WEBFLOW_API_TOKEN` to **Settings → Secrets and variables → Actions** in your repository.

### Recommended sync order

1. **Units first** — `python sync_units_github_to_webflow.py --publish`
2. **Scavengers** — `python sync_units_github_to_webflow.py --scavengers --publish`
3. **Weapons last** — `python sync_weapons_to_webflow.py --all --publish`

Weapons link back to unit items, so units must exist in Webflow first.

---

## 🔧 Configuration

The main constants are near the top of `sync_units_github_to_webflow.py`:

```python
GITHUB_REPO           = "beyond-all-reason/Beyond-All-Reason"
GITHUB_BRANCH         = "master"
GITHUB_UNITS_PATH     = "units"
WEBFLOW_SITE_ID       = "5c68622246b367adf6f3041d"
WEBFLOW_COLLECTION_ID = "6564c6553676389f8ba45a9e"
```

**Commander seeds** — the recursive tree starts from these three units:
```python
COMMANDERS = {"armcom", "corcom", "legcom"}
```

**Faction map** — maps filename prefix to Webflow reference item ID:
```python
FACTION_MAP = {
    "arm":    {"name": "Armada",  "id": "..."},
    "cor":    {"name": "Cortex",  "id": "..."},
    "leg":    {"name": "Legion",  "id": "..."},
    "raptor": {"name": "CHICKS",  "id": "..."},
    "scav":   {"name": "SCAV",    "id": "..."},
}
```

Scavenger units are always assigned the **SCAV** faction based on their file path (`units/Scavengers/`), regardless of their name prefix.

---

## 🐛 Troubleshooting

**"Error: Webflow API token required"**
Set `WEBFLOW_API_TOKEN` as an environment variable or use `--token`.

**"Unit 'xyz' not found in Webflow"**
This unit will be **automatically created as draft** in Webflow on the next run (not in `--dry-run` mode).

**"Field not described in schema: undefined"**
A field slug in the script doesn't match what's in Webflow. Open the Webflow CMS collection settings and verify the slug for that field.

**"404 Not Found" for buildpic DDS file**
The DDS filename in the unit lua uses uppercase (e.g. `ARMFLEA.DDS`) but GitHub filenames are lowercase. The script automatically converts to lowercase, but if you still get 404s, the file may not exist in BAR's `unitpics/` folder.

**"Archive download failed"**
The buildable cache couldn't be built. Check your internet connection. Run `--clear-cache` to retry.

**Icons/buildpics not appearing in Webflow**
- Raw GitHub URLs can take a few minutes to become accessible after the first commit
- Verify the file exists at `https://github.com/YOUR_USERNAME/YOUR_REPO/tree/main/icons` or `buildpics/`
- Make sure `GITHUB_TOKEN` is set correctly

**Rate limits**
The script has a built-in rate limiter (110 requests/minute). If you still hit limits, reduce the request rate in the `RateLimiter` class or run the sync less frequently.

---

## 🔒 Security

- ⚠️ Never commit your `.env` file or API tokens to version control
- ✅ Use environment variables or GitHub Secrets for all tokens
- ✅ Rotate API tokens periodically
- ✅ The script only makes `POST` (create) and `PATCH` (update) requests — it never deletes items

---

## 📄 License

**Custom License — Beyond All Reason Use Only**

This software may only be used in connection with the Beyond All Reason (BAR) game project. You may use, modify, and deploy it for BAR-related purposes, but you may NOT use it for any other project, game, or commercial purpose.

See [LICENSE](LICENSE) for full terms.

---

Made with ❤️ for the Beyond All Reason community
