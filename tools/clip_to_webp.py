"""A screen recording, cut and shrunk to a looping animated WebP for the
first launch's tour (ui/onboarding_steps.py's Clip) - QMovie plays it, so
the app ships no video player.

    python tools/clip_to_webp.py recording.mp4 --start 2.5 --end 9 [--crop x,y,w,h]
        -> assets/onboarding/circle.webp

--crop is in the recording's own pixels: the part of the screen worth
showing (OBS records the whole screen). Keep it short - a few seconds - and
under ~2 MB: it goes into the installer.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def convert(src: Path, out: Path, start: float, end: float, width: int, fps: int,
            crop: tuple[int, int, int, int] | None) -> int:
    import cv2
    from PIL import Image
    video = cv2.VideoCapture(str(src))
    if not video.isOpened():
        raise SystemExit(f"Can't open {src}")
    rate = video.get(cv2.CAP_PROP_FPS) or 30
    every = max(1, round(rate / fps))
    video.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    frames, i = [], 0
    while True:
        ok, frame = video.read()
        if not ok or video.get(cv2.CAP_PROP_POS_MSEC) > end * 1000:
            break
        if i % every == 0:
            if crop:
                x, y, w, h = crop
                frame = frame[y:y + h, x:x + w]
            h, w = frame.shape[:2]
            frame = cv2.resize(frame, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
            frames.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        i += 1
    if not frames:
        raise SystemExit("No frames in that range - check --start and --end.")
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=round(1000 / fps), loop=0,
                   quality=70, method=6)
    return out.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording", type=Path)
    ap.add_argument("--start", type=float, default=0.0, help="seconds into the recording")
    ap.add_argument("--end", type=float, default=8.0)
    ap.add_argument("--crop", default="", help="x,y,w,h in the recording's pixels")
    ap.add_argument("--width", type=int, default=840, help="pixels wide (shown at 420: sharp on HiDPI)")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--out", type=Path, default=ROOT / "assets" / "onboarding" / "circle.webp")
    a = ap.parse_args()
    crop = tuple(int(v) for v in a.crop.split(",")) if a.crop else None
    if crop is not None and len(crop) != 4:
        raise SystemExit("--crop is x,y,w,h")
    size = convert(a.recording, a.out, a.start, a.end, a.width, a.fps, crop)
    print(f"Wrote {a.out} ({size / 1_000_000:.1f} MB).")
    if size > 2_500_000:
        print("That's big for the installer: try a shorter --end, a --crop, or --fps 12.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
