# ESP32-CAM Streamer

Live MJPEG stream from an AI-Thinker ESP32-CAM, with a browser-based control panel, WiFi pairing via captive portal, and wireless firmware updates via ArduinoOTA. Built with PlatformIO + Arduino framework.

## What it does

- Live video stream over WiFi, viewable in any browser.
- Snapshot capture endpoint (`/capture`).
- On-page controls: resolution, JPEG quality, brightness, contrast, saturation, horizontal-mirror, vertical-flip.
- WiFi pairing through a captive-portal setup AP — no source-code edits needed to change networks.
- Stable hostname via mDNS at `http://esp32cam.local/` — no chasing IPs.
- Wireless firmware updates after the first cable flash.
- "Change WiFi" button to reset saved credentials remotely.

## Hardware

- AI-Thinker ESP32-CAM module (ESP32 + 4 MB PSRAM + OV2640 sensor).
- FTDI USB-to-serial adapter (only needed for the first firmware flash). Any FT232RL, CH340G, or CP2102 module works.
- A few female-to-female jumper wires.
- 5V power source rated 1 A or higher — a USB phone charger works. The FTDI's 5V is marginal and will brown out the camera under load.

## Setting up the project on a new laptop (recommended: VS Code + PlatformIO)

The whole toolchain auto-installs once you open the project in VS Code. There's no global Arduino setup to fight with.

### One-time installs

1. **VS Code** — https://code.visualstudio.com/
2. **PlatformIO IDE extension** — inside VS Code: Extensions tab (Ctrl+Shift+X) → search "PlatformIO IDE" → Install. First launch downloads ~600 MB of toolchains (Espressif compiler, esptool, etc.) — this is a one-time thing and takes a few minutes.
3. **USB-to-serial driver** for whatever FTDI chip you have. Find your COM port in Device Manager after plugging the FTDI in — if it appears as "USB Serial Port", you're already good. Otherwise:
   - FT232RL → usually auto-installed by Windows. If not: https://ftdichip.com/drivers/vcp-drivers/
   - CH340G → https://sparks.gogo.co.nz/ch340.html
   - CP2102 → https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers
4. **Git** — https://git-scm.com/ — only if you don't have it.

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

That's the whole development loop.

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
