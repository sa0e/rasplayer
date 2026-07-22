#!/usr/bin/env python3
"""One-shot import of the legacy db.csv into the SQLite database.

Usage:
    python migrate_csv.py --csv original/db.csv [--db ~/nfcplayer.db]
                          [--music-root /home/pi/Music] [--force]

Legacy flag mapping: rand -> random1, 3shot -> random3, stop -> stop,
anything else -> single. Duplicate card ids keep the LAST row (matching the
old script's dict behavior) with a warning. Targets are normalized (trailing
slashes stripped). With --music-root, missing targets are reported but still
imported so nothing is silently dropped — fix them later in the web UI.
"""

import argparse
import csv
import os
import sys

from nfcplayer import db


def flag_to_mode(flags):
    flags = (flags or "").strip()
    if "rand" in flags:
        return "random1"
    if "3shot" in flags:
        return "random3"
    if "stop" in flags:
        return "stop"
    return "single"


def default_label(target, mode):
    if mode == "stop":
        return "Stop"
    name = os.path.basename(target.rstrip("/"))
    return os.path.splitext(name)[0] if mode == "single" else name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="path to legacy db.csv")
    parser.add_argument("--db", default=None, help="SQLite db path (default: config)")
    parser.add_argument("--music-root", default=None, help="verify targets exist under this path")
    parser.add_argument("--force", action="store_true", help="import even if cards table is not empty")
    args = parser.parse_args()

    db.init(args.db)

    existing = db.list_cards(args.db)
    if existing and not args.force:
        sys.exit(
            f"Refusing to import: database already has {len(existing)} cards. "
            "Re-run with --force to overwrite matching ids."
        )

    rows = {}
    duplicates = []
    with open(args.csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            card_id = row["id"].strip()
            if not card_id:
                continue
            if card_id in rows:
                duplicates.append(card_id)
            mode = flag_to_mode(row["flags"])
            target = "" if mode == "stop" else row["target"].strip().strip("/")
            rows[card_id] = (mode, target)

    for card_id in duplicates:
        print(f"WARNING: duplicate id {card_id} — keeping the last row")

    missing = []
    for card_id, (mode, target) in rows.items():
        label = default_label(target, mode)
        db.upsert_card(card_id, label, mode, target, args.db)
        if args.music_root and mode != "stop":
            full = os.path.join(args.music_root, target)
            ok = os.path.isfile(full) if mode == "single" else os.path.isdir(full)
            if not ok:
                missing.append((card_id, target))

    print(f"\nImported {len(rows)} cards ({len(duplicates)} duplicate ids collapsed).")
    if missing:
        print(f"\n{len(missing)} targets not found under {args.music_root} (imported anyway — fix in the web UI):")
        for card_id, target in missing:
            print(f"  {card_id}: {target}")


if __name__ == "__main__":
    main()
