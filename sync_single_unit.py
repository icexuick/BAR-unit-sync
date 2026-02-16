#!/usr/bin/env python3
"""
Single Unit Sync Test Script
=============================

Test the sync functionality on a single unit before running the full sync.

Usage:
    python sync_single_unit.py armfast --dry-run
    python sync_single_unit.py armfast
    python sync_single_unit.py armfast --publish
"""

import os
import sys
import argparse
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Import from the main sync script
from sync_units_github_to_webflow import (
    GitHubUnitFetcher,
    WebflowAPI,
    LuaParser,
    GITHUB_REPO,
    GITHUB_BRANCH,
    WEBFLOW_SITE_ID,
    WEBFLOW_COLLECTION_ID,
    FIELD_MAPPING
)

def sync_single_unit(unit_name: str, dry_run: bool = False, auto_publish: bool = False, api_token: str = None):
    """
    Sync a single unit from GitHub to Webflow.
    
    Args:
        unit_name: Name of the unit (e.g., 'armfast')
        dry_run: If True, preview changes without updating
        auto_publish: If True, publish the item after updating
        api_token: Webflow API token
    """
    print("=" * 80)
    print(f"Testing Single Unit Sync: {unit_name}")
    print("=" * 80)
    print()
    
    # Initialize services
    github = GitHubUnitFetcher(GITHUB_REPO, GITHUB_BRANCH, github_token=os.environ.get("GITHUB_TOKEN"))
    webflow = WebflowAPI(api_token, WEBFLOW_SITE_ID, WEBFLOW_COLLECTION_ID)
    parser = LuaParser()
    
    # Step 1: Find the unit file on GitHub
    print(f"Step 1: Searching for {unit_name}.lua in GitHub...")
    all_files = github.get_unit_files("units")
    
    unit_file = None
    for file_info in all_files:
        if file_info['name'] == unit_name:
            unit_file = file_info
            break
    
    if not unit_file:
        print(f"❌ Error: Unit '{unit_name}' not found in GitHub repository")
        print(f"   Searched {len(all_files)} files")
        print()
        print("Available units (first 10):")
        for file_info in all_files[:10]:
            print(f"  - {file_info['name']}")
        return False
    
    print(f"✅ Found: {unit_file['path']}")
    print()
    
    # Step 2: Fetch and parse the unit file
    print("Step 2: Fetching and parsing unit data from GitHub...")
    lua_content = github.fetch_unit_data(unit_file['path'])
    
    if not lua_content:
        print("❌ Error: Failed to fetch file from GitHub")
        return False
    
    github_data = parser.parse_unit_file(lua_content, unit_name)
    
    if not github_data:
        print("❌ Error: Failed to parse Lua file")
        return False
    
    print("✅ Successfully parsed unit data")
    print()
    print("📝 GitHub Data:")
    print("-" * 80)
    for key in sorted(FIELD_MAPPING.keys()):
        if key in github_data:
            print(f"  {key}: {github_data[key]}")
    print()
    
    # Step 3: Map to Webflow fields
    print("Step 3: Mapping to Webflow fields...")
    webflow_fields = {}
    for github_key, webflow_key in FIELD_MAPPING.items():
        if github_key in github_data:
            value = github_data[github_key]
            if isinstance(value, (int, float)):
                value = int(value)
            webflow_fields[webflow_key] = value
    
    if not webflow_fields:
        print("⚠️  Warning: No fields to sync")
        return False
    
    print(f"✅ Mapped {len(webflow_fields)} fields")
    print()
    
    # Step 4: Get current Webflow data
    print("Step 4: Fetching current data from Webflow...")
    webflow_items = webflow.get_all_items()
    
    webflow_item = None
    for item in webflow_items:
        if item.get('fieldData', {}).get('name') == unit_name:
            webflow_item = item
            break
    
    if not webflow_item:
        print(f"❌ Error: Unit '{unit_name}' not found in Webflow")
        print(f"   The unit exists in GitHub but not in your Webflow CMS")
        print(f"   Please create it in Webflow first")
        return False
    
    print(f"✅ Found in Webflow (ID: {webflow_item['id']})")
    print()
    
    # Step 5: Compare and show changes
    print("Step 5: Comparing data...")
    current_data = webflow_item.get('fieldData', {})
    
    changes = {}
    for key, new_value in webflow_fields.items():
        current_value = current_data.get(key)
        if current_value != new_value:
            changes[key] = {
                'current': current_value,
                'new': new_value
            }
    
    if not changes:
        print("✅ No changes needed - data is already in sync!")
        return True
    
    print(f"📝 Changes to be made ({len(changes)} fields):")
    print("-" * 80)
    for key, change in changes.items():
        print(f"  {key}:")
        print(f"    Current: {change['current']}")
        print(f"    New:     {change['new']}")
    print()
    
    # Step 6: Update Webflow (unless dry run)
    if dry_run:
        print("ℹ️  DRY RUN - No changes were made")
        print("   Run without --dry-run to apply these changes")
        return True
    
    print("Step 6: Updating Webflow...")
    success = webflow.update_item(webflow_item['id'], webflow_fields)
    
    if not success:
        print("❌ Error: Failed to update Webflow")
        return False
    
    print("✅ Successfully updated in Webflow!")
    print()
    
    # Step 7: Publish (if requested)
    if auto_publish:
        print("Step 7: Publishing...")
        publish_success = webflow.publish_item(webflow_item['id'])
        
        if publish_success:
            print("✅ Successfully published!")
        else:
            print("⚠️  Warning: Update succeeded but publish failed")
        print()
    
    print("=" * 80)
    print("✅ Sync completed successfully!")
    print("=" * 80)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Test sync on a single unit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test armfast with dry run
  python sync_single_unit.py armfast --dry-run
  
  # Actually sync armfast
  python sync_single_unit.py armfast
  
  # Sync and publish
  python sync_single_unit.py armfast --publish
  
  # Use specific token
  python sync_single_unit.py armfast --token "your-token"
        """
    )
    
    parser.add_argument(
        'unit',
        type=str,
        help='Name of the unit to sync (e.g., armfast, armpw, cormist)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without updating Webflow'
    )
    
    parser.add_argument(
        '--publish',
        action='store_true',
        help='Automatically publish the updated item'
    )
    
    parser.add_argument(
        '--token',
        type=str,
        help='Webflow API token (or set WEBFLOW_API_TOKEN env var)'
    )
    
    args = parser.parse_args()
    
    # Get API token
    api_token = args.token or os.environ.get("WEBFLOW_API_TOKEN", "")
    if not api_token:
        print("Error: Webflow API token required")
        print("Set WEBFLOW_API_TOKEN environment variable or use --token")
        sys.exit(1)
    
    # Run sync
    success = sync_single_unit(
        args.unit,
        dry_run=args.dry_run,
        auto_publish=args.publish,
        api_token=api_token
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
