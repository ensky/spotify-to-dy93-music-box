#!/usr/bin/env python3
"""Fetch public Spotify playlists (no login needed).

Public playlists are readable from Spotify's embed page; the track list is
embedded in the page's __NEXT_DATA__ JSON. Works for playlists only.

Usage:
  python3 spotify.py "https://open.spotify.com/playlist/xxxx?si=..."   # print tracks
  python3 spotify.py <playlist_id>                                     # same

Module use:
  import spotify
  entity = spotify.fetch("<url or id>")   # {title, owner, tracks: [Track]}
  Track = dict(title, artist, duration_ms, uri)
"""

import html
import json
import re
import subprocess

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def extract_id(text):
    """Accept a full playlist URL (with tracking params) or a bare id."""
    m = re.search(r"playlist/([A-Za-z0-9]+)", text or "")
    if m:
        return m.group(1)
    text = (text or "").strip()
    return text if re.fullmatch(r"[A-Za-z0-9]+", text) else None


def fetch(url_or_id):
    """Return {id, url, title, owner, tracks} for a public playlist."""
    pid = extract_id(url_or_id)
    if not pid:
        raise ValueError(f"not a Spotify playlist id/URL: {url_or_id!r}")
    page = subprocess.run(
        ["curl", "-sL", "--fail", "-A", UA, f"https://open.spotify.com/embed/playlist/{pid}"],
        capture_output=True, text=True,
    )
    if page.returncode != 0 or "__NEXT_DATA__" not in page.stdout:
        raise RuntimeError(f"could not fetch Spotify playlist {pid} (private or blocked?)")
    data = json.loads(html.unescape(
        re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  page.stdout, re.DOTALL).group(1)))
    entity = data["props"]["pageProps"]["state"]["data"]["entity"]
    tracks = [
        {
            "title": t["title"],
            "artist": t["subtitle"],
            "duration_ms": t.get("duration", 0),
            "uri": t["uri"],
        }
        for t in entity.get("trackList", [])
    ]
    if not tracks:
        raise RuntimeError(f"playlist {entity.get('title', pid)} has no public tracks")
    return {
        "id": pid,
        "url": f"https://open.spotify.com/playlist/{pid}",
        "title": entity.get("title", ""),
        "owner": entity.get("subtitle", ""),
        "tracks": tracks,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    e = fetch(sys.argv[1])
    print(f"{e['title']} — {e['owner']} ({len(e['tracks'])} tracks) {e['url']}")
    for i, t in enumerate(e["tracks"], 1):
        mm, ss = divmod(t["duration_ms"] // 1000, 60)
        print(f"  {i:02d}  {t['title']}  —  {t['artist']}  ({mm}:{ss:02d})  {t['uri']}")
