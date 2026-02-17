# Beyond All Reason — GitHub to Webflow Unit Sync 🔄

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)

Automatically sync unit data from the [Beyond All Reason GitHub repository](https://github.com/beyond-all-reason/Beyond-All-Reason) to your Webflow CMS Units collection.

---

## 🎯 Features

- ✅ Fetches all `.lua` unit files from the BAR GitHub repository
- ✅ Only syncs **buildable units** (units that appear in any `buildoptions` list) + a whitelist for commanders
- ✅ Parses unit definitions and extracts all relevant fields
- ✅ Calculates **DPS** using BAR's official formula (damage × reload × salvo × burst × projectiles)
- ✅ Detects **weapon types** and filters out bogus/zero-damage/unequipped weapons
- ✅ Detects **special abilities** (Radar, Stealth, Shield, Transport, etc.)
- ✅ Detects **faction** (Armada, Cortex, Legion, CHICKS) from unit filename prefix
- ✅ Detects **unit type** (Aircraft, Bot, Vehicle, Ship, Hovercraft, Building, etc.)
- ✅ Detects **amphibious** units using BAR's official `alldefs_post.lua` logic
- ✅ Resolves **buildoptions** to Webflow item IDs (multi-reference field)
- ✅ Syncs **unit names and tooltips** from `language/en/units.json`
- ✅ Updates **only fields that have changed** — skips untouched units
- ✅ Dry-run mode to preview changes without writing to Webflow
- ✅ Optional auto-publishing of updated items
- ✅ **Strategic icon sync** — PNG → WebP, committed to GitHub, linked in Webflow
- ✅ Single-unit mode for testing (`--unit armzeus`)
- ✅ Detailed console output with readable field names

---

## 📊 Synced Fields

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
| `cloakcost` | `cloak-cost` | Number |
| `customparams.paralyzemultiplier` | `paralyze-multiplier` | Number |
| `customparams.techlevel` | `techlevel` | Number |

### Derived / computed fields

| Field | Webflow slug | Type | How it's computed |
|---|---|---|---|
| Unit display name | `unitname` | PlainText | From `language/en/units.json` |
| Tooltip | `tooltip` | PlainText | From `language/en/units.json` |
| Faction | `faction-ref` | Reference | Filename prefix: `arm` → Armada, `cor` → Cortex, `leg` → Legion, `raptor` → CHICKS |
| Unit Type | `unittype` | Reference | Detected from `movementclass`, `canfly`, speed, builder flags |
| Amphibious | `amphibious` | Switch | Based on BAR's `alldefs_post.lua` movement class lists |
| Buildoptions | `buildoptions-ref` | MultiReference | Units this unit can build, resolved to Webflow item IDs |
| DPS | `dps` | Number | `(max(dmg_vtol, dmg_default) × (1/reload)) × salvosize × burst × projectiles` |
| Weapon Range | `weaponrange` | Number | Highest range across all equipped non-bogus weapons |
| Weapons | `weapons` | PlainText | e.g. `LaserCannon, 2x MissileLauncher, EMP-BeamLaser` |
| Specials | `specials` | PlainText | Comma-separated special abilities (see below) |

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
| Stealth Detector | `seismicdistance > 0` |

---

## 🚀 Quick Start

### Prerequisites

- Python 3.7 or higher
- Webflow API token with write access to the CMS
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

3. **Set your Webflow API token:**
   ```bash
   # Option A: environment variable
   export WEBFLOW_API_TOKEN="your-token-here"

   # Option B: .env file
   cp .env.example .env
   # Edit .env and fill in your token
   ```

### Usage

**Dry run — preview changes without writing anything (recommended first):**
```bash
python sync_units_github_to_webflow.py --dry-run
```

**Test a single unit:**
```bash
python sync_units_github_to_webflow.py --unit armzeus --dry-run
python sync_units_github_to_webflow.py --unit armzeus
```

**Full sync:**
```bash
python sync_units_github_to_webflow.py
```

**Full sync with auto-publish:**
```bash
python sync_units_github_to_webflow.py --publish
```

**Sync including strategic icons:**
```bash
python sync_units_github_to_webflow.py --sync-icons
```

**Clear cache and re-fetch everything from GitHub:**
```bash
python sync_units_github_to_webflow.py --clear-cache
```

---

## 📖 Command Line Reference

| Flag | Description |
|---|---|
| `--dry-run` | Preview changes without updating Webflow |
| `--unit NAME` | Sync only one specific unit, e.g. `--unit armzeus` |
| `--publish` | Automatically publish updated items after sync |
| `--sync-icons` | Also sync strategic icons (PNG → WebP, committed to GitHub) |
| `--clear-cache` | Delete local cache files and re-fetch everything from GitHub |
| `--token TOKEN` | Provide Webflow API token via command line |
| `--help` | Show help message |

---

## 💾 Cache System

The script uses two local cache files to avoid re-downloading data on every run.

### `.unit_cache.json`
Caches individual unit `.lua` file contents fetched from GitHub.
- **First run**: downloads each file on demand
- **Subsequent runs**: loads from cache instantly ⚡

### `.buildable_cache.json`
Built once by downloading the entire BAR repository as a ZIP archive and scanning all unit files. Stores two indexes:

- `buildable` — set of all unit names that appear in any `buildoptions` list
- `buildoptions_map` — maps each unit name to the list of units it can build

> The script only syncs **buildable units** — those that appear in at least one factory's buildoptions. This filters out ~230 internal/unused units out of ~850 total. A small whitelist (commanders) is always included regardless.

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

**A weapon is skipped entirely if:**
- Its `name` field contains `bogus` or `mine`
- Its `customparams` block contains `bogus = 1`
- Both `dmg_default` and `dmg_vtol` are zero or absent

**EMP / paralyzer weapons** (`paralyzer = true`) appear in the `weapons` field with an `EMP-` prefix (e.g. `EMP-BeamLaser`) but do **not** contribute to the DPS value.

---

## 🎨 Strategic Icon Sync

When `--sync-icons` is used, unit icons are committed to your own GitHub repository as WebP files and linked from there in Webflow.

**How it works:**
1. Parses `icontypes.lua` from the BAR repo to find each unit's icon path
2. Downloads the PNG from the BAR GitHub repo
3. Converts PNG → WebP at 80% quality (transparency preserved)
4. Commits the WebP to your repo at `icons/<unitname>.webp`
5. Sets the raw GitHub URL in Webflow's `icon` field

**Add to `.env`:**
```
GITHUB_TOKEN=ghp_your_token_here
ICON_REPO_OWNER=your-github-username
ICON_REPO_NAME=bar-unit-sync
ICON_BRANCH=main
```

> The GitHub token needs `repo` scope. Create one at [github.com/settings/tokens](https://github.com/settings/tokens).

---

## 🤖 Automation

### GitHub Actions

Create `.github/workflows/sync.yml`:

```yaml
name: Sync BAR Units to Webflow

on:
  schedule:
    - cron: '0 3 * * *'   # every day at 03:00 UTC
  workflow_dispatch:         # allow manual trigger

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - run: pip install -r requirements.txt

      - name: Run sync
        env:
          WEBFLOW_API_TOKEN: ${{ secrets.WEBFLOW_API_TOKEN }}
        run: python sync_units_github_to_webflow.py --publish
```

Add `WEBFLOW_API_TOKEN` to **Settings → Secrets and variables → Actions** in your repository.

### Cron job (Linux / Mac)

```bash
crontab -e

# Add this line (runs daily at 03:00):
0 3 * * * cd /path/to/bar-unit-sync && python3 sync_units_github_to_webflow.py --publish >> /var/log/bar-sync.log 2>&1
```

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

**Whitelist** — units always synced even if not in any buildoptions:
```python
SYNC_WHITELIST = {"armcom", "corcom", "legcom"}
```

**Faction map** — maps filename prefix to Webflow reference item ID:
```python
FACTION_MAP = {
    "arm":    {"name": "Armada", "id": "..."},
    "cor":    {"name": "Cortex", "id": "..."},
    "leg":    {"name": "Legion", "id": "..."},
    "raptor": {"name": "CHICKS", "id": "..."},
}
```

---

## 🐛 Troubleshooting

**"Error: Webflow API token required"**
Set `WEBFLOW_API_TOKEN` as an environment variable or pass `--token your-token`.

**"Unit 'xyz' not found in Webflow"**
The unit exists on GitHub but hasn't been created in Webflow yet. Add it to the CMS first — the sync only updates existing items.

**"Field not described in schema: undefined"**
A field slug in the script doesn't match what's in Webflow. Open the Webflow CMS collection settings and verify the slug for that field.

**"Archive download failed"**
The buildable cache couldn't be built. Check your internet connection. Run `--clear-cache` to retry.

**Icons not appearing in Webflow**
Raw GitHub URLs can take a few minutes to become accessible after the first commit. Check that the file exists at `https://github.com/YOUR_USERNAME/YOUR_REPO/tree/main/icons`.

**Rate limits**
The script has a built-in rate limiter (110 requests/minute). If you still hit limits, reduce the request rate in the `RateLimiter` class or run the sync less frequently.

---

## 🔒 Security

- ⚠️ Never commit your `.env` file or API tokens to version control
- ✅ Use environment variables or GitHub Secrets for all tokens
- ✅ Rotate API tokens periodically
- ✅ The script only makes `PATCH` requests — it never creates or deletes Webflow CMS items

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

Made with ❤️ for the Beyond All Reason community
