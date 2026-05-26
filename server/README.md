# Crack detector (Python server)

Polls the ESP32-CAM's `/capture` endpoint, runs an OpenCV edge + Hough-line pipeline on each frame, and flags ones that contain crack-shaped geometry.

## Setup (one time)

```powershell
cd server
python -m venv .venv
.venv\Scripts\Activate.ps1            # PowerShell
# or .venv\Scripts\activate.bat       # cmd
# or source .venv/Scripts/activate    # bash on Windows
pip install -r requirements.txt
```

## Run

Default: live MJPEG stream + preview window (smooth, ~15 fps):

```powershell
.venv\Scripts\python.exe detector.py
```

Headless (no preview window):

```powershell
.venv\Scripts\python.exe detector.py --no-show
```

Polling mode (one frame every 3 s — old slow behavior, useful for debugging):

```powershell
.venv\Scripts\python.exe detector.py --poll --interval 3
```

Custom URL (if mDNS doesn't resolve, use the IP from the cam's serial log or `/stats` endpoint). Pass the **base** URL, no path:

```powershell
.venv\Scripts\python.exe detector.py --url http://192.168.1.45
```

Press **`q`** in the preview window or **Ctrl+C** in the terminal to stop.

## What it does

In default streaming mode:

1. Opens an MJPEG connection to `<url>:81/stream` (the cam's stream endpoint).
2. Pulls frames at the camera's frame rate (~15 fps).
3. On each frame: grayscale → Gaussian blur → Canny edges → Hough line segments → keep segments longer than `MIN_LINE_LENGTH` (default 80 px).
4. Archives a raw frame at most every `RAW_SAVE_EVERY_S` seconds (default 2 s) so disk doesn't fill.
5. If `MIN_LINES_FOR_CRACK` (default 1) qualifying lines are present, the frame is flagged and the annotated version (red lines) is saved to `captures/cracks/`. A `CRACK_COOLDOWN_S` (default 1.5 s) cooldown prevents spam-saving the same crack frame-after-frame.

## Tuning the detector

The defaults at the top of `detector.py` are a starting point. You will need to tune them for your actual environment:

| Constant | What it controls | Increase to | Decrease to |
|---|---|---|---|
| `CANNY_LOW` / `CANNY_HIGH` | Edge detection sensitivity | catch fewer / weaker edges | catch more / fainter edges |
| `MIN_LINE_LENGTH` | Shortest line that counts | be stricter (longer cracks only) | catch shorter cracks (more false positives) |
| `HOUGH_THRESHOLD` | Votes required to confirm a line | be stricter | be more permissive |
| `MIN_LINES_FOR_CRACK` | How many lines = "this is a crack" | require multiple lines (less alarming, more cautious) | alert on any single line |

Run with `--show` and a real wall in front of the camera to see what's being detected, then adjust the constants until you're getting useful results.

## Limitations

This is a **geometry-only detector** — it looks for long thin lines. It doesn't know whether a long line is a crack, a wire, a window frame, or shadow. Expect false positives on man-made structures (the corner of a brick, the edge of a window). The next iteration would add either:

- A pre-trained crack classifier (e.g. a small CNN trained on the Concrete Crack dataset), or
- Region-of-interest filtering so only the wall area is considered.

## Output layout

```
server/
  captures/
    raw/                   # every frame, regardless of detection
      20260525-185530.jpg
      20260525-185533.jpg
      ...
    cracks/                # only frames flagged as containing a crack
      20260525-185530.jpg  # with red overlay drawn on detected lines
```

Both directories are gitignored.
