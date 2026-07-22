"""In-memory ring buffer of recent card scans.

The reader thread pushes every scan here; the web UI polls it so the admin
can see the id of a freshly tapped card ("assign mode") without typing it.
"""

import threading
import time
from collections import deque


class ScanBus:
    def __init__(self, maxlen=20):
        self._lock = threading.Lock()
        self._scans = deque(maxlen=maxlen)
        self._seq = 0

    def push(self, card_id, known):
        with self._lock:
            self._seq += 1
            entry = {
                "seq": self._seq,
                "card_id": card_id,
                "known": known,
                "ts": time.time(),
            }
            self._scans.append(entry)
            return entry

    def latest(self):
        """Newest scan, or None if nothing has been scanned yet."""
        with self._lock:
            return self._scans[-1] if self._scans else None

    def latest_unknown(self):
        """Newest scan whose card wasn't in the database, or None."""
        with self._lock:
            for entry in reversed(self._scans):
                if not entry["known"]:
                    return entry
        return None
