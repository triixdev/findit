"""Vercel entry point: wraps the FindIt request handler from ../app.py."""
import os
import sys

# Vercel's filesystem is read-only except /tmp, so the database lives there.
os.environ.setdefault("DB_PATH", "/tmp/lostfound_lite.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as findit_app  # noqa: E402  (not named "app" so Vercel won't mistake it for a WSGI app)

findit_app.init_db()


class handler(findit_app.Handler):
    def do_GET(self):
        self.path = self.path.split("?")[0]
        super().do_GET()
