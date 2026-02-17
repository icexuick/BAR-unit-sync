# 🎨 Icon Sync Setup Guide

This guide helps you set up automatic icon syncing from BAR to Webflow using GitHub as the hosting platform.

## 📋 Overview

The icon sync feature:
1. Downloads PNG icons from BAR repository
2. Converts them to WebP (80% quality, smaller file size)
3. Commits them to YOUR GitHub repository
4. Uses the public GitHub raw URL in Webflow
5. Updates the CMS item with the URL

**Why GitHub?** Icons need a public URL for Webflow's image fields. GitHub provides free, permanent, public URLs via `raw.githubusercontent.com`.

## 🚀 Setup Steps

### Step 1: Create GitHub Personal Access Token

1. Go to [GitHub Settings → Tokens](https://github.com/settings/tokens)
2. Click **"Generate new token"** → **"Generate new token (classic)"**
3. Give it a name: `BAR Unit Sync Icons`
4. Select scopes:
   - ✅ **`repo`** (Full control of private repositories)
     - This allows the script to commit icons to your repo
5. Click **"Generate token"**
6. **Copy the token** (you won't see it again!)

### Step 2: Configure Your Repository

You have two options:

**Option A: Use existing `bar-unit-sync` repo (recommended)**
```bash
# The script will create an icons/ folder in your existing repo
# No action needed - just configure the .env below
```

**Option B: Create dedicated `bar-icons` repo**
```bash
# 1. Create new repository on GitHub
# 2. Name it: bar-icons
# 3. Make it public (for free hosting)
# 4. Update ICON_REPO_NAME in .env to "bar-icons"
```

### Step 3: Update .env File

Add these lines to your `.env` file:

```bash
# GitHub Token (with 'repo' scope)
GITHUB_TOKEN=ghp_your_token_here_abc123xyz

# Icon Repository Configuration
ICON_REPO_OWNER=icexuick              # ← Your GitHub username
ICON_REPO_NAME=bar-unit-sync          # ← Your repository name
ICON_BRANCH=main                       # ← Branch to commit to
```

**Important:** Replace `icexuick` with YOUR GitHub username!

### Step 4: Test It!

```bash
# Dry run first (see what would happen)
python sync_units_github_to_webflow.py --sync-icons --dry-run

# If it looks good, run for real
python sync_units_github_to_webflow.py --sync-icons
```

## 📂 What Happens

### During Sync:
```
Step 1a: Fetching icontypes.lua for icon paths...
Found 347 unit icons in icontypes.lua
✅ Will commit icons to: icexuick/bar-unit-sync (main branch)

Processing: armclaw (units/ArmBuildings/LandDefenceOffence/armclaw.lua)
  🎨 Syncing strategic icon...
    📥 Downloading icon: icons/wall_0.4.png
    🔄 Converting PNG → WebP (80% quality)
    📤 Committing to GitHub: armclaw.webp
       File exists, updating...
  ✅ Committed to GitHub: armclaw.webp
     URL: https://raw.githubusercontent.com/icexuick/bar-unit-sync/main/icons/armclaw.webp
  ✅ Icon updated
```

### In Your Repository:
```
bar-unit-sync/
├── icons/
│   ├── armclaw.webp
│   ├── armfast.webp
│   ├── armawac.webp
│   └── ... (300+ icons)
├── sync_units_github_to_webflow.py
└── ...
```

### In Webflow:
Each unit's `icon` field will contain:
```
https://raw.githubusercontent.com/icexuick/bar-unit-sync/main/icons/armclaw.webp
```

## ✅ Verification

### Check GitHub Commits
```bash
# View recent commits to see icon updates
git log --oneline -10
```

You'll see commits like:
```
abc1234 🎨 Add/update strategic icon: armclaw.webp
def5678 🎨 Add/update strategic icon: armfast.webp
```

### Check Webflow
1. Open your Units collection in Webflow
2. Select a unit (e.g., "armclaw")
3. Check the `Icon` field
4. It should show the image from the GitHub URL

### Test the URL
Open the URL directly in your browser:
```
https://raw.githubusercontent.com/YOUR-USERNAME/bar-unit-sync/main/icons/armclaw.webp
```

You should see the WebP icon image!

## 🔧 Troubleshooting

### Error: "GITHUB_TOKEN not found"
**Solution:** Add `GITHUB_TOKEN` to your `.env` file

### Error: "ICON_REPO_OWNER not set"
**Solution:** Add these to `.env`:
```bash
ICON_REPO_OWNER=your-github-username
ICON_REPO_NAME=bar-unit-sync
```

### Error: "403 Forbidden" or "Permission denied"
**Solution:** Your GitHub token needs the `repo` scope:
1. Go to [GitHub Settings → Tokens](https://github.com/settings/tokens)
2. Edit your token
3. Enable `repo` scope
4. Update token in `.env`

### Icons not showing in Webflow
**Possible causes:**
1. URL not updated in CMS → Run sync again
2. Image not publicly accessible → Check repo is public
3. Branch mismatch → Verify `ICON_BRANCH` in `.env`

**Debug:**
```bash
# Check if icon was committed
git log --all --oneline | grep "icon"

# Test URL directly
curl -I https://raw.githubusercontent.com/YOUR-USERNAME/bar-unit-sync/main/icons/armclaw.webp
```

## 🎯 Best Practices

### First Run
```bash
# 1. Test with one unit first (using sync_single_unit.py)
# 2. Verify icon appears in GitHub and Webflow
# 3. Run full sync with --sync-icons
```

### Regular Updates
```bash
# Update icons when BAR repository updates them
python sync_units_github_to_webflow.py --sync-icons --publish
```

### Clean Up
If you want to remove all icons and start fresh:
```bash
# Delete icons folder in GitHub
git rm -r icons/
git commit -m "Clean up icons"
git push

# Re-run sync
python sync_units_github_to_webflow.py --sync-icons
```

## 📝 Notes

- **WebP Quality:** Icons are converted at 80% quality (good balance of size/quality)
- **File Size:** WebP typically 40-70% smaller than PNG
- **Updates:** Script automatically updates existing icons if they change
- **Rate Limits:** GitHub API has rate limits (5000 req/hour with token)
- **Public Access:** Icons must be in a public repo or use a public branch

## 🚀 Next Steps

Once icon sync is working, you can extend this approach for other images:
- Unit build pictures
- Unit rotating models
- Faction logos
- Map thumbnails
- etc.

Just modify the script to handle different image sources and Webflow fields!

## 💡 Tips

**Tip 1:** Use a dedicated branch for icons
```bash
ICON_BRANCH=icons  # Separate icons from code
```

**Tip 2:** Monitor commit history
```bash
git log --oneline --author="bar-unit-sync" -20
```

**Tip 3:** Batch updates
Icons are only committed if they changed, so re-running sync is fast!

---

**Questions?** Open an issue on GitHub or check the main [README.md](README.md)
