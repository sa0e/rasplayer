import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nfcplayer import db
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
