"""Admin web interface: card mapping management, settings, assign mode."""

import os
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from . import __version__, bluetooth, config, db, player as player_mod, reader

MODE_LABELS = {
    "single": "Single file",
    "random1": "Random track from folder",
    "random3": "3 random tracks from folder",
    "stop": "Stop playback",
}


def _safe_resolve(rel_path):
    """Resolve rel_path under music_root; abort(400) on traversal attempts."""
    music_root = Path(db.get_setting("music_root")).resolve()
    resolved = (music_root / rel_path.lstrip("/")).resolve()
    if resolved != music_root and music_root not in resolved.parents:
        abort(400, "Path outside music root")
    return music_root, resolved


def _validate_card_form(form):
    """Returns (card_id, label, mode, target) or raises ValueError."""
    card_id = form.get("card_id", "").strip()
    label = form.get("label", "").strip()
    mode = form.get("mode", "").strip()
    target = form.get("target", "").strip().strip("/")

    if not card_id:
        raise ValueError("Card ID is required.")
    if not card_id.isdigit():
        raise ValueError("Card ID must be digits only (as typed by the reader).")
    if mode not in db.MODES:
        raise ValueError("Invalid play mode.")

    if mode == "stop":
        return card_id, label, mode, ""

    if not target:
        raise ValueError("A media target is required for this mode.")
    _, resolved = _safe_resolve(target)
    if mode == "single" and not resolved.is_file():
        raise ValueError(f"Not a file under the music folder: {target}")
    if mode in ("random1", "random3") and not resolved.is_dir():
        raise ValueError(f"Not a folder under the music folder: {target}")
    return card_id, label, mode, target


def create_app(scanbus, player):
    app = Flask(__name__)
    app.secret_key = os.urandom(16)  # only used for flash messages

    @app.context_processor
    def inject_globals():
        return {"mode_labels": MODE_LABELS, "app_version": __version__}

    # --- pages ---

    @app.get("/")
    def index():
        unknown_scan = scanbus.latest_unknown()
        # The scan was unknown at tap time; hide it once the card gets mapped.
        if unknown_scan and db.get_card(unknown_scan["card_id"]):
            unknown_scan = None
        return render_template(
            "index.html",
            cards=db.list_cards(),
            unknown_scan=unknown_scan,
        )

    @app.route("/cards/new", methods=["GET", "POST"])
    def card_new():
        if request.method == "POST":
            try:
                card_id, label, mode, target = _validate_card_form(request.form)
            except ValueError as exc:
                flash(str(exc), "error")
                return render_template(
                    "card_form.html", card=request.form, is_new=True
                )
            if db.get_card(card_id):
                flash(f"Card {card_id} is already mapped — edit it instead.", "error")
                return render_template(
                    "card_form.html", card=request.form, is_new=True
                )
            db.upsert_card(card_id, label, mode, target)
            flash(f"Card {card_id} mapped.", "ok")
            return redirect(url_for("index"))
        card = {
            "card_id": request.args.get("card_id", ""),
            "label": "",
            "mode": "single",
            "target": "",
        }
        return render_template("card_form.html", card=card, is_new=True)

    @app.route("/cards/<card_id>/edit", methods=["GET", "POST"])
    def card_edit(card_id):
        card = db.get_card(card_id)
        if card is None:
            abort(404)
        if request.method == "POST":
            form = dict(request.form)
            form["card_id"] = card_id  # id is not editable
            try:
                _, label, mode, target = _validate_card_form(form)
            except ValueError as exc:
                flash(str(exc), "error")
                return render_template("card_form.html", card=form, is_new=False)
            db.upsert_card(card_id, label, mode, target)
            flash(f"Card {card_id} updated.", "ok")
            return redirect(url_for("index"))
        return render_template("card_form.html", card=card, is_new=False)

    @app.post("/cards/<card_id>/delete")
    def card_delete(card_id):
        db.delete_card(card_id)
        flash(f"Card {card_id} deleted.", "ok")
        return redirect(url_for("index"))

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            music_root = request.form.get("music_root", "").strip()
            if not os.path.isdir(music_root):
                flash(f"Music folder does not exist: {music_root}", "error")
            else:
                db.set_setting("music_root", music_root)
                db.set_setting("nfc_device", request.form.get("nfc_device", "").strip())
                player_cmd = request.form.get("player_cmd", "").strip()
                db.set_setting("player_cmd", player_cmd or "mpg123 -q")
                db.set_setting("alsa_device", request.form.get("alsa_device", "").strip())
                flash("Settings saved. NFC device changes need a service restart.", "ok")
                return redirect(url_for("settings"))
        return render_template("settings.html", settings=db.get_settings())

    # --- JSON API ---

    @app.get("/api/browse")
    def api_browse():
        rel = request.args.get("path", "")
        music_root, resolved = _safe_resolve(rel)
        if not resolved.is_dir():
            abort(400, "Not a directory")
        dirs, files = [], []
        for entry in sorted(os.listdir(resolved), key=str.lower):
            full = resolved / entry
            rel_entry = str(full.relative_to(music_root))
            if full.is_dir():
                dirs.append({"name": entry, "path": rel_entry})
            elif full.suffix.lower() in config.AUDIO_EXTENSIONS:
                files.append({"name": entry, "path": rel_entry})
        parent = str(resolved.parent.relative_to(music_root)) if resolved != music_root else None
        if parent == ".":
            parent = ""
        return jsonify({
            "path": str(resolved.relative_to(music_root)) if resolved != music_root else "",
            "parent": parent,
            "dirs": dirs,
            "files": files,
        })

    @app.get("/api/last_scan")
    def api_last_scan():
        since = request.args.get("since", type=int, default=0)
        latest = scanbus.latest()
        if latest is None or latest["seq"] <= since:
            return jsonify({"seq": since})
        result = dict(latest)
        if latest["known"]:
            card = db.get_card(latest["card_id"])
            result["label"] = card["label"] if card else ""
        return jsonify(result)

    @app.post("/api/play/<card_id>")
    def api_play(card_id):
        card = db.get_card(card_id)
        if card is None:
            abort(404)
        player_mod.dispatch(card, player)
        return jsonify({"ok": True})

    @app.post("/api/stop")
    def api_stop():
        player.stop()
        return jsonify({"ok": True})

    # --- Bluetooth speaker ---

    @app.get("/api/bt/status")
    def api_bt_status():
        mac = db.get_setting("bt_device")
        status = {
            "mac": mac,
            "name": db.get_setting("bt_device_name"),
            "connected": False,
            "backend": bluetooth.audio_backend(),
        }
        try:
            bluetooth.ensure_available()
            if mac:
                status["connected"] = bluetooth.is_connected(mac)
        except bluetooth.BluetoothUnavailable as exc:
            status["error"] = str(exc)
        return jsonify(status)

    @app.post("/api/bt/scan")
    def api_bt_scan():
        seconds = request.args.get("seconds", type=int, default=10)
        seconds = max(3, min(seconds, 30))
        try:
            devices = bluetooth.scan(seconds)
            payload = {"devices": devices}
            if not devices:
                payload["diagnostics"] = bluetooth.diagnostics()
            return jsonify(payload)
        except bluetooth.BluetoothUnavailable as exc:
            return jsonify({"error": str(exc)}), 503

    @app.post("/api/bt/connect")
    def api_bt_connect():
        mac = (request.form.get("mac") or request.args.get("mac") or "").strip().upper()
        if not bluetooth.MAC_RE.fullmatch(mac):
            abort(400, "Invalid MAC address")
        try:
            ok, message = bluetooth.connect(mac)
        except bluetooth.BluetoothUnavailable as exc:
            return jsonify({"error": str(exc)}), 503
        if not ok:
            return jsonify({"error": message}), 502
        db.set_setting("bt_device", mac)
        db.set_setting("bt_device_name", message)
        return jsonify({"ok": True, "name": message})

    @app.post("/api/bt/forget")
    def api_bt_forget():
        mac = db.get_setting("bt_device")
        if mac:
            try:
                bluetooth.forget(mac)
            except bluetooth.BluetoothUnavailable:
                pass
        db.set_setting("bt_device", "")
        db.set_setting("bt_device_name", "")
        return jsonify({"ok": True})

    if config.DEV_MODE:
        @app.post("/api/simulate")
        def api_simulate():
            card_id = request.args.get("card_id") or request.form.get("card_id", "")
            card_id = card_id.strip()
            if not card_id:
                abort(400, "card_id required")
            reader.handle_scan(card_id, scanbus, player)
            return jsonify({"ok": True})

    return app
