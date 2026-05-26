# ESP32-CAM Streamer

Live MJPEG stream from an AI-Thinker ESP32-CAM, with a browser-based control panel, WiFi pairing via captive portal, and wireless firmware updates via ArduinoOTA. Built with PlatformIO + Arduino framework.

## What it does

**On the ESP32-CAM (firmware):**

- Live video stream over WiFi, viewable in any browser.
- Snapshot capture endpoint (`/capture`).
- On-page controls: resolution, JPEG quality, brightness, contrast, saturation, horizontal-mirror, vertical-flip.
- WiFi pairing through a captive-portal setup AP — no source-code edits needed to change networks.
- Stable hostname via mDNS at `http://esp32cam.local/` — no chasing IPs.
- Wireless firmware updates after the first cable flash.
- "Change WiFi" button to reset saved credentials remotely.
- `/stats` endpoint with chip temp, free RAM, RSSI, uptime.

**On a laptop (Python server, [`server/`](server/)):**

- Real-time crack detection pipeline using OpenCV (Canny + Hough lines + axis filter + ROI mask + darkness check).
- Push alerts to your phone via a Telegram bot when a crack is detected — with the annotated photo attached.
- Optional second-pass ML classifier (MobileNetV3-Small, transfer-learned on the Kaggle Surface Crack Detection dataset).

See [`server/README.md`](server/README.md) for the full Telegram bot setup, detection-pipeline tuning, and ML training instructions.

## Hardware

- AI-Thinker ESP32-CAM module (ESP32 + 4 MB PSRAM + OV2640 sensor).
- FTDI USB-to-serial adapter (only needed for the first firmware flash). Any FT232RL, CH340G, or CP2102 module works.
- A few female-to-female jumper wires.
- 5V power source rated 1 A or higher — a USB phone charger works. The FTDI's 5V is marginal and will brown out the camera under load.

## Setting up the project on a new laptop (recommended: VS Code + PlatformIO)

The whole toolchain auto-installs once you open the project in VS Code. There's no global Arduino setup to fight with.

### One-time installs

Everything you need to download once. Estimated time: 30–60 minutes total (most of it is just waiting for installs to finish).

| # | What | Why | Link |
|---|---|---|---|
| 1 | **VS Code** | The editor we'll use for both the C++ firmware and the Python server. | https://code.visualstudio.com/ |
| 2 | **PlatformIO IDE extension** for VS Code | Builds and flashes the firmware. Open VS Code → Extensions tab (Ctrl+Shift+X) → search "PlatformIO IDE" → Install. First launch downloads ~600 MB of toolchains (Espressif compiler, esptool, etc.). One-time cost, takes ~5 min. | (in-app) |
| 3 | **Python 3.10 or newer** | Runs the crack-detection server, Telegram alerts, and ML training. Make sure you tick "Add Python to PATH" during the Windows installer. | https://www.python.org/downloads/ |
| 4 | **Git** | To clone the repo. Skip if you already have it. | https://git-scm.com/ |
| 5 | **USB-to-serial driver** for your FTDI chip | The FTDI is needed only for the very first firmware flash. After that, OTA. Most FTDI chips work out of the box on Windows 10/11; if Device Manager shows "USB Serial Port" when you plug it in, you're done. Otherwise install per chip:<br>FT232RL → https://ftdichip.com/drivers/vcp-drivers/<br>CH340G → https://sparks.gogo.co.nz/ch340.html<br>CP2102 → https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers | (chip-specific) |

Verify each install in a terminal:

```powershell
code --version          # VS Code
python --version        # should be 3.10+
git --version
```

PlatformIO is verified inside VS Code (alien-head icon in the left sidebar appears once the extension is installed).

### Clone and open

```
git clone https://github.com/shreyanshmishra2607/esp32cam-streamer.git
cd esp32cam-streamer
code .
```

VS Code opens, PlatformIO recognizes the `platformio.ini`, and on the first build it auto-installs the project's dependencies (WiFiManager, ArduinoOTA, ESPmDNS, the Espressif Arduino framework). No manual library hunting.

### Build & flash from VS Code

The PlatformIO sidebar (the alien-head icon in the left rail) shows two environments: `esp32cam` (cable) and `esp32cam_ota` (wireless). Each has its own **Build**, **Upload**, **Monitor**, etc. under General/Platform tasks. Use those instead of the top-level Upload button so you control which environment runs.

- **First time**: wire up the FTDI + flash-mode jumper (see "First flash" below) → expand `esp32cam` → click **Upload**.
- **Every time after**: cam stays on its WiFi → expand `esp32cam_ota` → click **Upload**. No cable.
- **Watching serial logs**: expand `esp32cam` → click **Monitor**. (FTDI must be plugged in for serial.)

That's the whole development loop **for the firmware**. The Python detector server has its own setup below.

## Setting up the Python detector server

This is where the crack detection, image saving, and Telegram alerts happen. It runs on your laptop, not on the ESP32. The cam streams frames over WiFi → this server processes them.

### One-time setup

From the repo root, open a terminal:

```powershell
cd server
python -m venv .venv
```

That creates a local Python virtual environment at `server/.venv/` — a self-contained sandbox so this project's Python dependencies don't pollute (or get polluted by) your system Python.

Activate the venv. On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

(If PowerShell complains about execution policy, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` and accept the prompt. Then re-run the Activate line.)

Your prompt should now show `(.venv)` at the start — that means you're in the venv.

Install dependencies:

```powershell
pip install -r requirements.txt
```

This pulls `opencv-python`, `numpy`, and `requests` — the only runtime dependencies. Takes ~30 seconds.

### Create your Telegram bot (optional but recommended)

To get crack alerts pushed to your phone, you need a Telegram bot. The full step-by-step (with BotFather, getting the chat ID, etc.) is in [`server/README.md`](server/README.md#1b-create-your-telegram-bot-3-minutes-on-your-phone).

Short version:
1. In Telegram, talk to `@BotFather` → `/newbot` → get a token.
2. Send your new bot a message.
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser → find your chat ID.
4. Copy `server/secrets.example.json` to `server/secrets.json` and paste in your token + chat ID.

`secrets.json` is gitignored — it stays on your machine only.

### Run the detector

Make sure your laptop is on the **same WiFi** as the ESP32-CAM, then:

```powershell
.\.venv\Scripts\python.exe detector.py
```

Within ~2 seconds your phone gets a Telegram push: *"ESP32-CAM detector starting up."* The OpenCV preview window opens showing the live stream with detection overlays. Point the cam at a wall — when something crack-shaped is detected, your phone pings with the annotated photo.

Press **`q`** in the preview window (or **Ctrl+C** in the terminal) to stop.

For all run modes, detection-pipeline details, threshold tuning, and ML training, see [`server/README.md`](server/README.md).

## Alternative: Arduino IDE

If you'd rather use Arduino IDE 2.x instead of PlatformIO, here's the setup. Note that PlatformIO is what this repo is structured for — Arduino IDE works but needs a couple of manual fixes because Arduino expects a single `.ino` file in a folder of the same name.

### One-time installs

1. **Arduino IDE 2.x** — https://www.arduino.cc/en/software
2. **ESP32 board support**:
   - File → Preferences → "Additional boards manager URLs" → add:
     `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
   - Tools → Board → Boards Manager → search "esp32" → install **esp32 by Espressif Systems** (v2.0.17 or newer).
3. **Library**: Sketch → Include Library → Manage Libraries → search "WiFiManager" → install **WiFiManager by tzapu** (v2.0.17+).
4. Same USB-to-serial driver as above.

### Adapting the repo

Arduino IDE expects a folder whose name matches the `.ino` file. So:

1. Make a new folder named `ESP32CAM_Streamer`.
2. Copy `src/main.cpp` into it and rename it to `ESP32CAM_Streamer.ino`.
3. Copy `include/index_html.h` into the same folder (alongside the `.ino`).
4. In the `.ino`, change the include line from `#include "index_html.h"` (which still works) — no edit needed actually.
5. Open `ESP32CAM_Streamer.ino` in Arduino IDE.

### Board settings (Tools menu)

- **Board**: ESP32 Arduino → "AI Thinker ESP32-CAM"
- **CPU Frequency**: 240 MHz (default)
- **Flash Frequency**: 80 MHz
- **Flash Mode**: QIO
- **Partition Scheme**: **Minimal SPIFFS (1.9MB APP with OTA / 190KB SPIFFS)** — this is critical, default partition is too small.
- **Core Debug Level**: None
- **PSRAM**: **Enabled** — critical, otherwise the stream will stutter.
- **Port**: pick the COM port for your FTDI.

### Flash

Same dance: GPIO0 → GND jumper, plug in FTDI, click Upload, watch it write, remove jumper, press RST. Arduino IDE does not have an OTA-by-default workflow as clean as PlatformIO's — for OTA you'd run espota.py manually (Tools menu, after Sketch → Export Compiled Binary). PlatformIO does this in one click. **Recommendation: use PlatformIO for the OTA workflow.**

## Dependencies & build commands

There's no separate `requirements.txt` or `package.json` for this project — **`platformio.ini` is the manifest.** PlatformIO reads it, downloads the right toolchain version, the right framework, the right libraries, and caches them. Same role as those files in other ecosystems.

Pinned versions:

| Component | Pinned version |
|---|---|
| Espressif 32 platform | `~7.0.1` (any 7.0.x patch) |
| Arduino framework | bundled with the platform (3.20017) |
| WiFiManager (tzapu) | `^2.0.17` (latest 2.x) |
| ArduinoOTA, ESPmDNS, WiFi | bundled with the Arduino framework |

### One-liner build (no IDE needed)

If you have PlatformIO CLI installed (`pip install platformio` or via the VS Code extension), this is all you need from a fresh clone:

```
git clone https://github.com/shreyanshmishra2607/esp32cam-streamer.git
cd esp32cam-streamer
pio run                # downloads dependencies + builds both envs
```

The first run installs the platform + libraries (~5 minutes the very first time, cached after). Subsequent builds are seconds.

### Common commands

| Command | What it does |
|---|---|
| `pio run` | Builds every env defined in `platformio.ini`. |
| `pio run -e esp32cam` | Builds only the cable-upload env. |
| `pio run -e esp32cam -t upload` | Builds + flashes over USB (requires the FTDI dance, see below). |
| `pio run -e esp32cam_ota -t upload` | Builds + flashes over WiFi to a running cam. |
| `pio device monitor` | Opens the serial monitor on the auto-detected COM port at 115200 baud. |
| `pio pkg install` | Installs/refreshes dependencies without building. |
| `pio run -t clean` | Wipes the build cache (`.pio/build/`). |
| `pio system prune` | Frees disk space — removes unused PlatformIO caches. |

### How dependency resolution works here

When you (or any dev) runs `pio run` for the first time, PlatformIO:

1. Reads `platformio.ini` → sees `platform = espressif32 @ ~7.0.1`.
2. Downloads that platform and its toolchain (Xtensa GCC, esptool, mkspiffs, etc.) into `~/.platformio/`.
3. Reads `lib_deps = tzapu/WiFiManager @ ^2.0.17`.
4. Pulls that library from the PlatformIO Library Registry, caches it.
5. Compiles. Done.

If you change `lib_deps` to add another library, PlatformIO installs it on the next build. There's nothing to manually `pip install` or `npm install`.

## First flash (cable, one time only)

You only need the cable for the very first firmware upload. After that, every update is wireless.

1. Wire the FTDI to the cam: 5V→5V, GND→GND, FTDI TX→U0R (GPIO3), FTDI RX→U0T (GPIO1).
2. Jumper GPIO0 to GND on the cam (puts it in flash-download mode).
3. Plug the FTDI into your laptop's USB.
4. Build and flash:
   ```
   pio run -e esp32cam -t upload
   ```
5. When upload finishes, remove the GPIO0→GND jumper and press the RST button on the cam.

## Pairing it to a WiFi network (end-user flow)

This is the workflow for anyone who receives the device. No code, no cables to a computer, no serial monitor.

1. Power the cam from any USB charger or 5V source.
2. On your phone or laptop, open WiFi settings — connect to **`ESP32CAM_Setup`** (open network, no password).
3. The setup portal opens automatically on most phones within a few seconds. On Windows it can take up to 10 seconds, or just open any URL in a browser and you'll be redirected.
4. Tap **Configure WiFi**, pick your network, enter its password, hit **Save**.
5. A "WiFi saved" confirmation appears with a 30-second countdown. When it hits zero, the browser opens `http://esp32cam.local/` automatically — the live stream is there.

Credentials are stored in the ESP's non-volatile storage (NVS). On every subsequent boot, it auto-joins. If you ever need to change the network, hit the red **Change WiFi** button on the stream page — the cam erases the saved credentials and reopens the setup portal.

## Updating firmware over WiFi (developer flow)

After the first cable flash, you push new firmware over the same WiFi the cam is on. No cable, no buttons, no flash-mode jumper.

```
pio run -e esp32cam_ota -t upload
```

PlatformIO sends the new binary to `esp32cam.local` on port 3232, the running firmware writes it to the second app partition, the chip reboots into it. Takes about 20–30 seconds end-to-end. If mDNS doesn't resolve from your laptop, set `upload_port = <ip>` in `platformio.ini` for the `esp32cam_ota` env.

## Web endpoints

| Path | Port | What |
|---|---|---|
| `/` | 80 | Control page (HTML + JS) |
| `/stream` | **81** | `multipart/x-mixed-replace` MJPEG stream |
| `/capture` | 80 | Single JPEG snapshot |
| `/control?var=<name>&val=<int>` | 80 | Live camera-setting tweak (framesize / quality / brightness / contrast / saturation / hmirror / vflip) |
| `/reset_wifi` | 80 | Erase saved WiFi creds and reboot into setup portal |

Stream is on a separate port so a long-lived stream connection doesn't starve the control endpoints.

## Project layout

```
.
├── platformio.ini            ; two envs: esp32cam (cable) + esp32cam_ota (wireless)
├── src/main.cpp              ; camera + WiFiManager + ArduinoOTA + HTTP handlers
├── include/index_html.h      ; web UI (HTML + CSS + JS as a PROGMEM string)
└── .gitignore                ; ignores .pio/, .vscode/ artifacts, and personal notes
```

## Common pitfalls

- **Camera init fails with `0x105 (ESP_ERR_NOT_FOUND)`**: usually a power problem. FTDI's 5V rail can't supply the OV2640's startup current. Use a phone charger. The firmware also pulses the camera's power-down line before init, which handles software-reset edge cases.
- **Stream is laggy / stuttery**: confirm `psramFound: YES` in the serial log. If `NO`, the `-DBOARD_HAS_PSRAM` build flag isn't being applied. With PSRAM, expect ~25 fps at VGA.
- **`esp32cam.local` doesn't resolve**: some older Windows builds don't support mDNS. Use the IP from the serial monitor, or look up the device in your router's connected-devices list.

## License

MIT — do what you like.
