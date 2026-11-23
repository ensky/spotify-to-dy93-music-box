#!/usr/bin/env python3
"""Step 3: manage sync/ (the SD card mirror) and push it to the card.

Layout:
  sync/NN/NNN_label.mp3   exact mirror of what the card should contain;
                          inside each folder files are numbered from 001
  sync/sources.tsv        ledger: for every sync file, its YouTube source

Chip rules (enforced here):
  - folders "01".."99", files inside numbered 001, 002, ... per folder
  - MP3, sample rate <= 44.1 kHz, minimal number-only ID3v1 tags
  - no macOS ghost files (._*, .DS_Store) — dot_clean before ejecting

Subcommands:
  add SRC... [--folder N] [--label X] [--start S] [--dry-run]
                      stage song(s) into sync/. SRC = mp3 file (usually from
                      source/mp3/), YouTube URL, or search term (URL/search
                      go through yt.py + mp3.py automatically).
  push [--card DIR]   make the card an exact mirror of sync/ (copy new or
                      changed files, delete card-only files), then clean
                      ghost files. This is the ONLY thing to do when the
                      card is plugged in.
  check [--card DIR]  verify card: numbering from 001 per folder, audio
                      format, no junk, and content identical to sync/.
  clean [--card DIR]  dot_clean + remove ghost files (run before ejecting).
  list                show the sync/ tree with each file's YouTube source.

After pushing, eject safely:  diskutil eject SDCARD
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import yt  # noqa: E402
import mp3  # noqa: E402

SYNC_DIR = os.path.join(BASE_DIR, "sync")
LEDGER = os.path.join(SYNC_DIR, "sources.tsv")
DEFAULT_CARD = "/Volumes/SDCARD"
MAX_SAMPLE_RATE = mp3.MAX_SAMPLE_RATE
FILE_RE = re.compile(r"^(\d{3})_(.+)\.mp3$")
ID_RE = re.compile(r"\s*\[([\w-]{11})\]\s*$")


# ── generic helpers ──────────────────────────────────────────────────────────

def is_junk(name):
    return name == ".DS_Store" or name.startswith("._")


def iter_files(root):
    for dirpath, _dirs, files in os.walk(root, onerror=lambda e: None):
        for name in sorted(files):
            path = os.path.join(dirpath, name)
            yield path, os.path.relpath(path, root)


def md5sum(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ffprobe_json(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def probe_report(path):
    """Return (ok, message) for one mp3 file."""
    data = ffprobe_json(path)
    if not data:
        return False, "not readable by ffprobe (not an mp3?)"
    streams = data.get("streams", [])
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    art = [s for s in streams if s.get("codec_type") in ("video", "attached_pic")]
    if not audio:
        return False, "no audio stream"
    a = audio[0]
    if a.get("codec_name") != "mp3":
        return False, f"codec is {a.get('codec_name')}, not mp3 (m4a/aac will not play)"
    sample_rate = int(a.get("sample_rate", "0") or 0)
    if sample_rate > MAX_SAMPLE_RATE:
        return False, f"sample rate {sample_rate} Hz > {MAX_SAMPLE_RATE} (chip cannot read)"
    if art:
        return False, "embedded artwork in tags (chip may choke on it)"
    bitrate = int(data.get("format", {}).get("bit_rate", "0") or 0)
    br = f", {bitrate // 1000} kbps" if bitrate else ""
    warn = " (bitrate > 320k, unusual)" if bitrate > 320000 else ""
    return True, f"mp3, {sample_rate} Hz{br}{warn}"


def retag(path, num):
    """Losslessly rewrite the ID3v1 tag so title/track match the number."""
    tmp = path + ".retag.mp3"
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-map", "0:a", "-c", "copy",
         "-map_metadata", "-1", "-id3v2_version", "0", "-write_id3v1", "1",
         "-metadata", f"title={num}", "-metadata", f"track={num}", tmp],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"retag failed: {result.stderr[-300:]}")
    os.replace(tmp, path)


# ── ledger ───────────────────────────────────────────────────────────────────

def ledger_load():
    """Return {card_rel_path: (yt_id, yt_url, yt_title, added)}."""
    table = {}
    if not os.path.isfile(LEDGER):
        return table
    with open(LEDGER, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            while len(parts) < 5:
                parts.append("")
            table[parts[0]] = tuple(parts[1:5])
    return table


def ledger_append(card_rel, yt_id, yt_url, yt_title):
    table = ledger_load()
    table[card_rel] = (yt_id, yt_url, yt_title, datetime.date.today().isoformat())
    lines = [
        "# card_path\tyt_id\tyt_url\tyt_title\tadded",
    ]
    for rel in sorted(table):
        yt_id, yt_url, yt_title, added = table[rel]
        yt_title = str(yt_title).replace("\t", " ").replace("\n", " ")
        lines.append(f"{rel}\t{yt_id}\t{yt_url}\t{yt_title}\t{added}")
    with open(LEDGER, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── sync/ staging ────────────────────────────────────────────────────────────

def sync_mp3s(folder_path):
    out = []
    for name in os.listdir(folder_path):
        m = FILE_RE.match(name)
        if m and os.path.isfile(os.path.join(folder_path, name)):
            out.append((int(m.group(1)), m.group(2), os.path.join(folder_path, name)))
    return sorted(out)


def sync_folders():
    return sorted(
        d for d in os.listdir(SYNC_DIR)
        if re.fullmatch(r"\d{2}", d) and os.path.isdir(os.path.join(SYNC_DIR, d))
    ) if os.path.isdir(SYNC_DIR) else []


def next_folder():
    nums = [int(d) for d in sync_folders()]
    return (max(nums) + 1) if nums else 1


def next_file_num(folder_num):
    folder_path = os.path.join(SYNC_DIR, f"{folder_num:02d}")
    files = sync_mp3s(folder_path) if os.path.isdir(folder_path) else []
    return (max(num for num, _l, _p in files) + 1) if files else 1


def add(sources, folder=None, label=None, start=0.0, dry_run=False):
    if not os.path.isdir(SYNC_DIR):
        os.makedirs(SYNC_DIR, exist_ok=True)
    if dry_run:
        cur = folder if folder is not None else next_folder()
        for src in sources:
            print(f"  (dry-run) would stage at {cur:02d}/: {src}")
            cur += 1
        return

    for src in sources:
        yt_id, yt_url, yt_title = "", "", ""
        if os.path.isfile(src):
            mp3_path = src
        else:
            # treat as YouTube URL / search term: video -> mp3
            print(f"  '{src}' is not a local file — downloading via yt.py + mp3.py")
            video_path, yt_id = yt.download(src, capture=True)
            yt_url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else ""
            yt_title = os.path.splitext(os.path.basename(video_path))[0]
            mp3_path = mp3_convert(video_path, start=start)
        stem = os.path.splitext(os.path.basename(mp3_path))[0]
        song_label = label or ID_RE.sub("", stem)
        yt_title = yt_title or stem

        fnum = folder if folder is not None else next_folder()
        if not 1 <= fnum <= 99:
            sys.exit(f"ERROR: folder must be 01-99, got {fnum}")
        folder_path = os.path.join(SYNC_DIR, f"{fnum:02d}")
        os.makedirs(folder_path, exist_ok=True)
        file_num = next_file_num(fnum)
        dest = os.path.join(folder_path, f"{file_num:03d}_{mp3.sanitize_label(song_label)}.mp3")
        shutil.copyfile(mp3_path, dest)
        retag(dest, f"{file_num:03d}")
        ledger_append(os.path.relpath(dest, SYNC_DIR), yt_id or "-", yt_url or "-", yt_title)
        print(f"  staged: {os.path.relpath(dest, SYNC_DIR)}"
              + (f"  (source: {yt_url})" if yt_url else ""))
        label = None  # explicit label only applies to the first song
    clean_sync()


def mp3_convert(video_path, start=0.0):
    """Convert a downloaded video into source/mp3/ and return the mp3 path."""
    dest = os.path.join(mp3.DEFAULT_OUT, mp3.output_stem(video_path) + ".mp3")
    os.makedirs(mp3.DEFAULT_OUT, exist_ok=True)
    mp3.convert(video_path, dest, title=mp3.sanitize_label(mp3.output_stem(video_path)), start=start)
    print(f"  converted: {dest}")
    return dest


def restage():
    """Re-copy every ledgered song from source/mp3/ into sync/ (after re-encoding etc.)."""
    ledger = ledger_load()
    if not ledger:
        sys.exit("ERROR: ledger is empty")
    missing = []
    for card_rel in sorted(ledger):
        parts = card_rel.split(os.sep)
        label = FILE_RE.match(parts[-1]).group(2)
        lib_path = os.path.join(mp3.DEFAULT_OUT, label + ".mp3")
        if not os.path.isfile(lib_path):
            missing.append(card_rel)
            continue
        dest = os.path.join(SYNC_DIR, card_rel)
        shutil.copyfile(lib_path, dest)
        retag(dest, parts[-1][:3])
        print(f"  restaged: {card_rel}")
    if missing:
        print(f"  MISSING in source/mp3/ (not restaged): {', '.join(missing)}")
    clean_sync()


def clean_sync():
    for path, rel in iter_files(SYNC_DIR):
        if is_junk(os.path.basename(path)):
            os.remove(path)
            print(f"  removed junk from sync/: {rel}")


# ── push / check / clean on the card ─────────────────────────────────────────

def sync_desired():
    """Return {rel_path: abs_path} of mp3 files that the card should contain."""
    desired = {}
    for path, rel in iter_files(SYNC_DIR):
        if os.path.basename(path).lower().endswith(".mp3"):
            desired[rel] = path
    return desired


def push(card):
    if not os.path.isdir(card):
        sys.exit(
            f"ERROR: card not found: {card}\n"
            f"  mounted volumes: {sorted(os.listdir('/Volumes'))}"
        )
    desired = sync_desired()
    if not desired:
        sys.exit(f"ERROR: sync/ has no mp3 files: {SYNC_DIR}")

    # current card mp3 state
    actual = {}
    junk = []
    for path, rel in iter_files(card):
        name = os.path.basename(path)
        if is_junk(name):
            junk.append((path, rel))
        elif name.lower().endswith(".mp3"):
            actual[rel] = path

    added = updated = removed = unchanged = 0
    for rel, src_path in sorted(desired.items()):
        dest_path = os.path.join(card, rel)
        if rel not in actual:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copyfile(src_path, dest_path)
            added += 1
            print(f"  + {rel}")
        elif md5sum(src_path) != md5sum(actual[rel]):
            shutil.copyfile(src_path, dest_path)
            updated += 1
            print(f"  ~ {rel}")
        else:
            unchanged += 1
    for rel in sorted(set(actual) - set(desired)):
        os.remove(actual[rel])
        removed += 1
        print(f"  - {rel}")

    # prune card dirs that sync/ does not have
    for d in os.listdir(card):
        if re.fullmatch(r"\d{2}", d) and not os.path.isdir(os.path.join(SYNC_DIR, d)):
            shutil.rmtree(os.path.join(card, d), ignore_errors=True)
            print(f"  - {d}/ (not in sync/)")

    clean(card)
    print(f"  sync complete: {added} added, {updated} updated, {removed} removed, {unchanged} unchanged")


def check(card, quiet=False):
    """Verify card contents and that they mirror sync/. Returns True if all good."""
    ok = True
    if not os.path.isdir(card):
        print(f"  card not mounted: {card}")
        return False

    folders = sorted(
        d for d in os.listdir(card)
        if re.fullmatch(r"\d{2}", d) and os.path.isdir(os.path.join(card, d))
    )
    for d in folders:
        folder_path = os.path.join(card, d)
        files = sync_mp3s(folder_path)
        nums = [num for num, _l, _p in files]
        if nums != list(range(1, len(nums) + 1)):
            ok = False
            print(f"  BAD NUMBERING {d}/: {['%03d' % n for n in nums]}"
                  f"  <- files must start at 001 and be sequential; fix sync/ then push")
        for num, _label, path in files:
            rel = f"{d}/{os.path.basename(path)}"
            good, msg = probe_report(path)
            if good:
                if not quiet:
                    print(f"  OK    {rel}  ({msg})")
            else:
                ok = False
                print(f"  BAD   {rel}: {msg}")

    # junk / stray files
    for path, rel in iter_files(card):
        name = os.path.basename(path)
        parts = rel.split(os.sep)
        if is_junk(name):
            ok = False
            print(f"  JUNK file  {rel}  <- run clean or push")
        elif name.startswith("."):
            continue
        elif not name.lower().endswith(".mp3"):
            ok = False
            print(f"  UNEXPECTED file  {rel}")
        elif not (len(parts) == 2 and re.fullmatch(r"\d{2}", parts[0]) and FILE_RE.match(name)):
            ok = False
            print(f"  BAD NAME  {rel}  <- must be NN/NNN_label.mp3")

    # mirror comparison against sync/
    desired = sync_desired()
    actual_mp3 = {}
    for path, rel in iter_files(card):
        if os.path.basename(path).lower().endswith(".mp3") and not is_junk(os.path.basename(path)):
            actual_mp3[rel] = path
    for rel in sorted(set(desired) - set(actual_mp3)):
        ok = False
        print(f"  MISSING on card  {rel}  <- run push")
    for rel in sorted(set(actual_mp3) - set(desired)):
        ok = False
        print(f"  EXTRA on card  {rel}  <- run push")
    for rel in sorted(set(desired) & set(actual_mp3)):
        if md5sum(desired[rel]) != md5sum(actual_mp3[rel]):
            ok = False
            print(f"  DIFFERS  {rel}  <- run push")
    if desired:
        ledger = ledger_load()
        for rel in sorted(desired):
            if rel not in ledger:
                ok = False
                print(f"  NO SOURCE RECORDED  {rel}  <- every sync file needs a sources.tsv entry")

    print(f"\n  {'All good!' if ok else 'PROBLEMS FOUND'}")
    return ok


def clean(card):
    subprocess.run(["dot_clean", "-m", card], capture_output=True)
    removed = 0
    for path, _rel in list(iter_files(card)):
        if is_junk(os.path.basename(path)):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    shutil.rmtree(os.path.join(card, ".fseventsd"), ignore_errors=True)
    print(f"  cleaned {removed} junk file(s) from {card}")


def list_sync():
    ledger = ledger_load()
    folders = sync_folders()
    if not folders:
        print(f"  sync/ is empty: {SYNC_DIR}")
        return
    for d in folders:
        print(f"  {d}/")
        for num, label, path in sync_mp3s(os.path.join(SYNC_DIR, d)):
            rel = f"{d}/{os.path.basename(path)}"
            _yt_id, yt_url, yt_title, _added = ledger.get(rel, ("-", "-", "-", "-"))
            source = yt_url if yt_url and yt_url != "-" else yt_title or "-"
            print(f"    {num:03d}_{label}.mp3   <- {source}")


# ── dispatcher ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stage songs into sync/ and push to the SD card.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def card_arg(p):
        p.add_argument("--card", default=DEFAULT_CARD, help=f"card mount point (default: {DEFAULT_CARD})")

    p_add = sub.add_parser("add", help="stage song(s) into sync/")
    p_add.add_argument("sources", nargs="+", help="mp3 file, YouTube URL, or search term")
    p_add.add_argument("--folder", type=int, help="stage into folder 01-99 (default: next free folder)")
    p_add.add_argument("--label", help="override the NNN_<label> label (first song only)")
    p_add.add_argument("--start", type=float, default=0.0, help="trim seconds from the start (intro)")
    p_add.add_argument("--dry-run", action="store_true", help="print the plan, do nothing")

    p_push = sub.add_parser("push", help="make the card an exact mirror of sync/ + clean junk")
    card_arg(p_push)

    p_check = sub.add_parser("check", help="verify the card (numbering, audio, junk, mirror of sync/)")
    card_arg(p_check)

    p_clean = sub.add_parser("clean", help="dot_clean + remove ghost files (before ejecting)")
    card_arg(p_clean)

    p_restage = sub.add_parser("restage", help="re-copy all songs from source/mp3/ into sync/ (after re-encoding)")

    sub.add_parser("list", help="show sync/ tree with YouTube sources")

    args = parser.parse_args()
    if args.cmd == "add":
        add(args.sources, folder=args.folder, label=args.label, start=args.start, dry_run=args.dry_run)
    elif args.cmd == "push":
        push(args.card)
    elif args.cmd == "check":
        sys.exit(0 if check(args.card) else 1)
    elif args.cmd == "clean":
        clean(args.card)
    elif args.cmd == "restage":
        restage()
    elif args.cmd == "list":
        list_sync()


if __name__ == "__main__":
    main()
