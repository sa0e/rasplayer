"""Bluetooth speaker support via bluetoothctl (BlueZ).

The admin UI can scan for nearby speakers and pair/trust/connect one; the
chosen device is stored in settings and a background thread reconnects it
at boot and whenever it comes back in range. How audio reaches the speaker
depends on the system's stack:

- PipeWire / PulseAudio move the default sink to a connected Bluetooth
  speaker automatically, so playback needs no extra flags.
- Bare-ALSA systems running bluez-alsa need mpg123 pointed at the bluealsa
  PCM explicitly; player.py handles that while the speaker is connected.

Scanning runs inside a single persistent bluetoothctl session: BlueZ purges
unpaired ("temporary") devices as soon as the discovering client exits, so
a one-shot `scan on` followed by a separate `devices` call comes back
empty. The session transcript is parsed instead.
"""

import logging
import re
import shutil
import subprocess
import threading
import time

from . import __version__, db

log = logging.getLogger(__name__)

MAC_RE = re.compile(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m|[\x01\x02\r]")
_DEVICE_LINE_RE = re.compile(r"Device ([0-9A-Fa-f:]{17})\s+(.+)$")


class BluetoothUnavailable(RuntimeError):
    pass


def ensure_available():
    if shutil.which("bluetoothctl") is None:
        raise BluetoothUnavailable(
            "bluetoothctl not found — install the bluez package"
        )


def _btctl(*args, timeout=15):
    ensure_available()
    try:
        proc = subprocess.run(
            ["bluetoothctl", *args],
            capture_output=True, text=True, errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ""
    return proc.stdout + proc.stderr


def _session(setup, wait_seconds, teardown):
    """Run one interactive bluetoothctl session and return its transcript.

    Discovery results only live as long as the discovering client, so scan
    and the follow-up commands must share a single process.
    """
    ensure_available()
    proc = subprocess.Popen(
        ["bluetoothctl"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, errors="replace",
    )
    try:
        proc.stdin.write("".join(cmd + "\n" for cmd in setup))
        proc.stdin.flush()
        time.sleep(wait_seconds)
        proc.stdin.write("".join(cmd + "\n" for cmd in (*teardown, "exit")))
        proc.stdin.flush()
        output, _ = proc.communicate(timeout=20)
    except (subprocess.TimeoutExpired, BrokenPipeError, OSError):
        proc.kill()
        output = proc.communicate()[0] or ""
    return output


def _rfkill_state():
    """Output of 'rfkill list bluetooth', or '' if rfkill is unusable."""
    if shutil.which("rfkill") is None:
        return ""
    try:
        proc = subprocess.run(
            ["rfkill", "list", "bluetooth"],
            capture_output=True, text=True, errors="replace", timeout=5,
        )
        return proc.stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _rfkill_unblock():
    """Try to clear a soft rfkill block; needs write access to /dev/rfkill
    (the service user's netdev group membership provides it)."""
    if shutil.which("rfkill") is None:
        return False
    try:
        return subprocess.run(
            ["rfkill", "unblock", "bluetooth"], capture_output=True, timeout=5
        ).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _power_on_failed(output):
    return any(token in output for token in ("Blocked", "blocked", "Failed", "Error"))


def _check_adapter():
    """Power the adapter on; raise with a specific reason if that fails."""
    show = _btctl("show")
    if "No default controller" in show or not show.strip():
        raise BluetoothUnavailable(
            "No Bluetooth adapter found — is the Pi's Bluetooth enabled? "
            "(bluetoothctl show returned nothing useful)"
        )
    output = _btctl("power", "on")
    if _power_on_failed(output):
        # A lingering rfkill soft-block is the usual culprit (systemd
        # persists rfkill state across reboots). Clear it and retry once.
        if _rfkill_unblock():
            log.info("Power-on failed; cleared rfkill block and retrying")
            output = _btctl("power", "on")
    if not _power_on_failed(output):
        return

    detail = output.strip().splitlines()[-1] if output.strip() else "unknown error"
    rfkill = _rfkill_state()
    if re.search(r"Hard blocked:\s*yes", rfkill):
        raise BluetoothUnavailable(
            "Bluetooth is hard-blocked (disabled in firmware/config, e.g. "
            "dtoverlay=disable-bt in /boot/config.txt) — " + detail
        )
    if re.search(r"Soft blocked:\s*yes", rfkill):
        raise BluetoothUnavailable(
            "Bluetooth is soft-blocked by rfkill and could not be unblocked "
            "automatically — run: sudo rfkill unblock bluetooth (" + detail + ")"
        )
    raise BluetoothUnavailable(
        f"Could not power on the Bluetooth adapter: {detail}. Check "
        "'systemctl status bluetooth hciuart' and 'dmesg | grep -iE "
        "\"bluetooth|brcm\"' on the Pi for adapter/firmware errors "
        "(see README troubleshooting)."
    )


def diagnostics():
    """Quick facts shown in the UI when a scan finds nothing."""
    controller = _btctl("show")
    summary = " · ".join(
        line.strip() for line in controller.splitlines()
        if any(key in line for key in ("Controller", "Powered:", "Discovering:"))
    )
    return {
        "app_version": __version__,
        "bluetoothctl": shutil.which("bluetoothctl") or "not found",
        "bluetoothctl_version": _btctl("version").strip(),
        "controller": summary or controller.strip()[:200] or "no output",
        "rfkill": " · ".join(_rfkill_state().split()) or "unavailable",
        "backend": audio_backend(),
    }


# --- parsing (pure, unit-tested) ---

def parse_scan_output(output):
    """Extract {mac: name} from a bluetoothctl session transcript.

    Accepts both '[NEW] Device <mac> <name>' discovery events and plain
    'Device <mac> <name>' lines from a `devices` dump. Property-change
    noise ('[CHG] Device <mac> RSSI: -52', '[DEL] ...') is ignored. Later
    occurrences win, so the final `devices` dump refreshes stale names.
    """
    found = {}
    for raw in output.splitlines():
        line = _ANSI_RE.sub("", raw).strip()
        if "[CHG]" in line or "[DEL]" in line:
            continue
        match = _DEVICE_LINE_RE.search(line)
        if match:
            found[match.group(1).upper()] = match.group(2).strip()
    return found


def parse_info(output):
    """'bluetoothctl info <mac>' output -> dict of the fields we care about."""
    info = {"name": "", "icon": "", "connected": False, "paired": False, "trusted": False}
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            info["name"] = line[len("Name:"):].strip()
        elif line.startswith("Icon:"):
            info["icon"] = line[len("Icon:"):].strip()
        elif line.startswith("Connected:"):
            info["connected"] = line.endswith("yes")
        elif line.startswith("Paired:"):
            info["paired"] = line.endswith("yes")
        elif line.startswith("Trusted:"):
            info["trusted"] = line.endswith("yes")
    return info


# --- operations ---

def device_info(mac):
    return parse_info(_btctl("info", mac))


def is_connected(mac):
    return device_info(mac)["connected"]


def scan(seconds=10):
    """Discover nearby devices; audio devices sort first."""
    _check_adapter()
    output = _session(["scan on"], seconds, ["devices", "scan off"])
    log.debug("bluetoothctl scan transcript:\n%s", output)

    devices = []
    for mac, name in parse_scan_output(output).items():
        if name.replace("-", ":").upper() == mac:
            continue  # nameless device advertising only its address
        info = device_info(mac)
        devices.append({
            "mac": mac,
            "name": info["name"] or name,
            "icon": info["icon"],
            "paired": info["paired"],
            "connected": info["connected"],
        })
    devices.sort(key=lambda d: (not d["icon"].startswith("audio"), d["name"].lower()))
    log.info("Bluetooth scan found %d device(s)", len(devices))
    if not devices:
        log.info("Empty scan; transcript tail: %r", output[-600:])
    return devices


def connect(mac):
    """Pair (if needed), trust, and connect. Returns (ok, name_or_error)."""
    _check_adapter()
    info = device_info(mac)
    if not info["paired"] and not info["name"]:
        # BlueZ already purged this unpaired device; rediscover it so
        # pair/connect have something to talk to.
        _session(["scan on"], 6, ["scan off"])
        info = device_info(mac)
    if not info["paired"]:
        _btctl("pair", mac, timeout=30)
    _btctl("trust", mac)
    _btctl("connect", mac, timeout=20)
    info = device_info(mac)
    if info["connected"]:
        return True, info["name"] or mac
    return False, "Could not connect — make sure the speaker is on, in range, and in pairing mode."


def forget(mac):
    _btctl("disconnect", mac)
    _btctl("remove", mac)


def audio_backend():
    """How Bluetooth audio reaches the sound layer on this system."""
    if _proc_running("pipewire", "pulseaudio"):
        return "pulse"      # BT speaker becomes the default sink automatically
    if _proc_running("bluealsa", "bluealsad"):
        return "bluealsa"   # playback must target the bluealsa PCM explicitly
    return None


def _proc_running(*names):
    for name in names:
        if subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0:
            return True
    return False


# --- auto-reconnect ---

def _autoconnect_loop(interval=30):
    was_connected = None
    while True:
        mac = db.get_setting("bt_device")
        if mac:
            try:
                connected = is_connected(mac)
                if not connected:
                    _btctl("connect", mac, timeout=10)
                    connected = is_connected(mac)
                if connected != was_connected:
                    name = db.get_setting("bt_device_name") or mac
                    log.info(
                        "Bluetooth speaker %s: %s",
                        name, "connected" if connected else "not reachable",
                    )
                was_connected = connected
            except BluetoothUnavailable as exc:
                log.warning("%s — Bluetooth auto-connect disabled", exc)
                return
        else:
            was_connected = None
        time.sleep(interval)


def start_autoconnect():
    """Reconnect the saved speaker at boot and whenever it reappears."""
    thread = threading.Thread(
        target=_autoconnect_loop, name="bt-autoconnect", daemon=True
    )
    thread.start()
    return thread
