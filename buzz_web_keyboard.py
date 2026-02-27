from flask import Flask, request, jsonify, Response
from pynput.keyboard import Controller, KeyCode
import time
import threading
import secrets

app = Flask(__name__)
kb = Controller()
lock = threading.Lock()

# -----------------------
# CONFIG / STATE
# -----------------------
MAX_PLAYERS = 4
state = {
    "num_players": 2,  # host sets 2/3/4
    "slots": {1: None, 2: None, 3: None, 4: None},  # {player: {"token":..., "last_seen":...}}
}

# --- KEY MAPPING (edit if you want) ---
KEYMAP = {
    1: {"RED": "q", "BLUE": "w", "YELLOW": "e", "GREEN": "r", "ORANGE": "t"},
    2: {"RED": "a", "BLUE": "s", "YELLOW": "d", "GREEN": "f", "ORANGE": "g"},
    3: {"RED": "z", "BLUE": "x", "YELLOW": "c", "GREEN": "v", "ORANGE": "b"},
    4: {"RED": "u", "BLUE": "i", "YELLOW": "o", "GREEN": "p", "ORANGE": "l"},
}

# Debounce (avoid spam double taps)
DEBOUNCE = 0.06
_last_press = {}

# Slot timeout: if a phone disappears, free the slot
SLOT_TIMEOUT_SEC = 18
HEARTBEAT_INTERVAL_SEC = 5

def tap_key(ch: str):
    try:
        k = KeyCode.from_char(ch)
        kb.press(k)
        kb.release(k)
    except Exception:
        pass

def is_localhost(ip: str) -> bool:
  return ip in ("127.0.0.1", "::1")

def cleanup_loop():
    while True:
        time.sleep(3)
        now = time.time()
        with lock:
            for p in range(1, MAX_PLAYERS + 1):
                s = state["slots"].get(p)
                if s and now - s["last_seen"] > SLOT_TIMEOUT_SEC:
                    state["slots"][p] = None


threading.Thread(target=cleanup_loop, daemon=True).start()

# -----------------------
# PAGES
# -----------------------

HOST_HTML = r"""
<!doctype html>
<html lang="pt">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Buzz Host</title>
  <style>
    body{font-family:system-ui,Segoe UI,Arial;margin:16px;background:#0f1115;color:#e9edf1}
    .card{max-width:720px;margin:0 auto;background:#151924;border-radius:18px;padding:16px;box-shadow:0 6px 24px rgba(0,0,0,.35)}
    .row{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
    select,button{font-size:16px;padding:10px 12px;border-radius:12px;border:0}
    select{background:#20263a;color:#e9edf1}
    button{background:#3b82f6;color:#0b1220;font-weight:800;cursor:pointer}
    .muted{opacity:.8;font-size:13px;line-height:1.35;margin-top:10px}
    .slots{margin-top:14px;background:#101423;border-radius:14px;padding:12px}
    .slot{display:flex;justify-content:space-between;padding:8px 6px;border-bottom:1px solid rgba(255,255,255,.06)}
    .slot:last-child{border-bottom:0}
    .pill{padding:4px 10px;border-radius:999px;font-weight:700;font-size:12px}
    .free{background:#1f2937;color:#cbd5e1}
    .busy{background:#16a34a;color:#052e12}
    a{color:#93c5fd}
    code{background:#0b1020;padding:2px 6px;border-radius:8px}
  </style>
</head>
<body>
  <div class="card">
    <h2 style="margin:0 0 10px 0;">Buzz Host (PC)</h2>
    <div class="row">
      <label><b>Jogadores:</b>
        <select id="players">
          <option value="2">2</option>
          <option value="3">3</option>
          <option value="4">4</option>
        </select>
      </label>
      <button onclick="save()">Guardar</button>
      <div id="status" style="margin-left:auto;opacity:.85">—</div>
    </div>

    <div class="muted">
      No telemóvel, abre: <code>http://IP_DO_PC:5000/join</code><br/>
      Ex.: <code>http://192.168.1.129:5000/join</code>
    </div>

    <div class="slots">
      <div style="font-weight:800;margin-bottom:8px">Estado dos jogadores</div>
      <div id="slotlist"></div>
    </div>

    <div class="muted">
      Nota: Se um telemóvel fechar a página, o lugar liberta sozinho em ~%TIMEOUT%s.
    </div>
  </div>

<script>
  const TIMEOUT = %TIMEOUT%;
  const statusEl = document.getElementById("status");
  const sel = document.getElementById("players");
  const slotlist = document.getElementById("slotlist");

  function setStatus(t){ statusEl.textContent = t; }

  async function refresh(){
    const r = await fetch("/state");
    const s = await r.json();
    sel.value = String(s.num_players);

    slotlist.innerHTML = "";
    for(let p=1;p<=4;p++){
      const row = document.createElement("div");
      row.className="slot";
      const enabled = p <= s.num_players;
      const busy = !!s.slots[p];
      row.innerHTML = `
        <div>Jogador ${p} ${enabled ? "" : "(desativado)"}</div>
        <div class="pill ${busy ? "busy":"free"}">${busy ? "OCUPADO":"LIVRE"}</div>
      `;
      slotlist.appendChild(row);
    }
    setStatus("Atualizado");
  }

  async function save(){
    setStatus("A guardar...");
    const n = parseInt(sel.value,10);
    const r = await fetch("/config", {
      method:"POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({num_players:n})
    });
    const j = await r.json();
    if(j.ok) setStatus("Guardado");
    else setStatus("Erro: " + (j.err || "—"));
    await refresh();
  }

  refresh();
  setInterval(refresh, 2000);
</script>
</body>
</html>
""".replace("%TIMEOUT%", str(SLOT_TIMEOUT_SEC)).replace("%TIMEOUT%s", str(SLOT_TIMEOUT_SEC))


JOIN_HTML = r"""
<!doctype html>
<html lang="pt">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no"/>
  <title>Buzz Join</title>
  <style>
    body{font-family:system-ui,Segoe UI,Arial;margin:0;background:#0b0d10;color:#e9edf1}
    .wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px}
    .card{width:min(720px,100%);background:#141821;border-radius:20px;padding:16px;box-shadow:0 6px 24px rgba(0,0,0,.35)}
    h2{margin:0 0 12px 0}
    .grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
    button{border:0;border-radius:18px;padding:18px 12px;font-size:18px;font-weight:900;cursor:pointer}
    .p1{background:#ef4444;color:#1a0b0b}
    .p2{background:#3b82f6;color:#06121f}
    .p3{background:#facc15;color:#1a1404}
    .p4{background:#22c55e;color:#04150b}
    .disabled{opacity:.35;filter:grayscale(1);cursor:not-allowed}
    .muted{opacity:.8;font-size:13px;line-height:1.35;margin-top:10px}
    .msg{margin-top:12px;font-weight:700;opacity:.9}
    a{color:#93c5fd}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h2>Escolhe o teu jogador</h2>
      <div class="grid">
        <button id="b1" class="p1" onclick="pick(1)">Jogador 1</button>
        <button id="b2" class="p2" onclick="pick(2)">Jogador 2</button>
        <button id="b3" class="p3" onclick="pick(3)">Jogador 3</button>
        <button id="b4" class="p4" onclick="pick(4)">Jogador 4</button>
      </div>
      <div class="msg" id="msg">—</div>
      <div class="muted">
        Se um jogador estiver ocupado, não dá para entrar nele.<br/>
        Dica: adiciona aos favoritos: <code>/join</code>
      </div>
    </div>
  </div>

<script>
  const msg = document.getElementById("msg");

  function setMsg(t){ msg.textContent = t; }

  async function refresh(){
    const r = await fetch("/state");
    const s = await r.json();

    for(let p=1;p<=4;p++){
      const b = document.getElementById("b"+p);
      const enabled = p <= s.num_players;
      const busy = !!s.slots[p];
      const dis = (!enabled) || busy;
      b.classList.toggle("disabled", dis);
      b.disabled = dis;
    }
    setMsg("Escolhe um jogador livre.");
  }

  async function pick(p){
    setMsg("A entrar no Jogador " + p + "...");
    const r = await fetch("/claim", {
      method:"POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({player:p})
    });
    const j = await r.json();
    if(!j.ok){
      setMsg("Não deu: " + (j.err || "ocupado"));
      await refresh();
      return;
    }
    // store token + player, go to controller
    localStorage.setItem("buzz_token", j.token);
    localStorage.setItem("buzz_player", String(j.player));
    location.href = "/pad";
  }

  refresh();
  setInterval(refresh, 1500);
</script>
</body>
</html>
"""

PAD_HTML = r"""
<!doctype html>
<html lang="pt">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no"/>
  <title>Buzz Controller</title>
  <style>
    html,body{
      margin:0;
      height:100%;
      background:#0a0c10;
      font-family:system-ui,Segoe UI,Arial;
      -webkit-user-select:none;
      user-select:none;
    }

    .screen{
      min-height:100svh;
      display:flex;
      align-items:center;
      justify-content:center;
      padding:16px;
      box-sizing:border-box;
    }

    .frame{
      position:relative;
      width:min(420px, 92vw);
      display:flex;
      flex-direction:column;
      align-items:center;
      gap:10px;
    }

    .topbar{
      width:100%;
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:10px;
      color:#e9edf1;
      opacity:.9;
      font-weight:900;
      margin-bottom:2px;
    }

    .topbar .left{
      display:flex;
      flex-direction:column;
      gap:2px;
      min-width:0;
    }

    .smallstatus{
      font-size:12px;
      opacity:.75;
      font-weight:700;
      white-space:nowrap;
      overflow:hidden;
      text-overflow:ellipsis;
      max-width:44vw;
    }

    .actions{
      display:flex;
      gap:8px;
      align-items:center;
    }

    .btn{
      border:0;
      border-radius:12px;
      padding:10px 12px;
      font-weight:900;
      cursor:pointer;
      background:#111827;
      color:#e5e7eb;
    }

    .btn.primary{
      background:#1f2937;
    }

    .controller{
      width:100%;
      aspect-ratio:1 / 1.9;
      background:#1a1d25;
      border-radius:44px;
      padding:22px 18px;
      box-sizing:border-box;
      display:flex;
      flex-direction:column;
      align-items:center;
      gap:18px;
      box-shadow:0 15px 40px rgba(0,0,0,.6);
    }

    .red{
      width:44%;
      aspect-ratio:1 / 1;
      border-radius:50%;
      border:none;
      background:#ff2d2d;
      box-shadow:0 10px 25px rgba(255,0,0,.45);
    }

    .small{
      width:44%;
      height:12%;
      min-height:46px;
      border:none;
      border-radius:16px;
      box-shadow:0 6px 15px rgba(0,0,0,.45);
    }

    .blue{ background:#3ea6ff; }
    .orange{ background:#ff9c2f; }
    .green{ background:#47e77a; }
    .yellow{ background:#ffe347; }

    button:active{ transform:scale(.96); }
  </style>
</head>
<body>
  <div class="screen">
    <div class="frame">
      <div class="topbar">
        <div class="left">
          <div id="who">Buzz</div>
          <div class="smallstatus" id="status">Ligado</div>
        </div>
        <div class="actions">
          <button class="btn primary" id="fsBtn" onclick="toggleFullscreen()">Ecrã inteiro</button>
          <button class="btn" onclick="leave()">Sair</button>
        </div>
      </div>

      <div class="controller">
        <button class="red" id="RED" aria-label="Vermelho"></button>
        <button class="small blue" id="BLUE" aria-label="Azul"></button>
        <button class="small orange" id="ORANGE" aria-label="Laranja"></button>
        <button class="small green" id="GREEN" aria-label="Verde"></button>
        <button class="small yellow" id="YELLOW" aria-label="Amarelo"></button>
      </div>
    </div>
  </div>

<script>
  const token  = localStorage.getItem("buzz_token");
  const player = localStorage.getItem("buzz_player");

  const whoEl = document.getElementById("who");
  const statusEl = document.getElementById("status");
  const fsBtn = document.getElementById("fsBtn");

  function setStatus(t){ statusEl.textContent = t; }

  whoEl.textContent = player ? ("Jogador " + player) : "Sem sessão";

  function vibrate(){
    if(navigator.vibrate) navigator.vibrate(35);
  }

  async function press(btn){
    if(!token) { setStatus("Sem sessão (volta a /join)"); return; }
    try{
      vibrate();
      const r = await fetch("/press", {
        method:"POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({token, button: btn})
      });
      if(!r.ok){
        setStatus("Sessão expirada (volta a /join)");
        return;
      }
      setStatus("Enviado: " + btn);
      setTimeout(()=>setStatus("Ligado"), 180);
    }catch(e){
      setStatus("Erro (rede/firewall)");
    }
  }

  function bind(id){
    const el = document.getElementById(id);
    el.addEventListener("touchstart", (e)=>{ e.preventDefault(); press(id); }, {passive:false});
    el.addEventListener("mousedown", ()=>press(id));
  }
  ["RED","BLUE","ORANGE","GREEN","YELLOW"].forEach(bind);

  // ✅ HEARTBEAT
  async function heartbeat(){
    if(!token) return;
    try{
      await fetch("/heartbeat", {
        method:"POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({token})
      });
    }catch(e){}
  }
  setInterval(heartbeat, 5000);

  async function leave(){
    if(token){
      try{
        await fetch("/leave", {
          method:"POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({token})
        });
      }catch(e){}
    }
    localStorage.removeItem("buzz_token");
    localStorage.removeItem("buzz_player");
    location.href = "/join";
  }

  window.addEventListener("beforeunload", ()=>{
    try{ navigator.sendBeacon("/leave_beacon", JSON.stringify({token})); }catch(e){}
  });

  // ✅ FULLSCREEN
  function isFullscreen(){
    return !!(document.fullscreenElement || document.webkitFullscreenElement);
  }

  async function enterFullscreen(){
    const el = document.documentElement;
    if(el.requestFullscreen) await el.requestFullscreen();
    else if(el.webkitRequestFullscreen) await el.webkitRequestFullscreen(); // iOS Safari / older WebKit
    // tenta “empurrar” a barra do browser para fora (ajuda em Android)
    setTimeout(()=>window.scrollTo(0,1), 50);
  }

  async function exitFullscreen(){
    if(document.exitFullscreen) await document.exitFullscreen();
    else if(document.webkitExitFullscreen) await document.webkitExitFullscreen();
  }

  async function toggleFullscreen(){
    try{
      if(isFullscreen()){
        await exitFullscreen();
      }else{
        await enterFullscreen();
      }
      updateFsButton();
    }catch(e){
      setStatus("Fullscreen não suportado neste browser");
    }
  }

  function updateFsButton(){
    fsBtn.textContent = isFullscreen() ? "Sair do ecrã inteiro" : "Ecrã inteiro";
  }

  document.addEventListener("fullscreenchange", updateFsButton);
  document.addEventListener("webkitfullscreenchange", updateFsButton);
  updateFsButton();
</script>
</body>
</html>
"""

# -----------------------
# ROUTES
# -----------------------

@app.get("/")
def root():
    # Friendly default: host page on PC, join on mobile
    # But allow anyone to see a landing page
    ip = request.remote_addr or ""
    if is_localhost(ip):
        return Response(HOST_HTML, mimetype="text/html")
    return Response(
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        '<div style="font-family:system-ui;padding:16px">'
        '<h2>Buzz Web</h2>'
        '<p>Abra <b>/join</b> no telemóvel.</p>'
        '<p>No PC (host), abra <b>/</b> para configurar.</p>'
        '</div>',
        mimetype="text/html"
    )


@app.get("/host")
def host():
    # Only host on localhost
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return Response("Host só no PC (localhost).", status=403)
    return Response(HOST_HTML, mimetype="text/html")


@app.get("/join")
def join():
    return Response(JOIN_HTML, mimetype="text/html")


@app.get("/pad")
def pad():
    return Response(PAD_HTML, mimetype="text/html")


@app.get("/state")
def get_state():
    with lock:
        # Provide whether each slot is occupied, and current num_players
        slots = {p: (state["slots"][p] is not None) for p in range(1, MAX_PLAYERS + 1)}
        return jsonify({"num_players": state["num_players"], "slots": slots})


@app.post("/config")
def set_config():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403

    data = request.get_json(silent=True) or {}
    try:
        n = int(data.get("num_players"))
        if n not in (2, 3, 4):
            return jsonify({"ok": False, "err": "invalid_num_players"}), 400

        with lock:
            state["num_players"] = n
            # Free any slots above n
            for p in range(n + 1, MAX_PLAYERS + 1):
                state["slots"][p] = None

        return jsonify({"ok": True, "num_players": n})
    except Exception as e:
        return jsonify({"ok": False, "err": str(e)}), 500


@app.post("/claim")
def claim():
    data = request.get_json(silent=True) or {}
    try:
        p = int(data.get("player"))
        with lock:
            if p < 1 or p > MAX_PLAYERS:
                return jsonify({"ok": False, "err": "invalid_player"}), 400
            if p > state["num_players"]:
                return jsonify({"ok": False, "err": "disabled"}), 400
            if state["slots"][p] is not None:
                return jsonify({"ok": False, "err": "occupied"}), 409

            token = secrets.token_urlsafe(24)
            state["slots"][p] = {"token": token, "last_seen": time.time()}
            return jsonify({"ok": True, "player": p, "token": token})
    except Exception as e:
        return jsonify({"ok": False, "err": str(e)}), 500


def find_player_by_token(token: str):
    for p in range(1, MAX_PLAYERS + 1):
        s = state["slots"].get(p)
        if s and s["token"] == token:
            return p
    return None


@app.post("/heartbeat")
def heartbeat():
    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "")
    if not token:
        return jsonify({"ok": False}), 400
    with lock:
        p = find_player_by_token(token)
        if not p:
            return jsonify({"ok": False, "err": "no_session"}), 404
        state["slots"][p]["last_seen"] = time.time()
    return jsonify({"ok": True})


@app.post("/leave")
def leave():
    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "")
    with lock:
        p = find_player_by_token(token)
        if p:
            state["slots"][p] = None
    return jsonify({"ok": True})


@app.post("/leave_beacon")
def leave_beacon():
    # called by sendBeacon (raw body)
    try:
        raw = request.get_data(as_text=True) or ""
        # raw might be JSON string
        token = ""
        if '"token"' in raw:
            # very small parse without importing json for speed/robustness
            import json
            token = json.loads(raw).get("token", "")
        token = str(token or "")
        with lock:
            p = find_player_by_token(token)
            if p:
                state["slots"][p] = None
        return ("", 204)
    except Exception:
        return ("", 204)


@app.post("/press")
def press():
    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "")
    button = str(data.get("button") or "").upper()

    if not token or not button:
        return jsonify({"ok": False, "err": "missing"}), 400

    with lock:
        p = find_player_by_token(token)
        if not p:
            return jsonify({"ok": False, "err": "no_session"}), 404

        # keep alive on press too
        state["slots"][p]["last_seen"] = time.time()

        if p not in KEYMAP or button not in KEYMAP[p]:
            return jsonify({"ok": False, "err": "invalid_button"}), 400

        # debounce per (player, button)
        now = time.time()
        k = (p, button)
        last = _last_press.get(k, 0)
        if now - last < DEBOUNCE:
            return jsonify({"ok": True, "debounced": True})
        _last_press[k] = now

        key = KEYMAP[p][button]

    # Send key outside lock
    tap_key(key)
    return jsonify({"ok": True, "player": p, "button": button, "mode": "keyboard", "key": key})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

