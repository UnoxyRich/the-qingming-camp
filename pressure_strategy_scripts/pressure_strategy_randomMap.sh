#!/usr/bin/env bash
set -euo pipefail

ACTION_TICK_SECONDS="0.03"
LEADER_STARTUP_DELAY_SECONDS="3"
WAIT=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --action-tick)
      ACTION_TICK_SECONDS="$2"
      shift 2
      ;;
    --leader-startup-delay)
      LEADER_STARTUP_DELAY_SECONDS="$2"
      shift 2
      ;;
    --wait)
      WAIT=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

export PYTHONIOENCODING=utf-8

if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  echo "No Python interpreter found. Create .venv or install python3." >&2
  exit 1
fi

new_random_member_tag() {
  local exclude_csv="${1:-}"
  local length="${2:-3}"
  local letters=(A B C D E F G H J K L M N P Q R S T U V W X Y Z)
  local tag
  while true; do
    tag=""
    for ((i=0; i<length; i++)); do
      tag+="${letters[RANDOM % ${#letters[@]}]}"
    done
    if [[ ",${exclude_csv}," != *",${tag},"* ]]; then
      printf '%s\n' "$tag"
      return
    fi
  done
}

SERVER="10.31.0.101"
TEAM_NUM="26"
AGAINST_TEAM="random"
PER_TEAM_PLAYER="2"
MAP_MODE="random"
STRATEGY_NAME="pressure_strategy.PressureStrategy"

leader_tag="$(new_random_member_tag)"
follower_tag="$(new_random_member_tag "$leader_tag")"

leader_username="CTF-${TEAM_NUM}-${leader_tag}"
follower_username="CTF-${TEAM_NUM}-${follower_tag}"

bot_delays=("0" "$LEADER_STARTUP_DELAY_SECONDS")
bot_args_0=(main.py --my-team "$TEAM_NUM" --my-no "$follower_tag" --username "$follower_username" --server "$SERVER" --against "$AGAINST_TEAM" --per-team-player "$PER_TEAM_PLAYER" --map "$MAP_MODE" --action-tick "$ACTION_TICK_SECONDS" --strategy "$STRATEGY_NAME" --wait-for-users "$leader_username" --verbose)
bot_args_1=(main.py --my-team "$TEAM_NUM" --my-no "$leader_tag" --username "$leader_username" --server "$SERVER" --against "$AGAINST_TEAM" --per-team-player "$PER_TEAM_PLAYER" --map "$MAP_MODE" --action-tick "$ACTION_TICK_SECONDS" --strategy "$STRATEGY_NAME" --wait-for-users "$follower_username" --verbose)

echo
echo "=== PRESSURE STRATEGY RANDOM MAP ==="
echo "Our team:      $TEAM_NUM  ($leader_username, $follower_username)"
echo "Opponent:      $AGAINST_TEAM"
echo "Server:        $SERVER"
echo "Strategy:      PressureStrategy"
echo "Players/team:  $PER_TEAM_PLAYER"
echo "Map:           $MAP_MODE"
echo "Action tick:   ${ACTION_TICK_SECONDS}s"
echo

printf '[bot-2] %s' "$PYTHON_BIN"
printf ' %q' "${bot_args_0[@]}"
echo
printf '[bot-1] %s' "$PYTHON_BIN"
printf ' %q' "${bot_args_1[@]}"
echo
echo

if [[ "$DRY_RUN" == true ]]; then
  echo "(dry run - no processes started)"
  exit 0
fi

cd "$REPO_ROOT"

pids=()

start_bot() {
  local delay="$1"
  shift
  if [[ "$delay" != "0" ]]; then
    sleep "$delay"
  fi
  "$PYTHON_BIN" "$@" &
  pids+=("$!")
}

start_bot "${bot_delays[0]}" "${bot_args_0[@]}"
start_bot "${bot_delays[1]}" "${bot_args_1[@]}"

if [[ "$WAIT" != true ]]; then
  echo "Bots launched. Use --wait to block until they exit."
  exit 0
fi

cleanup() {
  for pid in "${pids[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}

trap cleanup EXIT INT TERM

for pid in "${pids[@]}"; do
  wait "$pid"
done