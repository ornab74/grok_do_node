from __future__ import annotations

from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from typing import Iterator


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def serve_fixture(path: Path) -> Iterator[str]:
    root = path.resolve().parent
    handler = partial(QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/{path.name}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
