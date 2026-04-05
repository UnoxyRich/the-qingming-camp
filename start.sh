#!/usr/bin/env bash
# Start the CTF bot (Attacker role) in empty-arena debug mode by default.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

export PYTHONIOENCODING=utf-8

TEAM_NUM="${TEAM_NUM:-7891114514}"
PLAYER_NUM="${PLAYER_NUM:-2}"
AGAINST_TEAM="${AGAINST_TEAM:-none}"
PER_TEAM_PLAYER="${PER_TEAM_PLAYER:-2}"
MAP_MODE="${MAP_MODE:-fixed}"

cd "$SCRIPT_DIR"

python main.py \
  --my-team "$TEAM_NUM" \
  --my-no "$PLAYER_NUM" \
  --against "$AGAINST_TEAM" \
  --per-team-player "$PER_TEAM_PLAYER" \
  --map "$MAP_MODE" \
  --strategy "ctf_strategy.AttackerStrategy" \
  --verbose
