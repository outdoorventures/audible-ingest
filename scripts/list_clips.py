#!/usr/bin/env python3
"""Show the clips, bookmarks, and standalone notes for a single book.

This hits Audible's internal sidecar (annotations) endpoint — it's fast,
doesn't touch the audiobook file, and doesn't spend any Whisper money.
Use it before a full ingest to answer "how many clips will this book cost?"

Usage:
    list_clips.py <ASIN>
    list_clips.py <ASIN> --json      # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import audible

AUTH_FILE = Path.home() / ".audible-ingest" / "config" / "auth.json"
SIDECAR_URL = "https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/sidecar"


def fmt_ms(ms: int | str) -> str:
    s = int(ms) // 1000
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fetch_annotations(asin: str) -> dict:
    if not AUTH_FILE.exists():
        sys.exit(f"No auth at {AUTH_FILE}. Run auth.py step1 / step2 first.")
    auth = audible.Authenticator.from_file(str(AUTH_FILE))
    with audible.Client(auth=auth) as client:
        return client.get(SIDECAR_URL, type="AUDI", key=asin)


def parse_records(payload: dict) -> dict:
    records = (payload.get("payload") or {}).get("records") or []
    clips = [r for r in records if r.get("type") == "audible.clip"]
    notes = [r for r in records if r.get("type") == "audible.note"]
    bookmarks = [r for r in records if r.get("type") == "audible.bookmark"]
    clips.sort(key=lambda c: int(c.get("startPosition", 0)))

    clip_positions = {c.get("startPosition") for c in clips}
    orphan_notes = [n for n in notes if n.get("startPosition") not in clip_positions]

    return {
        "clips": clips,
        "bookmarks": bookmarks,
        "orphan_notes": orphan_notes,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("asin")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    payload = fetch_annotations(args.asin)
    parsed = parse_records(payload)

    if args.json:
        out = {"asin": args.asin, **parsed}
        json.dump(out, sys.stdout, indent=2)
        return

    clips = parsed["clips"]
    bookmarks = parsed["bookmarks"]
    orphan_notes = parsed["orphan_notes"]

    print(f"ASIN: {args.asin}")
    print(f"  Clips:     {len(clips)}")
    print(f"  Bookmarks: {len(bookmarks)}")
    print(f"  Notes (standalone): {len(orphan_notes)}")
    print()

    if clips:
        print("=== Clips ===")
        for i, c in enumerate(clips, 1):
            start = c.get("startPosition", 0)
            end = c.get("endPosition", start)
            dur = (int(end) - int(start)) / 1000
            created = (c.get("creationTime") or "")[:10]
            note = (c.get("metadata") or {}).get("note", "")
            print(f"[{i:2d}] {fmt_ms(start)} → {fmt_ms(end)} ({dur:.0f}s) | {created}")
            if note:
                print(f"     NOTE: {note}")
        print()

    if orphan_notes:
        print("=== Standalone notes (no clip) ===")
        for n in orphan_notes:
            created = (n.get("creationTime") or "")[:10]
            pos = n.get("startPosition", 0)
            text = n.get("text", "")
            print(f"  [{created}] @ {fmt_ms(pos)}: {text}")
        print()

    # Rough cost hint for Whisper if the user proceeds.
    if clips:
        total_sec = sum(
            (int(c.get("endPosition", c.get("startPosition", 0))) - int(c.get("startPosition", 0))) / 1000
            for c in clips
        )
        total_min = total_sec / 60
        cost = total_min * 0.006
        print(f"Whisper cost estimate for all clips: ~${cost:.2f} ({total_min:.1f} min audio)")


if __name__ == "__main__":
    main()
