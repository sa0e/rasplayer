"""Entrypoint: python -m nfcplayer [--fake-input] [--port N]"""

import argparse
import logging

from . import config, db, reader, webapp
from .player import Player
from .scanbus import ScanBus


def main():
    parser = argparse.ArgumentParser(prog="nfcplayer")
    parser.add_argument(
        "--fake-input",
        action="store_true",
        help="read card ids from stdin instead of the NFC reader (dev mode)",
    )
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("nfcplayer")

    db.init()
    log.info("Database: %s", config.DB_PATH)
    log.info("Music root: %s", db.get_setting("music_root"))

    scanbus = ScanBus()
    player = Player()
    reader.start_reader(scanbus, player, fake_input=args.fake_input)

    app = webapp.create_app(scanbus, player)
    log.info("Admin interface on http://%s:%d/", args.host, args.port)
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
