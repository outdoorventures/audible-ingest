#!/usr/bin/env python3
"""Ingest one Audible book: clips + typed notes → transcribed markdown.

Pipeline:
    1. Fetch book metadata (title, author, narrator, runtime)
    2. Fetch annotations (sidecar) → clip positions + typed notes
    3. [skipped if --metadata-only] Download AAX via audible-cli
    4. [skipped if --metadata-only] ffmpeg extract each clip using activation_bytes
    5. [skipped if --metadata-only] Whisper-transcribe each clip (resume-safe)
    6. Emit `<output-dir>/<book-slug>.md`

Usage:
    ingest.py <ASIN> [--output-dir DIR] [--metadata-only] [--wider-window]

Env:
    OPENAI_API_KEY  required unless --metadata-only
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import audible
import httpx


ROOT = Path.home() / ".audible-ingest"
CONFIG_DIR = ROOT / "config"
AUTH_FILE = CONFIG_DIR / "auth.json"
ACTIVATION_CACHE = CONFIG_DIR / "activation_bytes"
BOOKS_DIR = ROOT / "books"
CLIPS_DIR = ROOT / "clips"
DEFAULT_OUTPUT = ROOT / "output"

SIDECAR_URL = "https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/sidecar"


# ---- helpers -------------------------------------------------------------

def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    return text or "untitled"


def fmt_ms(ms: int | str) -> str:
    s = int(ms) // 1000
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_runtime(minutes: int | None) -> str:
    if not minutes:
        return "?"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m}m" if h else f"{m}m"


def require_auth() -> audible.Authenticator:
    if not AUTH_FILE.exists():
        sys.exit(f"No auth at {AUTH_FILE}. Run auth.py step1 / step2 first.")
    return audible.Authenticator.from_file(str(AUTH_FILE))


# ---- pipeline stages -----------------------------------------------------

def fetch_metadata(client: audible.Client, asin: str) -> dict:
    """Return the library item for a single ASIN."""
    # `1.0/library/{asin}` returns one item in `{"item": {...}}`.
    resp = client.get(
        f"1.0/library/{asin}",
        response_groups="product_desc,product_attrs,contributors",
    )
    return resp.get("item") or {}


def fetch_annotations(client: audible.Client, asin: str) -> dict:
    return client.get(SIDECAR_URL, type="AUDI", key=asin)


def parse_clips(payload: dict) -> list[dict]:
    records = (payload.get("payload") or {}).get("records") or []
    clips = [r for r in records if r.get("type") == "audible.clip"]
    clips.sort(key=lambda c: int(c.get("startPosition", 0)))
    out = []
    for i, c in enumerate(clips, 1):
        start = int(c.get("startPosition", 0))
        end = int(c.get("endPosition", start))
        out.append({
            "index": i,
            "start_ms": start,
            "end_ms": end,
            "start_formatted": fmt_ms(start),
            "duration_s": (end - start) / 1000,
            "note": (c.get("metadata") or {}).get("note", ""),
            "created": (c.get("creationTime") or "")[:10],
            "annotation_id": c.get("annotationId", ""),
        })
    return out


def parse_orphan_notes(payload: dict, clips: list[dict]) -> list[dict]:
    records = (payload.get("payload") or {}).get("records") or []
    notes = [r for r in records if r.get("type") == "audible.note"]
    clip_positions = {c["start_ms"] for c in clips}
    return [
        {
            "position_ms": int(n.get("startPosition", 0)),
            "position_formatted": fmt_ms(n.get("startPosition", 0)),
            "text": n.get("text", ""),
            "created": (n.get("creationTime") or "")[:10],
        }
        for n in notes
        if int(n.get("startPosition", 0)) not in clip_positions
    ]


def get_activation_bytes(auth: audible.Authenticator) -> str:
    if ACTIVATION_CACHE.exists():
        return ACTIVATION_CACHE.read_text().strip()
    print(">> Fetching activation_bytes from Amazon (one-time per account)...")
    bytes_hex = auth.get_activation_bytes(extract=True)
    if isinstance(bytes_hex, bytes):
        bytes_hex = bytes_hex.decode()
    ACTIVATION_CACHE.write_text(bytes_hex.strip())
    print(f"   cached at {ACTIVATION_CACHE}")
    return bytes_hex.strip()


def download_aax(asin: str) -> Path:
    """Download AAX for ASIN into BOOKS_DIR/<ASIN>/; return the .aax path."""
    dest = BOOKS_DIR / asin
    dest.mkdir(parents=True, exist_ok=True)

    # Skip if we already have an aax/aaxc for this ASIN.
    existing = sorted(dest.glob("*.aax")) + sorted(dest.glob("*.aaxc"))
    if existing:
        print(f">> AAX already downloaded: {existing[0].name}")
        return existing[0]

    env = os.environ.copy()
    env["AUDIBLE_CONFIG_DIR"] = str(CONFIG_DIR)

    # Use the audible-cli that ships in our venv (next to the running python).
    audible_cli = str(Path(sys.executable).parent / "audible")
    if not Path(audible_cli).exists():
        audible_cli = shutil.which("audible") or "audible"
    cmd = [
        audible_cli,
        "-v", "ERROR",       # quiet logging (top-level flag, before subcommand)
        "download",
        "--asin", asin,
        "--aax-fallback",
        "--output-dir", str(dest),
        "--no-confirm",      # don't prompt
    ]
    print(f">> Downloading AAX for {asin}...")
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(
            f"audible-cli download failed (exit {result.returncode}):\n"
            f"  stdout: {result.stdout}\n"
            f"  stderr: {result.stderr}"
        )
    found = sorted(dest.glob("*.aax")) + sorted(dest.glob("*.aaxc"))
    if not found:
        sys.exit(f"Download finished but no .aax/.aaxc found in {dest}")
    print(f"   got {found[0].name}")
    return found[0]


def extract_clip(
    aax_path: Path,
    activation_bytes: str,
    clip: dict,
    out_path: Path,
    wider_window: bool = False,
) -> bool:
    """Extract one clip segment with ffmpeg. Returns True on success."""
    if out_path.exists():
        return True

    if wider_window:
        start_s = max(0, clip["start_ms"] / 1000 - 15)
        duration_s = 60.0
    else:
        start_s = clip["start_ms"] / 1000
        # Audible clips are usually 30s; enforce a minimum so Whisper has enough.
        duration_s = max(clip["duration_s"], 30.0)

    cmd = [
        "ffmpeg", "-y",
        "-activation_bytes", activation_bytes,
        "-ss", str(start_s),
        "-i", str(aax_path),
        "-t", str(duration_s),
        "-acodec", "libmp3lame",
        "-ab", "64k",
        "-ar", "22050",
        "-ac", "1",
        "-loglevel", "error",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"   ffmpeg ERROR on clip {clip['index']}: {result.stderr[:300]}")
        return False
    return True


def transcribe_clip(
    mp3_path: Path,
    whisper_prompt: str,
    api_key: str,
) -> str:
    with open(mp3_path, "rb") as f:
        files = {"file": (mp3_path.name, f, "audio/mpeg")}
        form = {
            "model": "whisper-1",
            "response_format": "verbose_json",
            "language": "en",
            "prompt": whisper_prompt,
        }
        resp = httpx.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files=files,
            data=form,
            timeout=120.0,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Whisper {resp.status_code}: {resp.text[:200]}")
    return resp.json().get("text", "").strip()


# ---- markdown emitter ----------------------------------------------------

def render_markdown(
    meta: dict,
    clips: list[dict],
    orphan_notes: list[dict],
    transcripts: dict[int, str],
    metadata_only: bool,
) -> str:
    title = meta.get("title", "Untitled")
    authors = ", ".join(a.get("name", "") for a in (meta.get("authors") or [])) or "?"
    narrators = ", ".join(n.get("name", "") for n in (meta.get("narrators") or [])) or "?"
    asin = meta.get("asin", "?")
    runtime = fmt_runtime(meta.get("runtime_length_min"))
    today = datetime.date.today().isoformat()

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"**Author:** {authors}")
    lines.append(f"**Narrator:** {narrators}")
    lines.append(f"**ASIN:** {asin}")
    lines.append(f"**Runtime:** {runtime}")
    lines.append(f"**Clips:** {len(clips)}")
    lines.append(f"**Extracted on:** {today}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if clips:
        lines.append("## Clips")
        lines.append("")
        for c in clips:
            lines.append(f"### {c['index']}")
            lines.append("")
            lines.append(
                f"**Timestamp:** {c['start_formatted']} | "
                f"**Duration:** {c['duration_s']:.0f}s | "
                f"**Created:** {c['created']}"
            )
            lines.append("")
            text = transcripts.get(c["index"], "").strip()
            if metadata_only:
                lines.append("> *(transcription skipped — re-run without --metadata-only)*")
            elif text:
                lines.append(f"> {text}")
            else:
                lines.append("> *(silence — no transcribable audio in this segment)*")
            lines.append("")
            note = c.get("note", "").strip()
            if note:
                lines.append(f"**My note:** {note}")
                lines.append("")

    if orphan_notes:
        lines.append("## Standalone Notes")
        lines.append("")
        lines.append("*Typed notes not attached to a clip.*")
        lines.append("")
        for n in orphan_notes:
            lines.append(f"- **{n['position_formatted']}** ({n['created']}): {n['text']}")
        lines.append("")

    return "\n".join(lines)


# ---- main ----------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("asin")
    p.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT),
        help=f"where to write the markdown (default: {DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--metadata-only",
        action="store_true",
        help="skip download/transcribe; emit markdown with clip positions + notes only",
    )
    p.add_argument(
        "--wider-window",
        action="store_true",
        help="extract 60s windows starting 15s earlier (rescue silent clip starts)",
    )
    args = p.parse_args()

    asin = args.asin.strip()
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    auth = require_auth()

    with audible.Client(auth=auth) as client:
        print(f">> Fetching metadata for {asin}")
        meta = fetch_metadata(client, asin)
        if not meta:
            sys.exit(f"No metadata returned for {asin}. Is it in your library?")
        title = meta.get("title", "(untitled)")
        print(f"   {title}")

        print(">> Fetching annotations (sidecar)")
        payload = fetch_annotations(client, asin)

    clips = parse_clips(payload)
    orphan_notes = parse_orphan_notes(payload, clips)
    print(f"   clips: {len(clips)}  standalone notes: {len(orphan_notes)}")

    transcripts: dict[int, str] = {}

    if not args.metadata_only and clips:
        # --- we need an OpenAI key + ffmpeg from here on ---
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            sys.exit(
                "OPENAI_API_KEY not set. Either export it, or re-run with "
                "--metadata-only to skip transcription."
            )
        if not shutil.which("ffmpeg"):
            sys.exit("ffmpeg not found on PATH. Install it (brew install ffmpeg) and retry.")

        activation_bytes = get_activation_bytes(auth)
        aax_path = download_aax(asin)

        # Per-ASIN clip directory; we keep these around between runs so reruns
        # only transcribe what's missing.
        book_clips_dir = CLIPS_DIR / asin
        book_clips_dir.mkdir(parents=True, exist_ok=True)

        # Transcript cache lives alongside the clips.
        transcripts_path = book_clips_dir / "transcripts.json"
        if transcripts_path.exists():
            transcripts = {int(k): v for k, v in json.loads(transcripts_path.read_text()).items()}

        authors = ", ".join(a.get("name", "") for a in (meta.get("authors") or []))
        narrators = ", ".join(n.get("name", "") for n in (meta.get("narrators") or []))
        whisper_prompt = f"{title} by {authors}. Audiobook narrated by {narrators}."

        print(f">> Extracting + transcribing {len(clips)} clip(s)")
        for c in clips:
            mp3_path = book_clips_dir / f"clip_{c['index']:02d}.mp3"
            # Extract if missing (or force re-extract when --wider-window).
            if args.wider_window and mp3_path.exists():
                mp3_path.unlink()
            ok = extract_clip(aax_path, activation_bytes, c, mp3_path, args.wider_window)
            if not ok:
                continue

            if c["index"] in transcripts and not args.wider_window:
                print(f"   [{c['index']:2d}] cached")
                continue
            try:
                text = transcribe_clip(mp3_path, whisper_prompt, api_key)
            except Exception as e:
                print(f"   [{c['index']:2d}] whisper failed: {e}")
                continue
            transcripts[c["index"]] = text
            # Save transcripts after each clip so a mid-run failure is resumable.
            transcripts_path.write_text(json.dumps(transcripts, indent=2))
            preview = text[:70].replace("\n", " ")
            print(f"   [{c['index']:2d}] {c['start_formatted']} → {preview}...")

    markdown = render_markdown(meta, clips, orphan_notes, transcripts, args.metadata_only)
    slug = slugify(title)
    out_path = output_dir / f"{slug}.md"
    out_path.write_text(markdown)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
