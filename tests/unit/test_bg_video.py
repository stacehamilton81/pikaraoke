"""Tests for the idle-screen YouTube background video rotation."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def karaoke_with_socketio(mock_karaoke):
    """Create a MockKaraoke instance with mocked SocketIO."""
    mock_karaoke.socketio = MagicMock()
    return mock_karaoke


class TestPickNextBgVideo:
    def test_picks_and_broadcasts_a_song_with_youtube_id(self, karaoke_with_socketio):
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

        k.pick_next_bg_video()

        assert k.current_bg_video == {
            "file_path": "/songs/Song---dQw4w9WgXcQ.mp4",
            "youtube_id": "dQw4w9WgXcQ",
            "title": "Song",
        }
        k.socketio.emit.assert_called_once_with(
            "bg_video_changed", k.current_bg_video, namespace="/"
        )

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

    def test_song_ended_picks_a_new_bg_video(self, karaoke_with_socketio):
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
        k.socketio.emit.reset_mock()

        k.events.emit("song_ended")

        assert k.current_bg_video["youtube_id"] == "xYz1234AbCd"
        k.socketio.emit.assert_called_once_with(
            "bg_video_changed", k.current_bg_video, namespace="/"
        )
