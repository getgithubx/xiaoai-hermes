"""Hardware-free failure-path tests for the flash transaction itself."""
import json
from pathlib import Path
import tempfile
import unittest

from apply_adb_firmware import transact


class TransactionTests(unittest.TestCase):
    def exercise(self, failure=None):
        storage = {0: b"old-lock", 4096: b"old-data"}
        items = [{"name": "security", "address": 0, "before": b"old-lock", "after": b"new-lock"},
                 {"name": "system", "address": 4096, "before": b"old-data", "after": b"new-data"}]
        writes = []
        failed = False

        def read(address, length):
            return storage[address]

        def write(address, data):
            nonlocal failed
            writes.append(address)
            if address == 4096 and failure and not failed:
                failed = True
                storage[address] = b"partial!"
                return failure == "bad-readback"
            storage[address] = data
            return True

        if failure == "preimage":
            storage[4096] = b"changed!"
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "result.json"
            state = {}
            if failure:
                with self.assertRaises(RuntimeError):
                    transact(read, write, items, state, log)
            else:
                transact(read, write, items, state, log)
                self.assertEqual(state["state"], "verified")
                self.assertEqual(storage, {0: b"new-lock", 4096: b"new-data"})
            if failure == "preimage":
                self.assertEqual(writes, [])
            elif failure:
                self.assertEqual(writes, [0, 4096, 4096, 0])
                self.assertEqual(storage, {0: b"old-lock", 4096: b"old-data"})
                self.assertEqual(json.loads(log.read_text())["state"], "rolled_back")

    def test_success(self):
        self.exercise()

    def test_preimage_change_blocks_all_writes(self):
        self.exercise("preimage")

    def test_partial_write_restores_system_before_lock(self):
        self.exercise("write-failure")

    def test_false_success_detected_and_rolled_back(self):
        self.exercise("bad-readback")


if __name__ == "__main__":
    unittest.main()
