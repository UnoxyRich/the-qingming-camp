#!/usr/bin/env bash
# Start the CTF bot (Defender role — TennisBall)
export PYTHONIOENCODING=utf-8

TEAM_NUM=7891114514
PLAYER_NUM=TennisBall
AGAINST_TEAM=1
PER_TEAM_PLAYER=1
MAP_MODE=fixed

python main.py \
  --my-team "$TEAM_NUM" \
  --my-no "$PLAYER_NUM" \
  --against "$AGAINST_TEAM" \
  --per-team-player "$PER_TEAM_PLAYER" \
  --map "$MAP_MODE" \
  --strategy "ctf_strategy.DefenderStrategy" \
  --verbose
