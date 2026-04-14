#!/usr/bin/env python3
"""Ingest every book in the user's library that has clips.

For each book, checks the sidecar endpoint for clip count (cheap), filters by
``--min-clips``, and runs ingest.py on the survivors. Idempotent: skips books
whose markdown already exists in the output directory.

Usage:
    batch.py [--min-clips N] [--output-dir DIR] [--dry-run] [--metadata-only]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import audible

HERE = Path(__file__).resolve().parent
LIST_LIBRARY = HERE / "list_library.py"
LIST_CLIPS = HERE / "list_clips.py"
INGEST = HERE / "ingest.py"

ROOT = Path.home() / ".audible-ingest"
AUTH_FILE = ROOT / "config" / "auth.json"
DEFAULT_OUTPUT = ROOT / "output"
SIDECAR_URL = "https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/sidecar"


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    return text or "untitled"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--min-clips", type=int, default=1)
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan, don't ingest",
    )
    p.add_argument(
        "--metadata-only",
        action="store_true",
        help="pass through to ingest.py",
    )
    args = p.parse_args()

    if not AUTH_FILE.exists():
        sys.exit(f"No auth at {AUTH_FILE}. Run auth.py first.")

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    auth = audible.Authenticator.from_file(str(AUTH_FILE))

    # Fetch library + annotation counts inline — avoids shelling out through
    # list_library.py just to re-parse text.
    print(">> Walking library and counting clips per book (this is cheap)...")
    books_with_clips: list[dict] = []
    with audible.Client(auth=auth) as client:
        page = 1
        library: list[dict] = []
        while True:
            resp = client.get(
                "1.0/library",
                num_results=50,
                page=page,
                response_groups="product_desc,product_attrs,contributors",
                sort_by="-PurchaseDate",
            )
            items = resp.get("items") or []
            if not items:
                break
            library.extend(items)
            if len(items) < 50:
                break
            page += 1

        for item in library:
            asin = item.get("asin")
            if not asin:
                continue
            title = item.get("title", "?")
            try:
                payload = client.get(SIDECAR_URL, type="AUDI", key=asin)
            except Exception as e:
                print(f"   [{asin}] sidecar error: {e}")
                continue
            records = (payload.get("payload") or {}).get("records") or []
            clip_count = sum(1 for r in records if r.get("type") == "audible.clip")
            if clip_count >= args.min_clips:
                books_with_clips.append({
                    "asin": asin,
                    "title": title,
                    "clips": clip_count,
                })

    books_with_clips.sort(key=lambda b: b["clips"], reverse=True)

    # Report plan.
    print(f"\nBooks with >= {args.min_clips} clips: {len(books_with_clips)}")
    total_clips = sum(b["clips"] for b in books_with_clips)
    # Whisper estimate: ~30s per clip at $0.006/min.
    est_cost = total_clips * 0.5 * 0.006 / 1  # 30s = 0.5 min
    print(f"Total clips to transcribe: {total_clips} (~${est_cost:.2f} Whisper)")
    print()
    for b in books_with_clips:
        slug = slugify(b["title"])
        target = output_dir / f"{slug}.md"
        status = "SKIP (exists)" if target.exists() else "ingest"
        print(f"  [{status:12s}] {b['asin']}  {b['clips']:3d} clips  {b['title']}")

    if args.dry_run:
        return

    # Actually ingest.
    print()
    for b in books_with_clips:
        slug = slugify(b["title"])
        target = output_dir / f"{slug}.md"
        if target.exists():
            continue
        print(f"\n=== {b['title']} ({b['asin']}, {b['clips']} clips) ===")
        cmd = [sys.executable, str(INGEST), b["asin"], "--output-dir", str(output_dir)]
        if args.metadata_only:
            cmd.append("--metadata-only")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"   ingest.py failed for {b['asin']} (continuing with next book)")


if __name__ == "__main__":
    main()
