import threading
import unittest

from feed_stability import (
    AsyncSnapshotWorker,
    ENABLE_EXTENDED_FLAGS,
    ENABLE_QUICK_EDIT_MODE,
    mode_without_quick_edit,
    snapshot_due,
)


class FeedStabilityTests(unittest.TestCase):
    def test_first_full_scan_is_immediate(self):
        self.assertTrue(snapshot_due(0.0, 0.0, True, False))

    def test_empty_roster_full_scan_is_retried_at_bounded_rate(self):
        self.assertFalse(snapshot_due(0.99, 0.0, False, False))
        self.assertTrue(snapshot_due(1.0, 0.0, False, False))

    def test_populated_roster_keeps_regular_refresh_interval(self):
        self.assertFalse(snapshot_due(2.49, 0.0, False, True))
        self.assertTrue(snapshot_due(2.5, 0.0, False, True))

    def test_quick_edit_is_removed_without_clobbering_other_console_flags(self):
        original = 0x0001 | 0x0002 | 0x0004 | ENABLE_QUICK_EDIT_MODE | 0x0100
        result = mode_without_quick_edit(original)
        self.assertFalse(result & ENABLE_QUICK_EDIT_MODE)
        self.assertTrue(result & ENABLE_EXTENDED_FLAGS)
        self.assertEqual(result & ~(ENABLE_QUICK_EDIT_MODE | ENABLE_EXTENDED_FLAGS),
                         original & ~(ENABLE_QUICK_EDIT_MODE | ENABLE_EXTENDED_FLAGS))

    def test_slow_snapshot_does_not_block_reader_and_only_one_request_runs(self):
        started = threading.Event()
        release = threading.Event()

        def capture():
            started.set()
            if not release.wait(2):
                raise TimeoutError('test did not release snapshot')
            return {'heroes': 10}

        worker = AsyncSnapshotWorker(capture)
        worker.start()
        try:
            self.assertTrue(worker.request())
            self.assertTrue(started.wait(1))
            self.assertTrue(worker.in_flight)
            self.assertFalse(worker.request())
            self.assertIsNone(worker.poll())
            release.set()
            result = worker.poll(timeout=1)
            self.assertIsNotNone(result)
            value, error, elapsed_ms = result
            self.assertEqual(value, {'heroes': 10})
            self.assertEqual(error, '')
            self.assertGreaterEqual(elapsed_ms, 0)
            self.assertFalse(worker.in_flight)
        finally:
            release.set()
            worker.stop()

    def test_snapshot_exception_is_returned_and_worker_can_retry(self):
        worker = AsyncSnapshotWorker(lambda: (_ for _ in ()).throw(ValueError('bad scan')))
        worker.start()
        try:
            self.assertTrue(worker.request())
            result = worker.poll(timeout=1)
            self.assertIsNotNone(result)
            value, error, elapsed_ms = result
            self.assertIsNone(value)
            self.assertEqual(error, 'ValueError: bad scan')
            self.assertGreaterEqual(elapsed_ms, 0)
            self.assertFalse(worker.in_flight)
        finally:
            worker.stop()


if __name__ == '__main__':
    unittest.main()
