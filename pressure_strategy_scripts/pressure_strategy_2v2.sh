#!/usr/bin/env bash
set -euo pipefail

MAP_MODE="random"
ACTION_TICK_SECONDS="0.03"
LEADER_STARTUP_DELAY_SECONDS="3"
TEAM_SPACING_DELAY_SECONDS="1"
WAIT=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --map-mode)
      MAP_MODE="$2"
      shift 2
      ;;
    --action-tick)
      ACTION_TICK_SECONDS="$2"
      shift 2
      ;;
    --leader-startup-delay)
      LEADER_STARTUP_DELAY_SECONDS="$2"
      shift 2
      ;;
    --team-spacing-delay)
      TEAM_SPACING_DELAY_SECONDS="$2"
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

if [[ "$MAP_MODE" != "fixed" && "$MAP_MODE" != "random" ]]; then
  echo "Map mode must be 'fixed' or 'random'." >&2
  exit 1
fi

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
  local letters=(A B C D E F G H J K L M N P Q R S T U V W X Y Z)
  local tag
  while true; do
    tag="${letters[RANDOM % ${#letters[@]}]}"
    if [[ ",${exclude_csv}," != *",${tag},"* ]]; then
      printf '%s\n' "$tag"
      return
    fi
  done
}

SERVER="10.31.0.101"
PER_TEAM_PLAYER="2"
TEAM_A="26"
TEAM_B="31"
STRATEGY_NAME="pressure_strategy.PressureStrategy"

team_a_leader_tag="$(new_random_member_tag)"
team_a_follower_tag="$(new_random_member_tag "$team_a_leader_tag")"
team_b_leader_tag="$(new_random_member_tag "$team_a_leader_tag,$team_a_follower_tag")"
team_b_follower_tag="$(new_random_member_tag "$team_a_leader_tag,$team_a_follower_tag,$team_b_leader_tag")"

team_a_leader_username="CTF-${TEAM_A}-${team_a_leader_tag}"
team_a_follower_username="CTF-${TEAM_A}-${team_a_follower_tag}"
team_b_leader_username="CTF-${TEAM_B}-${team_b_leader_tag}"
team_b_follower_username="CTF-${TEAM_B}-${team_b_follower_tag}"

bot_delays=("0" "$TEAM_SPACING_DELAY_SECONDS" "$LEADER_STARTUP_DELAY_SECONDS" "$((LEADER_STARTUP_DELAY_SECONDS + TEAM_SPACING_DELAY_SECONDS))")
bot_args_0=(main.py --my-team "$TEAM_A" --my-no "$team_a_follower_tag" --username "$team_a_follower_username" --server "$SERVER" --against "$TEAM_B" --per-team-player "$PER_TEAM_PLAYER" --map "$MAP_MODE" --action-tick "$ACTION_TICK_SECONDS" --strategy "$STRATEGY_NAME" --wait-for-users "$team_a_leader_username,$team_b_leader_username,$team_b_follower_username" --verbose)
bot_args_1=(main.py --my-team "$TEAM_B" --my-no "$team_b_follower_tag" --username "$team_b_follower_username" --server "$SERVER" --against "$TEAM_A" --per-team-player "$PER_TEAM_PLAYER" --map "$MAP_MODE" --action-tick "$ACTION_TICK_SECONDS" --strategy "$STRATEGY_NAME" --wait-for-users "$team_a_leader_username,$team_a_follower_username,$team_b_leader_username" --verbose)
bot_args_2=(main.py --my-team "$TEAM_A" --my-no "$team_a_leader_tag" --username "$team_a_leader_username" --server "$SERVER" --against "$TEAM_B" --per-team-player "$PER_TEAM_PLAYER" --map "$MAP_MODE" --action-tick "$ACTION_TICK_SECONDS" --strategy "$STRATEGY_NAME" --wait-for-users "$team_a_follower_username,$team_b_leader_username,$team_b_follower_username" --verbose)
bot_args_3=(main.py --my-team "$TEAM_B" --my-no "$team_b_leader_tag" --username "$team_b_leader_username" --server "$SERVER" --against "$TEAM_A" --per-team-player "$PER_TEAM_PLAYER" --map "$MAP_MODE" --action-tick "$ACTION_TICK_SECONDS" --strategy "$STRATEGY_NAME" --wait-for-users "$team_a_leader_username,$team_a_follower_username,$team_b_follower_username" --verbose)

echo
echo "=== PRESSURE STRATEGY 2V2 ==="
echo "Server:        $SERVER"
echo "Team A:        $TEAM_A  ($team_a_leader_username, $team_a_follower_username)"
echo "Team B:        $TEAM_B  ($team_b_leader_username, $team_b_follower_username)"
echo "Strategy:      PressureStrategy"
echo "Players/team:  $PER_TEAM_PLAYER"
echo "Map:           $MAP_MODE"
echo "Action tick:   ${ACTION_TICK_SECONDS}s"
echo

for index in 0 1 2 3; do
  eval "args=(\"\${bot_args_${index}[@]}\")"
  printf '[bot-%s] %s' "$index" "$PYTHON_BIN"
  printf ' %q' "${args[@]}"
  echo
done
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
start_bot "${bot_delays[2]}" "${bot_args_2[@]}"
start_bot "${bot_delays[3]}" "${bot_args_3[@]}"

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