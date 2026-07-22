"""Playback: mpg123 subprocess management and card-mode dispatch."""

import logging
import os
import random
import shlex
import subprocess
import threading

from . import bluetooth, config, db

log = logging.getLogger(__name__)


def output_args():
    """Extra mpg123 args for audio routing.

    An explicit ALSA device setting always wins. Otherwise, on bare-ALSA
    systems running bluez-alsa, target the configured Bluetooth speaker's
    PCM while it is connected. PipeWire/PulseAudio systems route the
    default output to the speaker themselves, so no args are needed.
    """
    alsa_device = db.get_setting("alsa_device")
    if alsa_device:
        return ["-a", alsa_device]
    mac = db.get_setting("bt_device")
    if mac:
        try:
            if bluetooth.audio_backend() == "bluealsa" and bluetooth.is_connected(mac):
                return ["-a", f"bluealsa:DEV={mac},PROFILE=a2dp"]
        except bluetooth.BluetoothUnavailable:
            pass
    return []


class Player:
    """Wraps a single playback subprocess. A new play() interrupts the old one."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None

    def play(self, paths):
        cmd = shlex.split(db.get_setting("player_cmd")) or ["mpg123", "-q"]
        cmd += output_args()
        cmd += list(paths)
        with self._lock:
            self._stop_locked()
            log.info("Playing: %s", " | ".join(paths))
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except OSError as exc:
                log.error("Failed to start player %r: %s", cmd[0], exc)
                self._proc = None

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        else:
            self._proc.wait()  # reap a track that finished on its own
        self._proc = None


def pick_random(directory, count):
    """Pick up to `count` playable files from a directory (non-recursive)."""
    try:
        entries = os.listdir(directory)
    except OSError as exc:
        log.error("Cannot list %s: %s", directory, exc)
        return []
    files = [
        os.path.join(directory, f)
        for f in entries
        if os.path.isfile(os.path.join(directory, f))
        and os.path.splitext(f)[1].lower() in config.AUDIO_EXTENSIONS
    ]
    if not files:
        log.warning("No playable files in %s", directory)
        return []
    return random.sample(files, min(count, len(files)))


def dispatch(card, player):
    """Act on a known card row: play its target or stop playback."""
    mode = card["mode"]
    if mode == "stop":
        log.info("Stop card scanned")
        player.stop()
        return

    music_root = db.get_setting("music_root")
    target = os.path.join(music_root, card["target"])

    if mode == "single":
        if not os.path.isfile(target):
            log.error("Missing file for card %s: %s", card["card_id"], target)
            return
        paths = [target]
    else:  # random1 / random3
        count = 3 if mode == "random3" else 1
        paths = pick_random(target, count)
        if not paths:
            return

    player.play(paths)
