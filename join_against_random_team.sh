#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing virtual environment interpreter at $PYTHON_BIN" >&2
  echo "Create the environment first or re-run dependency setup." >&2
  exit 1
fi

cd "$ROOT_DIR"

export PYTHONIOENCODING=utf-8

TEAM_NUM="${TEAM_NUM:-26}"
AGAINST_TEAM="${AGAINST_TEAM:-random}"
PER_TEAM_PLAYER="${PER_TEAM_PLAYER:-2}"
MAP_MODE="${MAP_MODE:-random}"
BOT_ONE_STRATEGY="${BOT_ONE_STRATEGY:-ctf_strategy.AttackerStrategy}"
BOT_TWO_STRATEGY="${BOT_TWO_STRATEGY:-ctf_strategy.DefenderStrategy}"

cleanup() {
  local pids
  pids="$(jobs -p)"
  if [[ -n "$pids" ]]; then
    kill $pids 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

launch_bot() {
  local player_no="$1"
  local strategy="$2"

  (
    cd "$ROOT_DIR"
    "$PYTHON_BIN" main.py \
      --my-team "$TEAM_NUM" \
      --my-no "$player_no" \
      --against "$AGAINST_TEAM" \
      --per-team-player "$PER_TEAM_PLAYER" \
      --map "$MAP_MODE" \
      --strategy "$strategy" \
      --verbose
  ) &
}

launch_bot 1 "$BOT_ONE_STRATEGY"
launch_bot 2 "$BOT_TWO_STRATEGY"

wait