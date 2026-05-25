"""
ESP32-CAM crack detector.

Polls the camera's /capture endpoint at a fixed interval, runs an OpenCV
edge + Hough-line pipeline, and flags frames that contain crack-shaped
geometry. Every frame is archived raw; flagged frames are also saved
with a red annotation overlay.

Usage:
    python detector.py
    python detector.py --url http://10.92.8.61/capture --interval 2
    python detector.py --show   # opens a live preview window
"""

from __future__ import annotations

import argparse
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
MIN_LINES_FOR_CRACK = 1       # how many qualifying lines trigger an alert
# ----------------------------------------------------------------------


def fetch_frame(url: str, timeout: float = 5.0) -> np.ndarray | None:
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  fetch failed: {e}", file=sys.stderr)
        return None
    arr = np.frombuffer(r.content, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def detect_crack(img: np.ndarray) -> tuple[bool, np.ndarray, int]:
    """Return (is_crack, annotated_image, qualifying_line_count)."""
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
            if length >= MIN_LINE_LENGTH:
                qualifying += 1
                cv2.line(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)

    return qualifying >= MIN_LINES_FOR_CRACK, annotated, qualifying


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://esp32cam.local/capture",
                        help="Camera capture endpoint (default: %(default)s)")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="Seconds between captures (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "captures",
                        help="Directory for saved frames (default: %(default)s)")
    parser.add_argument("--show", action="store_true",
                        help="Open a live OpenCV window with annotations")
    args = parser.parse_args()

    raw_dir = args.out / "raw"
    crack_dir = args.out / "cracks"
    raw_dir.mkdir(parents=True, exist_ok=True)
    crack_dir.mkdir(parents=True, exist_ok=True)

    print(f"Polling {args.url} every {args.interval}s — Ctrl+C to stop")
    print(f"Output: {args.out}")
    print("-" * 60)

    try:
        while True:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            img = fetch_frame(args.url)

            if img is None:
                time.sleep(args.interval)
                continue

            cv2.imwrite(str(raw_dir / f"{ts}.jpg"), img)

            is_crack, annotated, n_lines = detect_crack(img)

            if is_crack:
                cv2.imwrite(str(crack_dir / f"{ts}.jpg"), annotated)
                print(f"[{ts}]  *** CRACK ***  ({n_lines} line{'s' if n_lines != 1 else ''})  -> cracks/{ts}.jpg")
            else:
                print(f"[{ts}]  clean         ({n_lines} line{'s' if n_lines != 1 else ''})")

            if args.show:
                cv2.imshow("ESP32-CAM detector", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if args.show:
            cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
