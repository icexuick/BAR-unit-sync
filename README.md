# Beyond All Reason - GitHub to Webflow Unit Sync

This script automatically syncs unit data from the [Beyond All Reason GitHub repository](https://github.com/beyond-all-reason/Beyond-All-Reason) to your Webflow CMS Units collection.

## Features

- ✅ Automatically fetches all `.lua` unit files from GitHub
- ✅ Parses unit definitions and extracts relevant data
- ✅ Maps GitHub fields to Webflow CMS fields
- ✅ Updates only fields that have changed
- ✅ Supports dry-run mode to preview changes
- ✅ Optional auto-publishing of updated items
- ✅ Detailed logging and progress reporting
- ✅ Error handling and recovery

## Field Mapping

The following fields are synced from GitHub to Webflow:

| GitHub Field (lua) | Webflow Field | Description |
|-------------------|---------------|-------------|
| `energycost` | Energy Cost | Energy required to build |
| `metalcost` | Metal Cost | Metal required to build |
| `buildtime` | Build Cost | Time to build (buildtime) |
| `energymake` | Energy Make | Energy production |
| `workertime` | Buildpower | Construction power |
| `health` | Health | Unit health points |
| `speed` | Speed | Movement speed |
| `sightdistance` | Sightrange | Vision range |
| `radardistance` | Radarrange | Radar range |
| `sonardistance` | Sonarrange | Sonar range |
| `jammerdistance` | Jammerrange | Jammer range |
| `mass` | Mass | Unit mass (if field exists) |
| `paralyzemultiplier` | Paralyze Multiplier | Paralysis resistance (if field exists) |
| `cloakcost` | Cloak Cost | Cloaking energy cost (if field exists) |

**Note:** Weapon-related fields (Weapons, DPS, Weapon Range) are NOT synced and remain manually managed.

## Prerequisites

- Python 3.7 or higher
- Webflow API token with write access to the CMS
- Internet connection to access GitHub and Webflow APIs

## Installation

1. **Clone or download this script:**
   ```bash
   # If you have the script files
   cd /path/to/script/directory
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up your Webflow API token:**
   
   You need a Webflow API token with CMS write permissions. Get one from:
   https://webflow.com/dashboard/account/apps

   Then either:
   - Set it as an environment variable:
     ```bash
     export WEBFLOW_API_TOKEN="your-token-here"
     ```
   - Or pass it via command line flag (see Usage below)

## Usage

### Basic Commands

**Dry Run (recommended first time):**
```bash
python sync_units_github_to_webflow.py --dry-run
```
This shows what would be updated without making any changes.

**Sync and Update:**
```bash
python sync_units_github_to_webflow.py
```
Updates items in Webflow but doesn't publish them (leaves as drafts).

**Sync and Auto-Publish:**
```bash
python sync_units_github_to_webflow.py --publish
```
Updates items and automatically publishes them.

**Sync with Token:**
```bash
python sync_units_github_to_webflow.py --token "your-webflow-token"
```

### Command Line Options

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview changes without updating Webflow |
| `--publish` | Automatically publish updated items |
| `--token TOKEN` | Provide Webflow API token via command line |
| `--help` | Show help message |

## Example Output

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

Processing: armcom (units/ArmCommanders/armcom.lua)
  ✓ No changes needed

Processing: cormist (units/CorVehicles/cormist.lua)
  📝 Changes detected:
     health: 450 → 470
  ✅ Updated successfully

...

================================================================================
Sync Summary
================================================================================
Total units processed: 285
Updated: 47
Skipped (no changes): 220
Not found in Webflow: 15
Errors: 3
```

## Automation Options

### Option 1: Cron Job (Linux/Mac)

Run the sync automatically every day at 2 AM:

```bash
# Edit your crontab
crontab -e

# Add this line:
0 2 * * * cd /path/to/script && /usr/bin/python3 sync_units_github_to_webflow.py --publish >> /var/log/bar-sync.log 2>&1
```

### Option 2: GitHub Actions

Create a GitHub Action workflow that runs the sync on a schedule or when unit files change.

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
      run: |
        pip install -r requirements.txt
        
    - name: Run sync
      env:
        WEBFLOW_API_TOKEN: ${{ secrets.WEBFLOW_API_TOKEN }}
      run: |
        python sync_units_github_to_webflow.py --publish
```

Don't forget to add `WEBFLOW_API_TOKEN` to your repository secrets!

### Option 3: Windows Task Scheduler

1. Open Task Scheduler
2. Create a new task
3. Set trigger (e.g., daily at 2 AM)
4. Set action: 
   - Program: `python`
   - Arguments: `sync_units_github_to_webflow.py --publish`
   - Start in: `C:\path\to\script`

## Troubleshooting

### "Error: Webflow API token required"

Make sure you've set the `WEBFLOW_API_TOKEN` environment variable or pass it via `--token` flag.

### "Unit 'xyz' not found in Webflow"

The unit exists in GitHub but not in your Webflow CMS. You'll need to create it manually in Webflow first.

### "Failed to parse file"

The Lua file might have a different structure than expected. Check the file manually on GitHub.

### Rate Limits

If you're processing many units, you might hit API rate limits. The script will show errors if this happens. Consider:
- Running less frequently
- Processing units in smaller batches
- Contacting Webflow support for higher limits

## Configuration

You can modify these constants in the script:

```python
GITHUB_REPO = "beyond-all-reason/Beyond-All-Reason"
GITHUB_BRANCH = "master"
GITHUB_UNITS_PATH = "units"
WEBFLOW_SITE_ID = "5c68622246b367adf6f3041d"
WEBFLOW_COLLECTION_ID = "6564c6553676389f8ba45a9e"
```

### Adding New Field Mappings

To sync additional fields, add them to the `FIELD_MAPPING` dictionary:

```python
FIELD_MAPPING = {
    "energycost": "energy-cost",
    "metalcost": "metal-cost",
    # Add new mappings here:
    "your_github_field": "your-webflow-field",
}
```

### Skipping Fields

To skip certain fields (like weapon stats), add them to `SKIP_FIELDS`:

```python
SKIP_FIELDS = ["weapons", "dps", "weaponrange"]
```

## Security Notes

- Never commit your Webflow API token to version control
- Use environment variables or GitHub Secrets for tokens
- Keep your API token secure and rotate it periodically
- The script only makes PATCH requests to update existing items

## Support

For issues or questions:
- Check the Webflow API documentation: https://developers.webflow.com/
- Review the Beyond All Reason GitHub: https://github.com/beyond-all-reason/Beyond-All-Reason
- Check script logs for detailed error messages

## License

This script is provided as-is for the Beyond All Reason project.
