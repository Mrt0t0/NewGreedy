import logging
import unittest
from unittest import mock

from tests import reset_data   # imported first: points the data folder at a temp dir

import newgreedy

setUpModule = reset_data


class ClosedLoopHandler(logging.Handler):
    """What mitmdump leaves on the root logger once its event loop is closed."""

    def emit(self, record):
        raise RuntimeError("Event loop is closed")


ClosedLoopHandler.__module__ = "mitmproxy.log"


class Watchdog(unittest.TestCase):
    def test_restarts_after_a_crash_despite_mitmproxy_log_handler(self):
        calls = []

        def run(host, port):
            calls.append(1)
            logging.getLogger().addHandler(ClosedLoopHandler())
            if len(calls) == 1:
                raise SystemExit(1)

        with mock.patch.object(newgreedy, "_run_mitm", run), \
                mock.patch.object(newgreedy, "RESTART_DELAY_S", 0), \
                mock.patch.object(newgreedy, "_port_free", lambda *a, **k: True), \
                mock.patch.object(newgreedy.logger, "propagate", False):
            self.assertEqual(newgreedy._watchdog("127.0.0.1", 1), 0)
        self.assertEqual(len(calls), 2)
        self.assertFalse([h for h in logging.getLogger().handlers if isinstance(h, ClosedLoopHandler)])

    def test_gives_up_after_too_many_failures(self):
        def run(host, port):
            raise SystemExit(1)

        with mock.patch.object(newgreedy, "_run_mitm", run), \
                mock.patch.object(newgreedy, "RESTART_DELAY_S", 0), \
                mock.patch.object(newgreedy, "_port_free", lambda *a, **k: True), \
                mock.patch.object(newgreedy.logger, "propagate", False):
            self.assertEqual(newgreedy._watchdog("127.0.0.1", 1, max_restarts=2), 1)


if __name__ == "__main__":
    unittest.main()
