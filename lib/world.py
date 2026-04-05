from __future__ import annotations

import json
import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .actions import Action, Chat, MoveTo
from .observation import Observation, TeamName, normalize_team_name

DEFAULT_SERVER = "10.31.0.101"
DEFAULT_PORT = 25565
INIT_RETRY_SECONDS = 1.0
ONLINE_WAIT_TIMEOUT_SECONDS = 30.0
DEFAULT_LOG_DIR = Path("logs")
FAST_PATHFINDER_MAX_DROP_DOWN = 4
FAST_PATHFINDER_COST_MULTIPLIER = 8
SPRINT_REFRESH_SECONDS = 1.0
BFS_GOAL_SEARCH_RADIUS = 6
STUCK_MOVEMENT_SECONDS = 1.5
STUCK_PROGRESS_EPSILON = 0.75
STUCK_RECOVERY_COOLDOWN_SECONDS = 1.5
STUCK_RECOVERY_TURN_DISTANCE = 3.0
STUCK_RECOVERY_ANGLE_DEGREES = 30.0
LEAF_BLOCK_NAMES = (
    "oak_leaves",
    "spruce_leaves",
    "birch_leaves",
    "jungle_leaves",
    "acacia_leaves",
    "dark_oak_leaves",
    "mangrove_leaves",
    "azalea_leaves",
    "flowering_azalea_leaves",
    "cherry_leaves",
    "pale_oak_leaves",
)


def build_multi_log_path(
    *,
    team_num: int,
    player_num: int | str,
    when: datetime | None = None,
    log_dir: Path = DEFAULT_LOG_DIR,
) -> Path:
    timestamp = (when or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    return log_dir / f"{timestamp}-CTF-{team_num}-{player_num}-multi-shot.jsonl"


def build_final_shot_path(
    *,
    team_num: int,
    player_num: int | str,
    when: datetime | None = None,
    log_dir: Path = DEFAULT_LOG_DIR,
) -> Path:
    timestamp = (when or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    return log_dir / f"{timestamp}-CTF-{team_num}-{player_num}-final-shot.json"


def _resolve_runtime_team(runtime_team_info: Mapping[str, Any]) -> TeamName | None:
    for key in ("scoreboardTeam", "playerTeam", "botTeam"):
        normalized = normalize_team_name(runtime_team_info.get(key))
        if normalized is not None:
            return normalized
    return None


@dataclass(frozen=True, slots=True)
class ScanBounds:
    min_x: int = -28
    max_x: int = 28
    min_y: int = 0
    max_y: int = 2
    min_z: int = -40
    max_z: int = 40

    def to_dict(self) -> dict[str, int]:
        return {
            "min_x": self.min_x,
            "max_x": self.max_x,
            "min_y": self.min_y,
            "max_y": self.max_y,
            "min_z": self.min_z,
            "max_z": self.max_z,
            "width": self.max_x - self.min_x + 1,
            "height": self.max_y - self.min_y + 1,
            "depth": self.max_z - self.min_z + 1,
        }


@dataclass(frozen=True, slots=True)
class JavaScriptBridge:
    require: Any
    once: Any
    On: Any
    off: Any


class World:
    def __init__(
        self,
        *,
        js_bridge: JavaScriptBridge,
        team_num: int,
        player_num: int | str,
        username: str | None = None,
        against_team: int | str | None = None,
        total_player_per_team: int = 1,
        map_mode: str = "fixed",
        server: str = DEFAULT_SERVER,
        port: int = DEFAULT_PORT,
        verbose: bool = False,
        announce_intent: bool = True,
        expected_online_users: Iterable[str] | None = None,
        online_wait_timeout: float = ONLINE_WAIT_TIMEOUT_SECONDS,
        settle_seconds: float = 1.0,
        bounds: ScanBounds | None = None,
    ) -> None:
        self._js_bridge = js_bridge
        self.server = server
        self.port = port
        self.verbose = verbose
        self.announce_intent = announce_intent
        self.expected_online_users = frozenset(
            username.strip() for username in (expected_online_users or ()) if username.strip()
        )
        self.online_wait_timeout = max(0.0, online_wait_timeout)
        self.settle_seconds = settle_seconds
        self.bounds = bounds or ScanBounds()
        self.team_num = team_num
        self.player_num = player_num
        self.bot_name = (
            _normalize_explicit_username(username)
            if username is not None
            else _normalize_bot_name(team_num=team_num, player_num=player_num)
        )
        self.against_team = against_team
        self.total_player_per_team = total_player_per_team
        self.map_mode = _normalize_map_mode(map_mode)
        self.intent_message = _build_intent_message(
            against_team=against_team,
            total_player_per_team=total_player_per_team,
            map_mode=self.map_mode,
        )
        self.team: TeamName | None = None
        self._assigned_teams: dict[str, TeamName] = {}

        self._bot: Any | None = None
        self._require: Any | None = None
        self._block_to_json: Any | None = None
        self._entities_to_json: Any | None = None
        self._players_to_json: Any | None = None
        self._quick_snapshot_to_json: Any | None = None
        self._position_to_json: Any | None = None
        self._team_info_to_json: Any | None = None
        self._vec3: Any | None = None
        self._pathfinder: Any | None = None
        self._movements: Any | None = None
        self._fast_movements: Any | None = None
        self._goal_near: Any | None = None
        self._off: Any | None = None
        self._chat_listener: Any | None = None
        self._message_listener: Any | None = None
        self._message_object_listener: Any | None = None
        self._spawn_listener: Any | None = None
        self._respawn_listener: Any | None = None
        self._listeners_installed = False
        self._ready_observation: Observation | None = None
        self._intent_announced = False
        self._seen_online_users: set[str] = {self.bot_name}
        self._ready_prompt_received = False
        self._ready_prompt_event = threading.Event()
        self._ready_announced = False
        self._match_world_detected = False
        self._game_started = False
        self._game_ended = False
        self._game_start_event = threading.Event()
        self._active_log_path: Path = build_multi_log_path(
            team_num=self.team_num,
            player_num=self.player_num,
        )
        self._last_quick_snapshot: dict[str, Any] | None = None
        self._last_move_goal: tuple[int, int, int, bool] | None = None
        self._last_move_progress_position: tuple[float, float, float] | None = None
        self._last_move_progress_at = time.monotonic()
        self._stuck_recovery_until = 0.0
        self._last_sprint_refresh_at = 0.0

    def join_the_world(self) -> Any:
        self._log("Start joining the world...")
        bot = self._connect_bot()
        self._log("Bot successfully created!")
        self._install_game_start_listeners()

        if self.expected_online_users:
            self._wait_for_expected_players()

        # Step 1: Announce room intent (leader) or skip (follower)
        if self.announce_intent:
            while not self._intent_announced and not self._game_ended:
                self._announce_intent()
                if not self._intent_announced:
                    time.sleep(INIT_RETRY_SECONDS)
            self._log("Room intent sent. Now sending readiness...")
        else:
            self._log("Skipping intent announcement; waiting for teammate-created room.")

        if self._game_ended:
            raise RuntimeError("Game ended before the bot could join.")

        # Step 2: Immediately start sending "I'm ready!" every few seconds
        #         until the server responds with "Game start: ..."
        #         Per the docs: announce room -> send I'm ready! -> game starts
        ready_interval = 3.0  # seconds between ready pings
        while not self._game_started and not self._game_ended:
            self._safe_chat("I'm ready!")
            self._ready_announced = True
            self._log("Sent 'I'm ready!' in chat")
            self._game_start_event.wait(ready_interval)

        if self._game_ended and not self._game_started:
            raise RuntimeError("Game ended before start.")
        if self._ready_observation is None:
            self._ready_observation = self._initialize_until_ready()
        self._append_full_observation_log(self._active_log_path, self._ready_observation)
        return bot

    @property
    def game_started(self) -> bool:
        return self._game_started

    @property
    def game_ended(self) -> bool:
        return self._game_ended

    def observe(self) -> Observation:
        snapshot = self.inspect()
        return Observation.from_snapshot(
            snapshot_source=snapshot,
            bot_name=self.bot_name,
            assigned_teams=self._assigned_teams,
        ).validate()

    def quick_observe(self) -> dict[str, Any]:
        if not self._game_started:
            raise RuntimeError("quick_observe() is only available after game start.")
        snapshot = self._capture_quick_snapshot()
        delta_snapshot = _build_quick_snapshot_delta(self._last_quick_snapshot, snapshot)
        self._last_quick_snapshot = snapshot
        return delta_snapshot

    def execute_action(self, action: Action) -> None:
        if self._game_ended:
            return
        if isinstance(action, Chat):
            self._safe_chat(action.message)
            return
        if not isinstance(action, MoveTo):
            raise TypeError(f"Unsupported action type: {type(action)!r}")
        if self._bot is None or self._movements is None or self._goal_near is None:
            raise RuntimeError("Bot movement is not initialized.")
        pathfinder = getattr(self._bot, "pathfinder", None)
        if pathfinder is None:
            return
        goal_signature = (action.x, action.z, action.radius, action.sprint)
        try:
            current_position = _current_bot_position(self._bot)
            self._update_move_progress(current_position)
            now = time.monotonic()
            _refresh_sprint_jump_state(
                self._bot,
                now,
                last_refresh_at=self._last_sprint_refresh_at,
                refresh_interval=SPRINT_REFRESH_SECONDS,
            )
            self._last_sprint_refresh_at = now
            movements = (
                self._fast_movements
                if action.sprint and self._fast_movements is not None
                else self._movements
            )
            if getattr(pathfinder, "movements", None) is not movements:
                pathfinder.setMovements(movements)
            if now < self._stuck_recovery_until:
                return
            if self._last_move_goal == goal_signature:
                if self._maybe_recover_from_stuck(action, pathfinder, current_position):
                    return
                return
            goal_y = _current_goal_y(self._bot)
            goal_x, goal_y, goal_z = _resolve_bfs_goal(self._bot, action, goal_y)
            pathfinder.setGoal(self._goal_near(goal_x, goal_y, goal_z, action.radius))
            self._last_move_goal = goal_signature
            self._stuck_recovery_until = 0.0
            self._update_move_progress(current_position)
        except Exception:
            return

    def execute_actions(self, actions: Action | Iterable[Action] | None) -> None:
        if actions is None:
            return
        if isinstance(actions, (MoveTo, Chat)):
            self.execute_action(actions)
            return
        for action in actions:
            self.execute_action(action)

    def stop_actions(self) -> None:
        if self._bot is None or not hasattr(self._bot, "pathfinder"):
            return
        try:
            self._last_move_goal = None
            self._last_move_progress_position = None
            self._last_move_progress_at = time.monotonic()
            self._stuck_recovery_until = 0.0
            self._last_sprint_refresh_at = 0.0
            _set_control_state(self._bot, "sprint", False)
            _set_control_state(self._bot, "jump", False)
            self._bot.pathfinder.setGoal(None)
        except Exception:
            try:
                self._bot.pathfinder.stop()
            except Exception:
                pass

    def run(self, strategy: Any, *, tick_seconds: float = 1.0) -> None:
        self._active_log_path = build_multi_log_path(
            team_num=self.team_num,
            player_num=self.player_num,
        )
        self.run_with_logging(
            strategy,
            action_tick_seconds=tick_seconds,
            snapshot_tick_seconds=1.0,
            log_path=self._active_log_path,
        )

    def run_with_logging(
        self,
        strategy: Any,
        *,
        action_tick_seconds: float = 0.1,
        snapshot_tick_seconds: float = 1.0,
        log_path: Path,
    ) -> None:
        self._active_log_path = log_path
        self.join_the_world()
        current_observation = self._ready_observation
        strategy.on_game_start(current_observation)

        previous_dynamic_state: dict[str, Any] | None = None
        next_snapshot_at = 0.0
        self._append_log_line(
            log_path,
            {
                "event": "session_start",
                "timestamp": time.time(),
                "bot_name": self.bot_name,
                "team": current_observation.team,
                "action_tick_seconds": action_tick_seconds,
                "snapshot_tick_seconds": snapshot_tick_seconds,
            },
        )

        while not self._game_ended:
            try:
                delta_snapshot = self.quick_observe()
                current_observation.patch_observation(delta_snapshot).validate()
            except Exception:
                self._log("Failed to perform quick observe. Doing full observe instead.")
                current_observation = self.observe()
                self._last_quick_snapshot = None

            actions = strategy.compute_next_action(current_observation)
            try:
                self.execute_actions(actions)
            except Exception:
                pass
            now = time.monotonic()
            if now >= next_snapshot_at:
                current_dynamic_state = _build_dynamic_state(current_observation, actions)
                delta = _build_dynamic_delta(previous_dynamic_state, current_dynamic_state)
                self._append_log_line(
                    log_path,
                    {
                        "timestamp": time.time(),
                        "bot_name": self.bot_name,
                        **delta,
                    },
                )
                previous_dynamic_state = current_dynamic_state
                next_snapshot_at = now + snapshot_tick_seconds
            time.sleep(action_tick_seconds)

        self._append_log_line(
            log_path,
            {
                "event": "session_end",
                "timestamp": time.time(),
                "bot_name": self.bot_name,
                "team": self.team,
            },
        )

    def inspect(self) -> dict[str, Any]:
        self._connect_bot()
        time.sleep(self.settle_seconds)
        return self._capture_snapshot()

    def close(self) -> None:
        if self._bot is None:
            return
        try:
            self._remove_game_start_listeners()
            self._bot.quit()
        except Exception:
            pass
        finally:
            self._bot = None
            self.team = None
            self._assigned_teams = {}
            self._ready_observation = None
            self._intent_announced = False
            self._seen_online_users = {self.bot_name}
            self._ready_prompt_received = False
            self._ready_prompt_event.clear()
            self._ready_announced = False
            self._game_started = False
            self._game_ended = False
            self._game_start_event.clear()
            self._last_quick_snapshot = None
            self._last_move_goal = None

    def _capture_snapshot(self) -> dict[str, Any]:
        if self._bot is None or self._vec3 is None:
            raise RuntimeError("Bot is not connected. Call join_the_world() first.")

        bot_position = json.loads(self._position_to_json(self._bot.entity.position))
        runtime_team_info = json.loads(self._team_info_to_json(self._bot))
        runtime_team = _resolve_runtime_team(runtime_team_info)

        blocks: list[dict[str, Any]] = []
        for y in range(self.bounds.min_y, self.bounds.max_y + 1):
            for z in range(self.bounds.min_z, self.bounds.max_z + 1):
                for x in range(self.bounds.min_x, self.bounds.max_x + 1):
                    block = self._bot.blockAt(self._vec3(x, y, z))
                    raw_block = self._block_to_json(block)
                    if not raw_block:
                        continue
                    block_data = json.loads(raw_block)
                    if block_data["name"] in {"air", "cave_air", "void_air"}:
                        continue
                    blocks.append(block_data)

        entities = json.loads(self._entities_to_json(self._bot.entities))
        players = json.loads(self._players_to_json(self._bot))
        return {
            "server": {
                "host": self.server,
                "port": self.port,
                "username": self.bot_name,
            },
            "bounds": self.bounds.to_dict(),
            "plane_y": 1,
            "bot": {
                "position": bot_position,
                "username": self.bot_name,
                "team": runtime_team,
            },
            "summary": {
                "block_count": len(blocks),
                "entity_count": len(entities),
            },
            "blocks": blocks,
            "entities": entities,
            "players": players,
        }

    def _capture_quick_snapshot(self) -> dict[str, Any]:
        if self._bot is None or self._vec3 is None or self._quick_snapshot_to_json is None:
            raise RuntimeError("Bot quick observation is not initialized.")
        quick_snapshot = json.loads(
            self._quick_snapshot_to_json(self._bot, self._vec3, self.bounds.to_dict())
        )
        if not isinstance(quick_snapshot, dict):
            raise RuntimeError("Quick observation payload was not a JSON object.")
        return quick_snapshot

    def _connect_bot(self) -> Any:
        if self._bot is not None:
            return self._bot

        require = self._js_bridge.require
        once = self._js_bridge.once
        mineflayer = require("mineflayer")
        pathfinder = require("mineflayer-pathfinder")

        bot = mineflayer.createBot(
            {
                "host": self.server,
                "port": self.port,
                "username": self.bot_name,
                "hideErrors": False,
            }
        )
        once(bot, "login")
        time.sleep(5)
        # Note: 'spawn' fires before 'login' returns on this server,
        # so we must NOT call once(bot, "spawn") here — it already happened.
        bot.loadPlugin(pathfinder.pathfinder)

        self._bot = bot
        self._require = require
        (
            self._block_to_json,
            self._entities_to_json,
            self._players_to_json,
            self._quick_snapshot_to_json,
            self._position_to_json,
            self._team_info_to_json,
        ) = _build_js_helpers(require)
        self._vec3 = require("vec3")
        mc_data = require("minecraft-data")(bot.version)
        self._pathfinder = pathfinder
        self._movements = pathfinder.Movements(bot, mc_data)
        _apply_leaf_avoidance(self._movements, mc_data)
        self._fast_movements = _build_fast_movements(pathfinder, bot, mc_data)
        self._goal_near = pathfinder.goals.GoalNear
        return bot

    def _initialize_until_ready(self) -> Observation:
        while True:
            if self._game_ended:
                raise RuntimeError("Game ended during initialization.")
            try:
                self._verify_assigned_team()
                snapshot = self.inspect()
                observation = Observation.from_snapshot(
                    snapshot_source=snapshot,
                    bot_name=self.bot_name,
                    assigned_teams=self._assigned_teams,
                ).validate()
                self._validate_team_assignment(observation)
                self.team = observation.team
                return observation
            except Exception as exc:
                self._log(f"Initialization failed: {exc}")
                time.sleep(INIT_RETRY_SECONDS)

    def _verify_assigned_team(self) -> None:
        assigned_team = self._assigned_teams.get(self.bot_name)
        if assigned_team not in {"L", "R"}:
            raise ValueError("Bot was not assigned to a valid L/R team.")

    def _validate_team_assignment(self, observation: Observation) -> None:
        assigned_team = self._assigned_teams.get(self.bot_name)
        if observation.team != assigned_team:
            raise ValueError(
                f"Observation team mismatch after initialization: {observation.team!r} vs {assigned_team!r}."
            )
        for player in observation.players:
            expected_team = self._assigned_teams.get(player.name)
            if expected_team is None:
                continue
            if player.team != expected_team:
                raise ValueError(
                    f"Player team mismatch for {player.name!r}: {player.team!r} vs {expected_team!r}."
                )

    def _install_game_start_listeners(self) -> None:
        if self._listeners_installed or self._bot is None:
            return

        On = self._js_bridge.On
        off = self._js_bridge.off
        self._off = off

        @On(self._bot, "messagestr")
        def _on_messagestr(maybe_sender, maybe_message, *args):
            self._handle_incoming_message(maybe_sender, maybe_message, *args)

        @On(self._bot, "chat")
        def _on_chat(*args):
            self._handle_incoming_message(*args)

        @On(self._bot, "message")
        def _on_message(*args):
            self._handle_incoming_message(*args)

        @On(self._bot, "spawn")
        def _on_spawn(*_args):
            self._handle_world_transition("spawn")

        @On(self._bot, "respawn")
        def _on_respawn(*_args):
            self._handle_world_transition("respawn")

        self._message_listener = _on_messagestr
        self._chat_listener = _on_chat
        self._message_object_listener = _on_message
        self._spawn_listener = _on_spawn
        self._respawn_listener = _on_respawn
        self._listeners_installed = True

    def _remove_game_start_listeners(self) -> None:
        if not self._listeners_installed or self._bot is None or self._off is None:
            return
        try:
            if self._message_listener is not None:
                self._off(self._bot, "messagestr", self._message_listener)
            if self._chat_listener is not None:
                self._off(self._bot, "chat", self._chat_listener)
            if self._message_object_listener is not None:
                self._off(self._bot, "message", self._message_object_listener)
            if self._spawn_listener is not None:
                self._off(self._bot, "spawn", self._spawn_listener)
            if self._respawn_listener is not None:
                self._off(self._bot, "respawn", self._respawn_listener)
        except Exception:
            pass
        finally:
            self._message_listener = None
            self._chat_listener = None
            self._message_object_listener = None
            self._spawn_listener = None
            self._respawn_listener = None
            self._listeners_installed = False

    def _wait_for_expected_players(self) -> None:
        deadline = time.monotonic() + self.online_wait_timeout
        while not self._game_ended:
            self._refresh_online_users()
            missing_users = sorted(self.expected_online_users - self._seen_online_users)
            if not missing_users:
                self._log(
                    "All expected bots are online. Running match command immediately: "
                    f"{', '.join(sorted(self.expected_online_users))}"
                )
                return
            if time.monotonic() >= deadline:
                self._log(
                    "Timed out waiting for all expected bots to appear online; continuing anyway. "
                    f"Missing: {', '.join(missing_users)}"
                )
                return
            self._log(f"Waiting for bots to join server before match command: {', '.join(missing_users)}")
            time.sleep(INIT_RETRY_SECONDS)

    def _refresh_online_users(self) -> None:
        if self._bot is None or self._players_to_json is None:
            return
        try:
            players_payload = json.loads(self._players_to_json(self._bot))
        except Exception:
            return
        if not isinstance(players_payload, list):
            return
        for player in players_payload:
            if not isinstance(player, dict):
                continue
            username = player.get("username")
            if isinstance(username, str) and username:
                self._seen_online_users.add(username)

    def _handle_incoming_message(self, maybe_sender, maybe_message, *args: Any) -> None:
        texts: list[str] = []
        for raw in (maybe_sender, maybe_message, *args):
            text = _coerce_message_text(raw).strip()
            if text:
                texts.append(text)
        combined = " ".join(texts)

        if combined:
            self._log(f"Incoming message: {combined}")

        if "Are you ready?" in combined:
            self._ready_prompt_received = True
            self._ready_prompt_event.set()
            self._log("Received 'Are you ready?' from system")

        if "Game start: " in combined:
            # Try each text fragment individually for JSON extraction
            for t in texts:
                game_start_assignments = _extract_game_start_assignments(t)
                if game_start_assignments is not None:
                    self._assigned_teams = game_start_assignments
                    self.team = game_start_assignments.get(self.bot_name)
                    self._game_started = True
                    self._game_start_event.set()
                    self._log("Received 'Game start!' from system")
                    break

        if "Game over!" in combined:
            self._game_ended = True
            self._ready_prompt_event.set()
            self._game_start_event.set()
            self.stop_actions()
            self._log("Received 'Game over!' from system")

    def _handle_world_transition(self, source: str) -> None:
        if self._game_started or self._game_ended:
            return
        self._match_world_detected = True
        self._ready_prompt_received = True
        self._ready_prompt_event.set()
        self._log(f"Detected world transition via {source}; treating it as readiness phase.")

    def _announce_intent(self) -> None:
        self._intent_announced = self._safe_chat(self.intent_message) or self._intent_announced
        if self._intent_announced:
            self._log(f"Intent Announced: {self.intent_message}")

    def _announce_ready(self, *, force: bool = False) -> None:
        if self._ready_announced and not force:
            return
        self._ready_announced = self._safe_chat("I'm ready!") or self._ready_announced
        if self._ready_announced:
            self._log("Readiness Announced")

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)

    def _safe_chat(self, message: str) -> bool:
        if self._bot is None:
            return False
        try:
            self._bot.chat(message)
            return True
        except Exception:
            return False

    def _append_log_line(self, log_path: Path, payload: Mapping[str, Any]) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def _append_full_observation_log(
        self, log_path: Path, observation: Observation | None
    ) -> None:
        if observation is None:
            return
        self._append_log_line(
            log_path,
            {
                "event": "join_complete",
                "timestamp": time.time(),
                "bot_name": self.bot_name,
                "observation": observation.to_dict(),
            },
        )

    def _update_move_progress(self, position: tuple[float, float, float] | None) -> None:
        if position is None:
            return
        if self._last_move_progress_position is None:
            self._last_move_progress_position = position
            self._last_move_progress_at = time.monotonic()
            return
        if _horizontal_distance(position, self._last_move_progress_position) >= STUCK_PROGRESS_EPSILON:
            self._last_move_progress_position = position
            self._last_move_progress_at = time.monotonic()

    def _maybe_recover_from_stuck(
        self,
        action: MoveTo,
        pathfinder: Any,
        current_position: tuple[float, float, float] | None,
    ) -> bool:
        if current_position is None:
            return False
        if time.monotonic() - self._last_move_progress_at < STUCK_MOVEMENT_SECONDS:
            return False
        recovery_goal = _compute_recovery_goal(current_position, action)
        if recovery_goal is None:
            return False
        recovery_x, recovery_y, recovery_z = recovery_goal
        pathfinder.setGoal(self._goal_near(recovery_x, recovery_y, recovery_z, max(1, action.radius)))
        self._last_move_goal = None
        self._last_move_progress_position = current_position
        self._last_move_progress_at = time.monotonic()
        self._stuck_recovery_until = time.monotonic() + STUCK_RECOVERY_COOLDOWN_SECONDS
        self._log(
            f"Detected stuck movement for >{STUCK_MOVEMENT_SECONDS:.0f}s; rerouting via "
            f"({recovery_x}, {recovery_z}) before retrying target ({action.x}, {action.z})."
        )
        return True


def _normalize_bot_name(*, team_num: int, player_num: int | str) -> str:
    bot_name = f"CTF-{team_num}-{player_num}"
    if len(bot_name) > 16:
        raise ValueError(
            f"Generated bot username {bot_name!r} exceeds Minecraft's 16 character limit. "
            "Use a shorter player id or a shorter team number."
        )
    return bot_name


def _normalize_explicit_username(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Explicit bot username must not be empty.")
    if len(normalized) > 16:
        raise ValueError(
            f"Explicit bot username {normalized!r} exceeds Minecraft's 16 character limit."
        )
    return normalized


def _build_intent_message(
    *,
    against_team: int | str | None,
    total_player_per_team: int,
    map_mode: str,
) -> str:
    against_value = "none" if against_team is None else str(against_team)
    return f"with {against_value} {total_player_per_team} {map_mode}"


def _normalize_map_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"fixed", "random"}:
        raise ValueError("map_mode must be either 'fixed' or 'random'.")
    return normalized


def _extract_game_start_assignments(text: str) -> dict[str, TeamName] | None:
    marker = "game start:"
    lower_text = text.lower()
    marker_index = lower_text.find(marker)
    if marker_index < 0:
        return None
    payload_text = text[marker_index + len(marker) :].strip()
    if not payload_text:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(payload_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    assignments = _normalize_game_start_assignments(payload)
    return assignments or None


def _normalize_game_start_assignments(payload: Mapping[str, Any]) -> dict[str, TeamName]:
    assignments: dict[str, TeamName] = {}
    for team_name, usernames in payload.items():
        normalized_team = normalize_team_name(team_name)
        if normalized_team is None or not isinstance(usernames, list | tuple):
            continue
        for username in usernames:
            assignments[str(username)] = normalized_team
    return assignments


def _coerce_message_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)

    text_attr = getattr(value, "text", None)
    if isinstance(text_attr, str):
        return text_attr

    json_attr = getattr(value, "json", None)
    if json_attr is not None:
        if callable(json_attr):
            try:
                json_attr = json_attr()
            except Exception:
                json_attr = None
        flattened = _flatten_chat_json(json_attr)
        if flattened:
            return flattened

    if isinstance(value, dict):
        flattened = _flatten_chat_json(value)
        if flattened:
            return flattened

    to_string = getattr(value, "toString", None)
    if callable(to_string):
        try:
            rendered = to_string()
            if isinstance(rendered, str) and rendered != "[object Object]":
                return rendered
        except Exception:
            pass

    return ""


def _flatten_chat_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple):
        return " ".join(part for part in (_flatten_chat_json(item) for item in value) if part)
    if not isinstance(value, dict) and hasattr(value, "__iter__") and not hasattr(value, "keys"):
        try:
            return " ".join(
                part for part in (_flatten_chat_json(item) for item in value) if part
            )
        except Exception:
            pass
    if isinstance(value, dict) or hasattr(value, "keys") or hasattr(value, "get"):
        parts: list[str] = []
        text = _mapping_like_get(value, "text")
        if isinstance(text, str) and text:
            parts.append(text)
        extra = _mapping_like_get(value, "extra")
        if extra is not None:
            extra_text = _flatten_chat_json(extra)
            if extra_text:
                parts.append(extra_text)
        translate = _mapping_like_get(value, "translate")
        if isinstance(translate, str) and translate:
            parts.append(translate)
        with_value = _mapping_like_get(value, "with")
        if with_value is not None:
            with_text = _flatten_chat_json(with_value)
            if with_text:
                parts.append(with_text)
        return " ".join(part for part in parts if part)
    return ""


def _mapping_like_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    get_method = getattr(value, "get", None)
    if callable(get_method):
        try:
            return get_method(key)
        except Exception:
            pass
    try:
        return value[key]
    except Exception:
        pass
    return getattr(value, key, None)


def _normalize_actions(actions: Action | Iterable[Action] | None) -> tuple[Action, ...]:
    if actions is None:
        return ()
    if isinstance(actions, (MoveTo, Chat)):
        return (actions,)
    return tuple(actions)


def _serialize_action(action: Action) -> dict[str, Any]:
    if isinstance(action, MoveTo):
        return {
            "type": "MoveTo",
            "x": action.x,
            "z": action.z,
            "radius": action.radius,
            "sprint": action.sprint,
        }
    if isinstance(action, Chat):
        return {"type": "Chat", "message": action.message}
    raise TypeError(f"Unsupported action type: {type(action)!r}")


def _build_fast_movements(pathfinder: Any, bot: Any, mc_data: Any) -> Any:
    movements = pathfinder.Movements(bot, mc_data)
    _set_optional_attr(movements, "allowSprinting", True)
    _set_optional_attr(movements, "allowParkour", True)
    _set_optional_attr(movements, "allow1by1towers", False)
    _set_optional_attr(movements, "canDig", False)
    _set_optional_attr(movements, "canOpenDoors", True)
    _set_optional_attr(movements, "maxDropDown", FAST_PATHFINDER_MAX_DROP_DOWN)
    _set_optional_attr(movements, "placeCost", FAST_PATHFINDER_COST_MULTIPLIER)
    _set_optional_attr(movements, "digCost", FAST_PATHFINDER_COST_MULTIPLIER)
    _set_optional_attr(movements, "liquidCost", FAST_PATHFINDER_COST_MULTIPLIER)
    _set_optional_attr(movements, "entityCost", FAST_PATHFINDER_COST_MULTIPLIER)
    _set_optional_attr(movements, "dontCreateFlow", True)
    _clear_optional_mapping(movements, "entityIntersections")
    _apply_leaf_avoidance(movements, mc_data)
    return movements


def _apply_leaf_avoidance(movements: Any, mc_data: Any) -> None:
    blocks_to_avoid = getattr(movements, "blocksToAvoid", None)
    add_method = getattr(blocks_to_avoid, "add", None)
    if not callable(add_method):
        return
    for block_id in _collect_leaf_block_ids(mc_data):
        try:
            add_method(block_id)
        except Exception:
            continue


def _collect_leaf_block_ids(mc_data: Any) -> tuple[int, ...]:
    block_ids: list[int] = []
    blocks_by_name = getattr(mc_data, "blocksByName", None)
    for block_name in LEAF_BLOCK_NAMES:
        block_data = _mapping_like_get(blocks_by_name, block_name)
        if block_data is None:
            continue
        block_id = _mapping_like_get(block_data, "id")
        if isinstance(block_id, int):
            block_ids.append(block_id)
    return tuple(dict.fromkeys(block_ids))


def _current_goal_y(bot: Any) -> int:
    position = getattr(getattr(bot, "entity", None), "position", None)
    y = getattr(position, "y", 1)
    try:
        return int(y)
    except Exception:
        return 1


def _current_bot_position(bot: Any) -> tuple[float, float, float] | None:
    position = getattr(getattr(bot, "entity", None), "position", None)
    if position is None:
        return None
    x = getattr(position, "x", None)
    y = getattr(position, "y", None)
    z = getattr(position, "z", None)
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or not isinstance(z, (int, float)):
        return None
    return (float(x), float(y), float(z))


def _resolve_bfs_goal(bot: Any, action: MoveTo, goal_y: int) -> tuple[int, int, int]:
    target_x = int(action.x)
    target_z = int(action.z)
    resolved = _find_nearest_safe_goal(bot, target_x, goal_y, target_z, BFS_GOAL_SEARCH_RADIUS)
    if resolved is not None:
        return resolved
    return (target_x, goal_y, target_z)


def _find_nearest_safe_goal(
    bot: Any,
    target_x: int,
    goal_y: int,
    target_z: int,
    search_radius: int,
) -> tuple[int, int, int] | None:
    start = (target_x, target_z)
    queue: deque[tuple[int, int]] = deque((start,))
    visited = {start}
    while queue:
        x, z = queue.popleft()
        if _is_safe_goal_cell(bot, x, goal_y, z):
            return (x, goal_y, z)
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx = x + dx
            nz = z + dz
            if abs(nx - target_x) + abs(nz - target_z) > search_radius:
                continue
            candidate = (nx, nz)
            if candidate in visited:
                continue
            visited.add(candidate)
            queue.append(candidate)
    return None


def _is_safe_goal_cell(bot: Any, x: int, goal_y: int, z: int) -> bool:
    if not _is_walkable_cell(bot, x, goal_y, z):
        return False
    if _is_diagonal_pinched(bot, x, goal_y, z):
        return False
    return _cell_clearance_score(bot, x, goal_y, z) >= 1


def _cell_clearance_score(bot: Any, x: int, goal_y: int, z: int) -> int:
    score = 0
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        if _is_body_clear(bot, x + dx, goal_y, z + dz):
            score += 1
    return score


def _is_walkable_cell(bot: Any, x: int, goal_y: int, z: int) -> bool:
    if not _is_body_clear(bot, x, goal_y, z):
        return False
    floor_block = _block_at_grid(bot, x, goal_y - 1, z)
    return _is_solid_block(floor_block)


def _is_body_clear(bot: Any, x: int, goal_y: int, z: int) -> bool:
    feet_block = _block_at_grid(bot, x, goal_y, z)
    head_block = _block_at_grid(bot, x, goal_y + 1, z)
    return _is_passable_block(feet_block) and _is_passable_block(head_block)


def _is_diagonal_pinched(bot: Any, x: int, goal_y: int, z: int) -> bool:
    corners = ((1, 1), (1, -1), (-1, 1), (-1, -1))
    for dx, dz in corners:
        corner_block = _block_at_grid(bot, x + dx, goal_y, z + dz)
        side_x_block = _block_at_grid(bot, x + dx, goal_y, z)
        side_z_block = _block_at_grid(bot, x, goal_y, z + dz)
        if _is_solid_block(corner_block) and _is_solid_block(side_x_block) and _is_solid_block(side_z_block):
            return True
    return False


def _block_at_grid(bot: Any, x: int, y: int, z: int) -> Any:
    block_at = getattr(bot, "blockAt", None)
    if not callable(block_at):
        return None
    position = getattr(bot, "entity", None)
    vec3_ctor = None
    world = getattr(bot, "world", None)
    if world is not None:
        vec3_ctor = None
    try:
        if hasattr(bot, "pathfinder"):
            pass
        return block_at(type(getattr(bot.entity, "position", None))(x, y, z))
    except Exception:
        pass
    try:
        return block_at(bot.entity.position.__class__(x, y, z))
    except Exception:
        return None


def _is_passable_block(block: Any) -> bool:
    if block is None:
        return True
    name = getattr(block, "name", None)
    bounding_box = getattr(block, "boundingBox", None)
    if name in {"air", "cave_air", "void_air"}:
        return True
    return bounding_box in {None, "empty"}


def _is_solid_block(block: Any) -> bool:
    if block is None:
        return False
    if _is_passable_block(block):
        return False
    name = getattr(block, "name", None)
    return name not in {"water", "lava"}


def _horizontal_distance(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    dx = left[0] - right[0]
    dz = left[2] - right[2]
    return (dx * dx + dz * dz) ** 0.5


def _compute_recovery_goal(
    current_position: tuple[float, float, float],
    action: MoveTo,
) -> tuple[int, int, int] | None:
    current_x, current_y, current_z = current_position
    delta_x = float(action.x) - current_x
    delta_z = float(action.z) - current_z
    magnitude = (delta_x * delta_x + delta_z * delta_z) ** 0.5
    if magnitude < 0.001:
        return None
    opposite_angle = math.atan2(-delta_z, -delta_x)
    angle_offset = math.radians(random.uniform(-STUCK_RECOVERY_ANGLE_DEGREES, STUCK_RECOVERY_ANGLE_DEGREES))
    recovery_angle = opposite_angle + angle_offset
    recovery_x = int(round(current_x + math.cos(recovery_angle) * STUCK_RECOVERY_TURN_DISTANCE))
    recovery_z = int(round(current_z + math.sin(recovery_angle) * STUCK_RECOVERY_TURN_DISTANCE))
    recovery_y = int(round(current_y))
    return (recovery_x, recovery_y, recovery_z)


def _set_optional_attr(target: Any, name: str, value: Any) -> None:
    if target is None or not hasattr(target, name):
        return
    try:
        setattr(target, name, value)
    except Exception:
        pass


def _clear_optional_mapping(target: Any, name: str) -> None:
    if target is None or not hasattr(target, name):
        return
    try:
        mapping = getattr(target, name)
        clear = getattr(mapping, "clear", None)
        if callable(clear):
            clear()
    except Exception:
        pass


def _set_control_state(bot: Any, control: str, enabled: bool) -> None:
    if bot is None:
        return
    set_control_state = getattr(bot, "setControlState", None)
    if not callable(set_control_state):
        return
    try:
        set_control_state(control, enabled)
    except Exception:
        pass


def _refresh_sprint_jump_state(
    bot: Any,
    now: float,
    *,
    last_refresh_at: float,
    refresh_interval: float,
) -> None:
    should_toggle = last_refresh_at <= 0.0 or (now - last_refresh_at) >= refresh_interval
    if should_toggle:
        _set_control_state(bot, "sprint", False)
        _set_control_state(bot, "jump", False)
    _set_control_state(bot, "sprint", True)
    _set_control_state(bot, "jump", True)


def _build_dynamic_state(
    observation: Observation, actions: Action | Iterable[Action] | None
) -> dict[str, Any]:
    normalized_actions = _normalize_actions(actions)
    animals = tuple(
        {
            "id": entity.entity_id,
            "type": entity.entity_type,
            "name": entity.name,
            "position": entity.grid_position.to_dict(),
        }
        for entity in observation.entities
        if entity.entity_type == "animal"
    )
    return {
        "me": observation.me.to_dict(),
        "players": tuple(player.to_dict() for player in observation.players),
        "animals": animals,
        "flags_to_capture": tuple(flag.to_dict() for flag in observation.flags_to_capture),
        "flags_to_protect": tuple(flag.to_dict() for flag in observation.flags_to_protect),
        "actions": tuple(_serialize_action(action) for action in normalized_actions),
    }


def _build_dynamic_delta(
    previous_state: Mapping[str, Any] | None, current_state: Mapping[str, Any]
) -> dict[str, Any]:
    if previous_state is None:
        return dict(current_state)

    delta: dict[str, Any] = {}
    for key, value in current_state.items():
        if previous_state.get(key) != value:
            delta[key] = value
    return delta


def _build_quick_snapshot_delta(
    previous_snapshot: Mapping[str, Any] | None,
    current_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    if previous_snapshot is None:
        return dict(current_snapshot)

    delta: dict[str, Any] = {}
    for key in ("bot", "players", "animals", "blocks"):
        if previous_snapshot.get(key) != current_snapshot.get(key):
            delta[key] = current_snapshot.get(key)
    return delta


def _build_js_helpers(require):
    vm = require("node:vm")

    block_to_json = vm.runInThisContext(
        """
        (block) => {
          if (!block) return "";
          return JSON.stringify({
            name: block.name ?? null,
            displayName: block.displayName ?? null,
            type: block.type ?? null,
            boundingBox: block.boundingBox ?? null,
            position: block.position ? {
              x: block.position.x,
              y: block.position.y,
              z: block.position.z
            } : null
          });
        }
        """
    )
    entities_to_json = vm.runInThisContext(
        """
        (entities) => JSON.stringify(
          Object.values(entities ?? {}).map((entity) => ({
            id: entity.id ?? null,
            type: entity.type ?? null,
            kind: entity.kind ?? null,
            name: entity.name ?? null,
            username: entity.username ?? null,
            displayName: entity.displayName ?? null,
            team: entity.team ?? null,
            position: entity.position ? {
              x: entity.position.x,
              y: entity.position.y,
              z: entity.position.z
            } : null
          }))
        )
        """
    )
    players_to_json = vm.runInThisContext(
        """
        (bot) => {
          const teams = bot?.scoreboard?.teams ?? bot?.teams ?? null
          const equipmentItems = (entity) =>
            Array.isArray(entity?.equipment) ? entity.equipment.slice(0, 6) : []
          const isBannerItem = (item) => {
            const name = item?.name ?? ''
            return name.includes('banner') || name.includes('Flag')
          }
          const isHoldingBanner = (entity) => {
            if (!entity) return false
            const heldItem = entity.heldItem ?? null
            if (isBannerItem(heldItem)) return true
            return equipmentItems(entity).some((item) => isBannerItem(item))
          }
          const resolveHeldItemName = (entity) => {
            if (!entity) return null
            const heldItem = entity.heldItem ?? null
            if (heldItem?.name) return heldItem.name
            const equippedBanner = equipmentItems(entity).find((item) => isBannerItem(item))
            return equippedBanner?.name ?? null
          }
          const resolveTeam = (username) => {
            if (!teams || typeof teams !== 'object') return null
            for (const [teamName, teamInfo] of Object.entries(teams)) {
              const players = teamInfo?.players ?? teamInfo?.members ?? teamInfo?.entities ?? null
              if (Array.isArray(players) && players.includes(username)) return teamName
              if (players && typeof players === 'object' && Object.keys(players).includes(username)) return teamName
            }
            return null
          }

          return JSON.stringify(
            Object.entries(bot?.players ?? {}).map(([username, player]) => {
              const entity = player?.entity ?? null
              return {
                username,
                team:
                  resolveTeam(username) ??
                  (typeof player?.team === 'string' ? player.team : (player?.team?.name ?? null)),
                hasBanner: isHoldingBanner(entity),
                heldItemName: resolveHeldItemName(entity)
              }
            })
          )
        }
        """
    )
    quick_snapshot_to_json = vm.runInThisContext(
        """
        (bot, Vec3, bounds) => {
          const teams = bot?.scoreboard?.teams ?? bot?.teams ?? null
          const equipmentItems = (entity) =>
            Array.isArray(entity?.equipment) ? entity.equipment.slice(0, 6) : []
          const isBannerItem = (item) => {
            const name = item?.name ?? ''
            return name.includes('banner') || name.includes('Flag')
          }
          const isHoldingBanner = (entity) => {
            if (!entity) return false
            const heldItem = entity.heldItem ?? null
            if (isBannerItem(heldItem)) return true
            return equipmentItems(entity).some((item) => isBannerItem(item))
          }
          const resolveHeldItemName = (entity) => {
            if (!entity) return null
            const heldItem = entity.heldItem ?? null
            if (heldItem?.name) return heldItem.name
            const equippedBanner = equipmentItems(entity).find((item) => isBannerItem(item))
            return equippedBanner?.name ?? null
          }
          const serializePosition = (position) => position ? {
            x: position.x,
            y: position.y,
            z: position.z
          } : null
          const isTrackedPosition = (position) => {
            if (!position) return false
            const y = Math.floor(position.y ?? -999)
            return (
              (y === 1 || y === 2) &&
              position.x >= bounds.min_x &&
              position.x <= bounds.max_x &&
              position.z >= bounds.min_z &&
              position.z <= bounds.max_z
            )
          }
          const resolveTeam = (username) => {
            if (!teams || typeof teams !== 'object') return null
            for (const [teamName, teamInfo] of Object.entries(teams)) {
              const players = teamInfo?.players ?? teamInfo?.members ?? teamInfo?.entities ?? null
              if (Array.isArray(players) && players.includes(username)) return teamName
              if (players && typeof players === 'object' && Object.keys(players).includes(username)) return teamName
            }
            return null
          }

          const players = Object.entries(bot?.players ?? {})
            .map(([username, player]) => {
              const entity = player?.entity ?? null
              const position = entity?.position ?? null
              if (!isTrackedPosition(position)) return null
              return {
                username,
                team:
                  resolveTeam(username) ??
                  (typeof player?.team === 'string' ? player.team : (player?.team?.name ?? null)),
                hasBanner: isHoldingBanner(entity),
                heldItemName: resolveHeldItemName(entity),
                position: serializePosition(position)
              }
            })
            .filter(Boolean)
            .sort((left, right) => String(left.username).localeCompare(String(right.username)))

          const animals = Object.values(bot?.entities ?? {})
            .map((entity) => {
              if (entity?.type !== 'animal') return null
              const position = entity?.position ?? null
              if (!isTrackedPosition(position)) return null
              return {
                id: entity.id ?? null,
                type: entity.type ?? null,
                name: entity.name ?? null,
                displayName: entity.displayName ?? null,
                position: serializePosition(position)
              }
            })
            .filter(Boolean)
            .sort((left, right) => {
              const leftKey = `${left.id ?? ''}:${left.name ?? ''}`
              const rightKey = `${right.id ?? ''}:${right.name ?? ''}`
              return leftKey.localeCompare(rightKey)
            })

          const blocks = []
          for (let y = 1; y <= 2; y += 1) {
            for (let z = bounds.min_z; z <= bounds.max_z; z += 1) {
              for (let x = bounds.min_x; x <= bounds.max_x; x += 1) {
                const block = bot?.blockAt?.(Vec3(x, y, z)) ?? null
                const name = block?.name ?? null
                if (name !== 'blue_banner' && name !== 'red_banner') continue
                blocks.push({
                  name,
                  displayName: block?.displayName ?? null,
                  type: block?.type ?? null,
                  boundingBox: block?.boundingBox ?? null,
                  position: serializePosition(block?.position ?? null)
                })
              }
            }
          }
          blocks.sort((left, right) => {
            const leftPos = left.position ?? { x: 0, y: 0, z: 0 }
            const rightPos = right.position ?? { x: 0, y: 0, z: 0 }
            return (
              (leftPos.y - rightPos.y) ||
              (leftPos.z - rightPos.z) ||
              (leftPos.x - rightPos.x)
            )
          })

          const botPosition = bot?.entity?.position ?? null
          return JSON.stringify({
            bot: {
              username: bot?.username ?? null,
              team:
                resolveTeam(bot?.username) ??
                (typeof bot?.players?.[bot?.username]?.team === 'string'
                  ? bot.players[bot.username].team
                  : (bot?.players?.[bot?.username]?.team?.name ?? null)),
              position: serializePosition(botPosition)
            },
            players,
            animals,
            blocks
          })
        }
        """
    )
    position_to_json = vm.runInThisContext(
        """
        (pos) => JSON.stringify({
          x: pos?.x ?? null,
          y: pos?.y ?? null,
          z: pos?.z ?? null
        })
        """
    )
    team_info_to_json = vm.runInThisContext(
        """
        (bot) => JSON.stringify({
          botTeam: typeof bot?.team === 'string' ? bot.team : (bot?.team?.name ?? null),
          playerTeam:
            typeof bot?.players?.[bot?.username]?.team === 'string'
              ? bot.players[bot.username].team
              : (bot?.players?.[bot?.username]?.team?.name ?? null),
          scoreboardTeam: (() => {
            const teams = bot?.scoreboard?.teams ?? bot?.teams ?? null
            if (!teams || typeof teams !== 'object') return null
            for (const [teamName, teamInfo] of Object.entries(teams)) {
              const players = teamInfo?.players ?? teamInfo?.members ?? teamInfo?.entities ?? null
              if (Array.isArray(players) && players.includes(bot?.username)) return teamName
              if (players && typeof players === 'object' && Object.keys(players).includes(bot?.username)) return teamName
            }
            return null
          })()
        })
        """
    )

    return (
        block_to_json,
        entities_to_json,
        players_to_json,
        quick_snapshot_to_json,
        position_to_json,
        team_info_to_json,
    )
