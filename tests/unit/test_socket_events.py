"""Tests for Socket.IO connection handling in socket_events.py."""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask_socketio import SocketIO

from pikaraoke.routes.socket_events import setup_socket_events


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    return app


@pytest.fixture
def socketio(app):
    sio = SocketIO(app, async_mode="threading")
    setup_socket_events(sio)
    return sio


class TestUserRoomJoin:
    """singer_up_next targets a per-user room; verify clients actually join it."""

    def test_connect_joins_user_room_when_identified(self, app, socketio):
        flask_client = app.test_client()
        flask_client.set_cookie("pikaraoke_user_id", "5")
        client = socketio.test_client(app, flask_test_client=flask_client)

        socketio.emit("singer_up_next", {"title": "Test"}, room="user_5", namespace="/")

        received = client.get_received()
        assert any(e["name"] == "singer_up_next" for e in received)

    def test_connect_does_not_join_room_when_unidentified(self, app, socketio):
        flask_client = app.test_client()
        client = socketio.test_client(app, flask_test_client=flask_client)

        socketio.emit("singer_up_next", {"title": "Test"}, room="user_5", namespace="/")

        received = client.get_received()
        assert received == []

    def test_identify_joins_room_after_connect(self, app, socketio):
        """First-time picker flow: identity cookie is set only after the socket connects."""
        flask_client = app.test_client()
        flask_client.set_cookie("pikaraoke_user_id", "9")
        client = socketio.test_client(app, flask_test_client=flask_client)

        client.emit("identify")
        socketio.emit("singer_up_next", {"title": "Test"}, room="user_9", namespace="/")

        received = client.get_received()
        assert any(e["name"] == "singer_up_next" for e in received)


class TestBgVideoEnded:
    """Only the master splash screen may advance the idle background rotation."""

    @patch("pikaraoke.routes.socket_events.get_karaoke_instance")
    def test_master_splash_advances_rotation(self, mock_get_instance, app, socketio):
        mock_karaoke = MagicMock()
        mock_get_instance.return_value = mock_karaoke
        client = socketio.test_client(app, flask_test_client=app.test_client())

        client.emit("register_splash")  # first connection -> elected master
        client.emit("bg_video_ended")

        mock_karaoke.pick_next_bg_video.assert_called_once()

    @patch("pikaraoke.routes.socket_events.get_karaoke_instance")
    def test_slave_splash_does_not_advance_rotation(self, mock_get_instance, app, socketio):
        mock_karaoke = MagicMock()
        mock_get_instance.return_value = mock_karaoke
        master = socketio.test_client(app, flask_test_client=app.test_client())
        master.emit("register_splash")
        slave = socketio.test_client(app, flask_test_client=app.test_client())
        slave.emit("register_splash")  # second connection -> elected slave

        slave.emit("bg_video_ended")

        mock_karaoke.pick_next_bg_video.assert_not_called()
