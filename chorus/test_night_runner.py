import pathlib
import socket
import socketserver
import tempfile
import threading
import unittest
from unittest import mock

from chorus import night_runner


class NightRunnerOwnershipTest(unittest.TestCase):
    def tearDown(self):
        if night_runner.producer_lock is not None:
            night_runner.producer_lock.close()
            night_runner.producer_lock = None

    def test_only_one_runner_can_hold_the_production_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            run = pathlib.Path(directory)
            self.assertTrue(night_runner.acquire_producer_lock(run))
            first = night_runner.producer_lock
            night_runner.producer_lock = None
            self.assertFalse(night_runner.acquire_producer_lock(run))
            first.close()

    def test_nexus_receipt_is_required_before_execution(self):
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.rfile.readline()
                self.wfile.write(b'{"ok":true,"accepted":true,"verified":true,"receipt_id":"rcpt_test"}\n')

        with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.handle_request)
            thread.start()
            host, port = server.server_address
            with mock.patch.dict("os.environ", {"NEXUS_ADDR": f"{host}:{port}"}):
                receipt = night_runner.nexus_receipt("study-one", "flux.test")
            thread.join(timeout=2)
        self.assertEqual(receipt["receipt_id"], "rcpt_test")


if __name__ == "__main__":
    unittest.main()
