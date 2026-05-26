"""
ESP32-CAM crack detector.

Consumes the camera's live MJPEG stream (default) or polls /capture at a
fixed interval, runs an OpenCV edge + Hough-line pipeline on each frame,
and flags ones containing crack-shaped geometry. Every-Nth frame is
archived raw; flagged frames are saved with a red annotation overlay,
with a cooldown so consecutive detections don't spam disk.

Usage:
    python detector.py                    # smooth live stream mode (default)
    python detector.py --poll             # one frame every 3s
    python detector.py --url http://10.92.8.61
    python detector.py --no-show          # headless
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np
import requests


# ---------- detection thresholds (tune for your environment) ----------
CANNY_LOW = 50
CANNY_HIGH = 150
HOUGH_THRESHOLD = 60          # min votes for Hough to accept a line
MIN_LINE_LENGTH = 100         # pixel length below this is ignored
MAX_LINE_GAP = 10             # join collinear segments closer than this
MIN_LINES_FOR_CRACK = 2       # how many qualifying lines trigger an alert

# --- Layer-1 filters that suppress false positives ---
# 1) Reject any line within this many degrees of perfectly horizontal/vertical.
#    Kills picture frames, door/window frames, tile grout, table edges, etc.
#    Real cracks meander and rarely fall on axis. Set to 0 to disable.
AXIS_REJECT_TOLERANCE_DEG = 12
# 2) Reject any line whose midpoint is outside the central [margin, 1-margin]
#    region of the frame. Edges of the frame are dominated by ceiling/floor
#    lines, picture frames and shelving, none of which are cracks.
ROI_MARGIN = 0.1
# 3) Reject any line whose pixel brightness isn't notably darker than the
#    parallel band ~PERP_OFFSET px to either side. Real cracks are darker
#    than the wall they're on; window edges, painted lines, and seams often
#    aren't. This is the single most powerful filter — it kills picture
#    frames even when they're rotated off-axis.
DARK_CONTRAST_MIN = 18        # required mean-brightness drop (0-255 gray)
DARK_PERP_OFFSET = 4          # px to either side of the line we sample for comparison
DARK_SAMPLES = 20             # number of points along the line we sample
# ----------------------------------------------------------------------

# ---------- archival policy ----------
RAW_SAVE_EVERY_S = 2.0        # archive a raw frame at most this often
CRACK_COOLDOWN_S = 1.5        # don't save another crack image inside this window
TELEGRAM_COOLDOWN_S = 30.0    # don't send another alert inside this window (avoid spam)
# --------------------------------------


def load_telegram_config() -> tuple[str | None, str | None]:
    """Read bot token + chat ID from env vars first, then secrets.json."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    secrets_path = Path(__file__).parent / "secrets.json"
    if (not token or not chat_id) and secrets_path.exists():
        try:
            with open(secrets_path) as f:
                data = json.load(f)
            token = token or data.get("telegram_bot_token")
            chat_id = chat_id or data.get("telegram_chat_id")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  warning: could not read secrets.json: {e}", file=sys.stderr)

    if token and "PASTE_YOUR" in token:
        token = None
    if chat_id and "PASTE_YOUR" in str(chat_id):
        chat_id = None

    return token, chat_id


def telegram_send_text(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"  telegram text send failed: {e}", file=sys.stderr)
        return False


def telegram_send_photo(token: str, chat_id: str, image_bytes: bytes, caption: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    files = {"photo": ("crack.jpg", image_bytes, "image/jpeg")}
    data = {"chat_id": chat_id, "caption": caption}
    try:
        r = requests.post(url, files=files, data=data, timeout=15)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"  telegram photo send failed: {e}", file=sys.stderr)
        return False


def _is_axis_aligned(x1: int, y1: int, x2: int, y2: int) -> bool:
    """True if the line is within AXIS_REJECT_TOLERANCE_DEG of horizontal or vertical."""
    if AXIS_REJECT_TOLERANCE_DEG <= 0:
        return False
    angle = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
    if angle > 90:
        angle = 180 - angle
    return angle < AXIS_REJECT_TOLERANCE_DEG or angle > (90 - AXIS_REJECT_TOLERANCE_DEG)


def _is_outside_roi(x1: int, y1: int, x2: int, y2: int, w: int, h: int) -> bool:
    """True if the line's midpoint is outside the central ROI box."""
    if ROI_MARGIN <= 0:
        return False
    mx = (x1 + x2) / 2.0
    my = (y1 + y2) / 2.0
    return not (
        w * ROI_MARGIN <= mx <= w * (1.0 - ROI_MARGIN)
        and h * ROI_MARGIN <= my <= h * (1.0 - ROI_MARGIN)
    )


def _is_darker_than_neighbors(gray: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> bool:
    """True if the line is notably darker than the parallel bands a few px to either side.

    Picture frames and door edges fail this — their "line" is the boundary between
    two regions of similar brightness, not a thin dark mark on lighter material.
    Real cracks pass it.
    """
    if DARK_CONTRAST_MIN <= 0:
        return True
    h, w = gray.shape[:2]
    dx, dy = (x2 - x1), (y2 - y1)
    length = float(np.hypot(dx, dy))
    if length < 1.0:
        return False
    # Unit perpendicular vector
    nx, ny = -dy / length, dx / length

    line_total = 0
    line_n = 0
    side_total = 0
    side_n = 0
    for i in range(DARK_SAMPLES):
        t = i / max(1, DARK_SAMPLES - 1)
        cx, cy = x1 + t * dx, y1 + t * dy

        ix, iy = int(cx), int(cy)
        if 0 <= ix < w and 0 <= iy < h:
            line_total += int(gray[iy, ix])
            line_n += 1

        for sign in (+1, -1):
            sx = int(cx + sign * nx * DARK_PERP_OFFSET)
            sy = int(cy + sign * ny * DARK_PERP_OFFSET)
            if 0 <= sx < w and 0 <= sy < h:
                side_total += int(gray[sy, sx])
                side_n += 1

    if line_n == 0 or side_n == 0:
        return False
    line_mean = line_total / line_n
    side_mean = side_total / side_n
    return (side_mean - line_mean) >= DARK_CONTRAST_MIN


def detect_crack(img: np.ndarray) -> tuple[bool, np.ndarray, int]:
    """Return (is_crack, annotated_image, qualifying_line_count).

    Lines are color-coded by rejection reason so it's visible what each
    filter caught:
        red    = passed all filters (counted as crack-like)
        grey   = rejected: axis-aligned (frames, edges, grout)
        teal   = rejected: outside the central ROI (frame periphery)
        cyan   = rejected: not darker than neighbors (lighting boundary, not a crack)
    """
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, CANNY_LOW, CANNY_HIGH)

    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=HOUGH_THRESHOLD,
        minLineLength=MIN_LINE_LENGTH,
        maxLineGap=MAX_LINE_GAP,
    )

    annotated = img.copy()

    # Draw the ROI box so you can see what region is being considered
    if ROI_MARGIN > 0:
        mx, my = int(w * ROI_MARGIN), int(h * ROI_MARGIN)
        cv2.rectangle(annotated, (mx, my), (w - mx, h - my), (40, 200, 200), 1)

    qualifying = 0
    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line[0]
            length = float(np.hypot(x2 - x1, y2 - y1))
            if length < MIN_LINE_LENGTH:
                continue

            if _is_axis_aligned(x1, y1, x2, y2):
                cv2.line(annotated, (x1, y1), (x2, y2), (60, 60, 60), 1)
                continue
            if _is_outside_roi(x1, y1, x2, y2, w, h):
                cv2.line(annotated, (x1, y1), (x2, y2), (120, 120, 0), 1)
                continue
            if not _is_darker_than_neighbors(gray, x1, y1, x2, y2):
                cv2.line(annotated, (x1, y1), (x2, y2), (0, 120, 120), 1)
                continue

            qualifying += 1
            cv2.line(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)

    return qualifying >= MIN_LINES_FOR_CRACK, annotated, qualifying


def stream_mjpeg(url: str, connect_timeout: float = 5.0) -> Iterator[bytes]:
    """Yield raw JPEG frames from an MJPEG (multipart/x-mixed-replace) stream.

    Uses `requests` + a tiny multipart parser instead of cv2.VideoCapture.
    OpenCV's FFmpeg backend has known compatibility gaps with the
    ESP32-CAM's stream format on some Windows builds; this path is bulletproof.
    """
    with requests.get(url, stream=True, timeout=(connect_timeout, None)) as r:
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        m = re.search(r"boundary=([^\s;]+)", ctype, re.IGNORECASE)
        if not m:
            raise RuntimeError(f"no multipart boundary in Content-Type: {ctype!r}")
        boundary = b"--" + m.group(1).encode()

        buf = b""
        for chunk in r.iter_content(chunk_size=8192):
            if not chunk:
                continue
            buf += chunk
            while True:
                bidx = buf.find(boundary)
                if bidx < 0:
                    break
                hdr_start = bidx + len(boundary)
                hdr_end = buf.find(b"\r\n\r\n", hdr_start)
                if hdr_end < 0:
                    break
                headers = buf[hdr_start:hdr_end].decode("latin-1", errors="ignore")
                clen_match = re.search(r"content-length:\s*(\d+)", headers, re.IGNORECASE)
                if not clen_match:
                    # malformed part — skip past it
                    buf = buf[hdr_end + 4:]
                    continue
                clen = int(clen_match.group(1))
                payload_start = hdr_end + 4
                payload_end = payload_start + clen
                if len(buf) < payload_end:
                    break  # need more bytes
                yield buf[payload_start:payload_end]
                buf = buf[payload_end:]


def fetch_capture(url: str, timeout: float = 5.0) -> np.ndarray | None:
    """One-shot HTTP GET to the /capture endpoint."""
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  fetch failed: {e}", file=sys.stderr)
        return None
    arr = np.frombuffer(r.content, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def run_stream(base_url: str, raw_dir: Path, crack_dir: Path, show: bool,
               tg_token: str | None, tg_chat: str | None) -> None:
    """Consume the cam's MJPEG stream at port 81 via a plain requests+parser path."""
    stream_url = f"{base_url}:81/stream"
    print(f"Streaming from {stream_url}")
    print(f"Output:        {raw_dir.parent}")
    print(f"Save policy:   raw every {RAW_SAVE_EVERY_S}s, crack cooldown {CRACK_COOLDOWN_S}s")
    print(f"Telegram:      {'ON' if tg_token and tg_chat else 'OFF (no token / chat id)'}")
    print("-" * 60)

    last_raw_save = 0.0
    last_crack_save = 0.0
    last_tg_send = 0.0
    frame_count = 0
    last_log = time.time()

    try:
        while True:
            try:
                for jpeg_bytes in stream_mjpeg(stream_url):
                    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is None:
                        continue

                    now = time.time()
                    is_crack, annotated, n_lines = detect_crack(frame)
                    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                    frame_count += 1

                    # Heartbeat log every 5 seconds so you see progress even when clean
                    if now - last_log >= 5.0:
                        fps = frame_count / (now - last_log)
                        print(f"[{ts}]  streaming  ({fps:.1f} fps)")
                        frame_count = 0
                        last_log = now

                    if now - last_raw_save >= RAW_SAVE_EVERY_S:
                        cv2.imwrite(str(raw_dir / f"{ts}.jpg"), frame)
                        last_raw_save = now

                    if is_crack and now - last_crack_save >= CRACK_COOLDOWN_S:
                        cv2.imwrite(str(crack_dir / f"{ts}.jpg"), annotated)
                        print(f"[{ts}]  *** CRACK ***  ({n_lines} line{'s' if n_lines != 1 else ''})  -> cracks/{ts}.jpg")
                        last_crack_save = now

                        if tg_token and tg_chat and now - last_tg_send >= TELEGRAM_COOLDOWN_S:
                            ok, jpg = cv2.imencode(".jpg", annotated)
                            if ok and telegram_send_photo(
                                tg_token, tg_chat, jpg.tobytes(),
                                caption=f"Crack detected — {n_lines} line(s) at {ts}",
                            ):
                                print(f"           telegram alert sent")
                                last_tg_send = now

                    if show:
                        cv2.imshow("ESP32-CAM detector  (q to quit)", annotated)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            return

            except (requests.RequestException, RuntimeError) as e:
                print(f"  stream error: {e} — reconnecting in 2s", file=sys.stderr)
                time.sleep(2.0)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if show:
            cv2.destroyAllWindows()


def run_poll(base_url: str, interval: float, raw_dir: Path, crack_dir: Path, show: bool,
             tg_token: str | None, tg_chat: str | None) -> None:
    """One HTTP /capture per interval — fallback / debug mode."""
    capture_url = f"{base_url}/capture"
    print(f"Polling {capture_url} every {interval}s")
    print(f"Output:   {raw_dir.parent}")
    print(f"Telegram: {'ON' if tg_token and tg_chat else 'OFF (no token / chat id)'}")
    print("-" * 60)

    last_tg_send = 0.0

    try:
        while True:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            img = fetch_capture(capture_url)
            if img is None:
                time.sleep(interval)
                continue

            cv2.imwrite(str(raw_dir / f"{ts}.jpg"), img)

            is_crack, annotated, n_lines = detect_crack(img)
            now = time.time()
            if is_crack:
                cv2.imwrite(str(crack_dir / f"{ts}.jpg"), annotated)
                print(f"[{ts}]  *** CRACK ***  ({n_lines} line{'s' if n_lines != 1 else ''})  -> cracks/{ts}.jpg")

                if tg_token and tg_chat and now - last_tg_send >= TELEGRAM_COOLDOWN_S:
                    ok, jpg = cv2.imencode(".jpg", annotated)
                    if ok and telegram_send_photo(
                        tg_token, tg_chat, jpg.tobytes(),
                        caption=f"Crack detected — {n_lines} line(s) at {ts}",
                    ):
                        print(f"           telegram alert sent")
                        last_tg_send = now
            else:
                print(f"[{ts}]  clean         ({n_lines} line{'s' if n_lines != 1 else ''})")

            if show:
                cv2.imshow("ESP32-CAM detector  (q to quit)", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if show:
            cv2.destroyAllWindows()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://esp32cam.local",
                        help="Base URL of the cam, no path (default: %(default)s)")
    parser.add_argument("--poll", action="store_true",
                        help="Use one-shot /capture polling instead of the MJPEG stream")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="Seconds between captures in --poll mode (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "captures",
                        help="Output directory for archives (default: %(default)s)")
    parser.add_argument("--no-show", dest="show", action="store_false",
                        help="Run headless (no OpenCV preview window)")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Disable Telegram alerts even if secrets.json / env vars are set")
    parser.set_defaults(show=True)
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    raw_dir = args.out / "raw"
    crack_dir = args.out / "cracks"
    raw_dir.mkdir(parents=True, exist_ok=True)
    crack_dir.mkdir(parents=True, exist_ok=True)

    tg_token, tg_chat = (None, None) if args.no_telegram else load_telegram_config()
    if tg_token and tg_chat:
        if telegram_send_text(tg_token, tg_chat, "ESP32-CAM detector starting up."):
            print("Telegram: startup message delivered.")
        else:
            print("Telegram: configured but startup ping failed — check token / chat id.")

    if args.poll:
        run_poll(base_url, args.interval, raw_dir, crack_dir, args.show, tg_token, tg_chat)
    else:
        run_stream(base_url, raw_dir, crack_dir, args.show, tg_token, tg_chat)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
