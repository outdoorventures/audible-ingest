---
name: audible-ingest
description: >
  Extract Audible audiobook clips and user notes, then transcribe the
  clip audio with OpenAI Whisper, producing one structured markdown
  file per book. Use this skill whenever the user wants to get their
  Audible clips, bookmarks, notes, or highlights out of Audible — even
  if they don't explicitly mention transcription or Whisper. Triggers on
  phrases like "my audible clips", "ingest an audiobook", "transcribe
  my bookmarks", "export audible notes", "get my audible highlights",
  "audible clip extraction", or any request involving exporting
  audiobook annotations to text. Also trigger if the user mentions
  a specific audiobook title or ASIN in the context of extracting
  notes or transcribing clips.
allowed-tools: Bash Read Write Edit Glob Grep
---

# Audible Ingest

Extract an Audible book's clips + user notes, transcribe the clip audio with OpenAI Whisper, and write one structured markdown file per book.

This skill wraps a proven pipeline:

1. OAuth with Audible (browser-based, one-time)
2. Fetch annotations from Audible's internal sidecar endpoint (clips, bookmarks, notes with ms-precise positions)
3. Download DRM-protected AAX via `audible-cli`
4. Extract each clip segment with `ffmpeg -activation_bytes` (on-the-fly decrypt, no full-file decrypt needed)
5. Transcribe each clip with OpenAI Whisper (~$0.006/min)
6. Emit markdown: book metadata + numbered clips with timestamps, transcripts, and user's typed notes

## Environment

All runtime state lives under `~/.audible-ingest/`:

- `~/.audible-ingest/venv/` — Python virtual environment (auto-created by setup)
- `~/.audible-ingest/config/auth.json` — Audible OAuth tokens (written by auth flow)
- `~/.audible-ingest/config/activation_bytes` — Cached DRM key for this account
- `~/.audible-ingest/books/` — Downloaded AAX files (large; gitignored)
- `~/.audible-ingest/clips/<ASIN>/` — Extracted mp3 segments (transient)
- `~/.audible-ingest/output/` — Default output dir for generated markdown (overridable)

Scripts live at the repo root: `scripts/setup.sh`, `scripts/auth.py`, `scripts/list_library.py`, `scripts/list_clips.py`, `scripts/ingest.py`, `scripts/batch.py`.

## First-Run Setup (do this before anything else)

Check whether the environment is ready:

```bash
test -f ~/.audible-ingest/config/auth.json && echo AUTH_OK || echo AUTH_NEEDED
test -f ~/.audible-ingest/venv/bin/python && echo VENV_OK || echo VENV_NEEDED
command -v ffmpeg > /dev/null && echo FFMPEG_OK || echo FFMPEG_NEEDED
```

If anything is NEEDED, run setup in this order:

1. **ffmpeg**: if missing, tell the user to `brew install ffmpeg` (macOS) or use their distro's package manager. Do NOT install silently — they may prefer a different package manager.
2. **venv + deps**: `bash <skill-dir>/scripts/setup.sh` — creates `~/.audible-ingest/venv/` and installs `audible`, `audible-cli`, `httpx`, plus configures audible-cli to read from `~/.audible-ingest/config/auth.json`.
3. **OPENAI_API_KEY**: check `echo "${OPENAI_API_KEY:-NOT_SET}"`. If not set, ask the user to paste it. Do NOT write it to disk or to shell rc files unless they ask — use it inline for the session.
4. **Audible OAuth**: `~/.audible-ingest/venv/bin/python <skill-dir>/scripts/auth.py step1` — prints a login URL. Tell the user to open it, log in, then copy the full redirect URL (which will start with `https://www.amazon.com/ap/maplanding...`) and paste it back. Then run `scripts/auth.py step2 '<full redirect URL>'`.
5. **activation_bytes** (one-time per account): runs automatically the first time `ingest.py` is called; cached at `~/.audible-ingest/config/activation_bytes`.

## Usage

### List the library

```bash
~/.audible-ingest/venv/bin/python <skill-dir>/scripts/list_library.py
```

Prints all books with ASIN, title, author, narrator, and runtime. Use this to find the ASIN the user wants, or to confirm a title is in their library before trying to ingest.

### List clips for a book (cheap, no download, no Whisper)

```bash
~/.audible-ingest/venv/bin/python <skill-dir>/scripts/list_clips.py <ASIN>
```

Fetches the sidecar annotations and prints clip count, positions, and any typed notes. Use this to:
- Check if a book has any clips before spending Whisper money
- Show the user what's in a book so they can decide whether to ingest
- Build a batch queue ("ingest everything with 10+ clips")

### Ingest one book

```bash
~/.audible-ingest/venv/bin/python <skill-dir>/scripts/ingest.py <ASIN> [--output-dir DIR] [--metadata-only]
```

- With no flags: full pipeline (download → extract → transcribe → markdown)
- `--metadata-only`: skip the download/transcribe step; emit markdown with clip positions + user's typed notes only. Useful if the user doesn't have an OPENAI_API_KEY or wants to try without paying for Whisper.
- `--output-dir`: where to write the markdown. Default is `~/.audible-ingest/output/`.

If the user gives a title instead of an ASIN, use `list_library.py` first to resolve it; suggest confirming if the match is ambiguous.

### Ingest all books with clips

```bash
~/.audible-ingest/venv/bin/python <skill-dir>/scripts/batch.py [--min-clips N] [--output-dir DIR]
```

Walks the library, keeps only books with >= `--min-clips` clips (default 1), and ingests each. Idempotent — skips books whose markdown already exists.

### Output format

Each book becomes `<output-dir>/<book-slug>.md`:

```markdown
# Book Title

**Author:** Author Name
**Narrator:** Narrator Name
**ASIN:** B0XXXXXXXX
**Runtime:** 5h 55m
**Clips:** 36
**Extracted on:** 2026-04-14

---

## Clips

### 1

**Timestamp:** 00:02:12 | **Duration:** 30s | **Created:** 2021-07-08

> Transcribed audio from the book.

**My note:** The user's typed note, if they added one.
```

The format is deliberately simple so downstream tools (wiki ingesters, note apps, search indexers) can parse it easily.

## Common Tasks

### "What do I have in my Audible library?"

Run `list_library.py`. Present the books in a readable table with ASINs. If the user asks which have clips, follow up with `list_clips.py` for each — or use the `--min-clips` filter in `batch.py` to get the short list.

### "Extract clips from [book title]"

1. Resolve title to ASIN via `list_library.py` — confirm the match with the user if any ambiguity
2. Run `list_clips.py <ASIN>` first; tell the user how many clips exist and what the Whisper cost will be (~$0.006 × clips × 0.5 min)
3. Run `ingest.py <ASIN>` if they confirm

### "Do all my books"

Run `batch.py --min-clips 3` to skip books with trivial clip counts. Report progress as each book finishes. Estimate total cost before starting.

### "I already have auth set up elsewhere"

If `~/.audible` exists from a prior `audible-cli` install, the skill can reuse it — `setup.sh` detects and links. Don't re-run OAuth.

## Troubleshooting

- **"Clip transcript is empty"** — the clip starts on silence at a chapter boundary. Re-run with `--wider-window` flag (extends window to 60s starting 15s earlier). See `references/troubleshooting.md` for the full failure modes.
- **"InvalidValue" on auth** — the authorization code expired between step1 and step2 (codes are single-use and time-limited). Re-run `auth.py step1` and complete quickly.
- **"activation_bytes not found"** — Audible's DRM license server occasionally rate-limits. Wait 5 minutes and retry.

## Security and Etiquette

- This skill decrypts AAX files using activation_bytes tied to the user's Audible account. It only works on audiobooks they legally own.
- Do not commit `~/.audible-ingest/` to git. The repo's `.gitignore` excludes it.
- Do not share `auth.json` or `activation_bytes` — those are account credentials.
- OPENAI_API_KEY should come from the environment. Never write it to files the skill creates.

## Full Docs

For the publishable README (install, contributing, legal), see `README.md` in the repo root.
