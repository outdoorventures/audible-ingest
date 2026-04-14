#!/usr/bin/env python3
"""List the user's Audible library.

Prints one row per book with ASIN, title, author(s), narrator(s), and runtime.
Use --json to emit the raw list for downstream scripting.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import audible

AUTH_FILE = Path.home() / ".audible-ingest" / "config" / "auth.json"


def fetch_library() -> list[dict]:
    if not AUTH_FILE.exists():
        sys.exit(f"No auth at {AUTH_FILE}. Run auth.py step1 / step2 first.")
    auth = audible.Authenticator.from_file(str(AUTH_FILE))

    all_items: list[dict] = []
    with audible.Client(auth=auth) as client:
        page = 1
        while True:
            resp = client.get(
                "1.0/library",
                num_results=50,
                page=page,
                response_groups="product_desc,product_attrs,contributors",
                sort_by="-PurchaseDate",
            )
            items = resp.get("items", [])
            if not items:
                break
            all_items.extend(items)
            if len(items) < 50:
                break
            page += 1
    return all_items


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--json", action="store_true", help="print raw JSON to stdout")
    args = p.parse_args()

    items = fetch_library()

    if args.json:
        json.dump(items, sys.stdout, indent=2)
        return

    print(f"Total: {len(items)} books\n")
    for i, item in enumerate(items, 1):
        asin = item.get("asin", "?")
        title = item.get("title", "?")
        authors = ", ".join(a.get("name", "") for a in (item.get("authors") or []))
        # Narrators can be null (not just missing) — guard against None iteration.
        narrators = ", ".join(n.get("name", "") for n in (item.get("narrators") or []))
        runtime_min = item.get("runtime_length_min") or 0
        hours = runtime_min / 60
        print(f"[{i:3d}] {asin}  {title}")
        print(f"      by {authors or '?'} | narr. {narrators or '?'} | {hours:.1f}h")


if __name__ == "__main__":
    main()
