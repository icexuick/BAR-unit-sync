"""Re-link all weapons to their parent units in Webflow.

Reads the weapons collection, matches weapon names to unit names,
and bulk-updates the attached-unit-weapons field on all units.
"""
import os
import requests
import time

from dotenv import load_dotenv

load_dotenv()

WEBFLOW_API_TOKEN = os.getenv("WEBFLOW_API_TOKEN", "")
BASE = "https://api.webflow.com/v2"
UNITS_COLL = "6564c6553676389f8ba45a9e"
WEAPONS_COLL = "699446edb237b8c196b4c683"
SCAV_FACTION_ID = "6564c6553676389f8ba461dc"

headers = {
    "Authorization": f"Bearer {WEBFLOW_API_TOKEN}",
    "accept": "application/json",
    "content-type": "application/json",
}


def api_get(url, params=None):
    time.sleep(1.0)
    r = requests.get(url, headers=headers, params=params)
    if r.status_code == 429:
        wait = int(r.headers.get("Retry-After", 10))
        print(f"  Rate limited, waiting {wait}s...")
        time.sleep(wait)
        r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    return r.json()


def api_patch(url, json_data):
    time.sleep(1.0)
    r = requests.patch(url, headers=headers, json=json_data)
    if r.status_code == 429:
        wait = int(r.headers.get("Retry-After", 10))
        print(f"  Rate limited, waiting {wait}s...")
        time.sleep(wait)
        r = requests.patch(url, headers=headers, json=json_data)
    r.raise_for_status()
    return r.json()


def api_post(url, json_data):
    time.sleep(1.0)
    r = requests.post(url, headers=headers, json=json_data)
    if r.status_code == 429:
        wait = int(r.headers.get("Retry-After", 10))
        print(f"  Rate limited, waiting {wait}s...")
        time.sleep(wait)
        r = requests.post(url, headers=headers, json=json_data)
    r.raise_for_status()
    return r.json()


def fetch_all(collection_id):
    items = []
    offset = 0
    while True:
        data = api_get(
            f"{BASE}/collections/{collection_id}/items",
            params={"limit": 100, "offset": offset},
        )
        items.extend(data.get("items", []))
        total = data.get("pagination", {}).get("total", 0)
        if offset + 100 >= total:
            break
        offset += 100
    return items


def main():
    print("=" * 70)
    print("Re-link weapons to units in Webflow")
    print("=" * 70)

    # Fetch all units
    print("Fetching all units...")
    all_units = fetch_all(UNITS_COLL)
    units = []
    unit_name_to_item = {}
    for u in all_units:
        fd = u.get("fieldData", {})
        name = fd.get("name", "")
        if not name or u.get("isDraft") or u.get("isArchived"):
            continue
        if fd.get("faction-ref") == SCAV_FACTION_ID:
            continue
        units.append(u)
        unit_name_to_item[name] = u
    print(f"  {len(units)} active non-scav units")

    # Build sorted unit names (longest first for greedy matching)
    unit_names_sorted = sorted(unit_name_to_item.keys(), key=len, reverse=True)

    # Fetch all weapons
    print("Fetching all weapons...")
    all_weapons = fetch_all(WEAPONS_COLL)
    weapons = [w for w in all_weapons if not w.get("isDraft") and not w.get("isArchived")]
    print(f"  {len(weapons)} active weapons")

    # Match weapons to units by name prefix
    # Weapon name format: "unitname-weapondefname" (e.g. "armbanth-armbantha_fire")
    unit_weapon_ids = {}  # unit_name -> [weapon_id, ...]
    unmatched = []
    for w in weapons:
        fd = w.get("fieldData", {})
        wname = fd.get("name", "")
        matched = False
        for uname in unit_names_sorted:
            if wname.startswith(uname + "-"):
                unit_weapon_ids.setdefault(uname, []).append(w["id"])
                matched = True
                break
        if not matched:
            unmatched.append(wname)

    print(f"  Matched weapons to {len(unit_weapon_ids)} units")
    if unmatched:
        print(f"  Unmatched weapons: {len(unmatched)}")
        for w in unmatched[:5]:
            print(f"    {w}")

    # Compare with current state and build updates
    updates = []
    publish_ids = []
    unchanged = 0
    for uname, wids in sorted(unit_weapon_ids.items()):
        unit = unit_name_to_item.get(uname)
        if not unit:
            continue
        current = unit.get("fieldData", {}).get("attached-unit-weapons", []) or []
        if set(current) == set(wids):
            unchanged += 1
            continue
        updates.append({
            "id": unit["id"],
            "fieldData": {"attached-unit-weapons": wids},
        })
        publish_ids.append(unit["id"])

    # Also clear weapons for units that have attached weapons but shouldn't
    for u in units:
        fd = u.get("fieldData", {})
        uname = fd.get("name", "")
        current = fd.get("attached-unit-weapons", []) or []
        if current and uname not in unit_weapon_ids:
            updates.append({
                "id": u["id"],
                "fieldData": {"attached-unit-weapons": []},
            })
            publish_ids.append(u["id"])

    print(f"\n  {unchanged} units already correct")
    print(f"  {len(updates)} units need updating")

    if not updates:
        print("\nNothing to do!")
        return

    # Bulk update in batches of 100
    print(f"\nUpdating {len(updates)} units...")
    for i in range(0, len(updates), 100):
        batch = updates[i : i + 100]
        resp = api_patch(
            f"{BASE}/collections/{UNITS_COLL}/items",
            {"items": batch},
        )
        updated = len(resp.get("items", []))
        print(f"  Batch {i // 100 + 1}: updated {updated} units")

    # Publish
    if publish_ids:
        unique_ids = list(dict.fromkeys(publish_ids))
        print(f"\nPublishing {len(unique_ids)} units...")
        for i in range(0, len(unique_ids), 100):
            batch = unique_ids[i : i + 100]
            api_post(
                f"{BASE}/collections/{UNITS_COLL}/items/publish",
                {"itemIds": batch},
            )
            print(f"  Published batch {i // 100 + 1}: {len(batch)} units")

    print("\nDone!")


if __name__ == "__main__":
    main()
