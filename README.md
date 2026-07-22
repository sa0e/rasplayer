# NFC Player

NFC-card-triggered music player for a Raspberry Pi Zero 2 W, with an admin
web interface for managing card → media mappings.

A child taps an NFC card on the USB reader; the matching song (or a random
pick from a folder) plays. Tapping a new card stops the current one and
starts the new one. No volume/pause/seek — deliberately simple.

## How it works

- One Python process, run as a systemd service.
- The USB NFC reader acts as a keyboard that "types" the card number + Enter.
  A reader thread opens the input device via `evdev` and **grabs it
  exclusively**, so it works headless and card numbers never reach a console.
- Mappings live in SQLite (`nfcplayer.db`), edited via the web UI on port 8080.
- Playback is `mpg123` as a subprocess (configurable on the Settings page —
  swap in `mpv --no-video --really-quiet` if you need non-MP3 formats).

## Install on the Pi

```sh
sudo apt install mpg123 python3-venv python3-dev
mkdir -p /home/pi/nfcplayer && cd /home/pi/nfcplayer
# copy the nfcplayer/ package, migrate_csv.py, requirements.txt, deploy/ here
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Find the reader's stable device path (plug it in first):

```sh
ls /dev/input/by-id/
# look for something like usb-Sycreader_USB_Reader-event-kbd
```

You can set this later on the web UI's **Settings** page; auto-detection by
device name (rfid/nfc/card/ic reader) usually works out of the box.

### Migrate the old CSV

```sh
venv/bin/python migrate_csv.py --csv original/db.csv \
    --db /home/pi/nfcplayer/nfcplayer.db --music-root /home/pi/Music
```

This maps the legacy flags (`rand` → random-1, `3shot` → random-3), collapses
duplicate card ids (last row wins, with warnings), and reports targets that
don't exist on disk so you can fix them in the web UI.

### Enable the service

```sh
sudo cp deploy/nfcplayer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nfcplayer
journalctl -u nfcplayer -f   # watch it live
```

The service runs as user `pi` with the `input` group (evdev access) and
`audio` group (ALSA). Open `http://<pi-address>:8080/` from any browser on
your network.

## Admin web interface

- **Cards** — list, add, edit, delete mappings. Each card has a label, a play
  mode (single file / random track from folder / 3 random tracks / stop), and
  a target picked via a built-in browser of your music folder.
- **Assign mode** — open the Cards page (or the Add-card form) and tap an
  unmapped card on the reader: its id appears in a banner with a one-click
  "Map this card" button. No typing card numbers.
- **Settings** — music folder path, NFC device path, player command, ALSA
  output device. Music folder changes apply immediately; device changes need
  a service restart.
- **Bluetooth speaker** — on the Settings page: scan for nearby speakers,
  connect one (put it in pairing mode the first time), and it's remembered.
  A background thread reconnects it automatically at boot and whenever it
  comes back in range/powers on. "Forget speaker" unpairs and reverts to the
  default output.

There is no authentication — the UI is meant for a trusted home LAN.

### Bluetooth audio notes

Bluetooth control uses `bluetoothctl` (the `bluez` package, preinstalled on
Raspberry Pi OS). How audio reaches the speaker depends on your image:

- **Raspberry Pi OS (desktop / with PipeWire or PulseAudio):** nothing to
  do — when the speaker connects it becomes the default output and mpg123
  follows it.
- **Raspberry Pi OS Lite (bare ALSA):** install bluez-alsa so the speaker
  shows up as an ALSA PCM: `sudo apt install bluez-alsa-utils`. The player
  detects this and targets the speaker's PCM automatically while it is
  connected; if the speaker drops off, playback falls back to the default
  ALSA output.

The service unit already adds the `bluetooth` group so `bluetoothctl` works
without root.

## Development without the hardware

```sh
NFCPLAYER_DEV=1 NFCPLAYER_DB=/tmp/nfc.db NFCPLAYER_MUSIC_ROOT=~/Music \
    python -m nfcplayer --fake-input
```

- `--fake-input`: type card ids + Enter on stdin instead of using evdev.
- `NFCPLAYER_DEV=1` enables `POST /api/simulate?card_id=...`, which injects a
  scan through the exact same dispatch path — drive assign mode and playback
  entirely from the browser/curl.

Run the tests with:

```sh
python -m unittest discover tests
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `NFCPLAYER_DB` | `~/nfcplayer.db` | SQLite database path |
| `NFCPLAYER_HOST` / `NFCPLAYER_PORT` | `0.0.0.0` / `8080` | web UI bind |
| `NFCPLAYER_DEV` | `0` | enable the simulate endpoint |
| `NFCPLAYER_MUSIC_ROOT` | `/home/pi/Music` | *initial* music root (seeded into settings on first run) |
| `NFCPLAYER_DEVICE` | *(auto-detect)* | *initial* NFC device path |
| `NFCPLAYER_PLAYER_CMD` | `mpg123 -q` | *initial* player command |

The *initial* values only seed the settings table on first run; after that,
edit them on the Settings page.
