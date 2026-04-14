# Troubleshooting

Failure modes the skill should recognize and recover from, in rough order of
how often they come up.

## Auth

### "InvalidValue" on `auth.py step2`
Authorization codes in the redirect URL are single-use and time-limited
(roughly 60–90 seconds). Re-run `auth.py step1` and paste the new redirect URL
into `step2` quickly.

### "Domain mismatch" / country code errors
`auth.py` hard-codes `country_code="us"` and marketplace `AF2M0KC94RCEA`.
If the user's Audible account is on `.co.uk`, `.de`, etc., edit `auth.py` and
set the correct marketplace ID from
[audible's locale map](https://github.com/mkb79/Audible/blob/master/src/audible/localization.py).

### `~/.audible-ingest/config/auth.json` keeps getting re-created empty
If step2 crashed mid-write, delete the empty file and re-run step2 with the
redirect URL. If that URL has expired, start from step1.

## Library / sidecar

### Book is in the app but not in `list_library.py` output
The library endpoint paginates in chunks of 50. If a book is on a later page
and pagination stopped early, rerun — occasional transient failures. If it's
consistently missing, confirm it's not in a "Wish List" (those don't count as
owned).

### `sidecar` returns 404 or an empty `records` array
Means you have no clips/notes/bookmarks on that book. Expected for books you
haven't annotated. `list_clips.py` will show `Clips: 0`.

### `sidecar` returns 403
Auth token expired. Delete `~/.audible-ingest/config/auth.json` and re-run
`auth.py step1` / `step2`.

## Download / DRM

### `audible-cli download` fails with "not available as AAX"
Amazon has started serving some titles as AAXC only. AAXC uses a per-file
voucher, not the account-wide `activation_bytes`. Options:

1. Check if another format is available: `audible-cli download --asin X
   --aaxc` then inspect the voucher JSON alongside the file.
2. Decrypt with [aaxtomp3](https://github.com/KrumpetPirate/AAXtoMP3) or
   [audible-tools](https://github.com/inAudible-NG) using the voucher.
3. Fall back to `--metadata-only` for that book so at least the clip
   positions + typed notes are captured.

### `activation_bytes not found` / 503 from license server
Amazon rate-limits the license server. Wait 5 minutes and retry `ingest.py`
— `get_activation_bytes` will try again. Once cached at
`~/.audible-ingest/config/activation_bytes` it's reused forever (until the
user re-registers on a new device).

### `ffmpeg: Activation bytes seem to be incorrect`
The cached activation_bytes is wrong. Delete
`~/.audible-ingest/config/activation_bytes` and re-run; the next run will
fetch fresh bytes.

## Extraction / transcription

### Clip's transcript is empty
The 30s window starts on silence — usually a chapter boundary. Re-run with
`--wider-window`:

```bash
scripts/ingest.py <ASIN> --wider-window
```

This extends each clip to 60s starting 15s earlier. Forces re-extraction even
if the mp3 already exists.

### Whisper returns garbled/hallucinated text for a clip
Whisper occasionally invents text during silence at the start. The skill's
Whisper prompt ("$Book by $Author. Audiobook narrated by $Narrator.") helps
anchor the model; if output is still bad for a specific clip, delete that
clip's mp3 and rerun with `--wider-window`.

### `httpx.ReadTimeout` during Whisper call
Transient. The transcripts file is saved after each successful clip, so rerun
`ingest.py` and it'll resume from where it left off.

## Markdown output

### Book slug collision (two books share a title)
`slugify()` is deterministic per title, so two different ASINs with the same
title will write to the same file. Rare in practice (different editions have
different "unabridged" suffixes in Audible). If it hits you, pass
`--output-dir` pointing at a per-ASIN subdir.

### Wrong author on output (e.g. "Various")
Audible's `contributors` response group sometimes returns a collective name
for anthologies. Edit the output markdown's author field manually — this
script just reflects what the API returned.
