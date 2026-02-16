# Quick Start Guide

Get up and running with BAR Unit Sync in 5 minutes! ⚡

## Step 1: Get Your Webflow API Token

1. Go to https://webflow.com/dashboard/account/apps
2. Create a new API token or use an existing one
3. Copy the token (you'll need it in Step 3)

## Step 2: Clone and Setup

```bash
# Clone the repository
git clone https://github.com/your-username/bar-unit-sync.git
cd bar-unit-sync

# Run the setup script (Linux/Mac)
./setup.sh

# Or manually (Windows/Linux/Mac)
pip install -r requirements.txt
cp .env.example .env
```

## Step 3: Configure

Edit the `.env` file and add your Webflow API token:

```bash
WEBFLOW_API_TOKEN=your_actual_token_here
```

## Step 4: Test with Dry Run

Run a dry-run to see what would be updated:

```bash
python sync_units_github_to_webflow.py --dry-run
```

This will show you all the changes that would be made without actually updating anything.

## Step 5: Run the Sync

If everything looks good, run the actual sync:

```bash
python sync_units_github_to_webflow.py
```

Or auto-publish the changes:

```bash
python sync_units_github_to_webflow.py --publish
```

## That's It! 🎉

Your units are now synced! The script will:
- ✅ Fetch all unit files from GitHub
- ✅ Parse the Lua definitions
- ✅ Update changed fields in Webflow
- ✅ Show you a detailed summary

## Next Steps

### Automate with GitHub Actions

1. Fork this repository
2. Add your `WEBFLOW_API_TOKEN` to GitHub Secrets
3. The workflow will automatically run daily at 2 AM UTC

### Customize

Edit `sync_units_github_to_webflow.py` to:
- Change which fields are synced
- Modify the field mapping
- Adjust the sync behavior

See [README.md](README.md) for detailed documentation.

## Need Help?

- Check the [README.md](README.md) for detailed documentation
- Run `python sync_units_github_to_webflow.py --help`
- Open an issue on GitHub

---

**Pro Tip:** Always run with `--dry-run` first to preview changes! 💡
