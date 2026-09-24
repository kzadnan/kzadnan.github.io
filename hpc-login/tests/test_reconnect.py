import unittest

from hpc_login.reconnect import backoff_seconds, decide_reconnect


class ReconnectTests(unittest.TestCase):
    def test_backoff_doubles_and_caps(self):
        self.assertEqual(backoff_seconds(0), 0)
        self.assertEqual(backoff_seconds(1), 1)
        self.assertEqual(backoff_seconds(2), 2)
        self.assertEqual(backoff_seconds(3), 4)
        self.assertEqual(backoff_seconds(4), 8)
        self.assertEqual(backoff_seconds(10), 60)
        self.assertEqual(backoff_seconds(10, cap=15), 15)

    def test_live_master_is_left_alone(self):
        decision = decide_reconnect(
            master_running=True,
            connect_in_progress=False,
            consecutive_failures=4,
            seconds_since_last_attempt=100,
            force=True,
        )
        self.assertFalse(decision.should_start)
        self.assertEqual(decision.reason, "master is running")

    def test_force_starts_immediately_when_master_is_down(self):
        decision = decide_reconnect(
            master_running=False,
            connect_in_progress=True,
            consecutive_failures=5,
            seconds_since_last_attempt=0,
            force=True,
        )
        self.assertTrue(decision.should_start)
        self.assertEqual(decision.reason, "reconnect requested")

    def test_in_progress_attempt_is_not_duplicated(self):
        decision = decide_reconnect(
            master_running=False,
            connect_in_progress=True,
            consecutive_failures=0,
            seconds_since_last_attempt=0,
        )
        self.assertFalse(decision.should_start)
        self.assertEqual(decision.reason, "connection attempt in progress")

    def test_waits_for_backoff_then_retries(self):
        waiting = decide_reconnect(
            master_running=False,
            connect_in_progress=False,
            consecutive_failures=3,
            seconds_since_last_attempt=1,
        )
        self.assertFalse(waiting.should_start)
        self.assertAlmostEqual(waiting.wait_seconds, 3)
        ready = decide_reconnect(
            master_running=False,
            connect_in_progress=False,
            consecutive_failures=3,
            seconds_since_last_attempt=4,
        )
        self.assertTrue(ready.should_start)
        self.assertEqual(ready.reason, "backoff elapsed")

    def test_first_drop_reconnects_immediately(self):
        decision = decide_reconnect(
            master_running=False,
            connect_in_progress=False,
            consecutive_failures=0,
            seconds_since_last_attempt=None,
        )
        self.assertTrue(decision.should_start)
        self.assertEqual(decision.reason, "master is down")


if __name__ == "__main__":
    unittest.main()
