from flask import Flask, request, jsonify, Response
import vgamepad as vg
import time
import threading
import secrets
from collections import deque

app = Flask(__name__)
lock = threading.RLock()

EVENTS_MAX = 200000
events = deque(maxlen=EVENTS_MAX)
_event_id = 0
blocked_ips = set()

def push_event(kind: str, player: int | None = None, button: str | None = None, meta: str | None = None):
    global _event_id
    with lock:
        _event_id += 1
        events.append({
            "id": _event_id,
            "ts": time.time(),
            "kind": kind,      # "press" | "join" | "leave" | "timeout"
            "player": player,  # int or None
            "button": button,  # "RED"/"BLUE"/...
            "meta": meta       # optional (ip, reason, etc.)
        })

# -----------------------
# CONFIG / STATE
# -----------------------
MAX_PLAYERS = 4
state = {
    "num_players": 2,  # host sets 2/3/4
    "slots": {1: None, 2: None, 3: None, 4: None},  # {player: {"token":..., "last_seen":...}}
}

# --- KEY MAPPING (edit if you want) ---
# KEYMAP = {
#     1: {"RED": "q", "BLUE": "w", "YELLOW": "e", "GREEN": "r", "ORANGE": "t"},
#     2: {"RED": "a", "BLUE": "s", "YELLOW": "d", "GREEN": "f", "ORANGE": "g"},
#     3: {"RED": "z", "BLUE": "x", "YELLOW": "c", "GREEN": "v", "ORANGE": "b"},
#     4: {"RED": "u", "BLUE": "i", "YELLOW": "o", "GREEN": "p", "ORANGE": "l"},
# }

# -----------------------
# VIRTUAL GAMEPADS (XBOX 360)
# -----------------------
try:
    GAMEPADS = {
        1: vg.VX360Gamepad(),
        2: vg.VX360Gamepad(),
        3: vg.VX360Gamepad(),
        4: vg.VX360Gamepad(),
    }
    VIGEM_OK = True
except Exception as e:
    GAMEPADS = {}
    VIGEM_OK = False
    VIGEM_ERR = str(e)

print("VIGEM_OK =", VIGEM_OK)
if not VIGEM_OK:
    print("Erro:", VIGEM_ERR)

# Mapear botoes Buzz -> botoes do comando Xbox
# (podes mudar depois no PCSX2, mas isto e um bom default)
XBTN = vg.XUSB_BUTTON
BTNMAP = {
    "RED":    XBTN.XUSB_GAMEPAD_A,
    "BLUE":   XBTN.XUSB_GAMEPAD_B,
    "ORANGE": XBTN.XUSB_GAMEPAD_X,
    "GREEN":  XBTN.XUSB_GAMEPAD_Y,
    "YELLOW": XBTN.XUSB_GAMEPAD_LEFT_SHOULDER,
}

def tap_gamepad_button(player: int, buzz_button: str, hold_ms: int = 50):
    """Pressiona e solta um botão no comando virtual do player."""
    if not VIGEM_OK:
        return
    pad = GAMEPADS.get(player)
    if not pad:
        return
    xb = BTNMAP.get(buzz_button)
    if not xb:
        return

    pad.press_button(button=xb)
    pad.update()
    time.sleep(hold_ms / 1000.0)
    pad.release_button(button=xb)
    pad.update()

# Debounce (avoid spam double taps)
DEBOUNCE = 0.06
_last_press = {}

# Slot timeout: if a phone disappears, free the slot
SLOT_TIMEOUT_SEC = 45
HEARTBEAT_INTERVAL_SEC = 5


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
                    push_event("timeout", player=p)


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

    .slot .right{ display:flex; gap:10px; align-items:center; }

    .pill.busy{
      cursor:pointer;
      user-select:none;
      position:relative;
      padding-right:28px;
    }
    .pill.busy::after{
      content:"▾";
      position:absolute;
      right:10px;
      top:50%;
      transform:translateY(-50%);
      opacity:.9;
      font-weight:900;
    }

    .dd{
      position:relative;
      display:inline-block;
    }
    .ddmenu{
      position:absolute;
      right:0;
      top:calc(100% + 8px);
      background:#0b1020;
      border:1px solid rgba(255,255,255,.10);
      border-radius:14px;
      min-width:220px;
      padding:6px;
      box-shadow:0 18px 50px rgba(0,0,0,.55);
      z-index:50;
      display:none;
    }
    .dd.open .ddmenu{ display:block; }

    .dditem{
      width:100%;
      display:flex;
      justify-content:space-between;
      gap:10px;
      align-items:center;
      border:0;
      background:transparent;
      color:#e9edf1;
      padding:10px 10px;
      border-radius:12px;
      cursor:pointer;
      font-weight:900;
      text-align:left;
    }
    .dditem:hover{ background: rgba(255,255,255,.06); }
    .dditem.danger{ color:#fecaca; }
    .dditem small{ opacity:.75; font-weight:800; }

    .eventbox{
      margin-top:10px;
      background:#0b1020;
      border:1px solid rgba(255,255,255,.08);
      border-radius:14px;
      padding:10px;
      height: 320px;               /* ≈ ~20 linhas visíveis (ajusta se quiseres) */
      overflow-y:auto;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,.04);
    }

    .ev{
      display:flex;
      gap:10px;
      align-items:center;
      padding:6px 6px;
      border-bottom:1px solid rgba(255,255,255,.06);
      font-family:ui-monospace,Consolas,monospace;
      font-size:13px;
    }
    .ev:last-child{ border-bottom:0; }

    .ts{ opacity:.7; min-width: 92px; }
    .badge{
      font-family:system-ui;
      font-size:11px;
      font-weight:900;
      padding:3px 8px;
      border-radius:999px;
      background:#111827;
      border:1px solid rgba(255,255,255,.08);
    }
    .badge.join{ background: rgba(34,197,94,.15); border-color: rgba(34,197,94,.35); }
    .badge.leave{ background: rgba(239,68,68,.15); border-color: rgba(239,68,68,.35); }
    .badge.timeout{ background: rgba(250,204,21,.12); border-color: rgba(250,204,21,.35); }
    .badge.press{ background: rgba(59,130,246,.12); border-color: rgba(59,130,246,.35); }
    .badge.kick{ background: rgba(148,163,184,.12); border-color: rgba(148,163,184,.35); }
    .badge.block{ background: rgba(239,68,68,.12); border-color: rgba(239,68,68,.35); }
    .badge.unblock{ background: rgba(34,197,94,.12); border-color: rgba(34,197,94,.35); }

    .btnname{ font-weight:900; }
    .meta{ opacity:.7; margin-left:auto; }
  
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

    <div class="slots" style="margin-top:12px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
        <div style="font-weight:900">Atividade</div>
        <div id="online" style="opacity:.85;font-size:12px;font-weight:800"></div>
      </div>

      <div id="eventbox" class="eventbox">
        <div id="eventlist"></div>
      </div>

      <div class="muted" style="margin-top:8px">
        Dica: podes fazer scroll aqui. Se estiveres no fundo, o log segue automaticamente.
      </div>
    </div>

    <div class="slots" style="margin-top:12px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
        <div style="font-weight:900">IPs bloqueados</div>
        <div style="opacity:.75;font-size:12px;font-weight:800" id="blockedCount">—</div>
      </div>
      <div id="blockedList" class="muted" style="margin-top:10px"></div>
    </div>

    <div class="muted">
      Nota: Se um telemóvel fechar a página, o lugar liberta sozinho em ~%TIMEOUT%s.
    </div>
  </div>

<script>
  const statusEl = document.getElementById("status");
  const sel = document.getElementById("players");
  const slotlist = document.getElementById("slotlist");

  const eventbox = document.getElementById("eventbox");
  const eventlist = document.getElementById("eventlist");
  const onlineEl = document.getElementById("online");

  const nice = {RED:"Vermelho", BLUE:"Azul", GREEN:"Verde", YELLOW:"Amarelo", ORANGE:"Laranja"};
  const btnColor = { RED:"#ef4444", BLUE:"#3b82f6", GREEN:"#22c55e", YELLOW:"#facc15", ORANGE:"#fb923c" };

  const blockedListEl = document.getElementById("blockedList");
  const blockedCountEl = document.getElementById("blockedCount");

  let lastEventId = 0;

  let selectDirty = false;     // user mudou o select e ainda não guardou
  let dropdownOpenP = null;    // qual player tem dropdown aberta (1..4) ou null

  sel.addEventListener("change", ()=>{
    selectDirty = true;
  });

  function fmtTime(ts){
    return new Date(ts*1000).toLocaleTimeString();
  }

  function isNearBottom(el){
    return (el.scrollHeight - el.scrollTop - el.clientHeight) < 40;
  }

  function openDropdownFor(p){
    closeAllDropdowns();
    const dd = document.querySelector(`.dd[data-p='${p}']`);
    if(dd) dd.classList.add("open");
    dropdownOpenP = p;
  }

  function togglePlayerDropdown(p){
    const dd = document.querySelector(`.dd[data-p='${p}']`);
    const isOpen = dd && dd.classList.contains("open");
    if(isOpen){
      closeAllDropdowns();
      dropdownOpenP = null;
    } else {
      openDropdownFor(p);
    }
  }

  function closeAllDropdowns(){
    document.querySelectorAll(".dd.open").forEach(el => el.classList.remove("open"));
    dropdownOpenP = null;
  }

  async function refreshBlocked(){
    try{
      const r = await fetch("/blocked", { cache:"no-store" });
      const j = await r.json();
      if(!j.ok) return;

      const ips = j.ips || [];
      blockedCountEl.textContent = ips.length ? (ips.length + " bloqueado(s)") : "Nenhum";

      if(!ips.length){
        blockedListEl.innerHTML = "<span style='opacity:.8'>—</span>";
        return;
      }

      blockedListEl.innerHTML = ips.map(ip => `
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 6px;border-bottom:1px solid rgba(255,255,255,.06)">
          <code>${ip}</code>
          <button
            style="background:#22c55e;color:#052e12;border:0;border-radius:12px;padding:8px 10px;font-weight:900;cursor:pointer"
            data-ip="${ip}"
            onclick="unblockFromBtn(this)">
            Desbloquear
          </button>
        </div>
      `).join("");
    }catch(e){}
  }

  async function unblockIp(ip){
    if(!confirm("Desbloquear IP " + ip + "?")) return;
    try{
      const r = await fetch("/unblock", {
        method:"POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ip})
      });
      const j = await r.json();
      if(!j.ok){
        setStatus("Erro unblock: " + (j.err || r.status));
        return;
      }
      setStatus("IP desbloqueado: " + j.ip);
      await refreshBlocked();
      await refreshEvents();
    }catch(e){
      setStatus("Erro unblock (rede)");
    }
  }

  document.addEventListener("click", (e)=>{
    // fecha dropdown se clicares fora
    if(!e.target.closest(".dd")){
      closeAllDropdowns();
      dropdownOpenP = null;
    }
  });

  async function kick(p){
    if(!confirm("Desligar o Jogador " + p + "?")) return;
    try{
      const r = await fetch("/kick", {
        method:"POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({player:p})
      });
      const j = await r.json();
      if(!j.ok){
        setStatus("Erro kick: " + (j.err || r.status));
        return;
      }
      setStatus("Jogador " + p + " desligado");
      await refresh();
      await refreshEvents();
    }catch(e){
      setStatus("Erro kick (rede)");
    }
  }

  function blockFromBtn(btn, p){
    const ip = (btn && btn.dataset) ? (btn.dataset.ip || "") : "";
    return blockByPlayer(p, ip);
  }

  function unblockFromBtn(btn){
    const ip = (btn && btn.dataset) ? (btn.dataset.ip || "") : "";
    if(!ip){
      setStatus("IP inválido");
      return;
    }
    unblockIp(ip);
  }

  async function blockByPlayer(p, ip){
    const msg = ip
      ? `Bloquear IP ${ip} e desligar o Jogador ${p}?`
      : `Bloquear IP deste jogador e desligar o Jogador ${p}?`;
    if(!confirm(msg)) return;

    try{
      const r = await fetch("/block", {
        method:"POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({mode:"block", player:p, ip: ip || ""})
      });
      const j = await r.json();
      if(!j.ok){
        setStatus("Erro block: " + (j.err || r.status));
        return;
      }
      setStatus("IP bloqueado: " + j.ip);
      await refresh();
      await refreshEvents();
    }catch(e){
      console.error(e);
      setStatus("Erro block (rede)");
    }
  }

  function renderEvent(e){
    const kind = e.kind || "press";
    const p = e.player ?? "?";

    let label = kind.toUpperCase();
    let text = "";
    let meta = e.meta ? `(${e.meta})` : "";

    if(kind === "press"){
      const b = String(e.button || "").toUpperCase();
      const name = nice[b] || b || "—";
      const color = btnColor[b] || "#e9edf1";
      text = `P${p}: <span class="btnname" style="color:${color}">${name}</span>`;
    } else if(kind === "join"){
      text = `P${p} entrou ${meta}`;
    } else if(kind === "leave"){
      text = `P${p} saiu`;
    } else if(kind === "timeout"){
      text = `P${p} timeout (inativo)`;
    } else if(kind === "kick"){
      text = `P${p} foi desconectado (host) ${e.meta ? "(" + e.meta + ")" : ""}`;
    } else if(kind === "block"){
      text = `IP bloqueado: ${e.meta || ""}`;
    } else if(kind === "unblock"){
      text = `IP desbloqueado: ${e.meta || ""}`;
    } else {
      text = `P${p}: ${kind}`;
    }

    const row = document.createElement("div");
    row.className = "ev";
    row.innerHTML = `
      <span class="ts">[${fmtTime(e.ts)}]</span>
      <span class="badge ${kind}">${label}</span>
      <span>${text}</span>
    `;
    return row;
  }

  async function refreshEvents(){
    try{
      const stick = isNearBottom(eventbox);

      const r = await fetch("/events?since=" + lastEventId);
      const j = await r.json();
      if(!j.ok) return;

      for(const e of j.events){
        lastEventId = Math.max(lastEventId, e.id || 0);
        eventlist.appendChild(renderEvent(e));
      }

      // Optional: prevent DOM from growing forever (keeps it “unlimited enough”)
      while(eventlist.children.length > 2000){
        eventlist.removeChild(eventlist.firstChild);
      }

      if(stick){
        eventbox.scrollTop = eventbox.scrollHeight;
      }
    }catch(e){}
  }

  function setStatus(t){ statusEl.textContent = t; }

  async function refresh(){
    const r = await fetch("/state");
    const s = await r.json();
    const freezeSlots = !!document.querySelector(".dd.open");
    if(!selectDirty){
      sel.value = String(s.num_players);
    }

    let connected = [];
    for(let p=1;p<=4;p++){
      const slot = s.slots[p] || {busy:false};
      if(slot.busy) connected.push("P"+p);
    }

    if(!freezeSlots) {
      slotlist.innerHTML = "";
      for(let p=1;p<=4;p++){
        const row = document.createElement("div");
        row.className="slot";
        const enabled = p <= s.num_players;
        const slot = s.slots[p] || {busy:false};
        const busy = !!slot.busy;
        const ip = slot.ip || "";

        row.innerHTML = `
          <div>Jogador ${p} ${enabled ? "" : "(desativado)"}</div>
          <div class="right">
            ${
              busy
                ? `
                  <div class="dd" data-p="${p}">
                    <div class="pill busy" onclick="togglePlayerDropdown(${p})">LIGADO</div>
                    <div class="ddmenu">
                      <button class="dditem" onclick="kick(${p}); closeAllDropdowns();">
                        Kick <small>desligar</small>
                      </button>
                      <button
                        class="dditem danger"
                        data-ip="${ip}"
                        onclick="blockFromBtn(this, ${p}); closeAllDropdowns();">
                        Bloquear IP <small>${ip || "—"}</small>
                      </button>
                    </div>
                  </div>
                `
                : (enabled ? `<div class="pill free">LIVRE</div>` : `<div class="pill free" style="opacity:.55">—</div>`)
            }
          </div>
        `;
        slotlist.appendChild(row);
      }
    }

    onlineEl.textContent = connected.length ? ("Ligados: " + connected.join(" • ")) : "Ninguém ligado";
    if(!freezeSlots && dropdownOpenP){
      openDropdownFor(dropdownOpenP);
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
    if(j.ok){
      setStatus("Guardado");
      selectDirty = false;
    } else {
      setStatus("Erro: " + (j.err || "—"));
    }
    await refresh();
  }

  refresh();
  refreshEvents();
  setInterval(refresh, 2000);
  setInterval(refreshEvents, 250);
  refreshBlocked();
  setInterval(refreshBlocked, 2000);
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
        Se um lugar estiver ocupado, terás de esperar pela tua vez para entrar nele.<br/>
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
      const slot = s.slots[p] || {busy:false};
      const busy = !!slot.busy;
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
  // Lê sempre do storage
  let tokenNow = localStorage.getItem("buzz_token");
  let player   = localStorage.getItem("buzz_player");

  const whoEl    = document.getElementById("who");
  const statusEl = document.getElementById("status");
  const fsBtn    = document.getElementById("fsBtn");

  function setStatus(t){ statusEl.textContent = t; }

  function setButtonsEnabled(on){
    ["RED","BLUE","ORANGE","GREEN","YELLOW"].forEach(id=>{
      const el = document.getElementById(id);
      if(!el) return;
      el.disabled = !on;
      el.style.opacity = on ? "1" : ".35";
    });
  }

  function clearSession(){
    localStorage.removeItem("buzz_token");
    localStorage.removeItem("buzz_player");
    tokenNow = null;
    player = null;
  }

  function goJoin(){
    clearSession();
    location.replace("/join");
  }

  async function ensureSession(){
    tokenNow = localStorage.getItem("buzz_token");
    if(!tokenNow){
      goJoin();
      return false;
    }

    try{
      const r = await fetch("/session?token=" + encodeURIComponent(tokenNow), { cache: "no-store" });
      if(!r.ok){
        goJoin();
        return false;
      }

      const j = await r.json();
      if(!j.ok){
        goJoin();
        return false;
      }

      // opcional: corrigir player se necessário
      if(j.player && String(j.player) !== String(player)){
        player = String(j.player);
        localStorage.setItem("buzz_player", player);
      }

      whoEl.textContent = player ? ("Jogador " + player) : "Sem sessão";
      return true;

    }catch(e){
      // sem rede: não forces /join
      setButtonsEnabled(false);
      setStatus("Sem rede (ver Wi-Fi)");
      return false;
    }
  }

  whoEl.textContent = player ? ("Jogador " + player) : "Sem sessão";

  function vibrate(){
    if(navigator.vibrate) navigator.vibrate(35);
  }

  async function press(btn){
    tokenNow = localStorage.getItem("buzz_token");
    if(!tokenNow){
      goJoin();
      return;
    }

    try{
      vibrate();
      const r = await fetch("/press", {
        method:"POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ token: tokenNow, button: btn })
      });

      if(!r.ok) {
        let err = "";
        try{ err = (await r.json()).err || ""; }catch(e){}
        if(r.status === 403 || err === "blocked"){ goJoin(); return; }
        if(r.status === 404 || err === "no_session"){ goJoin(); return; }

        setStatus("Erro (" + r.status + ")");
        setButtonsEnabled(false);
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
    if(!el) return;
    el.addEventListener("touchstart", (e)=>{ e.preventDefault(); press(id); }, {passive:false});
    el.addEventListener("mousedown", ()=>press(id));
  }
  ["RED","BLUE","ORANGE","GREEN","YELLOW"].forEach(bind);

  // ✅ HEARTBEAT
  async function heartbeat(){
    tokenNow = localStorage.getItem("buzz_token");
    if(!tokenNow) return;
    try{
      const r = await fetch("/heartbeat", {
        method:"POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ token: tokenNow })
      });

      // se a sessão morreu, manda para /join
      if(!r.ok){
        let err = "";
        try{ err = (await r.json()).err || ""; }catch(e){}
        if(r.status === 403 || err === "blocked"){ goJoin(); return; }
        if(r.status === 404 || err === "no_session"){ goJoin(); return; }
      }
    }catch(e){}
  }
  setInterval(heartbeat, 5000);

  async function leave(){
    tokenNow = localStorage.getItem("buzz_token");
    if(tokenNow){
      try{
        await fetch("/leave", {
          method:"POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({ token: tokenNow })
        });
      }catch(e){}
    }
    goJoin();
  }

  // ✅ FULLSCREEN
  function isFullscreen(){
    return !!(document.fullscreenElement || document.webkitFullscreenElement);
  }
  async function enterFullscreen(){
    const el = document.documentElement;
    if(el.requestFullscreen) await el.requestFullscreen();
    else if(el.webkitRequestFullscreen) await el.webkitRequestFullscreen();
    setTimeout(()=>window.scrollTo(0,1), 50);
  }
  async function exitFullscreen(){
    if(document.exitFullscreen) await document.exitFullscreen();
    else if(document.webkitExitFullscreen) await document.webkitExitFullscreen();
  }
  async function toggleFullscreen(){
    try{
      if(isFullscreen()) await exitFullscreen();
      else await enterFullscreen();
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

  // --- BOOT ---
  setButtonsEnabled(false);
  setStatus("A verificar sessão...");
  ensureSession().then((ok)=>{
    if(ok){
      setButtonsEnabled(true);
      setStatus("Ligado");
    }
  });
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
    ip = request.remote_addr or ""
    host = is_localhost(ip)

    with lock:
        slots = {}
        for p in range(1, MAX_PLAYERS + 1):
            s = state["slots"][p]
            if not s:
                slots[p] = {"busy": False}
            else:
                slots[p] = {"busy": True}
                if host:
                    slots[p]["ip"] = s.get("ip", "")
        return jsonify({"num_players": state["num_players"], "slots": slots, "host": host})
    
@app.get("/events")
def get_events():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403

    since = request.args.get("since", default=0, type=int)
    with lock:
        out = [e for e in events if e["id"] > since]
    return jsonify({"ok": True, "events": out})

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
        state["slots"][p]["last_seen"] = time.time()
    return jsonify({"ok": True, "player": p})

@app.post("/kick")
def kick():
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

    with lock:
        s = state["slots"].get(p)
        if not s:
            return jsonify({"ok": True, "already_free": True})

        kicked_ip = s.get("ip", "")
        state["slots"][p] = None
        push_event("kick", player=p, meta=kicked_ip or "host")

    return jsonify({"ok": True, "player": p})


@app.post("/block")
def block_ip():
    ip = request.remote_addr or ""
    if not is_localhost(ip):
        return jsonify({"ok": False, "err": "host_only"}), 403

    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "block").lower()  # "block" | "unblock"
    target_ip = str(data.get("ip") or "")
    player = data.get("player", None)

    with lock:
        # se não veio ip, tenta inferir do player
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

        # block
        blocked_ips.add(target_ip)
        push_event("block", meta=target_ip)

        # kick todos os slots desse ip (caso esteja ligado em algum)
        kicked = []
        for p in range(1, MAX_PLAYERS + 1):
            s = state["slots"].get(p)
            if s and s.get("ip") == target_ip:
                state["slots"][p] = None
                kicked.append(p)
                push_event("kick", player=p, meta=target_ip)

        return jsonify({"ok": True, "mode": "block", "ip": target_ip, "kicked": kicked})
    

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
        ip = request.remote_addr or ""
        with lock:
            if (not is_localhost(ip)) and ip in blocked_ips:
                return jsonify({"ok": False, "err": "blocked"}), 403

            if p < 1 or p > MAX_PLAYERS:
                return jsonify({"ok": False, "err": "invalid_player"}), 400
            if p > state["num_players"]:
                return jsonify({"ok": False, "err": "disabled"}), 400
            if state["slots"][p] is not None:
                return jsonify({"ok": False, "err": "occupied"}), 409

            token = secrets.token_urlsafe(24)
            state["slots"][p] = {"token": token, "last_seen": time.time(), "ip": ip}
            push_event("join", player=p, meta=ip)
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
    ip = request.remote_addr or ""
    with lock:
        if (not is_localhost(ip)) and ip in blocked_ips:
            return jsonify({"ok": False, "err": "blocked"}), 403
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
            push_event("leave", player=p)
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
                push_event("leave", player=p)
        return ("", 204)
    except Exception:
        return ("", 204)


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

    with lock:
        p = find_player_by_token(token)
        if not p:
            return jsonify({"ok": False, "err": "no_session"}), 404

        # keep alive on press too
        state["slots"][p]["last_seen"] = time.time()

        #  valida o botão pelo BTNMAP (vgamepad)
        if button not in BTNMAP:
            return jsonify({"ok": False, "err": "invalid_button"}), 400

        # debounce per (player, button)
        now = time.time()
        k = (p, button)
        last = _last_press.get(k, 0)
        if now - last < DEBOUNCE:
            return jsonify({"ok": True, "debounced": True})
        _last_press[k] = now

        # log for dashboard
        push_event("press", player=p, button=button)

    # Send key outside lock
    tap_gamepad_button(p, button, hold_ms=50)
    return jsonify({"ok": True, "player": p, "button": button, "mode": "xinput"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)