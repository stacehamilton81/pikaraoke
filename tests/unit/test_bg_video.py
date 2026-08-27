"""Tests for the idle-screen YouTube background video rotation."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def karaoke_with_socketio(mock_karaoke):
    """Create a MockKaraoke instance with mocked SocketIO."""
    mock_karaoke.socketio = MagicMock()
    return mock_karaoke


class TestFindOfficialVideoId:
    """Tests for _find_official_video_id: prefer a real official video over
    whatever karaoke/cover version the library file itself was downloaded from."""

    @patch("pikaraoke.karaoke.get_search_results")
    def test_returns_first_non_karaoke_result(self, mock_search, karaoke_with_socketio):
        mock_search.return_value = [
            ["Song Title (Karaoke Version)", "url1", "karaoke_id", "Channel", "3:00"],
            ["Song Title (Official Video)", "url2", "official_id", "Artist", "3:15"],
        ]

        result = karaoke_with_socketio._find_official_video_id("Song Title (Karaoke Version)")

        assert result == "official_id"

    @patch("pikaraoke.karaoke.get_search_results")
    def test_filters_covers_and_instrumentals(self, mock_search, karaoke_with_socketio):
        mock_search.return_value = [
            ["Song Title (Instrumental)", "url1", "id1", "Channel", "3:00"],
            ["Song Title (Cover)", "url2", "id2", "Channel", "3:00"],
            ["Song Title (Tribute Band)", "url3", "id3", "Channel", "3:00"],
            ["Song Title", "url4", "clean_id", "Artist", "3:15"],
        ]

        result = karaoke_with_socketio._find_official_video_id("Song Title")

        assert result == "clean_id"

    @patch("pikaraoke.karaoke.get_search_results")
    def test_returns_none_when_every_result_is_karaoke(self, mock_search, karaoke_with_socketio):
        mock_search.return_value = [
            ["Song Title (Karaoke)", "url1", "id1", "Channel", "3:00"],
            ["Song Title (Karaoke Instrumental)", "url2", "id2", "Channel", "3:00"],
        ]

        result = karaoke_with_socketio._find_official_video_id("Song Title")

        assert result is None

    @patch("pikaraoke.karaoke.get_search_results")
    def test_returns_none_on_search_failure(self, mock_search, karaoke_with_socketio):
        mock_search.side_effect = subprocess.CalledProcessError(1, "yt-dlp")

        result = karaoke_with_socketio._find_official_video_id("Song Title")

        assert result is None

    def test_returns_none_for_empty_query(self, karaoke_with_socketio):
        """A title that's pure noise words strips down to nothing worth searching."""
        result = karaoke_with_socketio._find_official_video_id("(Karaoke Version)")

        assert result is None


class TestPickNextBgVideo:
    @patch("pikaraoke.karaoke.get_search_results")
    def test_uses_official_video_id_when_found(self, mock_search, karaoke_with_socketio):
        k = karaoke_with_socketio
        k.db.insert_songs(
            [
                {
                    "file_path": "/songs/Song---dQw4w9WgXcQ.mp4",
                    "youtube_id": "dQw4w9WgXcQ",
                    "format": "mp4",
                }
            ]
        )
        mock_search.return_value = [["Song (Official Video)", "url", "official_id", "A", "3:00"]]

        k.pick_next_bg_video()

        assert k.current_bg_video == {
            "file_path": "/songs/Song---dQw4w9WgXcQ.mp4",
            "youtube_id": "official_id",
            "title": "Song",
        }
        k.socketio.emit.assert_called_once_with(
            "bg_video_changed", k.current_bg_video, namespace="/"
        )

    @patch("pikaraoke.karaoke.get_search_results")
    def test_falls_back_to_library_video_id_when_search_finds_nothing(
        self, mock_search, karaoke_with_socketio
    ):
        k = karaoke_with_socketio
        k.db.insert_songs(
            [
                {
                    "file_path": "/songs/Song---dQw4w9WgXcQ.mp4",
                    "youtube_id": "dQw4w9WgXcQ",
                    "format": "mp4",
                }
            ]
        )
        mock_search.return_value = [["Song (Karaoke)", "url", "karaoke_id", "A", "3:00"]]

        k.pick_next_bg_video()

        assert k.current_bg_video["youtube_id"] == "dQw4w9WgXcQ"

    def test_broadcasts_none_when_library_has_no_eligible_songs(self, karaoke_with_socketio):
        k = karaoke_with_socketio
        k.current_bg_video = {"file_path": "/stale.mp4", "youtube_id": "x", "title": "Stale"}

        k.pick_next_bg_video()

        assert k.current_bg_video is None
        k.socketio.emit.assert_called_once_with("bg_video_changed", None, namespace="/")


class TestBgVideoPlaybackHooks:
    def test_playback_started_clears_bg_video(self, karaoke_with_socketio):
        k = karaoke_with_socketio
        k.current_bg_video = {"file_path": "/songs/a.mp4", "youtube_id": "abc", "title": "A"}
        k.socketio.emit.reset_mock()

        k.events.emit("playback_started")

        assert k.current_bg_video is None
        k.socketio.emit.assert_called_once_with("bg_video_changed", None, namespace="/")

    def test_set_display_active_true_broadcasts(self, karaoke_with_socketio):
        k = karaoke_with_socketio
        k.display_active = False
        k.socketio.emit.reset_mock()

        k.set_display_active(True)

        assert k.display_active is True
        k.socketio.emit.assert_called_once_with("display_active_changed", True, namespace="/")

    def test_set_display_active_false_broadcasts(self, karaoke_with_socketio):
        k = karaoke_with_socketio
        k.socketio.emit.reset_mock()

        k.set_display_active(False)

        assert k.display_active is False
        k.socketio.emit.assert_called_once_with("display_active_changed", False, namespace="/")

    @patch("pikaraoke.karaoke.get_search_results")
    def test_song_ended_picks_a_new_bg_video(self, mock_search, karaoke_with_socketio):
        k = karaoke_with_socketio
        k.db.insert_songs(
            [
                {
                    "file_path": "/songs/Next---xYz1234AbCd.mp4",
                    "youtube_id": "xYz1234AbCd",
                    "format": "mp4",
                }
            ]
        )
        mock_search.return_value = []
        k.socketio.emit.reset_mock()

        k.events.emit("song_ended")

        assert k.current_bg_video["youtube_id"] == "xYz1234AbCd"
        k.socketio.emit.assert_called_once_with(
            "bg_video_changed", k.current_bg_video, namespace="/"
        )
