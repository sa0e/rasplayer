"""NFC reader input.

The USB NFC reader is a keyboard-HID device that "types" the card number
followed by Enter. The evdev loop opens that device directly and grabs it
exclusively, so the daemon needs no terminal and card numbers never reach
a console. `--fake-input` substitutes stdin lines for development without
the hardware; both paths feed the same dispatch function.
"""

import logging
import sys
import threading
import time

from . import db, player as player_mod

log = logging.getLogger(__name__)

# evdev keycode -> digit character (top-row and numpad digits)
_DIGIT_KEYS = {
    2: "1", 3: "2", 4: "3", 5: "4", 6: "5",
    7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
    79: "1", 80: "2", 81: "3", 75: "4", 76: "5",
    77: "6", 71: "7", 72: "8", 73: "9", 82: "0",
}
_ENTER_KEYS = {28, 96}  # KEY_ENTER, KEY_KPENTER


class KeyDecoder:
    """Accumulates digit key-downs into a card id, emitted on Enter."""

    def __init__(self):
        self._buffer = []

    def feed(self, keycode, value):
        """Feed one key event; returns a completed card id or None."""
        if value != 1:  # only key-down
            return None
        if keycode in _ENTER_KEYS:
            card_id = "".join(self._buffer)
            self._buffer = []
            return card_id or None
        digit = _DIGIT_KEYS.get(keycode)
        if digit is not None:
            self._buffer.append(digit)
        return None


def handle_scan(card_id, scanbus, player):
    """Common dispatch path for real, fake, and simulated scans."""
    card = db.get_card(card_id)
    scanbus.push(card_id, known=card is not None)
    if card is None:
        log.info("Unknown card: %s", card_id)
        return
    log.info("Card %s (%s), mode=%s", card_id, card["label"], card["mode"])
    player_mod.dispatch(card, player)


def _find_device():
    """Resolve the input device: configured path, or scan by name."""
    import evdev

    configured = db.get_setting("nfc_device")
    if configured:
        return evdev.InputDevice(configured)

    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        name = dev.name.lower()
        if any(hint in name for hint in ("rfid", "nfc", "ic reader", "card")):
            log.info("Auto-detected reader: %s (%s)", dev.name, path)
            return dev
        dev.close()
    raise FileNotFoundError(
        "No NFC reader found. Set the device path on the Settings page "
        "(see /dev/input/by-id/ for stable paths)."
    )


def _evdev_loop(scanbus, player):
    import evdev

    while True:
        try:
            device = _find_device()
            device.grab()
            log.info("Reading from %s (grabbed)", device.path)
            decoder = KeyDecoder()
            for event in device.read_loop():
                if event.type != evdev.ecodes.EV_KEY:
                    continue
                card_id = decoder.feed(event.code, event.value)
                if card_id:
                    handle_scan(card_id, scanbus, player)
        except (OSError, FileNotFoundError) as exc:
            log.warning("Reader unavailable (%s); retrying in 3s", exc)
            time.sleep(3)


def _stdin_loop(scanbus, player):
    log.info("Fake input mode: type a card id and press Enter")
    for line in sys.stdin:
        card_id = line.strip()
        if card_id:
            handle_scan(card_id, scanbus, player)
    log.info("stdin closed; fake reader exiting")


def start_reader(scanbus, player, fake_input=False):
    """Start the reader loop in a daemon thread."""
    target = _stdin_loop if fake_input else _evdev_loop
    thread = threading.Thread(
        target=target, args=(scanbus, player), name="nfc-reader", daemon=True
    )
    thread.start()
    return thread
