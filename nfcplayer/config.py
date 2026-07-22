"""Bootstrap configuration from environment variables.

Runtime-tunable values (music root, NFC device, player command, ALSA device)
live in the SQLite settings table and are edited on the web UI's Settings
page. Environment variables here only cover what must be known before the
database is open, plus the initial defaults seeded into the settings table
on first run.
"""

import os

# Where the SQLite database lives. Must be writable by the service user.
DB_PATH = os.environ.get("NFCPLAYER_DB", os.path.join(os.path.expanduser("~"), "nfcplayer.db"))

# Web interface bind address.
HOST = os.environ.get("NFCPLAYER_HOST", "0.0.0.0")
PORT = int(os.environ.get("NFCPLAYER_PORT", "8080"))

# Dev mode enables POST /api/simulate for testing without an NFC reader.
DEV_MODE = os.environ.get("NFCPLAYER_DEV", "0") == "1"

# Defaults seeded into the settings table on first run.
SETTINGS_DEFAULTS = {
    "music_root": os.environ.get("NFCPLAYER_MUSIC_ROOT", "/home/pi/Music"),
    "nfc_device": os.environ.get("NFCPLAYER_DEVICE", ""),
    "player_cmd": os.environ.get("NFCPLAYER_PLAYER_CMD", "mpg123 -q"),
    "alsa_device": os.environ.get("NFCPLAYER_ALSA_DEVICE", ""),
    "bt_device": "",       # MAC of the chosen Bluetooth speaker ('' = none)
    "bt_device_name": "",  # its human name, for display only
}

# File extensions considered playable when picking from a directory.
AUDIO_EXTENSIONS = {".mp3", ".ogg", ".flac", ".wav", ".m4a", ".aac", ".wma", ".opus"}
