from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

import vgamepad as vg

from state import lock, MAX_PLAYERS, state

log = logging.getLogger("buzz")

# --------- vgamepad setup ----------
def _safe_disconnect(pad):
    try:
        if hasattr(pad, "disconnect"):
            pad.disconnect()
            return
    except Exception:
        pass
    try:
        del pad
    except Exception:
        pass


GAMEPADS: dict[int, object] = {}
PADTYPE: dict[int, str] = {}  # "x360" | "ds4"
VIGEM_OK = True
VIGEM_ERR = ""

# Buzz buttons
BUZZ_BUTTONS = {"RED", "BLUE", "ORANGE", "GREEN", "YELLOW"}

# X360 mapping
XBTN = vg.XUSB_BUTTON
X360_BTNMAP = {
    "RED": XBTN.XUSB_GAMEPAD_A,
    "BLUE": XBTN.XUSB_GAMEPAD_B,
    "ORANGE": XBTN.XUSB_GAMEPAD_X,
    "GREEN": XBTN.XUSB_GAMEPAD_Y,
    "YELLOW": XBTN.XUSB_GAMEPAD_LEFT_SHOULDER,
}

def _resolve_attr(obj, names: list[str]):
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None

DSBTN = vg.DS4_BUTTONS
DS4_BTNMAP = {
    "RED": _resolve_attr(DSBTN, ["DS4_BUTTON_CROSS", "DS4_BUTTON_X"]),
    "BLUE": _resolve_attr(DSBTN, ["DS4_BUTTON_CIRCLE", "DS4_BUTTON_O"]),
    "ORANGE": _resolve_attr(DSBTN, ["DS4_BUTTON_SQUARE"]),
    "GREEN": _resolve_attr(DSBTN, ["DS4_BUTTON_TRIANGLE"]),
    "YELLOW": _resolve_attr(DSBTN, ["DS4_BUTTON_SHOULDER_LEFT", "DS4_BUTTON_L1"]),
}

def ensure_gamepads_upto(n: int):
    global VIGEM_OK, VIGEM_ERR
    # create
    for p in range(1, n + 1):
        if p in GAMEPADS:
            continue
        try:
            if p <= 4:
                GAMEPADS[p] = vg.VX360Gamepad()
                PADTYPE[p] = "x360"
            else:
                GAMEPADS[p] = vg.VDS4Gamepad()
                PADTYPE[p] = "ds4"
        except Exception as e:
            VIGEM_OK = False
            VIGEM_ERR = str(e)
            log.error("Failed to create virtual pad: %s", VIGEM_ERR)
            break

    # remove
    for p in list(GAMEPADS.keys()):
        if p > n:
            _safe_disconnect(GAMEPADS[p])
            GAMEPADS.pop(p, None)
            PADTYPE.pop(p, None)


def _tap_gamepad_button(player: int, buzz_button: str, hold_ms: int = 50):
    """Runs in worker thread (ok to sleep here)."""
    if not VIGEM_OK:
        return

    with lock:
        ensure_gamepads_upto(int(state["num_players"]))

    pad = GAMEPADS.get(player)
    if not pad:
        return

    kind = PADTYPE.get(player, "x360")
    buzz_button = str(buzz_button).upper()

    if kind == "x360":
        btn = X360_BTNMAP.get(buzz_button)
        if not btn:
            return
        pad.press_button(button=btn)
        pad.update()
        time.sleep(hold_ms / 1000.0)
        pad.release_button(button=btn)
        pad.update()
        return

    btn = DS4_BTNMAP.get(buzz_button)
    if not btn:
        return
    pad.press_button(button=btn)
    pad.update()
    time.sleep(hold_ms / 1000.0)
    pad.release_button(button=btn)
    pad.update()


# --------- press queue ----------
@dataclass(frozen=True)
class PressJob:
    player: int
    button: str
    hold_ms: int = 50


press_q: "queue.Queue[PressJob]" = queue.Queue(maxsize=2000)

def enqueue_press(player: int, button: str, hold_ms: int = 50) -> bool:
    try:
        press_q.put_nowait(PressJob(player=player, button=button, hold_ms=hold_ms))
        return True
    except queue.Full:
        return False


def press_worker_loop():
    log.info("Press worker started")
    while True:
        job = press_q.get()
        try:
            _tap_gamepad_button(job.player, job.button, job.hold_ms)
        except Exception as e:
            log.warning("Press worker error: %s", e)
        finally:
            press_q.task_done()


def start_workers():
    # initial pads
    with lock:
        ensure_gamepads_upto(int(state["num_players"]))

    if any(v is None for v in DS4_BTNMAP.values()):
        log.warning("DS4 mapping incomplete: %s", DS4_BTNMAP)

    t = threading.Thread(target=press_worker_loop, daemon=True)
    t.start()