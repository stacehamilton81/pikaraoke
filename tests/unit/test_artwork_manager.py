"""Unit tests for artwork_manager module."""

import os
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.artwork_manager import (
    ArtworkBlocked,
    ArtworkLookupFailed,
    ArtworkManager,
    find_artwork_url,
)
from pikaraoke.lib.karaoke_database import KaraokeDatabase


@pytest.fixture(autouse=True)
def _no_rate_limit_sleep():
    """Skip the real inter-request/backoff sleeps so tests run fast."""
    with patch("pikaraoke.lib.artwork_manager.time.sleep"):
        yield


@pytest.fixture
def db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "test.db"))
    yield d
    d.close()


@pytest.fixture
def song_manager():
    """A minimal stand-in exposing only what ArtworkManager calls."""
    manager = MagicMock()
    manager.display_name_from_path.return_value = "Bohemian Rhapsody - Queen"
    return manager


@pytest.fixture
def artwork_manager(db, song_manager, tmp_path):
    return ArtworkManager(db, song_manager, str(tmp_path / "data"))


def _response(status_code=200, json_data=None):
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    if json_data is not None:
        response.json.return_value = json_data
    return response


def _itunes_response(artwork_url="https://example.com/art/100x100bb.jpg"):
    return _response(json_data={"results": [{"artworkUrl100": artwork_url}]})


def _image_response(content=b"fake-jpeg-bytes"):
    response = _response()
    response.content = content
    return response


class TestFindArtworkUrl:
    @patch("requests.get")
    def test_returns_upsized_artwork_url(self, mock_get):
        mock_get.return_value = _itunes_response()
        assert (
            find_artwork_url("Queen Bohemian Rhapsody") == "https://example.com/art/600x600bb.jpg"
        )

    @patch("requests.get")
    def test_returns_none_on_empty_results(self, mock_get):
        mock_get.return_value = _response(json_data={"results": []})
        assert find_artwork_url("nonexistent song") is None

    @patch("requests.get")
    def test_raises_lookup_failed_on_request_error(self, mock_get):
        import requests

        mock_get.side_effect = requests.exceptions.ConnectionError("boom")
        with pytest.raises(ArtworkLookupFailed):
            find_artwork_url("Queen Bohemian Rhapsody")

    @pytest.mark.parametrize("status_code", [403, 429])
    @patch("requests.get")
    def test_raises_blocked_on_403_or_429(self, mock_get, status_code):
        mock_get.return_value = _response(status_code=status_code)
        with pytest.raises(ArtworkBlocked):
            find_artwork_url("Queen Bohemian Rhapsody")

    def test_returns_none_for_blank_query(self):
        assert find_artwork_url("   ") is None


class TestArtworkManagerFetchForSong:
    @patch("requests.get")
    def test_caches_artwork_on_success(self, mock_get, artwork_manager, db):
        db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
        mock_get.side_effect = [_itunes_response(), _image_response()]

        artwork_manager.fetch_for_song("/songs/a.mp4")

        cached = db.get_artwork_paths(["/songs/a.mp4"])
        assert "/songs/a.mp4" in cached
        filename = cached["/songs/a.mp4"]
        assert os.path.exists(artwork_manager.artwork_file_path(filename))

    @patch("requests.get")
    def test_marks_not_found_when_itunes_has_no_match(self, mock_get, artwork_manager, db):
        db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
        mock_get.return_value = _response(json_data={"results": []})

        artwork_manager.fetch_for_song("/songs/a.mp4")

        assert db.get_artwork_paths(["/songs/a.mp4"]) == {}
        assert db.count_songs_needing_artwork() == 0

    @patch("requests.get")
    def test_leaves_song_pending_when_blocked(self, mock_get, artwork_manager, db):
        """A 429/403 must NOT be recorded as 'not found' -- it should stay
        pending so a later run retries it instead of silently losing artwork."""
        db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
        mock_get.return_value = _response(status_code=429)

        artwork_manager.fetch_for_song("/songs/a.mp4")

        assert db.get_artwork_paths(["/songs/a.mp4"]) == {}
        assert db.count_songs_needing_artwork() == 1

    @patch("requests.get")
    def test_leaves_song_pending_on_download_failure(self, mock_get, artwork_manager, db):
        import requests

        db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
        mock_get.side_effect = [_itunes_response(), requests.exceptions.ConnectionError("boom")]

        artwork_manager.fetch_for_song("/songs/a.mp4")

        assert db.get_artwork_paths(["/songs/a.mp4"]) == {}
        assert db.count_songs_needing_artwork() == 1


class TestArtworkManagerBackfill:
    @patch("requests.get")
    def test_backfill_processes_all_pending_songs(self, mock_get, artwork_manager, db):
        db.insert_songs(
            [
                {"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/songs/b.mp4", "youtube_id": None, "format": "mp4"},
            ]
        )
        mock_get.side_effect = [
            _itunes_response(),
            _image_response(),
            _itunes_response(),
            _image_response(),
        ]

        result = artwork_manager.backfill()

        assert result == {"checked": 2, "found": 2, "blocked": False}
        assert db.count_songs_needing_artwork() == 0

    @patch("requests.get")
    def test_backfill_reports_progress(self, mock_get, artwork_manager, db):
        db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
        mock_get.side_effect = [_itunes_response(), _image_response()]

        progress_calls = []
        artwork_manager.backfill(
            progress_callback=lambda done, total: progress_calls.append((done, total))
        )

        assert progress_calls == [(1, 1)]

    def test_backfill_with_nothing_pending(self, artwork_manager):
        assert artwork_manager.backfill() == {"checked": 0, "found": 0, "blocked": False}

    @patch("requests.get")
    def test_backfill_retries_the_same_song_after_a_block(self, mock_get, artwork_manager, db):
        """One transient 429 shouldn't lose the song -- it should be retried
        and succeed once the API stops blocking us."""
        db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
        mock_get.side_effect = [
            _response(status_code=429),
            _itunes_response(),
            _image_response(),
        ]

        result = artwork_manager.backfill()

        assert result == {"checked": 1, "found": 1, "blocked": False}

    @patch("requests.get")
    def test_backfill_stops_early_after_repeated_blocks(self, mock_get, artwork_manager, db):
        db.insert_songs(
            [
                {"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/songs/b.mp4", "youtube_id": None, "format": "mp4"},
            ]
        )
        mock_get.return_value = _response(status_code=403)

        result = artwork_manager.backfill()

        assert result["blocked"] is True
        assert result["found"] == 0
        # Neither song was ever marked -- both remain pending for the next run.
        assert db.count_songs_needing_artwork() == 2

    @patch("requests.get")
    def test_backfill_skips_song_on_non_blocking_failure(self, mock_get, artwork_manager, db):
        import requests

        db.insert_songs(
            [
                {"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/songs/b.mp4", "youtube_id": None, "format": "mp4"},
            ]
        )
        mock_get.side_effect = [
            requests.exceptions.ConnectionError("boom"),
            _itunes_response(),
            _image_response(),
        ]

        result = artwork_manager.backfill()

        # A single non-blocking failure doesn't stop the run -- it moves on
        # to the next song, leaving the failed one pending for a later retry.
        assert result["found"] == 1
        assert result["blocked"] is False
        assert db.count_songs_needing_artwork() == 1
