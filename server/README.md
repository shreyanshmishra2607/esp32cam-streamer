# Crack detector (Python server)

Consumes the ESP32-CAM's MJPEG stream, runs an OpenCV multi-filter pipeline to detect crack-shaped patterns in each frame, archives both raw and annotated images to disk, and pushes a Telegram alert (with the annotated photo attached) when a crack is detected.

A second-stage ML classifier (MobileNetV3-Small, trained via `train_model.py`) can be plugged in for higher precision — see [Layer 2](#layer-2--ml-classifier-optional).

```
ESP32-CAM ──MJPEG─► OpenCV pipeline ──verdict──► save to disk
                                            └──► Telegram push (with photo)
```

---

## 1. One-time setup

### 1a. Python environment

```powershell
cd server
python -m venv .venv
.venv\Scripts\Activate.ps1            # PowerShell
# or .venv\Scripts\activate.bat       # cmd
# or source .venv/Scripts/activate    # bash on Windows
pip install -r requirements.txt
```

This installs `opencv-python`, `numpy`, `requests` — the runtime dependencies. Training-only dependencies (`torch`, `torchvision`) come later if you choose to train the Layer 2 model.

### 1b. Create your Telegram bot (~3 minutes, on your phone)

The detector pushes crack alerts to you via Telegram. To get pushes on your phone, you need a bot.

1. Open the **Telegram** app on your phone.
2. Search for the user **`@BotFather`** and open the chat. Tap **Start**.
3. Send the message **`/newbot`**.
4. BotFather asks for a **display name** — type whatever, e.g. `ESP32 Crack Detector`.
5. BotFather asks for a **username** — must end in `bot`, e.g. `mycam_crack_bot`. Try another if taken.
6. BotFather replies with a **token** that looks like `7651234567:AAEf...lots-of-random-chars`. Copy it. This is the bot's password — keep it private.

Now you need your **chat ID** (so the bot knows which user to send alerts to):

7. In Telegram, search for your new bot's username (or tap the link BotFather sent). Tap **Start**, then send any message like `hi`.
8. In any web browser, open the URL:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Replace `<YOUR_TOKEN>` with the actual token from step 6.
9. The browser shows JSON like:
   ```json
   {
     "ok": true,
     "result": [{
       "message": {
         "from": {"id": 987654321, ...},
         "chat": {"id": 987654321, ...},
         "text": "hi"
       }
     }]
   }
   ```
   The number after `"chat":{"id":` is your **chat ID**. Copy it.

If `"result": []` is empty, you haven't sent a message to the bot yet — go back to step 7.

### 1c. Drop the credentials into `secrets.json`

Copy the template and edit:

```powershell
copy secrets.example.json secrets.json
notepad secrets.json
```

Replace the two placeholder strings with your real token and chat ID:

```json
{
  "telegram_bot_token": "7651234567:AAEf-real-token-here",
  "telegram_chat_id": "987654321"
}
```

Save. `secrets.json` is gitignored — it will never end up on GitHub.

If you'd rather not use a file, the script also accepts environment variables:

```powershell
$env:TELEGRAM_BOT_TOKEN = "7651234567:AAEf..."
$env:TELEGRAM_CHAT_ID   = "987654321"
```

### 1d. Make sure your laptop and the cam are on the same WiFi

The detector and the cam must be reachable to each other. Easy way to confirm: open `http://esp32cam.local/` in any browser on the laptop — if the stream loads, you're good.

If `esp32cam.local` doesn't resolve, find the cam's IP from its serial monitor (or look in your router's connected-devices list) and use that instead via `--url`.

---

## 2. Run

### Default (smooth MJPEG stream + preview + Telegram alerts):

```powershell
.\.venv\Scripts\python.exe detector.py
```

Within ~2 seconds you should get a **Telegram push on your phone** saying `ESP32-CAM detector starting up.` That confirms the whole pipeline is working before any crack is needed.

### Other modes:

| Command | What |
|---|---|
| `detector.py` | Default: live MJPEG stream + preview window + Telegram alerts. |
| `detector.py --no-show` | Headless (no preview window). |
| `detector.py --no-telegram` | Disable Telegram even if `secrets.json` is set up. |
| `detector.py --poll` | One frame every 3 s via `/capture` (port 80 only) — fallback if streaming has issues. |
| `detector.py --poll --interval 1` | Poll every 1 second instead of 3. |
| `detector.py --url http://192.168.1.45` | Use a raw IP instead of `esp32cam.local`. |
| `detector.py --out d:\crack-logs` | Send saved frames to a different directory. |

Stop the detector with **`q`** in the preview window or **Ctrl+C** in the terminal.

---

## 3. How detection works

The pipeline runs four stages on every frame. A line must pass all of them to be counted as crack-like.

### Stage 1 — Edge & line extraction

```
grayscale → Gaussian blur (5×5) → Canny edges → HoughLinesP
```

Canny extracts all edges in the frame. HoughLinesP turns them into line segments. Defaults pick segments at least 100 px long.

### Stage 2 — Axis filter

Lines within ±12° of horizontal or vertical are rejected. Picture frames, door/window frames, tile grout, and table edges are almost always axis-aligned. Cracks meander and rarely fall on axis.

### Stage 3 — ROI filter

Lines whose midpoint falls outside the central 80% of the frame are rejected. Ceiling/floor lines, lamp edges, and other periphery clutter get dropped.

### Stage 4 — Darkness check

For each surviving line, sample pixel brightness *along* the line and along the parallel bands ~4 px to either side. The line must be at least 18 gray levels darker than its surroundings. **This is the single most powerful filter** — it kills picture frames even when they're tilted off-axis, because their "line" is a boundary between two regions of similar brightness, not a thin dark mark on lighter material. Real cracks pass it.

### Color-coded rejection overlay (preview window)

Every line is drawn in a different color so you can see *why* each one was kept or rejected:

| Color | Meaning |
|---|---|
| 🟥 Red (thick) | Survived all filters → counted as crack-like |
| ⬜ Grey (thin) | Rejected: axis-aligned |
| 🟨 Dim yellow (thin) | Rejected: outside the central ROI |
| 🟦 Dim teal (thin) | Rejected: not darker than its neighbors |

The faint cyan box drawn on the frame is the ROI boundary.

A frame is flagged as containing a crack when at least `MIN_LINES_FOR_CRACK` (default 2) red lines are found.

---

## 4. Tuning

All thresholds live at the top of `detector.py`. Common adjustments:

| Symptom | Knob | Direction |
|---|---|---|
| Still too many false positives | `DARK_CONTRAST_MIN` | bump from 18 → 25 (require sharper contrast) |
| Missing real cracks | `DARK_CONTRAST_MIN` | drop from 18 → 12 |
| Missing diagonal cracks | `MIN_LINE_LENGTH` | drop from 100 → 70 |
| Edge-of-frame noise still leaking through | `ROI_MARGIN` | bump from 0.1 → 0.15 |
| Want to alert on every single suspicious line | `MIN_LINES_FOR_CRACK` | drop from 2 → 1 |
| Too many Telegram pushes | `TELEGRAM_COOLDOWN_S` | bump from 30 → 60 or 90 |

Re-run after each change. No restart of the cam needed — this is all server-side.

---

## 5. Telegram behavior

- **Startup ping** delivered the moment the detector launches, so you can verify the pipeline before walking off.
- **Per-detection ping** with the annotated photo + caption `Crack detected — N line(s) at HH:MM:SS`.
- **30-second cooldown** between Telegram pushes (configurable). Disk archiving has its own 1.5-second cooldown so saving doesn't get throttled.
- If `secrets.json` is missing or has placeholder values, Telegram is silently disabled — the rest of the detector still runs.

To revoke a leaked token: open `@BotFather` → `/revoke` → pick the bot → BotFather issues a new token. Update `secrets.json` with the new value.

---

## 6. Layer 2 — ML classifier (optional)

The OpenCV pipeline above is fast and surprisingly good after tuning, but it doesn't *understand* whether a thin dark line is a crack or just (e.g.) a strand of hair on the lens. To upgrade precision, train a binary classifier and use it as a second-pass filter.

`train_model.py` is the training script:

1. Install training-only deps (one time):
   ```powershell
   .\.venv\Scripts\pip.exe install torch torchvision --index-url https://download.pytorch.org/whl/cpu
   ```
2. Download the [Surface Crack Detection dataset](https://www.kaggle.com/datasets/arunrk7/surface-crack-detection) from Kaggle (~230 MB, free account required).
3. Unzip so `server/dataset/Positive/` and `server/dataset/Negative/` contain the images (20k each).
4. Train:
   ```powershell
   .\.venv\Scripts\python.exe train_model.py
   ```
   3 epochs, ~15 minutes on CPU. Produces `crack_classifier.onnx` (~6 MB) + `crack_classifier_labels.txt`.

Once the ONNX file exists, `detector.py` will load it via `cv2.dnn` and only fire a Telegram alert when **both** the OpenCV pipeline AND the model agree on `crack` — best of both: geometric speed, ML accuracy.

*(Note: ONNX loading inside `detector.py` is the next planned commit — currently the script trains and exports the model but the detector hasn't been wired to consume it yet.)*

---

## 7. Output layout

```
server/
  captures/                       (gitignored)
    raw/                          every frame at ~2-second intervals
      20260526-131634.jpg
      20260526-131638.jpg
      ...
    cracks/                       only frames flagged as containing a crack
      20260526-131638.jpg         with red lines drawn on detected cracks
      ...

  secrets.json                    (gitignored) bot token + chat ID
  crack_classifier.onnx           (gitignored) trained model, if any
  dataset/                        (gitignored) training images, if downloaded
```

---

## 8. Troubleshooting

**"Stream timeout triggered after 30000 ms"**
The ESP32-CAM's stream endpoint on port 81 only accepts one client at a time. Close any browser tab viewing the cam, then re-run. If that doesn't help, use `--poll` mode (uses port 80 instead of 81).

**`esp32cam.local` doesn't resolve on Windows**
Some Windows builds don't run mDNS. Use the cam's IP directly via `--url http://192.168.x.x`. Find the IP from the cam's `/stats` JSON endpoint or your router's device list.

**Telegram says "configured but startup ping failed"**
Re-check the token + chat ID in `secrets.json`. Token format is `digits:letters_and_dashes`. The simplest test: paste your token into `https://api.telegram.org/bot<TOKEN>/getMe` in a browser — should return your bot's name.

**The CV window opens then immediately closes**
Streaming connection couldn't establish. Use `--poll` to verify the rest of the pipeline works, then debug the stream separately.

**`Camera init failed: 0x105` appears in the cam's serial log**
That's a firmware-side problem, not the server's. Power-cycle the ESP32 fully (unplug and replug) — software reset doesn't always re-init the OV2640. See the firmware README for details.
