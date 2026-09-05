"""Graceful HTTP process-shutdown tests."""

from __future__ import annotations

import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch
from urllib.request import urlopen

from iip.surfaces.http import DrainingThreadingHTTPServer, _shutdown_handler


class SlowHandler(BaseHTTPRequestHandler):
    started = Event()
    release = Event()

    def do_GET(self) -> None:  # noqa: N802
        self.started.set()
        if not self.release.wait(2):
            self.send_error(504)
            return
        body = b"complete"
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        del args


class HttpShutdownTests(unittest.TestCase):
    def setUp(self) -> None:
        SlowHandler.started.clear()
        SlowHandler.release.clear()

    def test_server_close_waits_for_active_request(self) -> None:
        server = DrainingThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        serving = Thread(target=server.serve_forever)
        serving.start()
        result: list[tuple[int, bytes]] = []

        def request() -> None:
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/slow",
                timeout=3,
            ) as response:
                result.append((response.status, response.read()))

        client = Thread(target=request)
        client.start()
        self.assertTrue(SlowHandler.started.wait(1))

        server.shutdown()
        serving.join(1)
        self.assertFalse(serving.is_alive())
        closed = Event()

        def close() -> None:
            server.server_close()
            closed.set()

        closing = Thread(target=close)
        closing.start()
        self.assertFalse(closed.wait(0.05))
        SlowHandler.release.set()

        self.assertTrue(closed.wait(2))
        closing.join(1)
        client.join(1)
        self.assertEqual(result, [(200, b"complete")])

    def test_signal_handler_requests_shutdown_once_off_signal_thread(self) -> None:
        class Server:
            def __init__(self) -> None:
                self.calls = 0
                self.called = Event()

            def shutdown(self) -> None:
                self.calls += 1
                self.called.set()

        server = Server()
        requested = Event()
        stop = _shutdown_handler(server, requested)  # type: ignore[arg-type]

        with patch("builtins.print") as output:
            stop(15, object())
            self.assertTrue(server.called.wait(1))
            stop(15, object())

        self.assertEqual(server.calls, 1)
        output.assert_called_once_with(
            "IIP reference API draining active requests",
            flush=True,
        )

    def test_request_threads_are_explicitly_non_daemon_and_joined(self) -> None:
        self.assertFalse(DrainingThreadingHTTPServer.daemon_threads)
        self.assertTrue(DrainingThreadingHTTPServer.block_on_close)

    def test_main_installs_signal_draining_before_serving(self) -> None:
        source = Path(__file__).resolve().parents[1].joinpath(
            "src/iip/surfaces/http.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "server = DrainingThreadingHTTPServer((host, port), ApiHandler)",
            source,
        )
        self.assertIn(
            "for shutdown_signal in (signal.SIGTERM, signal.SIGINT)",
            source,
        )
        self.assertLess(
            source.rindex("server.server_close()"),
            source.rindex("runtime.close()"),
        )


if __name__ == "__main__":
    unittest.main()
