#!/usr/bin/env bash
# One-time setup for audible-ingest.
# Creates a venv at ~/.audible-ingest/venv/ and installs Python deps.
# Also writes the audible-cli config pointing at ~/.audible-ingest/config/auth.json.

set -euo pipefail

ROOT="$HOME/.audible-ingest"
CONFIG_DIR="$ROOT/config"
VENV="$ROOT/venv"
BOOKS="$ROOT/books"
CLIPS="$ROOT/clips"
OUTPUT="$ROOT/output"

mkdir -p "$CONFIG_DIR" "$BOOKS" "$CLIPS" "$OUTPUT"

# --- Python venv ---
if [[ ! -x "$VENV/bin/python" ]]; then
  echo ">> Creating venv at $VENV"
  python3 -m venv "$VENV"
else
  echo ">> venv already exists at $VENV"
fi

# --- Reuse existing ~/.audible auth if the user already has audible-cli set up ---
# audible-cli's default config lives at ~/.audible. If we see auth there and not
# in our dir, link it so first-time users who already have it don't re-OAuth.
LEGACY_CONFIG="$HOME/.audible"
if [[ -d "$LEGACY_CONFIG" && ! -f "$CONFIG_DIR/auth.json" ]]; then
  # Find the first .json auth file in the legacy dir.
  LEGACY_AUTH="$(ls "$LEGACY_CONFIG"/*.json 2>/dev/null | head -n1 || true)"
  if [[ -n "$LEGACY_AUTH" ]]; then
    echo ">> Found existing audible auth at $LEGACY_AUTH — linking it in"
    ln -sf "$LEGACY_AUTH" "$CONFIG_DIR/auth.json"
  fi
fi

# --- Upgrade pip and install deps ---
echo ">> Installing Python dependencies"
"$VENV/bin/pip" install --upgrade pip >/dev/null
# httpx comes in as a transitive dep of audible (pinned to <0.24 by audible
# 0.8.2). Don't re-pin it here or resolver will conflict.
"$VENV/bin/pip" install \
  "audible>=0.8.2" \
  "audible-cli>=0.3.2" \
  >/dev/null

# --- audible-cli config ---
# audible-cli reads AUDIBLE_CONFIG_DIR from env. We set it at call time in our
# scripts. Write config.toml pointing at auth.json so audible-cli can find it.
if [[ ! -f "$CONFIG_DIR/config.toml" ]]; then
  cat > "$CONFIG_DIR/config.toml" <<'EOF'
title = "audible-ingest config"

[APP]
primary_profile = "default"

[profile.default]
auth_file = "auth.json"
country_code = "us"
EOF
  echo ">> Wrote $CONFIG_DIR/config.toml"
fi

echo
echo "Setup complete. Locations:"
echo "  venv:    $VENV"
echo "  config:  $CONFIG_DIR"
echo "  books:   $BOOKS"
echo "  clips:   $CLIPS"
echo "  output:  $OUTPUT"
echo
if [[ -f "$CONFIG_DIR/auth.json" || -L "$CONFIG_DIR/auth.json" ]]; then
  echo "Auth: already configured at $CONFIG_DIR/auth.json"
else
  echo "Next: run auth.py step1 to start OAuth."
fi
