import unittest
from unittest.mock import patch

from assistant import text_normalize


class _FakeTimer:
    def __init__(self, _interval, function):
        self.function = function
        self.cancelled = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class AccentizerCacheTests(unittest.TestCase):
    def test_stale_timer_does_not_unload_rescheduled_accentizer(self):
        original_accentizer = text_normalize._ACCENTIZER
        original_timer = text_normalize._ACCENTIZER_UNLOAD_TIMER
        try:
            text_normalize._ACCENTIZER = object()
            text_normalize._ACCENTIZER_UNLOAD_TIMER = None
            timers = []

            def make_timer(interval, function):
                timer = _FakeTimer(interval, function)
                timers.append(timer)
                return timer

            with patch.object(text_normalize.threading, "Timer", side_effect=make_timer):
                text_normalize._schedule_accentizer_unload()
                stale_timer = timers[-1]
                text_normalize._schedule_accentizer_unload()
                current_timer = timers[-1]
                replacement_model = object()
                text_normalize._ACCENTIZER = replacement_model

                stale_timer.function()
                self.assertIs(text_normalize._ACCENTIZER, replacement_model)
                self.assertTrue(stale_timer.cancelled)

                current_timer.function()
                self.assertIsNone(text_normalize._ACCENTIZER)
        finally:
            current = text_normalize._ACCENTIZER_UNLOAD_TIMER
            if current is not None:
                current.cancel()
            text_normalize._ACCENTIZER = original_accentizer
            text_normalize._ACCENTIZER_UNLOAD_TIMER = original_timer


if __name__ == "__main__":
    unittest.main()
