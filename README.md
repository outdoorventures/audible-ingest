# audible-ingest

Extract your Audible clips, bookmarks, and typed notes, transcribe the clip
audio with OpenAI Whisper, and emit a tidy markdown file per book.

This is what the Audible app *should* let you do: get your own highlights out
in a format you can search, sync, or paste into a note app.

## What it does

For each book you ingest, you get a markdown file like:

```markdown
# The Psychology of Money

**Author:** Morgan Housel
**Narrator:** Chris Hill
**ASIN:** B08D9TXF3H
**Runtime:** 5h 55m
**Clips:** 36
**Extracted on:** 2026-04-14

---

## Clips

### 1

**Timestamp:** 00:02:12 | **Duration:** 30s | **Created:** 2021-07-08

> A genius is the man who can do the average thing when everyone else around
> him is losing his mind.

**My note:** Reminds me of Buffett in 2008.
```

Book text comes from Whisper transcription. Your typed notes are preserved
verbatim.

## Install

```bash
git clone https://github.com/outdoorventures/audible-ingest.git
cd audible-ingest
bash scripts/setup.sh
```

`setup.sh` creates a Python venv at `~/.audible-ingest/venv/` and installs
`audible`, `audible-cli`, and `httpx`. You also need:

- **ffmpeg** — `brew install ffmpeg` (macOS) or your distro's package manager.
  Used to decrypt and slice clips from the AAX file.
- **OpenAI API key** — export as `OPENAI_API_KEY` for the session. Needed only
  if you want Whisper transcriptions; skip by passing `--metadata-only`.

### Claude Code skill (optional)

If you use [Claude Code](https://docs.claude.com/claude-code), symlink this
repo into your skills directory and Claude will auto-trigger the pipeline on
requests like "export my Audible clips" or "ingest *The Psychology of Money*":

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)" ~/.claude/skills/audible-ingest
```

## First-time auth

Audible's OAuth is browser-based and can't be scripted end-to-end, so auth is
two steps:

```bash
~/.audible-ingest/venv/bin/python scripts/auth.py step1
# Open the printed URL, log in, copy the full redirect URL from your browser
~/.audible-ingest/venv/bin/python scripts/auth.py step2 '<paste full redirect URL>'
```

Tokens are saved to `~/.audible-ingest/config/auth.json`. You only do this
once per Audible account.

## Usage

### List your library

```bash
~/.audible-ingest/venv/bin/python scripts/list_library.py
```

### Preview a book's clips (free, no download)

```bash
~/.audible-ingest/venv/bin/python scripts/list_clips.py B08D9TXF3H
```

Prints clip count, positions, any typed notes, and a Whisper cost estimate.

### Ingest one book

```bash
export OPENAI_API_KEY=sk-...
~/.audible-ingest/venv/bin/python scripts/ingest.py B08D9TXF3H
```

Flags:

- `--metadata-only` — skip the download + Whisper steps; emit markdown with
  clip positions and your typed notes only. Useful without an OpenAI key.
- `--output-dir DIR` — where to write the markdown (default:
  `~/.audible-ingest/output/`).
- `--wider-window` — extract 60s windows starting 15s earlier. Use this if a
  clip's transcript came out empty (usually means it sits on a silent chapter
  boundary).

### Ingest everything

```bash
~/.audible-ingest/venv/bin/python scripts/batch.py --min-clips 3
```

Walks your library, keeps only books with ≥3 clips, and ingests each. Prints
a Whisper cost estimate before starting. Idempotent — skips books whose
markdown already exists.

## How it works

1. `audible` Python library handles OAuth and authenticated API calls.
2. Annotations come from Audible's internal sidecar endpoint
   (`cde-ta-g7g.amazon.com/FionaCDEServiceEngine/sidecar?type=AUDI&key=ASIN`).
   This returns your clips, bookmarks, and typed notes with ms-precision
   positions.
3. `audible-cli download --aax-fallback` grabs the DRM'd AAX file.
4. ffmpeg decrypts on the fly with `-activation_bytes <your-key> -ss <start>
   -t <dur>`. No full-file decryption needed — we stream out each clip
   directly. Your activation_bytes are cached at
   `~/.audible-ingest/config/activation_bytes`.
5. Each mp3 clip goes to the Whisper API (`whisper-1` model, `en`, ~$0.006/min).
6. A markdown renderer stitches metadata + clips + transcripts + typed notes.

Transcripts are saved per clip as they complete, so reruns only re-transcribe
what's missing.

## Troubleshooting

**"Clip transcript is empty"** — the clip starts on a silent chapter
boundary. Re-run with `--wider-window` to extract a 60s window starting 15s
earlier.

**"InvalidValue" during auth step2** — the authorization code in your redirect
URL is single-use and time-limited. Re-run step1 and complete step2 within a
minute or two.

**"activation_bytes not found"** — Amazon's DRM license server occasionally
rate-limits. Wait 5 minutes and retry; once cached it won't be fetched again.

**AAXC vs AAX** — newer Audible downloads come as AAXC, which requires a
per-file voucher instead of account-wide `activation_bytes`. This tool passes
`--aax-fallback` to prefer AAX; if only AAXC is available, the extract step
will fail and you'll want to check `audible-cli`'s docs for voucher handling.

## Cost

Whisper is ~$0.006/minute of audio. A 30s clip is about $0.003. A book with
50 clips costs around $0.15.

## Legal

This tool only works on audiobooks you already own. The `activation_bytes`
key is tied to your Audible account — it won't decrypt anyone else's files.
Don't commit `~/.audible-ingest/` or share your `auth.json` /
`activation_bytes`.

Respect Audible's terms of service. Don't redistribute decrypted files.

## Layout

```
audible-ingest/
├── SKILL.md              # entry point for the Claude Code skill
├── scripts/
│   ├── setup.sh          # venv + deps
│   ├── auth.py           # OAuth step1 / step2
│   ├── list_library.py   # all books
│   ├── list_clips.py     # per-book preview
│   ├── ingest.py         # full pipeline
│   └── batch.py          # every book with clips
├── references/
│   └── troubleshooting.md
├── requirements.txt
├── LICENSE
└── README.md
```

Runtime state (never committed): `~/.audible-ingest/`.

## Contributing

Small scope skill — issues and PRs welcome for:

- Non-US Audible marketplaces (setup assumes `.com`)
- AAXC voucher handling
- Additional output formats (json, csv, opml)

## License

MIT. See [LICENSE](LICENSE).
