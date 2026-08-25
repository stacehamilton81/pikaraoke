"""Socket.IO event handlers for PiKaraoke."""

import logging

from flask import request
from flask_socketio import join_room

from pikaraoke.lib.current_app import get_current_user_id, get_karaoke_instance

# Track connected splash screen clients and the elected master
splash_connections = set()
master_splash_id = None


def setup_socket_events(socketio):
    """Register Socket.IO event handlers.

    Args:
        socketio: The SocketIO instance.
    """

    def _join_user_room() -> None:
        """Join the current client to their user-specific room, if identified.

        Lets singer_up_next notifications target only that user's browser(s).
        """
        user_id = get_current_user_id()
        if user_id:
            join_room(f"user_{user_id}")

    @socketio.on("connect")
    def handle_connect() -> None:
        """Join the connecting client to their user-specific room, if already identified."""
        _join_user_room()

    @socketio.on("identify")
    def handle_identify() -> None:
        """Re-join the user room once identity is set (e.g. first-time picker, after connect)."""
        _join_user_room()

    @socketio.on("end_song")
    def end_song(reason: str) -> None:
        """Handle end_song WebSocket event from client.

        Args:
            reason: Reason for ending the song (e.g., 'complete', 'error').
        """
        k = get_karaoke_instance()
        k.playback_controller.end_song(reason)

    @socketio.on("start_song")
    def start_song() -> None:
        """Handle start_song WebSocket event when playback begins."""
        k = get_karaoke_instance()
        k.playback_controller.start_song()

    @socketio.on("clear_notification")
    def clear_notification() -> None:
        """Handle clear_notification WebSocket event to dismiss notifications."""
        k = get_karaoke_instance()
        k.reset_now_playing_notification()

    @socketio.on("register_splash")
    def register_splash() -> None:
        """Handle splash screen registration and assign master/slave roles."""
        global master_splash_id
        sid = request.sid
        splash_connections.add(sid)
        logging.info(f"Splash screen registered: {sid}")

        if master_splash_id is None:
            master_splash_id = sid
            socketio.emit("splash_role", "master", room=sid)
            logging.info(f"Master splash screens assigned: {sid}")
        else:
            socketio.emit("splash_role", "slave", room=sid)
            logging.info(f"Slave splash screens assigned: {sid}")

    @socketio.on("playback_position")
    def handle_playback_position(position: float) -> None:
        """Handle playback_position WebSocket event from the master splash screen.

        Args:
            position: Current playback position in seconds.
        """
        global master_splash_id
        sid = request.sid
        if sid == master_splash_id:
            k = get_karaoke_instance()
            k.playback_controller.now_playing_position = position
            # Broadcast position to all other splash screens (slaves)
            socketio.emit("playback_position", position, include_self=False)

    @socketio.on("bg_video_ended")
    def handle_bg_video_ended() -> None:
        """Handle the master splash screen's idle-background YouTube video finishing.

        Only the master advances the rotation, mirroring playback_position, so
        multiple splash screens don't race to pick different next videos.
        """
        global master_splash_id
        sid = request.sid
        if sid == master_splash_id:
            k = get_karaoke_instance()
            k.pick_next_bg_video()

    @socketio.on("disconnect")
    def handle_disconnect() -> None:
        """Handle Socket.IO client disconnection and manage splash role handover."""
        global master_splash_id
        sid = request.sid
        if sid in splash_connections:
            splash_connections.remove(sid)
            logging.info(f"Splash screen disconnected: {sid}")
            if sid == master_splash_id:
                master_splash_id = None
                logging.info("Master splash disconnected, electing new master")
                if splash_connections:
                    # Elect new master from remaining connections
                    new_master = next(iter(splash_connections))
                    master_splash_id = new_master
                    socketio.emit("splash_role", "master", room=new_master)
                    logging.info(f"New master splash elected: {new_master}")
