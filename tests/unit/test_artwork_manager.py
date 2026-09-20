"""Unit tests for artwork_manager module."""

import os
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.artwork_manager import ArtworkManager, find_artwork_url
from pikaraoke.lib.karaoke_database import KaraokeDatabase


@pytest.fixture(autouse=True)
def _no_rate_limit_sleep():
    """Skip the real inter-request sleep so tests run fast."""
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


def _itunes_response(artwork_url="https://example.com/art/100x100bb.jpg"):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"results": [{"artworkUrl100": artwork_url}]}
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
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"results": []}
        mock_get.return_value = response
        assert find_artwork_url("nonexistent song") is None

    @patch("requests.get")
    def test_returns_none_on_request_error(self, mock_get):
        import requests

        mock_get.side_effect = requests.exceptions.ConnectionError("boom")
        assert find_artwork_url("Queen Bohemian Rhapsody") is None

    def test_returns_none_for_blank_query(self):
        assert find_artwork_url("   ") is None


class TestArtworkManagerFetchForSong:
    @patch("requests.get")
    def test_caches_artwork_on_success(self, mock_get, artwork_manager, db, song_manager):
        db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
        image_response = MagicMock()
        image_response.raise_for_status = MagicMock()
        image_response.content = b"fake-jpeg-bytes"
        mock_get.side_effect = [_itunes_response(), image_response]

        artwork_manager.fetch_for_song("/songs/a.mp4")

        cached = db.get_artwork_paths(["/songs/a.mp4"])
        assert "/songs/a.mp4" in cached
        filename = cached["/songs/a.mp4"]
        assert os.path.exists(artwork_manager.artwork_file_path(filename))

    @patch("requests.get")
    def test_marks_not_found_when_itunes_has_no_match(self, mock_get, artwork_manager, db):
        db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"results": []}
        mock_get.return_value = response

        artwork_manager.fetch_for_song("/songs/a.mp4")

        assert db.get_artwork_paths(["/songs/a.mp4"]) == {}
        assert db.count_songs_needing_artwork() == 0

    @patch("requests.get")
    def test_marks_error_when_image_download_fails(self, mock_get, artwork_manager, db):
        import requests

        db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
        mock_get.side_effect = [_itunes_response(), requests.exceptions.ConnectionError("boom")]

        artwork_manager.fetch_for_song("/songs/a.mp4")

        assert db.get_artwork_paths(["/songs/a.mp4"]) == {}
        assert db.count_songs_needing_artwork() == 0


class TestArtworkManagerBackfill:
    @patch("requests.get")
    def test_backfill_processes_all_pending_songs(self, mock_get, artwork_manager, db):
        db.insert_songs(
            [
                {"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/songs/b.mp4", "youtube_id": None, "format": "mp4"},
            ]
        )
        image_response = MagicMock()
        image_response.raise_for_status = MagicMock()
        image_response.content = b"fake-jpeg-bytes"
        mock_get.side_effect = [
            _itunes_response(),
            image_response,
            _itunes_response(),
            image_response,
        ]

        result = artwork_manager.backfill()

        assert result == {"checked": 2, "found": 2}
        assert db.count_songs_needing_artwork() == 0

    @patch("requests.get")
    def test_backfill_reports_progress(self, mock_get, artwork_manager, db):
        db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
        image_response = MagicMock()
        image_response.raise_for_status = MagicMock()
        image_response.content = b"fake-jpeg-bytes"
        mock_get.side_effect = [_itunes_response(), image_response]

        progress_calls = []
        artwork_manager.backfill(
            progress_callback=lambda done, total: progress_calls.append((done, total))
        )

        assert progress_calls == [(1, 1)]

    def test_backfill_with_nothing_pending(self, artwork_manager):
        assert artwork_manager.backfill() == {"checked": 0, "found": 0}
