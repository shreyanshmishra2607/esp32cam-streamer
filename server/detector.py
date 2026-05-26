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
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import requests


# ---------- detection thresholds (tune for your environment) ----------
CANNY_LOW = 50
CANNY_HIGH = 150
HOUGH_THRESHOLD = 60          # min votes for Hough to accept a line
MIN_LINE_LENGTH = 80          # pixel length below this is ignored
MAX_LINE_GAP = 10             # join collinear segments closer than this
MIN_LINES_FOR_CRACK = 2       # how many qualifying lines trigger an alert
# Reject any line whose angle is within this many degrees of perfectly
# horizontal or vertical — kills false positives from picture frames,
# door frames, window frames, tile grout, table edges, etc. Real cracks
# meander and rarely fall on axis. Set to 0 to disable the filter.
AXIS_REJECT_TOLERANCE_DEG = 12
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
    # angle is now in [0, 90]; 0 = horizontal, 90 = vertical
    return angle < AXIS_REJECT_TOLERANCE_DEG or angle > (90 - AXIS_REJECT_TOLERANCE_DEG)


def detect_crack(img: np.ndarray) -> tuple[bool, np.ndarray, int]:
    """Return (is_crack, annotated_image, qualifying_line_count).

    Lines that are nearly horizontal or vertical are drawn in dim grey so
    you can see what was rejected; lines that qualify as crack-like are
    drawn in red.
    """
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
    qualifying = 0
    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line[0]
            length = float(np.hypot(x2 - x1, y2 - y1))
            if length < MIN_LINE_LENGTH:
                continue
            if _is_axis_aligned(x1, y1, x2, y2):
                # Draw rejected lines in dim grey so we can see what the filter caught
                cv2.line(annotated, (x1, y1), (x2, y2), (60, 60, 60), 1)
                continue
            qualifying += 1
            cv2.line(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)

    return qualifying >= MIN_LINES_FOR_CRACK, annotated, qualifying


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
    """Consume the cam's MJPEG stream at port 81 — high FPS, low latency."""
    stream_url = f"{base_url}:81/stream"
    print(f"Streaming from {stream_url}")
    print(f"Output:        {raw_dir.parent}")
    print(f"Save policy:   raw every {RAW_SAVE_EVERY_S}s, crack cooldown {CRACK_COOLDOWN_S}s")
    print(f"Telegram:      {'ON' if tg_token and tg_chat else 'OFF (no token / chat id)'}")
    print("-" * 60)

    cap = cv2.VideoCapture(stream_url)
    if not cap.isOpened():
        print(f"Could not open {stream_url}", file=sys.stderr)
        sys.exit(1)

    last_raw_save = 0.0
    last_crack_save = 0.0
    last_tg_send = 0.0

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                # MJPEG stream hiccup — reconnect
                cap.release()
                time.sleep(1.0)
                cap = cv2.VideoCapture(stream_url)
                continue

            now = time.time()
            is_crack, annotated, n_lines = detect_crack(frame)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")

            if now - last_raw_save >= RAW_SAVE_EVERY_S:
                cv2.imwrite(str(raw_dir / f"{ts}.jpg"), frame)
                last_raw_save = now

            if is_crack and now - last_crack_save >= CRACK_COOLDOWN_S:
                crack_path = crack_dir / f"{ts}.jpg"
                cv2.imwrite(str(crack_path), annotated)
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
                    break

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        cap.release()
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
