#!/usr/bin/env python3
"""Step 1: download YouTube videos into source/yt/.

Raw videos are kept as the source of truth for every song; mp3.py converts
them and sync.py stages them onto the SD card. Files are named
"<title> [<video id>].<ext>" so the source stays traceable.

Requires: yt-dlp (brew install yt-dlp) and ffmpeg.

Usage:
  python3 yt.py "https://www.youtube.com/watch?v=xxxx"      # URL
  python3 yt.py "快快呦! Pop!"                              # search term (top result)
  python3 yt.py --list "Go Away!"                           # top 5 candidates, no download
"""

import argparse
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(BASE_DIR, "source", "yt")
ARCHIVE_NAME = ".archive.txt"

# Try DASH first; fall back to HLS/progressive if YouTube returns 403/SABR
FORMATS = ["bv*[height<=1080]+ba/b[height<=1080]", "93/91/18/best[acodec!=none]/best"]


def is_url(text):
    return text.startswith("http://") or text.startswith("https://")


def to_target(query_or_url, search_limit=1):
    if is_url(query_or_url):
        return query_or_url, query_or_url
    prefix = f"ytsearch{search_limit}:"
    return prefix + query_or_url, f"(search) {query_or_url}"


def run(args):
    result = subprocess.run(args)
    if result.returncode != 0:
        sys.exit(result.returncode)


def list_candidates(query, limit=5):
    arg, label = to_target(query, search_limit=limit)
    print(f"\n=== Candidates for: {label} ===")
    run([
        "yt-dlp", "--no-warnings", "--no-playlist",
        "--print", "%(id)s | %(title)s | %(channel)s | %(duration_string)s",
        arg,
    ])


def download(query_or_url, out_dir=DEFAULT_OUT, capture=False):
    """Download one video. Returns (filepath, video_id) in capture mode."""
    target, label = to_target(query_or_url)
    print(f"\n=== Downloading video: {label} ===")
    base_cmd = [
        "yt-dlp", "--no-warnings", "--no-playlist", "--newline",
        "--merge-output-format", "mp4",
        "-P", out_dir,
        "-o", "%(title)s [%(id)s].%(ext)s",
        "--download-archive", os.path.join(out_dir, ARCHIVE_NAME),
        "--print", "after_move:filepath",
    ]
    last_err = ""
    for fmt in FORMATS:
        cmd = base_cmd + ["-f", fmt, target]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            paths = [ln.strip() for ln in result.stdout.splitlines() if os.path.isfile(ln.strip())]
            if not paths:
                raise RuntimeError(
                    f"no file produced for: {label}\n"
                    "  (already downloaded before? see source/yt/)"
                )
            path = paths[0]
            m = None
            import re
            for candidate in paths:
                m = re.search(r"\[([\w-]{11})\]\.\w+$", os.path.basename(candidate))
                if m:
                    break
            vid = m.group(1) if m else ""
            print(f"  saved: {path}")
            return (path, vid) if capture else (None, "")
        last_err = result.stderr[-2000:]
        if fmt != FORMATS[-1]:
            print(f"  format '{fmt}' failed, retrying with fallback '{FORMATS[-1]}'...")
    sys.stderr.write(last_err)
    if capture:
        raise RuntimeError(f"yt-dlp failed for: {label}")
    sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description="Download YouTube videos into source/yt/.")
    parser.add_argument("queries", nargs="*", help="YouTube URLs or search terms")
    parser.add_argument("--file", help="file with one query or URL per line (# = comment)")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"output dir (default: {DEFAULT_OUT})")
    parser.add_argument("--list", action="store_true", help="list top 5 search candidates, no download")
    args = parser.parse_args()

    items = list(args.queries)
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            items += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not items:
        parser.error("provide queries/URLs or --file")

    os.makedirs(args.out, exist_ok=True)
    if args.list:
        for item in items:
            list_candidates(item)
    else:
        for item in items:
            download(item, args.out)
        print("\nAll done!")


if __name__ == "__main__":
    main()
