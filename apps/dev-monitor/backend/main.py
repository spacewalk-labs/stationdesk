#!/usr/bin/env python3
"""System observation and app-store service, using only Python's stdlib."""
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import sys

# Explicit script-local modules also import under python3 -I.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from handler import Handler
from card_delivery import start
import updates
import observability


def main():
    observability.start()
    stop = start(os.environ['STATIONDESK_ROOT'])
    updates_stop = updates.start(os.environ['STATIONDESK_ROOT'])
    try:
        ThreadingHTTPServer(('127.0.0.1', int(os.environ['STATIONDESK_PORT'])), Handler).serve_forever()
    finally:
        stop.set()
        updates_stop.set()


if __name__ == '__main__':
    main()
