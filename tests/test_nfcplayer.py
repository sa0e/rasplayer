import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nfcplayer import bluetooth, db
from nfcplayer.reader import KeyDecoder
from nfcplayer.player import pick_random

# evdev keycodes: KEY_1..KEY_0 are 2..11, KEY_ENTER is 28
KEYCODES = {"1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
            "6": 7, "7": 8, "8": 9, "9": 10, "0": 11}


def type_card(decoder, digits):
    result = None
    for d in digits:
        assert decoder.feed(KEYCODES[d], 1) is None
        decoder.feed(KEYCODES[d], 0)  # key-up, ignored
    result = decoder.feed(28, 1)  # Enter down
    decoder.feed(28, 0)
    return result


class TestKeyDecoder(unittest.TestCase):
    def test_simple_scan(self):
        self.assertEqual(type_card(KeyDecoder(), "0003645892"), "0003645892")

    def test_consecutive_scans(self):
        decoder = KeyDecoder()
        self.assertEqual(type_card(decoder, "111"), "111")
        self.assertEqual(type_card(decoder, "222"), "222")

    def test_empty_enter_ignored(self):
        self.assertIsNone(KeyDecoder().feed(28, 1))

    def test_non_digit_keys_ignored(self):
        decoder = KeyDecoder()
        decoder.feed(30, 1)  # KEY_A
        self.assertEqual(type_card(decoder, "42"), "42")

    def test_numpad_digits(self):
        decoder = KeyDecoder()
        decoder.feed(82, 1)  # KP0
        decoder.feed(79, 1)  # KP1
        self.assertEqual(decoder.feed(96, 1), "01")  # KP Enter


class TestDb(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db.init(self.db_path)

    def tearDown(self):
        os.unlink(self.db_path)

    def test_upsert_and_get(self):
        db.upsert_card("0001", "Song", "single", "a.mp3", self.db_path)
        card = db.get_card("0001", self.db_path)
        self.assertEqual(card["label"], "Song")
        db.upsert_card("0001", "Song2", "random1", "folder", self.db_path)
        card = db.get_card("0001", self.db_path)
        self.assertEqual(card["mode"], "random1")
        self.assertEqual(len(db.list_cards(self.db_path)), 1)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            db.upsert_card("0001", "x", "shuffle", "a.mp3", self.db_path)

    def test_settings_roundtrip(self):
        db.set_setting("music_root", "/tmp/music", self.db_path)
        self.assertEqual(db.get_setting("music_root", self.db_path), "/tmp/music")

    def test_delete(self):
        db.upsert_card("0001", "Song", "single", "a.mp3", self.db_path)
        db.delete_card("0001", self.db_path)
        self.assertIsNone(db.get_card("0001", self.db_path))


class TestPickRandom(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def _touch(self, name):
        open(os.path.join(self.dir.name, name), "w").close()

    def test_empty_dir(self):
        self.assertEqual(pick_random(self.dir.name, 3), [])

    def test_fewer_files_than_count(self):
        self._touch("a.mp3")
        self.assertEqual(len(pick_random(self.dir.name, 3)), 1)

    def test_ignores_non_audio(self):
        self._touch("a.mp3")
        self._touch("cover.jpg")
        picks = pick_random(self.dir.name, 5)
        self.assertEqual(len(picks), 1)
        self.assertTrue(picks[0].endswith("a.mp3"))

    def test_missing_dir(self):
        self.assertEqual(pick_random("/nonexistent/dir", 1), [])


class TestBrowseTraversal(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.music = tempfile.TemporaryDirectory()
        os.environ["NFCPLAYER_DB"] = self.db_path

        import importlib
        from nfcplayer import config
        importlib.reload(config)
        db.init(self.db_path)
        db.set_setting("music_root", self.music.name, self.db_path)

        self.patcher = unittest.mock.patch.object(config, "DB_PATH", self.db_path)
        self.patcher.start()
        # db module reads config.DB_PATH via its own import; patch there too
        self.patcher2 = unittest.mock.patch.object(
            sys.modules["nfcplayer.db"].config, "DB_PATH", self.db_path
        )
        self.patcher2.start()

        from nfcplayer.scanbus import ScanBus
        from nfcplayer.player import Player
        from nfcplayer.webapp import create_app
        app = create_app(ScanBus(), Player())
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        self.patcher.stop()
        self.patcher2.stop()
        self.music.cleanup()
        os.unlink(self.db_path)

    def test_traversal_rejected(self):
        resp = self.client.get("/api/browse?path=../../etc")
        self.assertEqual(resp.status_code, 400)

    def test_root_browse_ok(self):
        os.mkdir(os.path.join(self.music.name, "Kids"))
        resp = self.client.get("/api/browse?path=")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["dirs"][0]["name"], "Kids")

    def test_subdir_browse(self):
        sub = os.path.join(self.music.name, "Kids")
        os.mkdir(sub)
        open(os.path.join(sub, "song.mp3"), "w").close()
        resp = self.client.get("/api/browse?path=Kids")
        data = resp.get_json()
        self.assertEqual(data["parent"], "")
        self.assertEqual(data["files"][0]["path"], "Kids/song.mp3")


class TestBluetoothParsing(unittest.TestCase):
    def test_parse_scan_output(self):
        # A realistic interactive-session transcript: ANSI colors on [NEW]
        # events, [CHG] RSSI noise, and a plain `devices` dump at the end.
        output = (
            "Discovery started\n"
            "\x1b[0;93mDiscovering: yes\x1b[0m\n"
            "\x1b[0;92m[NEW]\x1b[0m Device AA:BB:CC:DD:EE:FF JBL Flip 5\n"
            "\x1b[0;92m[NEW]\x1b[0m Device 11:22:33:44:55:66 11-22-33-44-55-66\n"
            "[CHG] Device AA:BB:CC:DD:EE:FF RSSI: -52\n"
            "[DEL] Device 99:99:99:99:99:99 Gone Device\n"
            "Device AA:BB:CC:DD:EE:FF JBL Flip 5 Renamed\n"
        )
        found = bluetooth.parse_scan_output(output)
        # RSSI/[DEL] noise ignored; the devices-dump name wins over [NEW]
        self.assertEqual(found["AA:BB:CC:DD:EE:FF"], "JBL Flip 5 Renamed")
        self.assertEqual(found["11:22:33:44:55:66"], "11-22-33-44-55-66")
        self.assertNotIn("99:99:99:99:99:99", found)

    def test_parse_scan_output_empty(self):
        self.assertEqual(bluetooth.parse_scan_output(""), {})

    def test_parse_scan_output_prompt_prefixed(self):
        # Lines can carry the interactive prompt before the event tag.
        output = "[bluetooth]# \x1b[0;92m[NEW]\x1b[0m Device AA:BB:CC:DD:EE:FF Boombox\n"
        self.assertEqual(
            bluetooth.parse_scan_output(output),
            {"AA:BB:CC:DD:EE:FF": "Boombox"},
        )

    def test_parse_info(self):
        output = (
            "Device AA:BB:CC:DD:EE:FF (public)\n"
            "\tName: JBL Flip 5\n"
            "\tAlias: JBL Flip 5\n"
            "\tIcon: audio-card\n"
            "\tPaired: yes\n"
            "\tTrusted: yes\n"
            "\tBlocked: no\n"
            "\tConnected: no\n"
        )
        info = bluetooth.parse_info(output)
        self.assertEqual(info["name"], "JBL Flip 5")
        self.assertEqual(info["icon"], "audio-card")
        self.assertTrue(info["paired"])
        self.assertTrue(info["trusted"])
        self.assertFalse(info["connected"])

    def test_mac_validation(self):
        self.assertTrue(bluetooth.MAC_RE.fullmatch("AA:BB:CC:DD:EE:FF"))
        self.assertIsNone(bluetooth.MAC_RE.fullmatch("AA:BB:CC:DD:EE"))
        self.assertIsNone(bluetooth.MAC_RE.fullmatch("not-a-mac; rm -rf"))


class TestOutputArgs(unittest.TestCase):
    """Audio routing precedence: explicit ALSA device > bluealsa BT > default."""

    def _args(self, settings, backend=None, connected=False):
        from nfcplayer import player
        with unittest.mock.patch.object(
            player.db, "get_setting", side_effect=lambda k: settings.get(k, "")
        ), unittest.mock.patch.object(
            player.bluetooth, "audio_backend", return_value=backend
        ), unittest.mock.patch.object(
            player.bluetooth, "is_connected", return_value=connected
        ):
            return player.output_args()

    def test_explicit_alsa_device_wins(self):
        args = self._args(
            {"alsa_device": "hw:1,0", "bt_device": "AA:BB:CC:DD:EE:FF"},
            backend="bluealsa", connected=True,
        )
        self.assertEqual(args, ["-a", "hw:1,0"])

    def test_bluealsa_when_connected(self):
        args = self._args(
            {"bt_device": "AA:BB:CC:DD:EE:FF"}, backend="bluealsa", connected=True
        )
        self.assertEqual(args, ["-a", "bluealsa:DEV=AA:BB:CC:DD:EE:FF,PROFILE=a2dp"])

    def test_bluealsa_not_connected_falls_back(self):
        args = self._args(
            {"bt_device": "AA:BB:CC:DD:EE:FF"}, backend="bluealsa", connected=False
        )
        self.assertEqual(args, [])

    def test_pulse_backend_needs_no_args(self):
        args = self._args(
            {"bt_device": "AA:BB:CC:DD:EE:FF"}, backend="pulse", connected=True
        )
        self.assertEqual(args, [])

    def test_no_bt_configured(self):
        self.assertEqual(self._args({}), [])


class TestMigration(unittest.TestCase):
    def test_real_csv(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            csv_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "original", "db.csv",
            )
            if not os.path.exists(csv_path):
                self.skipTest("original/db.csv not present")

            import subprocess
            result = subprocess.run(
                [sys.executable, "migrate_csv.py", "--csv", csv_path, "--db", db_path],
                capture_output=True, text=True,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            cards = db.list_cards(db_path)
            ids = {c["card_id"] for c in cards}
            self.assertEqual(len(cards), len(ids))  # no duplicates
            self.assertGreater(len(cards), 50)
            modes = {c["mode"] for c in cards}
            self.assertLessEqual(modes, {"single", "random1", "random3", "stop"})
        finally:
            os.unlink(db_path)


if __name__ == "__main__":
    unittest.main()
