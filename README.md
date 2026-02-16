# Beyond All Reason - GitHub to Webflow Unit Sync 🔄

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)

Automatically sync unit data from the [Beyond All Reason GitHub repository](https://github.com/beyond-all-reason/Beyond-All-Reason) to your Webflow CMS Units collection.

## 🎯 Features

- ✅ Automatically fetches all `.lua` unit files from GitHub
- ✅ Parses unit definitions and extracts relevant data
- ✅ Maps GitHub fields to Webflow CMS fields
- ✅ Updates only fields that have changed
- ✅ Supports dry-run mode to preview changes
- ✅ Optional auto-publishing of updated items
- ✅ **Persistent cache** - stores unit file list locally for fast subsequent runs
- ✅ **Strategic icon sync** - downloads PNG from GitHub, converts to WebP (80% quality), uploads to Webflow Assets
- ✅ Detailed logging and progress reporting
- ✅ Error handling and recovery
- ✅ Recursive directory scanning

## 📊 Field Mapping

The following fields are synced from GitHub to Webflow:

| GitHub Field (lua) | Webflow Field | Description |
|-------------------|---------------|-------------|
| `energycost` | Energy Cost | Energy required to build |
| `metalcost` | Metal Cost | Metal required to build |
| `buildtime` | Build Cost | Time to build |
| `energymake` | Energy Make | Energy production |
| `workertime` | Buildpower | Construction power |
| `health` | Health | Unit health points |
| `speed` | Speed | Movement speed |
| `sightdistance` | Sightrange | Vision range |
| `radardistance` | Radarrange | Radar range |
| `sonardistance` | Sonarrange | Sonar range |
| `jammerdistance` | Jammerrange | Jammer range |
| `mass` | Mass | Unit mass |
| `cloakcost` | Cloak cost | Energy cost for cloaking |
| `customparams.paralyzemultiplier` | Paralyze Multiplier | Paralyze damage multiplier |

**Note:** Weapon-related fields (Weapons, DPS, Weapon Range) are NOT synced and remain manually managed.

## 🚀 Quick Start

### Prerequisites

- Python 3.7 or higher
- Webflow API token with write access to the CMS
- Internet connection

### Installation

1. **Clone this repository:**
   ```bash
   git clone https://github.com/your-username/bar-unit-sync.git
   cd bar-unit-sync
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure your API token:**
   ```bash
   cp .env.example .env
   # Edit .env and add your Webflow API token
   ```

   Or set it as an environment variable:
   ```bash
   export WEBFLOW_API_TOKEN="your-token-here"
   ```

### Usage

**Dry Run (recommended first time):**
```bash
python sync_units_github_to_webflow.py --dry-run
```

**Sync and Update:**
```bash
python sync_units_github_to_webflow.py
```

**Sync and Auto-Publish:**
```bash
python sync_units_github_to_webflow.py --publish
```

**Sync with Token:**
```bash
python sync_units_github_to_webflow.py --token "your-webflow-token"
```

## 📖 Documentation

### Command Line Options

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview changes without updating Webflow |
| `--publish` | Automatically publish updated items |
| `--sync-icons` | Also sync strategic icons (PNG → WebP, 80% quality) |
| `--clear-cache` | Clear the unit file cache before syncing |
| `--token TOKEN` | Provide Webflow API token via command line |
| `--help` | Show help message |

### Cache System

The script uses a **persistent cache** (`.unit_cache.json`) to store the list of unit files from GitHub. This means:

- **First run**: Fetches all unit files from GitHub (~30-60 seconds)
- **Subsequent runs**: Loads from cache (<1 second) ⚡

**When to clear cache:**
```bash
# Clear cache if new units were added to GitHub
python sync_single_unit.py armfast --clear-cache

# Or for full sync
python sync_units_github_to_webflow.py --clear-cache
```

The cache is automatically invalidated if you switch to a different repository or branch.

### Strategic Icon Sync

The script can automatically sync strategic icons for units:

**How it works:**
1. Parses `icontypes.lua` from BAR repository to find icon paths
2. Downloads PNG icons from GitHub
3. Converts PNG → WebP with 80% quality (preserves transparency)
4. Uploads to Webflow Assets API
5. Links asset to unit's `icon` field

**Usage:**
```bash
# Sync with icons
python sync_units_github_to_webflow.py --sync-icons

# Dry run with icons (see what would be uploaded)
python sync_units_github_to_webflow.py --sync-icons --dry-run

# Sync icons and publish
python sync_units_github_to_webflow.py --sync-icons --publish
```

**Requirements:**
- Webflow API token with `assets:write` scope
- Icon must exist in `icontypes.lua` for the unit
- PNG file must be accessible in BAR repository

### Example Output

```
================================================================================
Beyond All Reason - Unit Data Sync
================================================================================

Step 1: Fetching unit files from GitHub...
Found 347 unit files

Step 2: Fetching items from Webflow...
Found 285 items in Webflow

Step 3: Processing units...

Processing: armfast (units/ArmBots/T2/armfast.lua)
  📝 Changes detected:
     energy-cost: 4140 → 3800
     metal-cost: 171 → 160
  ✅ Updated successfully

================================================================================
Sync Summary
================================================================================
Total units processed: 285
Updated: 47
Skipped (no changes): 220
Not found in Webflow: 15
Errors: 3
```

## 🤖 Automation

### GitHub Actions (Recommended)

This repository includes a GitHub Actions workflow that automatically syncs units on a schedule.

Create `.github/workflows/sync-to-webflow.yml`:

```yaml
name: Sync Units to Webflow

on:
  schedule:
    # Run every day at 2 AM UTC
    - cron: '0 2 * * *'
  workflow_dispatch:  # Allow manual trigger

jobs:
  sync:
    runs-on: ubuntu-latest
    
    steps:
    - name: Checkout code
      uses: actions/checkout@v3
      
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
        
    - name: Install dependencies
      run: pip install -r requirements.txt
        
    - name: Run sync
      env:
        WEBFLOW_API_TOKEN: ${{ secrets.WEBFLOW_API_TOKEN }}
      run: python sync_units_github_to_webflow.py --publish
```

**Setup:**
1. Go to your repository Settings → Secrets and variables → Actions
2. Add a new secret: `WEBFLOW_API_TOKEN`
3. Paste your Webflow API token
4. The workflow will run automatically every day at 2 AM UTC

### Cron Job (Linux/Mac)

```bash
# Edit crontab
crontab -e

# Add this line (runs daily at 2 AM)
0 2 * * * cd /path/to/bar-unit-sync && python3 sync_units_github_to_webflow.py --publish >> /var/log/bar-sync.log 2>&1
```

### Windows Task Scheduler

1. Open Task Scheduler
2. Create Basic Task
3. Set trigger: Daily at 2:00 AM
4. Set action:
   - Program: `python`
   - Arguments: `sync_units_github_to_webflow.py --publish`
   - Start in: `C:\path\to\bar-unit-sync`

## 🧪 Testing

Run the test script to verify everything works:

```bash
python test_sync.py
```

This will:
- Test the Lua parsing logic
- Verify field mapping
- Test GitHub API fetching
- Validate expected outputs

## 🔧 Configuration

Edit these constants in `sync_units_github_to_webflow.py` if needed:

```python
GITHUB_REPO = "beyond-all-reason/Beyond-All-Reason"
GITHUB_BRANCH = "master"
GITHUB_UNITS_PATH = "units"
WEBFLOW_SITE_ID = "5c68622246b367adf6f3041d"
WEBFLOW_COLLECTION_ID = "6564c6553676389f8ba45a9e"
```

### Adding New Fields

To sync additional fields, add them to the `FIELD_MAPPING` dictionary:

```python
FIELD_MAPPING = {
    "energycost": "energy-cost",
    "your_new_field": "your-webflow-slug",
}
```

**Important:** Make sure the field exists in your Webflow CMS collection first!

## 🐛 Troubleshooting

### "Error: Webflow API token required"
Set the `WEBFLOW_API_TOKEN` environment variable or use `--token` flag.

### "Unit 'xyz' not found in Webflow"
The unit exists in GitHub but not in Webflow. Create it manually in Webflow first.

### "Failed to parse file"
The Lua file structure might be different than expected. Check the file on GitHub.

### Rate Limits
If processing many units hits API limits, consider:
- Running less frequently
- Processing in smaller batches
- Contacting Webflow support for higher limits

## 📝 Adding Missing Fields to Webflow

The following fields from the request are not yet in Webflow:
- `mass` (Mass)
- `paralyzemultiplier` (Paralyze Multiplier)  
- `cloakcost` (Cloak Cost)

To add these:
1. Go to Webflow CMS → Units collection → Settings
2. Add new fields with appropriate types (Number)
3. Update `FIELD_MAPPING` in the script
4. Run sync

## 🔒 Security

- ⚠️ Never commit your `.env` file or API tokens
- ✅ Use environment variables or GitHub Secrets
- ✅ Rotate API tokens periodically
- ✅ The script only makes PATCH requests to update existing items

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📧 Support

For issues or questions:
- Open an issue in this repository
- Check [Webflow API documentation](https://developers.webflow.com/)
- Review [Beyond All Reason GitHub](https://github.com/beyond-all-reason/Beyond-All-Reason)

## 🙏 Acknowledgments

- Beyond All Reason development team
- Webflow API documentation
- Python community

---

Made with ❤️ for the Beyond All Reason community
