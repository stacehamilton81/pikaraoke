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
# iTunes' documented search API limit is ~20 requests/minute; this stays safely
# under that (864 songs at this rate takes ~50 minutes for a full backfill).
_RATE_LIMIT_SECONDS = 3.5
_ARTWORK_SIZE = "600x600"
_ARTWORK_URL_RE = re.compile(r"\d+x\d+bb\.(jpg|png)$")
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PiKaraoke album art lookup)"}

_last_request_time = 0.0


class ArtworkBlocked(Exception):
    """The API is rate-limiting or temporarily blocking this IP (HTTP 429/403).

    Distinct from "no match found" -- callers should back off and retry later
    rather than treating the song as having no artwork.
    """


class ArtworkLookupFailed(Exception):
    """A lookup or download failed for a reason unrelated to rate limiting
    (network error, timeout, 5xx). Safe to retry later without backing off."""


def _rate_limit() -> None:
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _RATE_LIMIT_SECONDS:
        time.sleep(_RATE_LIMIT_SECONDS - elapsed)
    _last_request_time = time.time()


def find_artwork_url(query: str) -> str | None:
    """Look up album artwork via the iTunes Search API.

    Returns a high-resolution artwork image URL, or None if the lookup
    completed successfully but found no match.

    Raises:
        ArtworkBlocked: the API returned 429/403 -- back off, don't retry yet.
        ArtworkLookupFailed: any other transient failure -- safe to retry.
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
            headers=_HEADERS,
            timeout=_REQUEST_TIMEOUT,
        )
        if response.status_code in (403, 429):
            raise ArtworkBlocked(f"iTunes returned HTTP {response.status_code}")
        response.raise_for_status()
        results = response.json().get("results", [])
    except ArtworkBlocked:
        raise
    except (requests.exceptions.RequestException, ValueError) as e:
        raise ArtworkLookupFailed(str(e)) from e

    if not results:
        return None
    artwork_url = results[0].get("artworkUrl100")
    if not artwork_url:
        return None
    # iTunes thumbnails are named "<size>x<size>bb.jpg" -- swap in a larger size.
    return _ARTWORK_URL_RE.sub(f"{_ARTWORK_SIZE}bb.\\1", artwork_url)


def _download_image(url: str, dest_path: str) -> None:
    """Download an artwork image to dest_path.

    Raises:
        ArtworkBlocked: the host returned 429/403.
        ArtworkLookupFailed: any other download failure.
    """
    import requests

    try:
        response = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        if response.status_code in (403, 429):
            raise ArtworkBlocked(f"Artwork host returned HTTP {response.status_code}")
        response.raise_for_status()
    except ArtworkBlocked:
        raise
    except requests.exceptions.RequestException as e:
        raise ArtworkLookupFailed(str(e)) from e
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(response.content)


class ArtworkManager:
    """Fetches and caches album artwork images for songs, one file per song."""

    def __init__(self, db: KaraokeDatabase, song_manager: SongManager, data_directory: str) -> None:
        self._db = db
        self._song_manager = song_manager
        self.artwork_dir = os.path.join(data_directory, ARTWORK_SUBDIR)

    def artwork_file_path(self, artwork_filename: str) -> str:
        """Return the absolute path to a cached artwork file by its stored filename."""
        return os.path.join(self.artwork_dir, artwork_filename)

    # Consecutive-block backoff: start at 30s, double each time, cap at 5 minutes.
    _INITIAL_BACKOFF_SECONDS = 30
    _MAX_BACKOFF_SECONDS = 300
    _MAX_CONSECUTIVE_BLOCKS = 8

    def fetch_for_song(self, file_path: str) -> None:
        """Look up and cache artwork for a single song (called after a new download).

        Best-effort: if the API is currently blocking us, this leaves the song
        pending rather than retrying inline (the next backfill run will pick it up).
        """
        try:
            self._fetch_and_store(file_path)
        except (ArtworkBlocked, ArtworkLookupFailed) as e:
            logging.warning(f"Artwork lookup deferred for '{file_path}': {e}")

    def backfill(self, progress_callback: Callable[[int, int], None] | None = None) -> dict:
        """Fetch artwork for every song that hasn't been checked yet.

        Backs off and retries (rather than giving up) when the API is
        rate-limiting/blocking us, up to _MAX_CONSECUTIVE_BLOCKS in a row.

        Args:
            progress_callback: Optional callable(done, total) invoked after each song.

        Returns:
            {"checked": int, "found": int, "blocked": bool} -- "blocked" means
            the run stopped early because the API kept refusing us.
        """
        pending = self._db.get_songs_needing_artwork()
        found = 0
        checked = 0
        blocked = False
        backoff = self._INITIAL_BACKOFF_SECONDS
        consecutive_blocks = 0

        i = 0
        while i < len(pending):
            song = pending[i]
            try:
                if self._fetch_and_store(song["file_path"]):
                    found += 1
                consecutive_blocks = 0
                backoff = self._INITIAL_BACKOFF_SECONDS
            except ArtworkLookupFailed as e:
                logging.warning(f"Artwork lookup failed for '{song['file_path']}': {e}")
            except ArtworkBlocked as e:
                consecutive_blocks += 1
                if consecutive_blocks > self._MAX_CONSECUTIVE_BLOCKS:
                    logging.warning(f"Artwork backfill stopping early, still blocked: {e}")
                    blocked = True
                    break
                logging.info(f"Artwork API blocked us ({e}); retrying in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, self._MAX_BACKOFF_SECONDS)
                continue  # retry the same song

            checked += 1
            i += 1
            if progress_callback:
                progress_callback(i, len(pending))

        return {"checked": checked, "found": found, "blocked": blocked}

    def _fetch_and_store(self, file_path: str) -> bool:
        """Returns True if artwork was found and cached, False if genuinely not found.

        Raises ArtworkBlocked or ArtworkLookupFailed on transient failures,
        leaving the song's status untouched so it's retried later.
        """
        query = self._song_manager.display_name_from_path(file_path).replace(" - ", " ")
        artwork_url = find_artwork_url(query)

        if not artwork_url:
            self._db.set_artwork_status(file_path, None, "not_found")
            return False

        filename = f"{uuid.uuid4().hex}.jpg"
        _download_image(artwork_url, self.artwork_file_path(filename))

        self._db.set_artwork_status(file_path, filename, "found")
        return True
