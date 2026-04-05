#!/usr/bin/env bash
set -euo pipefail

export PYTHONIOENCODING=utf-8

TEAM_NUM=7891114514
AGAINST_TEAM=1
PER_TEAM_PLAYER=2
MAP_MODE=fixed
BOT_ONE_NAME=UnoxyRich
BOT_TWO_NAME=TennisBall
BOT_ONE_NO=1
BOT_TWO_NO=2

cleanup() {
  pids="$(jobs -p)"
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

python main.py \
  --my-team "$TEAM_NUM" \
  --my-no "$BOT_ONE_NO" \
  --username "$BOT_ONE_NAME" \
  --against "$AGAINST_TEAM" \
  --per-team-player "$PER_TEAM_PLAYER" \
  --map "$MAP_MODE" \
  --strategy "ctf_strategy.AttackerStrategy" \
  --verbose &

python main.py \
  --my-team "$TEAM_NUM" \
  --my-no "$BOT_TWO_NO" \
  --username "$BOT_TWO_NAME" \
  --against "$AGAINST_TEAM" \
  --per-team-player "$PER_TEAM_PLAYER" \
  --map "$MAP_MODE" \
  --strategy "ctf_strategy.AttackerStrategy" \
  --verbose &

wait
