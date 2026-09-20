"""Album artwork lookup via the iTunes Search API, downloaded and cached locally.

Pure library module (no Flask imports) -- callers resolve the cached filename
returned here into a servable URL via routes/images.py.
"""

import logging
import os
import re
import time
import uuid
from collections.abc import Callable

from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.song_manager import SongManager

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ARTWORK_SUBDIR = "artwork"

_REQUEST_TIMEOUT = 5
_RATE_LIMIT_SECONDS = 0.5
_ARTWORK_SIZE = "600x600"
_ARTWORK_URL_RE = re.compile(r"\d+x\d+bb\.(jpg|png)$")

_last_request_time = 0.0


def _rate_limit() -> None:
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _RATE_LIMIT_SECONDS:
        time.sleep(_RATE_LIMIT_SECONDS - elapsed)
    _last_request_time = time.time()


def find_artwork_url(query: str) -> str | None:
    """Look up album artwork via the iTunes Search API.

    Returns a high-resolution artwork image URL, or None if nothing was found
    or the lookup failed.
    """
    query = query.strip()
    if not query:
        return None

    # Imported lazily (not at module level) so `requests` -- and the ssl/urllib3
    # machinery it pulls in -- isn't loaded until after gevent's monkey-patching
    # has run, matching the same lazy-import pattern used for the Last.fm calls.
    import requests

    _rate_limit()
    try:
        response = requests.get(
            ITUNES_SEARCH_URL,
            params={"term": query, "media": "music", "entity": "song", "limit": 1},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except (requests.exceptions.RequestException, ValueError) as e:
        logging.warning(f"iTunes artwork lookup failed for '{query}': {e}")
        return None

    if not results:
        return None
    artwork_url = results[0].get("artworkUrl100")
    if not artwork_url:
        return None
    # iTunes thumbnails are named "<size>x<size>bb.jpg" -- swap in a larger size.
    return _ARTWORK_URL_RE.sub(f"{_ARTWORK_SIZE}bb.\\1", artwork_url)


def _download_image(url: str, dest_path: str) -> bool:
    import requests

    try:
        response = requests.get(url, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.warning(f"Failed to download artwork from {url}: {e}")
        return False
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(response.content)
    return True


class ArtworkManager:
    """Fetches and caches album artwork images for songs, one file per song."""

    def __init__(self, db: KaraokeDatabase, song_manager: SongManager, data_directory: str) -> None:
        self._db = db
        self._song_manager = song_manager
        self.artwork_dir = os.path.join(data_directory, ARTWORK_SUBDIR)

    def artwork_file_path(self, artwork_filename: str) -> str:
        """Return the absolute path to a cached artwork file by its stored filename."""
        return os.path.join(self.artwork_dir, artwork_filename)

    def fetch_for_song(self, file_path: str) -> None:
        """Look up and cache artwork for a single song (called after a new download)."""
        self._fetch_and_store(file_path)

    def backfill(self, progress_callback: Callable[[int, int], None] | None = None) -> dict:
        """Fetch artwork for every song that hasn't been checked yet.

        Args:
            progress_callback: Optional callable(done, total) invoked after each song.

        Returns:
            {"checked": int, "found": int}
        """
        pending = self._db.get_songs_needing_artwork()
        found = 0
        for i, song in enumerate(pending, start=1):
            if self._fetch_and_store(song["file_path"]):
                found += 1
            if progress_callback:
                progress_callback(i, len(pending))
        return {"checked": len(pending), "found": found}

    def _fetch_and_store(self, file_path: str) -> bool:
        query = self._song_manager.display_name_from_path(file_path).replace(" - ", " ")
        artwork_url = find_artwork_url(query)

        if not artwork_url:
            self._db.set_artwork_status(file_path, None, "not_found")
            return False

        filename = f"{uuid.uuid4().hex}.jpg"
        if not _download_image(artwork_url, self.artwork_file_path(filename)):
            self._db.set_artwork_status(file_path, None, "error")
            return False

        self._db.set_artwork_status(file_path, filename, "found")
        return True
