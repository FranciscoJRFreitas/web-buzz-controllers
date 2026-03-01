# Buzz Web (Phones as Buzzers -> Virtual Gamepads)

Turn any phones on your Wi-Fi into simple "Buzz!" controllers, and output those button presses as **virtual gamepad buttons** on your Windows PC using **ViGEm + vgamepad**.

- **Host page (PC / localhost):** configure player count, rename players, see who's connected, kick/block users, live event log
- **Join page (phones):** pick an available player slot (1-8) with the **saved player names**
- **Pad page (phones):** big colored buzzer buttons (Red/Blue/Orange/Green/Yellow) + shows the **saved name** at top-left
- **Output:** virtual controllers (Players **1-4 = Xbox 360 / XInput**, Players **5-8 = DualShock 4**)

> ⚠️ Windows-only for the **virtual gamepad** part (ViGEm). The web UI can load anywhere, but the controller output requires Windows + ViGEm.

---

## Table of Contents

- [Buzz Web (Phones as Buzzers -\> Virtual Gamepads)](#buzz-web-phones-as-buzzers---virtual-gamepads)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Requirements](#requirements)
    - [PC / Host (recommended: Windows 10/11)](#pc--host-recommended-windows-1011)
    - [Phones / Controllers](#phones--controllers)
  - [Installation](#installation)
    - [1) Install ViGEmBus (Windows)](#1-install-vigembus-windows)
    - [2) Clone this repo](#2-clone-this-repo)
    - [3) Create a virtual environment (recommended)](#3-create-a-virtual-environment-recommended)
    - [4) Install dependencies](#4-install-dependencies)
  - [Run](#run)
    - [Open the host dashboard (PC)](#open-the-host-dashboard-pc)
    - [Join from phones](#join-from-phones)
- [IMPORTANT](#important)
  - [Buzz](#buzz)
      - [Steps to set up Buzz with PCSX2:](#steps-to-set-up-buzz-with-pcsx2)
  - [How it works](#how-it-works)
  - [Timeouts, reconnect grace, debounce](#timeouts-reconnect-grace-debounce)
  - [Host-only endpoints (localhost)](#host-only-endpoints-localhost)
  - [Configuration file](#configuration-file)
  - [Network / Firewall notes](#network--firewall-notes)
  - [License](#license)

---

## Features

- **Up to 8 players**
  - Players 1-4 -> **XInput (Xbox 360)** via `VX360Gamepad`
  - Players 5-8 -> **DS4** via `VDS4Gamepad`
- **Lazy gamepad creation (no device spam)**
  - Only creates virtual pads **up to** the configured number of players
  - Removes pads above that number when you reduce players
- **Player names (persisted)**
  - Rename players from the host dashboard (editable label)
  - **Auto-save after 3s idle**, or **Enter**, or **blur**
  - **Escape** reverts to the saved name
  - Names are stored in `buzz_config.json` that you can edit before running the server
  - The names are displayed on **Join** and **Pad** pages
- **Host dashboard**
  - Set number of active players (2-8)
  - See connected players + IPs (**host-only**)
  - Kick player / Block IP / Unblock IP
  - Share page with QR + "Copy Join URL"
  - Live event log via **SSE** (joins/leaves/timeouts/presses/renames/config/log clears)
- **Session + heartbeat**
  - Slots expire automatically if a phone disappears (**timeout**)
  - **Reconnect** support (short grace window to reclaim the same slot)
- **Debounce**
  - Prevents spam double taps (server-side)
- **Fast press handling**
  - `/press` enqueues button presses to a worker thread (no sleeping in request thread)

---

## Requirements

### PC / Host (recommended: Windows 10/11)

1. **Python 3.10+** (3.11/3.12 are fine)
2. **ViGEmBus driver** installed (required for virtual controllers)
3. Python packages:
   - `flask`
   - `vgamepad`
   - `qrcode[pil]` (optional but recommended for the QR share page)

### Phones / Controllers

- Any modern mobile browser on the **same Wi-Fi** as the PC (Android/iOS)
- No app install needed

---

## Installation

### 1) Install ViGEmBus (Windows)

Install the ViGEmBus driver so Windows can accept virtual controllers.

- Installation link: https://vigembusdriver.com/
- After install, reboot if needed.

> If ViGEm isn't installed, the server can still run, but it won't be able to create virtual controllers.

### 2) Clone this repo

```bash
git clone https://github.com/FranciscoJRFreitas/web-buzz-controllers.git
cd web-buzz-controllers
```

### 3) Create a virtual environment (recommended)

```bash
python -m venv .venv
```

Activate it:

**Windows (PowerShell):**

```powershell
.venv\Scripts\Activate.ps1
```

**Windows (CMD):**

```bat
.venv\Scripts\activate.bat
```

### 4) Install dependencies

```bash
pip install -U pip
pip install -r requirements.txt
```

---

## Run

In the terminal:

```bash
python server.py
```

Server listens on:

- `http://0.0.0.0:5000`

### Open the host dashboard (PC)

On the same PC (localhost):

- `http://127.0.0.1:5000/` (or `http://localhost:5000/`)
- or `http://127.0.0.1:5000/host`

> Host endpoints are restricted to **localhost** for safety.

### Join from phones

On each phone (same Wi-Fi), open:

`http://PC_IP:5000/join`

Example:

`http://192.168.1.129:5000/join`

Pick a player slot, then you'll be redirected to the buzzer pad.

---

# IMPORTANT

## Buzz

In order for `Buzz` to run on your PC, I recommend you install it via a PS2 emulator, such as [`PCSX2`](https://pcsx2.net/), and use the virtual gamepads created by this server as input devices.

#### Steps to set up Buzz with PCSX2:

1. You need a BIOS file to run games on PCSX2. You can acquire this from your own PS2 console (Google "how to dump PS2 BIOS") or, if you don't have a PS2: [PS2 BIOS Dump](https://archive.org/download/ps2-bios-megadump) and extract the .zip;
2. Install PCSX2 and go through the initial setup;
3. After the setup is complete, open PCSX2 and select the folder you downloaded the BIOS file to (ex: C:\\Users\\<your_user>\\Downloads\\ps2-bios-megadump\\PS2_BIOS);
4. Select a .bin BIOS file (ex: ps2-0120e-20000902.bin);
5. Map the controller inputs in PCSX2 to the virtual gamepads created by this server by:
    - Going to "Config" > "Controllers" > "USB Port 1" and at the top of the window, select "Buzz Controller" as the device;
    - Do the same for "USB Port 2" if you want to use a second controller (5-8 players require mapping to USB Port 2);
    - For each player slot (1-4) in USB port 1 and (5-8) in USB port 2, select the corresponding virtual gamepad. You can do this by going to the `http://PC_IP:5000/pads` page, going to each player and map each button to the corresponding button in PCSX2. (After being in a pad page, where you have the colored buttons, go to the PCSX2 mapping page, press the "Red" input and then press the corresponding button in the pad page, do this for all buttons and repeat for each player slot)
    - Everything is set up! You can now use your phones as Buzz controllers in PCSX2 and run the Buzz games.
6. To run the Buzz games, you can use your own PS2 discs or find the ISOs online. Load the ISO in PCSX2 and start playing with your phone controllers!

---

## How it works

- Phones claim a **player slot** (`POST /claim`) and receive a session token stored in `localStorage`
- Phone sends:
  - `POST /press` when a button is tapped
  - `POST /heartbeat` every 5 seconds to keep the session alive
- PC translates Buzz button -> gamepad button:
  - XInput mapping for P1-4
  - DS4 mapping for P5-8
- Host can adjust active players via `POST /config`, which also:
  - Frees slots above that number
  - Creates/removes virtual pads to match (lazy pads)
- Host can rename players via `POST /label` (persisted to disk)

---

## Timeouts, reconnect grace, debounce

Current defaults (can be changed in code):

- Slot timeout: **45s** without heartbeat (`SLOT_TIMEOUT_SEC`)
- Reconnect grace window: **25s** (`RECLAIM_GRACE_SEC`)
- Debounce: **0.06s** (`DEBOUNCE_SEC`)
- Press queue capacity: **2000** (worker thread)

---

## Host-only endpoints (localhost)

These routes are blocked for non-local clients:

- Pages: `/`, `/host`, `/share`
- APIs: `/events/stream`, `/join_url`, `/state` (returns IPs/tokens only for host), `/label`, `/labels`, `/kick`, `/kick_all`, `/blocked`, `/block`, `/unblock`, `/logs/clear`, `/config`

---

## Configuration file

The app persists configuration in `buzz_config.json`:

- `num_players` (2-8)
- `labels` for players 1-8

You can edit this file manually while the server is stopped.

---

## Network / Firewall notes

If phones can't connect:

1. Make sure PC and phones are on the same Wi-Fi / subnet
2. Allow inbound connections on port **5000**
3. Use the correct PC LAN IP (not 127.0.0.1)

Quick check on PC:

```bash
ipconfig
```

---

## License

This project is licensed under the MIT License - see the [LICENSE](docs/LICENSE) file for details.
