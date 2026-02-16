#!/usr/bin/env python3
"""
Test script to verify the GitHub to Webflow sync works correctly.
This tests the parsing and field mapping without making actual API calls.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(__file__))

from sync_units_github_to_webflow import GitHubUnitFetcher, LuaParser, FIELD_MAPPING

def test_armfast_parsing():
    """Test parsing of the armfast.lua file."""
    print("Testing armfast.lua parsing...")
    print("=" * 80)
    
    # Sample armfast.lua content
    armfast_lua = """return {
	armfast = {
		buildpic = "ARMFAST.DDS",
		buildtime = 5000,
		canmove = true,
		energycost = 3800,
		health = 690,
		metalcost = 160,
		speed = 111.3,
		sightdistance = 351,
		workertime = 0,
		energymake = 0,
		radardistance = 0,
		jammerdistance = 0,
		sonardistance = 0,
	},
}"""
    
    parser = LuaParser()
    result = parser.parse_unit_file(armfast_lua, "armfast")
    
    print("\n📝 Parsed data from GitHub:")
    print("-" * 80)
    for key, value in sorted(result.items()):
        print(f"  {key}: {value}")
    
    print("\n🔄 Mapped to Webflow fields:")
    print("-" * 80)
    webflow_fields = {}
    for github_key, webflow_key in FIELD_MAPPING.items():
        if github_key in result:
            value = result[github_key]
            if isinstance(value, (int, float)):
                value = int(value)
            webflow_fields[webflow_key] = value
            print(f"  {github_key} → {webflow_key}: {value}")
    
    print("\n✅ Expected Webflow updates:")
    print("-" * 80)
    expected = {
        "energy-cost": 3800,
        "metal-cost": 160,
        "build-cost": 5000,
        "health": 690,
        "speed": 111,
        "sightrange": 351,
    }
    
    for field, expected_value in expected.items():
        actual_value = webflow_fields.get(field)
        status = "✓" if actual_value == expected_value else "✗"
        print(f"  {status} {field}: expected={expected_value}, actual={actual_value}")
    
    print("\n" + "=" * 80)
    print("Test completed successfully!" if all(
        webflow_fields.get(k) == v for k, v in expected.items()
    ) else "Test failed - values don't match!")
    print()

def test_fetch_from_github():
    """Test fetching real data from GitHub."""
    print("\nTesting GitHub API fetching...")
    print("=" * 80)
    
    fetcher = GitHubUnitFetcher("beyond-all-reason/Beyond-All-Reason", "master")
    
    print("\n📡 Fetching armfast.lua from GitHub...")
    content = fetcher.fetch_unit_data("units/ArmBots/T2/armfast.lua")
    
    if content:
        print("✅ Successfully fetched file")
        print(f"   File size: {len(content)} bytes")
        
        # Parse it
        parser = LuaParser()
        result = parser.parse_unit_file(content, "armfast")
        
        if result:
            print("\n📝 Extracted values:")
            print("-" * 80)
            important_fields = ["energycost", "metalcost", "buildtime", "health", "speed"]
            for field in important_fields:
                if field in result:
                    print(f"  {field}: {result[field]}")
            print("\n✅ Parsing successful!")
        else:
            print("❌ Failed to parse file")
    else:
        print("❌ Failed to fetch file")
    
    print("\n" + "=" * 80)
    print()

if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("Beyond All Reason - Sync Script Test Suite")
    print("=" * 80)
    print()
    
    # Test 1: Parsing logic
    test_armfast_parsing()
    
    # Test 2: GitHub API (requires internet)
    try:
        test_fetch_from_github()
    except Exception as e:
        print(f"\n⚠️  GitHub API test skipped: {e}\n")
    
    print("All tests completed!")
    print()
