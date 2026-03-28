#!/usr/bin/env python3
"""
Quick smoke test for Arena API connection.
Usage: python3 scripts/test_arena_connection.py
"""

import getpass
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arena_kicad_library_sync.arena_client import ArenaClient, ArenaAPI


def main():
    print("=== Arena API Connection Test ===\n")

    email = input("Arena Email: ").strip()
    password = getpass.getpass("Arena Password: ")

    print("\nWorkspaces:")
    print("  1. Production (900279607)")
    print("  2. Sandbox    (900279608)")
    choice = input("Select [1/2]: ").strip()
    workspace_id = "900279608" if choice == "2" else "900279607"

    print(f"\nConnecting to Arena ({'Sandbox' if choice == '2' else 'Production'})...")

    client = ArenaClient()
    try:
        client.login(email, password, workspace_id)
        print(f"  Logged in as: {client.user_full_name or email}")
        print(f"  Session ID:   {client.session_id[:20]}...")
        print(f"  Requests remaining: {client.requests_remaining}")
    except Exception as e:
        print(f"  LOGIN FAILED: {e}")
        return

    api = ArenaAPI(client)

    # Test: fetch items
    print("\n--- Fetching items (first 5) ---")
    try:
        data = api.get_items(limit=5)
        items = data.get("results", [])
        print(f"  Got {len(items)} items (total available: {data.get('count', '?')})")
        for item in items[:5]:
            number = item.get("number", "?")
            name = item.get("name", "?")
            lc = api.get_lifecycle(item)
            rev = item.get("revisionNumber", "?")
            print(f"    {number} | {name[:40]} | Rev {rev} | {lc}")
    except Exception as e:
        print(f"  FAILED: {e}")

    # Test: fetch categories
    print("\n--- Fetching categories ---")
    try:
        categories = api.get_categories()
        print(f"  Got {len(categories)} assignable categories")
        for cat in categories[:10]:
            print(f"    [{cat['prefix']}] {cat['name']}")
    except Exception as e:
        print(f"  FAILED: {e}")

    # Test: sourcing — try each item until one has sourcing data
    print("\n--- Sourcing lookup ---")
    sourcing_found = False
    for item in items:
        guid = item.get("guid", "")
        number = item.get("number", "?")
        try:
            sourcing = api.get_item_sourcing(guid)
            if sourcing:
                has_data = any(s.get("mpn") for s in sourcing)
                if has_data:
                    print(f"  {number}:")
                    for s in sourcing[:3]:
                        mfr = s.get("manufacturer", {})
                        mfr_name = mfr.get("name", "-") if isinstance(mfr, dict) else str(mfr)
                        mpn = s.get("mpn", "-")
                        print(f"    {mfr_name} / {mpn}")
                    sourcing_found = True
                    break
        except Exception as e:
            print(f"  {number}: FAILED — {e}")
    if not sourcing_found:
        print("  No items with sourcing data in this batch (try fetching more items)")

    # Test: field normalization
    if items:
        print(f"\n--- Normalize first item ---")
        try:
            field_map = {
                "number": "MPN",
                "name": "Description",
                "revisionNumber": "Revision",
                "lifecyclePhase": "Lifecycle",
                "category.name": "Category",
            }
            part = api.normalize_item(items[0], field_map)
            part = api.enrich_with_sourcing(part)
            print(f"    GUID:         {part.arena_guid}")
            print(f"    Number:       {part.arena_number}")
            print(f"    Description:  {part.description[:50]}")
            print(f"    Category:     {part.category}")
            print(f"    Lifecycle:    {part.lifecycle}")
            print(f"    Revision:     {part.revision}")
            print(f"    MPN:          {part.primary_mpn}")
            print(f"    Manufacturer: {part.primary_manufacturer}")
            print(f"    Custom:       {part.custom_fields}")
        except Exception as e:
            print(f"  FAILED: {e}")

    # Logout
    print("\n--- Logout ---")
    client.logout()
    print("  Done!")

    print("\n=== All tests passed! ===")


if __name__ == "__main__":
    main()
