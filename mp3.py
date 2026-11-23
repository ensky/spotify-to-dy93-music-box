#!/usr/bin/env python3
"""Step 2: convert videos from source/yt/ into chip-safe mp3 in source/mp3/.

Chip requirements enforced here:
  - MP3 (MPEG-1 Layer III), sample rate <= 44.1 kHz (48 kHz fails on the chip)
  - default 128k CBR (--bitrate 320 also possible)
  - minimal ID3v1 tags only (no embedded artwork, no ID3v2)

Usage:
  python3 mp3.py "source/yt/玩具國 [aOWZZPkh78s].mp4"
  python3 mp3.py --start 10.05 "source/yt/玩具國 [aOWZZPkh78s].mp4"   # skip 10.05s intro
  python3 mp3.py *.mp4 --bitrate 320k
"""

import argparse
import os
import re
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(BASE_DIR, "source", "mp3")
MAX_SAMPLE_RATE = 44100  # Hz; the chip cannot read 48 kHz


def sanitize_label(text, max_len=20):
    text = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_")
    return text[:max_len] or "song"


def convert(src, dest, title, start=0.0, bitrate="128k"):
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start and start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += [
        "-i", src, "-map", "0:a", "-vn",
        "-codec:a", "libmp3lame", "-b:a", bitrate, "-ar", str(MAX_SAMPLE_RATE), "-ac", "2",
        "-map_metadata", "-1", "-id3v2_version", "0", "-write_id3v1", "1",
        "-metadata", f"title={title}", "-metadata", f"track={title}",
        dest,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")


def main():
    parser = argparse.ArgumentParser(description="Convert video to chip-safe mp3 in source/mp3/.")
    parser.add_argument("inputs", nargs="+", help="video files (usually under source/yt/)")
    parser.add_argument("--start", type=float, default=0.0, help="trim seconds from the start (intro)")
    parser.add_argument("--bitrate", default="128k", help="mp3 bitrate (default: 128k)")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"output dir (default: {DEFAULT_OUT})")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    for src in args.inputs:
        if not os.path.isfile(src):
            sys.exit(f"ERROR: not a file: {src}")
        stem = os.path.splitext(os.path.basename(src))[0]
        dest = os.path.join(args.out, stem + ".mp3")
        convert(src, dest, title=sanitize_label(stem), start=args.start, bitrate=args.bitrate)
        print(f"  converted: {dest}")


if __name__ == "__main__":
    main()
