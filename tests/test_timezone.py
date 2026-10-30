"""Kodi's naive local lastplayed/dateadded <-> MDBList's UTC
(resources/lib/utils.py), converted with the offset in effect at that time,
and watched_sync not re-pushing a watch saved an hour off by older versions.

Run from the repo root (kodistubs provides xbmc):
  uv run python -m unittest tests.test_timezone
"""
import os
import time
import unittest

from resources.lib import utils, watched_sync


class LocalTimeConversionTests(unittest.TestCase):
    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Vilnius"
        time.tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def test_summer_time_uses_summer_offset(self):
        self.assertEqual(utils.local_time_to_utc_iso("2026-07-15 20:00:00"), "2026-07-15T17:00:00")

    def test_winter_time_uses_winter_offset(self):
        self.assertEqual(utils.local_time_to_utc_iso("2026-01-15 20:00:00"), "2026-01-15T18:00:00")

    def test_utc_to_local_uses_offset_at_that_time(self):
        self.assertEqual(utils.utc_iso_to_local_time("2026-07-15T17:00:00Z"), "2026-07-15 20:00:00")
        self.assertEqual(utils.utc_iso_to_local_time("2026-01-15T18:00:00Z"), "2026-01-15 20:00:00")

    def test_round_trip(self):
        for value in ("2026-03-28 12:34:56", "2026-10-30 08:00:00"):
            self.assertEqual(utils.utc_iso_to_local_time(utils.local_time_to_utc_iso(value)), value)

    def test_empty_and_invalid(self):
        self.assertIsNone(utils.local_time_to_utc_iso(None))
        self.assertIsNone(utils.local_time_to_utc_iso("not a date"))
        self.assertIsNone(utils.utc_iso_to_local_time(""))


class WatchedAtChangedTests(unittest.TestCase):
    def _changed(self, known, current):
        return watched_sync._watched_at_changed({"watched_at": known}, {"watched_at": current})

    def test_same_value(self):
        self.assertFalse(self._changed("2026-07-15T17:00:00", "2026-07-15T17:00:00"))

    def test_drift_within_tolerance(self):
        self.assertFalse(self._changed("2026-07-15T17:00:00", "2026-07-15T17:00:30"))

    def test_dst_shift_from_older_versions(self):
        self.assertFalse(self._changed("2026-07-15T18:00:00", "2026-07-15T17:00:00"))
        self.assertFalse(self._changed("2026-07-15T16:00:20", "2026-07-15T17:00:00"))

    def test_rewatch(self):
        self.assertTrue(self._changed("2026-07-15T17:00:00", "2026-07-16T21:10:00"))
        self.assertTrue(self._changed("2026-07-15T17:00:00", "2026-07-15T17:30:00"))
        self.assertTrue(self._changed("2026-07-15T17:00:00", "2026-07-15T19:00:00"))


if __name__ == "__main__":
    unittest.main()
