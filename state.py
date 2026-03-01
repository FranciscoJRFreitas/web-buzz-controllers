from __future__ import annotations

import json
import logging
import time
import threading
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger("buzz")

MAX_PLAYERS = 8
EVENTS_MAX = 200000
player_labels = {p: f"Player {p}" for p in range(1, MAX_PLAYERS + 1)}

CONFIG_PATH = Path("buzz_config.json")

lock = threading.RLock()

# Events
events = deque(maxlen=EVENTS_MAX)
_event_id = 0
events_cv = threading.Condition(lock)

# Security / moderation
blocked_ips: set[str] = set()

# Player labels (persisted)
player_labels: dict[int, str] = {p: f"Player {p}" for p in range(1, MAX_PLAYERS + 1)}

# Slots
state: dict[str, Any] = {
    "num_players": 2,
    "slots": {p: None for p in range(1, MAX_PLAYERS + 1)},  # None or dict(token, last_seen_mono, ip)
}

# Grace reclaim: token -> {"player": int, "expires_mono": float}
RECLAIM_GRACE_SEC = 25
reclaim_tokens: dict[str, dict[str, Any]] = {}

# Debounce
DEBOUNCE_SEC = 0.06
DEBOUNCE_TTL_SEC = 5.0  # cleanup old keys
_last_press_mono: dict[tuple[int, str], float] = {}


def now_mono() -> float:
    return time.monotonic()


def load_config() -> None:
    if not CONFIG_PATH.exists():
        return
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        n = int(data.get("num_players", state["num_players"]))
        if 2 <= n <= MAX_PLAYERS:
            state["num_players"] = n

        labels = data.get("labels", {})
        if isinstance(labels, dict):
            for k, v in labels.items():
                try:
                    p = int(k)
                    if 1 <= p <= MAX_PLAYERS and isinstance(v, str) and v.strip():
                        player_labels[p] = v.strip()[:24]
                except Exception:
                    continue

        log.info("Loaded config: num_players=%s", state["num_players"])
    except Exception as e:
        log.warning("Failed to load config: %s", e)


def save_config() -> None:
    try:
        data = {
            "num_players": int(state["num_players"]),
            "labels": {str(p): player_labels[p] for p in range(1, MAX_PLAYERS + 1)},
        }
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Failed to save config: %s", e)


def push_event(kind: str, player: int | None = None, button: str | None = None, meta: str | None = None) -> dict[str, Any]:
    """Event timestamp ts is wall clock (for display); ordering uses id."""
    global _event_id
    with lock:
        _event_id += 1
        ev = {
            "id": _event_id,
            "ts": time.time(),
            "kind": kind,
            "player": player,
            "button": button,
            "meta": meta,
        }
        events.append(ev)
        events_cv.notify_all()
        return ev


def label_for_player(p: int) -> str:
    return player_labels.get(p) or f"Player {p}"


def set_label(p: int, name: str) -> None:
    name = (name or "").strip()
    if not name:
        name = f"Player {p}"
    player_labels[p] = name[:24]
    save_config()


def is_slot_busy(p: int) -> bool:
    return state["slots"].get(p) is not None


def find_player_by_token(token: str) -> int | None:
    for p in range(1, MAX_PLAYERS + 1):
        s = state["slots"].get(p)
        if s and s.get("token") == token:
            return p
    return None


def mark_reclaim(token: str, player: int) -> None:
    """Allow a token to reclaim the same player for a short grace period."""
    if not token:
        return
    reclaim_tokens[token] = {"player": int(player), "expires_mono": now_mono() + RECLAIM_GRACE_SEC}


def prune_reclaim() -> None:
    t = now_mono()
    dead = [tok for tok, v in reclaim_tokens.items() if v.get("expires_mono", 0) < t]
    for tok in dead:
        reclaim_tokens.pop(tok, None)


def debounce_allow(player: int, button: str) -> bool:
    """Return True if allowed (not debounced). Uses monotonic time."""
    t = now_mono()
    k = (player, button)
    last = _last_press_mono.get(k, 0.0)
    if t - last < DEBOUNCE_SEC:
        return False
    _last_press_mono[k] = t
    return True


def debounce_cleanup_loop() -> None:
    while True:
        time.sleep(2.0)
        t = now_mono()
        with lock:
            # remove very old debounce keys
            dead = [k for k, ts in _last_press_mono.items() if (t - ts) > DEBOUNCE_TTL_SEC]
            for k in dead:
                _last_press_mono.pop(k, None)
            prune_reclaim()