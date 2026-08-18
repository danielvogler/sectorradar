#!/usr/bin/env python3
"""Serve the built site with caching turned off.

`python -m http.server` sends no cache headers, which leaves the browser free
to apply its own heuristics — and it does. During a session of rebuilding the
page every few minutes, a reload kept showing a version from twenty minutes
earlier, and several bugs were reported against output that had already been
fixed. That costs far more than the bytes it saves.

The data document is compiled into index.html, so a cached page is stale
*data*, not merely a stale wrapper. Nothing here may be cached.
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Quiet. One line per asset per reload drowns anything worth seeing."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if not (DIST / "index.html").exists():
        print(f"nothing built at {DIST} — run `make web` first")
        return 1

    handler = partial(NoCacheHandler, directory=str(DIST))
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"serving {DIST} at http://localhost:{args.port} — Ctrl-C to stop")
        print("caching is off, so a reload always shows the current build.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
