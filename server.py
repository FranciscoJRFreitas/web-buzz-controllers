from __future__ import annotations

import csv
import json
import logging
import time
import threading
import webbrowser
from typing import Any, Iterator

from flask import Flask, Response, jsonify, render_template, request

from net import get_lan_ip, make_qr_data_uri
from pads import BUZZ_BUTTONS, enqueue_press, ensure_gamepads_upto, start_workers
from state import (
    MAX_PLAYERS,
    lock,
    now_mono,
    blocked_ips,
    debounce_allow,
    debounce_cleanup_loop,
    events,
    events_cv,
    label_for_player,
    load_config,
    mark_reclaim,
    player_labels,
    push_event,
    reclaim_tokens,
    save_config,
    set_label as set_label_state,
    state,
    find_player_by_token,
)

# -------- logging --------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("buzz")

app = Flask(__name__)

SLOT_TIMEOUT_SEC = 45

def is_localhost(ip: str) -> bool:
    # keep your current rule (simple LAN app)
    return ip in ("127.0.0.1", "::1")


# -------- background cleanup (monotonic) --------
def cleanup_loop():
    while True:
        time.sleep(3)
        t = now_mono()
        with lock:
            for p in range(1, MAX_PLAYERS + 1):
                s = state["slots"].get(p)
                if not s:
                    continue
                last = float(s.get("last_seen_mono", 0.0))
                if (t - last) > SLOT_TIMEOUT_SEC:
                    token = str(s.get("token") or "")
                    state["slots"][p] = None
                    mark_reclaim(token, p)
                    push_event("timeout", player=p, meta=label_for_player(p))
                    log.info("timeout p=%s", p)


# -------- SSE --------
def sse_format(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/events/stream")
def events_stream():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403

    # from last id
    try:
        last_id = int(request.args.get("since", "0"))
    except Exception:
        last_id = 0

    def gen() -> Iterator[str]:
        nonlocal last_id
        # send initial comment (helps proxies)
        yield ": connected\n\n"
        while True:
            with lock:
                new = [e for e in events if int(e["id"]) > last_id]
                if new:
                    for e in new:
                        last_id = max(last_id, int(e["id"]))
                        # enrich with label for UI convenience
                        if e.get("player"):
                            e = dict(e)
                            e["label"] = label_for_player(int(e["player"]))
                        yield sse_format("ev", e)
                    continue

                # no events => wait (and keepalive)
                events_cv.wait(timeout=15.0)

            # keepalive ping
            yield ": ping\n\n"

    return Response(gen(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # nginx
    })


# -------- pages --------
@app.get("/")
def root():
    ip = request.remote_addr or ""
    if is_localhost(ip):
        return Response(render_template("host.html", timeout=SLOT_TIMEOUT_SEC), mimetype="text/html")

    return Response(
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        '<div style="font-family:system-ui;padding:16px">'
        '<h2>Buzz Web</h2>'
        '<p>Open <b>/join</b> on mobile.</p>'
        '<p>On PC (host), open <b>/</b> to configure.</p>'
        '</div>',
        mimetype="text/html",
    )


@app.get("/host")
def host():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return Response("Host only on PC (localhost).", status=403)
    return Response(render_template("host.html", timeout=SLOT_TIMEOUT_SEC), mimetype="text/html")


@app.get("/join")
def join():
    return Response(render_template("join.html"), mimetype="text/html")


@app.get("/pad")
def pad():
    return Response(render_template("pad.html"), mimetype="text/html")


@app.get("/share")
def share():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return Response("Share only on PC (localhost).", status=403)

    lan_ip = get_lan_ip()
    join_url = f"http://{lan_ip}:5000/join"
    qr_uri = make_qr_data_uri(join_url)

    return Response(
        render_template("share.html", join_url=join_url, qr_uri=qr_uri),
        mimetype="text/html",
    )


@app.get("/join_url")
def join_url():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403
    lan_ip = get_lan_ip()
    return jsonify({"ok": True, "url": f"http://{lan_ip}:5000/join"})


# -------- state APIs --------
@app.get("/state")
def get_state():
    ip = request.remote_addr or ""
    host = is_localhost(ip)

    with lock:
        slots: dict[int, dict[str, Any]] = {}
        for p in range(1, MAX_PLAYERS + 1):
            s = state["slots"][p]
            if not s:
                slots[p] = {"busy": False}
            else:
                slots[p] = {"busy": True}
                if host:
                    slots[p]["ip"] = s.get("ip", "")
                    slots[p]["token"] = s.get("token", "")
            slots[p]["label"] = player_labels.get(p, f"Player {p}")

        labels = {p: player_labels.get(p, f"Player {p}") for p in range(1, MAX_PLAYERS + 1)}

        return jsonify({
            "num_players": state["num_players"],
            "slots": slots,
            "labels": labels,
            "host": host
        })
    

@app.post("/label")
def label_route():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403

    data = request.get_json(silent=True) or {}
    try:
        p = int(data.get("player"))
    except Exception:
        return jsonify({"ok": False, "err": "invalid_player"}), 400

    if p < 1 or p > MAX_PLAYERS:
        return jsonify({"ok": False, "err": "invalid_player"}), 400

    name = str(data.get("label") or "")
    with lock:
        set_label_state(p, name)
        push_event("label", player=p, meta=player_labels[p])

    return jsonify({"ok": True, "player": p, "label": player_labels[p]})


@app.get("/labels")
def get_labels():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403
    with lock:
        return jsonify({"ok": True, "labels": {str(p): player_labels[p] for p in range(1, MAX_PLAYERS + 1)}})


# -------- host quick actions --------
@app.post("/kick_all")
def kick_all():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403

    with lock:
        for p in range(1, MAX_PLAYERS + 1):
            s = state["slots"].get(p)
            if s:
                tok = str(s.get("token") or "")
                state["slots"][p] = None
                mark_reclaim(tok, p)
        push_event("reset", meta="kick_all")
    return jsonify({"ok": True})


@app.post("/logs/clear")
def logs_clear():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403
    with lock:
        events.clear()
        push_event("log", meta="cleared")  # create a new first event after clear
    return jsonify({"ok": True})


# -------- moderation (existing) --------
@app.get("/blocked")
def blocked_list():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403
    with lock:
        return jsonify({"ok": True, "ips": sorted(blocked_ips)})


@app.post("/unblock")
def unblock_ip():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403
    data = request.get_json(silent=True) or {}
    target_ip = str(data.get("ip") or "")
    if not target_ip:
        return jsonify({"ok": False, "err": "missing_ip"}), 400
    with lock:
        blocked_ips.discard(target_ip)
        push_event("unblock", meta=target_ip)
    return jsonify({"ok": True, "ip": target_ip})


@app.post("/block")
def block_ip():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403

    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "block").lower()
    target_ip = str(data.get("ip") or "")
    player = data.get("player", None)

    with lock:
        if (not target_ip) and player is not None:
            try:
                p = int(player)
                s = state["slots"].get(p)
                if s:
                    target_ip = str(s.get("ip") or "")
            except Exception:
                pass

        if not target_ip:
            return jsonify({"ok": False, "err": "missing_ip"}), 400

        if mode == "unblock":
            blocked_ips.discard(target_ip)
            push_event("unblock", meta=target_ip)
            return jsonify({"ok": True, "mode": "unblock", "ip": target_ip})

        blocked_ips.add(target_ip)
        push_event("block", meta=target_ip)

        kicked = []
        for p in range(1, MAX_PLAYERS + 1):
            s = state["slots"].get(p)
            if s and s.get("ip") == target_ip:
                tok = str(s.get("token") or "")
                state["slots"][p] = None
                mark_reclaim(tok, p)
                kicked.append(p)
                push_event("kick", player=p, meta=target_ip)

        return jsonify({"ok": True, "mode": "block", "ip": target_ip, "kicked": kicked})


@app.post("/kick")
def kick():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403

    data = request.get_json(silent=True) or {}
    try:
        p = int(data.get("player"))
        if p < 1 or p > MAX_PLAYERS:
            raise ValueError()
    except Exception:
        return jsonify({"ok": False, "err": "invalid_player"}), 400

    with lock:
        s = state["slots"].get(p)
        if not s:
            return jsonify({"ok": True, "already_free": True})

        kicked_ip = str(s.get("ip") or "")
        tok = str(s.get("token") or "")
        state["slots"][p] = None
        mark_reclaim(tok, p)
        push_event("kick", player=p, meta=kicked_ip or "host")

    return jsonify({"ok": True, "player": p})


# -------- config --------
@app.post("/config")
def set_config():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403

    data = request.get_json(silent=True) or {}
    try:
        n = int(data.get("num_players"))
    except Exception:
        return jsonify({"ok": False, "err": "invalid_num_players"}), 400

    if n not in range(2, MAX_PLAYERS + 1):
        return jsonify({"ok": False, "err": "invalid_num_players"}), 400

    with lock:
        state["num_players"] = n
        for p in range(n + 1, MAX_PLAYERS + 1):
            s = state["slots"].get(p)
            if s:
                tok = str(s.get("token") or "")
                mark_reclaim(tok, p)
            state["slots"][p] = None
        ensure_gamepads_upto(n)
        save_config()
        push_event("config", meta=f"num_players={n}")

    return jsonify({"ok": True, "num_players": n})


# -------- session --------
@app.get("/session")
def session_info():
    ip = request.remote_addr or ""
    with lock:
        if (not is_localhost(ip)) and ip in blocked_ips:
            return jsonify({"ok": False, "err": "blocked"}), 403

    token = request.args.get("token", default="", type=str)
    if not token:
        return jsonify({"ok": False, "err": "missing"}), 400

    with lock:
        p = find_player_by_token(token)
        if not p:
            return jsonify({"ok": False, "err": "no_session"}), 404
        state["slots"][p]["last_seen_mono"] = now_mono()

    return jsonify({"ok": True, "player": p, "label": label_for_player(int(p))})


# -------- join/claim/reclaim --------
@app.post("/claim")
def claim():
    data = request.get_json(silent=True) or {}
    ip = request.remote_addr or ""
    with lock:
        if (not is_localhost(ip)) and ip in blocked_ips:
            return jsonify({"ok": False, "err": "blocked"}), 403

    try:
        p = int(data.get("player"))
        if p < 1 or p > MAX_PLAYERS:
            raise ValueError()
    except Exception:
        return jsonify({"ok": False, "err": "invalid_player"}), 400

    with lock:
        if p > int(state["num_players"]):
            return jsonify({"ok": False, "err": "disabled"}), 400
        if state["slots"][p] is not None:
            return jsonify({"ok": False, "err": "occupied"}), 409

        ensure_gamepads_upto(int(state["num_players"]))

        import secrets
        token = secrets.token_urlsafe(24)
        state["slots"][p] = {"token": token, "last_seen_mono": now_mono(), "ip": ip}
        push_event("join", player=p, meta=ip)
        log.info("join p=%s ip=%s", p, ip)
        return jsonify({"ok": True, "player": p, "token": token, "label": label_for_player(p)})


@app.post("/reclaim")
def reclaim():
    """Reconnect using the old token; allowed if token is in grace window and player is free."""
    data = request.get_json(silent=True) or {}
    ip = request.remote_addr or ""

    with lock:
        if (not is_localhost(ip)) and ip in blocked_ips:
            return jsonify({"ok": False, "err": "blocked"}), 403

    token = str(data.get("token") or "")
    if not token:
        return jsonify({"ok": False, "err": "missing"}), 400

    with lock:
        # If already active, treat as ok
        p_existing = find_player_by_token(token)
        if p_existing:
            state["slots"][p_existing]["last_seen_mono"] = now_mono()
            return jsonify({"ok": True, "player": p_existing, "token": token, "label": label_for_player(p_existing)})

        # Grace window
        info = reclaim_tokens.get(token)
        if not info:
            return jsonify({"ok": False, "err": "no_grace"}), 404
        if float(info.get("expires_mono", 0.0)) < now_mono():
            reclaim_tokens.pop(token, None)
            return jsonify({"ok": False, "err": "expired"}), 410

        p = int(info.get("player"))
        if p > int(state["num_players"]):
            return jsonify({"ok": False, "err": "disabled"}), 400
        if state["slots"][p] is not None:
            return jsonify({"ok": False, "err": "occupied"}), 409

        state["slots"][p] = {"token": token, "last_seen_mono": now_mono(), "ip": ip}
        reclaim_tokens.pop(token, None)
        push_event("rejoin", player=p, meta=ip)
        log.info("reclaim p=%s ip=%s", p, ip)
        return jsonify({"ok": True, "player": p, "token": token, "label": label_for_player(p)})


# -------- heartbeat / leave --------
@app.post("/heartbeat")
def heartbeat():
    ip = request.remote_addr or ""
    with lock:
        if (not is_localhost(ip)) and ip in blocked_ips:
            return jsonify({"ok": False, "err": "blocked"}), 403

    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "")
    if not token:
        return jsonify({"ok": False, "err": "missing"}), 400

    with lock:
        p = find_player_by_token(token)
        if not p:
            return jsonify({"ok": False, "err": "no_session"}), 404
        state["slots"][p]["last_seen_mono"] = now_mono()

    return jsonify({"ok": True})


@app.post("/leave")
def leave():
    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "")
    with lock:
        p = find_player_by_token(token)
        if p:
            state["slots"][p] = None
            mark_reclaim(token, p)
            push_event("leave", player=p)
            log.info("leave p=%s", p)
    return jsonify({"ok": True})


@app.post("/leave_beacon")
def leave_beacon():
    # called by sendBeacon (raw body)
    try:
        raw = request.get_data(as_text=True) or ""
        token = ""
        if '"token"' in raw:
            token = json.loads(raw).get("token", "")
        token = str(token or "")
        with lock:
            p = find_player_by_token(token)
            if p:
                state["slots"][p] = None
                mark_reclaim(token, p)
                push_event("leave", player=p, meta="beacon")
        return ("", 204)
    except Exception:
        return ("", 204)


# -------- press (no sleep here) --------
@app.post("/press")
def press():
    ip = request.remote_addr or ""
    with lock:
        if (not is_localhost(ip)) and ip in blocked_ips:
            return jsonify({"ok": False, "err": "blocked"}), 403

    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "")
    button = str(data.get("button") or "").upper()

    if not token or not button:
        return jsonify({"ok": False, "err": "missing"}), 400

    if button not in BUZZ_BUTTONS:
        return jsonify({"ok": False, "err": "invalid_button"}), 400

    with lock:
        p = find_player_by_token(token)
        if not p:
            return jsonify({"ok": False, "err": "no_session"}), 404

        # keep alive
        state["slots"][p]["last_seen_mono"] = now_mono()

        # debounce (monotonic)
        if not debounce_allow(int(p), button):
            return jsonify({"ok": True, "debounced": True})

        # log event (include label in meta for convenience)
        push_event("press", player=int(p), button=button, meta=label_for_player(int(p)))

    # enqueue press (fast; no sleep)
    ok = enqueue_press(int(p), button, hold_ms=50)
    if not ok:
        return jsonify({"ok": False, "err": "queue_full"}), 503

    mode = "xinput" if int(p) <= 4 else "ds4"
    return jsonify({"ok": True, "player": int(p), "button": button, "mode": mode})


# -------- startup --------
def start_background():
    threading.Thread(target=cleanup_loop, daemon=True).start()
    threading.Thread(target=debounce_cleanup_loop, daemon=True).start()
    start_workers()


if __name__ == "__main__":
    load_config()
    start_background()

    def open_browser():
        time.sleep(0.6)
        webbrowser.open("http://127.0.0.1:5000/")

    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)