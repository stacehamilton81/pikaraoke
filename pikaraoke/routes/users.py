"""User management and favorites routes."""

from __future__ import annotations

import json

import flask_babel
from flask import render_template, request
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.current_app import (
    get_current_user_id,
    get_karaoke_instance,
    get_site_name,
)

_ = flask_babel.gettext

users_bp = Blueprint("users", __name__)


class CreateUserForm(Schema):
    username = fields.String(required=True, metadata={"description": "Display name for the user"})


@users_bp.route("/users", methods=["GET"])
def get_users():
    """List all users."""
    k = get_karaoke_instance()
    return json.dumps(k.db.get_users())


@users_bp.route("/users", methods=["POST"])
@users_bp.arguments(CreateUserForm, location="json")
def create_user(body):
    """Create or return an existing user by username."""
    username = body["username"].strip()
    if not username:
        return json.dumps({"error": "Username cannot be empty"}), 400
    k = get_karaoke_instance()
    user = k.db.create_user(username)
    return json.dumps(user), 201


@users_bp.route("/favorites", methods=["GET"])
def favorites_page():
    """My Favorites page."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    user_id = get_current_user_id()
    user = k.db.get_user_by_id(user_id) if user_id else None
    favorites: list[str] = []
    if user_id:
        favorites = k.db.get_favorites(user_id)
    return render_template(
        "favorites.html",
        site_title=site_name,
        title=_("Favorites"),
        user=user,
        favorites=favorites,
    )


@users_bp.route("/api/favorites", methods=["GET"])
def get_favorites():
    """Return the current user's favorited file paths as JSON."""
    k = get_karaoke_instance()
    user_id = get_current_user_id()
    if not user_id:
        return json.dumps([])
    return json.dumps(k.db.get_favorites(user_id))


@users_bp.route("/api/favorites", methods=["POST"])
def add_favorite():
    """Add a song to the current user's favorites."""
    k = get_karaoke_instance()
    user_id = get_current_user_id()
    if not user_id:
        return json.dumps({"success": False, "error": "No user identified"}), 401
    data = request.get_json(silent=True) or {}
    file_path = data.get("file_path", "")
    if not file_path:
        return json.dumps({"success": False, "error": "file_path required"}), 400
    ok = k.db.add_favorite(user_id, file_path)
    return json.dumps({"success": ok})


@users_bp.route("/api/favorites", methods=["DELETE"])
def remove_favorite():
    """Remove a song from the current user's favorites."""
    k = get_karaoke_instance()
    user_id = get_current_user_id()
    if not user_id:
        return json.dumps({"success": False, "error": "No user identified"}), 401
    data = request.get_json(silent=True) or {}
    file_path = data.get("file_path", "")
    if not file_path:
        return json.dumps({"success": False, "error": "file_path required"}), 400
    ok = k.db.remove_favorite(user_id, file_path)
    return json.dumps({"success": ok})
