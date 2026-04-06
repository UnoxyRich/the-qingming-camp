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

TEAM_26_NUM="${TEAM_26_NUM:-26}"
TEAM_31_NUM="${TEAM_31_NUM:-31}"
PER_TEAM_PLAYER="${PER_TEAM_PLAYER:-2}"
MAP_MODE="${MAP_MODE:-random}"
TEAM_26_STRATEGY="${TEAM_26_STRATEGY:-ctf_strategy.AttackerStrategy}"
TEAM_31_STRATEGY="${TEAM_31_STRATEGY:-ctf_strategy.DefenderStrategy}"

cleanup() {
  local pids
  pids="$(jobs -p)"
  if [[ -n "$pids" ]]; then
    kill $pids 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

launch_bot() {
  local team_num="$1"
  local against_team="$2"
  local player_no="$3"
  local strategy="$4"

  (
    cd "$ROOT_DIR"
    "$PYTHON_BIN" main.py \
      --my-team "$team_num" \
      --my-no "$player_no" \
      --against "$against_team" \
      --per-team-player "$PER_TEAM_PLAYER" \
      --map "$MAP_MODE" \
      --strategy "$strategy" \
      --verbose
  ) &
}

launch_bot "$TEAM_26_NUM" "$TEAM_31_NUM" 1 "$TEAM_26_STRATEGY"
launch_bot "$TEAM_26_NUM" "$TEAM_31_NUM" 2 "$TEAM_26_STRATEGY"
launch_bot "$TEAM_31_NUM" "$TEAM_26_NUM" 1 "$TEAM_31_STRATEGY"
launch_bot "$TEAM_31_NUM" "$TEAM_26_NUM" 2 "$TEAM_31_STRATEGY"

wait