#!/usr/bin/env python3
"""Run the full pipeline: Spotify playlists -> sync/ folders -> SD card.

Reads sync/playlists.tsv (folder -> playlist mapping), fetches each public
playlist, and makes sync/<folder>/ match it EXACTLY: track order = file
number (001 = first track), tracks removed from the playlist are removed
from sync/. Songs are sourced from YouTube (yt.py + mp3.py, 192k CBR
44.1 kHz); existing songs are reused through sync/sources.tsv and
sync/title_aliases.tsv. The number <-> Spotify mapping is recorded in
sync/sources.tsv (spotify_uri column).

Usage:
  python3 pipeline.py --dry-run    # print playlists and plan, do nothing
  python3 pipeline.py              # reconcile + push if card is mounted
  python3 pipeline.py --no-push    # reconcile sync/ only
"""

import argparse
import os
import re
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import mp3  # noqa: E402
import spotify  # noqa: E402
import sync  # noqa: E402
import yt  # noqa: E402

PLAYLISTS = os.path.join(sync.SYNC_DIR, "playlists.tsv")
ALIASES = os.path.join(sync.SYNC_DIR, "title_aliases.tsv")


def norm(text):
    """Normalize a playlist title for alias lookup: lowercase, drop non-word chars."""
    return re.sub(r"[^\w]+", "", text.lower(), flags=re.UNICODE)


def load_playlists():
    rows = []
    with open(PLAYLISTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                folder, pid, name = (line.split("\t") + ["", ""])[:3]
                rows.append((folder, pid, name))
    return rows


def load_aliases():
    """Return {normalized playlist title: library label}."""
    mapping = {}
    if os.path.isfile(ALIASES):
        with open(ALIASES, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    key, label = line.split("\t")[:2]
                    mapping[norm(key)] = label
    return mapping


def reconcile_folder(folder_num, tracks, aliases):
    """Make sync/<folder>/ match the playlist exactly (order included)."""
    table = sync.ledger_load()
    folder = f"{folder_num:02d}"
    folder_dir = os.path.join(sync.SYNC_DIR, folder)
    os.makedirs(folder_dir, exist_ok=True)

    staged = []
    for pos, t in enumerate(tracks, 1):
        title, artist, uri = t["title"], t["artist"], t["uri"]
        key = norm(title)
        label = aliases.get(key) or mp3.sanitize_label(title)

        # find an existing ledger row: prefer Spotify uri, else same folder + label
        row = next((r for r in table.values() if r.get("spotify_uri") == uri), None)
        if row is None:
            row = next(
                (r for rel, r in table.items()
                 if rel.split(os.sep)[0] == folder
                 and sync.FILE_RE.match(os.path.basename(rel))
                 and norm(sync.FILE_RE.match(os.path.basename(rel)).group(2)) == norm(label)),
                None,
            )
        yt_id = row.get("yt_id") if row and row.get("yt_id", "-") != "-" else ""
        yt_title = row.get("yt_title", "-") if row else "-"

        lib = sync.library_path_for(label)
        if not os.path.isfile(lib):
            print(f"  [{folder} #{pos:02d}] new song: {title} — {artist}")
            if yt_id:
                video, _ = yt.download(f"https://www.youtube.com/watch?v={yt_id}", capture=True)
            else:
                video, new_id = yt.download(f"{title} {artist}", capture=True)
                yt_id = new_id or yt_id
            yt_title = mp3.output_stem(video)
            lib = sync.convert_to_library(video, label)
        else:
            print(f"  [{folder} #{pos:02d}] reuse library: {label}.mp3")

        rel = f"{folder}/{pos:03d}_{mp3.sanitize_label(label)}.mp3"
        dest = os.path.join(sync.SYNC_DIR, rel)
        if not os.path.isfile(dest):
            shutil.copyfile(lib, dest)
            sync.retag(dest, f"{pos:03d}")
            print(f"  staged: {rel}")

        yt_url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else "-"
        sync.ledger_update(rel, yt_id=yt_id or "-", yt_url=yt_url, yt_title=yt_title or "-",
                           spotify_uri=uri)
        if row and row.get("card_path") and row["card_path"] != rel:
            sync.ledger_drop(row["card_path"])  # song moved to a new slot
        staged.append(rel)

    # remove anything in this folder that the playlist no longer has
    for rel, _row in list(table.items()):
        if rel.split(os.sep)[0] == folder and rel not in staged:
            p = os.path.join(sync.SYNC_DIR, rel)
            if os.path.isfile(p):
                os.remove(p)
            sync.ledger_drop(rel)
            print(f"  removed (not in playlist): {rel}")


def main():
    ap = argparse.ArgumentParser(description="Sync Spotify playlists to SD card folders.")
    ap.add_argument("--no-push", action="store_true", help="reconcile sync/ only")
    ap.add_argument("--dry-run", action="store_true", help="print playlists and plan, do nothing")
    ap.add_argument("--card", default=sync.DEFAULT_CARD)
    args = ap.parse_args()

    mapped = load_playlists()
    if not mapped:
        sys.exit(f"ERROR: no folder->playlist mapping in {PLAYLISTS}")
    aliases = load_aliases()

    for folder_num, pid, name in mapped:
        entity = spotify.fetch(pid)
        print(f"\n=== folder {folder_num}: {entity['title']} "
              f"({len(entity['tracks'])} tracks) [{name}] ===")
        if args.dry_run:
            for i, t in enumerate(entity["tracks"], 1):
                print(f"  {i:02d}  {t['title']}  —  {t['artist']}  ({t['uri']})")
            continue
        reconcile_folder(int(folder_num), entity["tracks"], aliases)
    sync.clean_sync()

    if args.dry_run:
        return
    if args.no_push:
        print("\n  sync/ updated (--no-push). Run: python3 sync.py push")
        return
    if os.path.isdir(args.card):
        sync.push(args.card)
        ok = sync.check(args.card, quiet=True)
        sys.exit(0 if ok else 1)
    else:
        print(f"\n  card not mounted ({args.card}) — sync/ is ready."
              f"\n  Plug in the card, then run: python3 sync.py push")
        sys.exit(1)


if __name__ == "__main__":
    main()
