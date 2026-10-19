import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from legacy.scrapers import cf_session
from legacy.scrapers.scrape_krl import KCIClient, active_stations, minutes, normalize, windows, run
from legacy.scrapers.fetch_osm import convert, route_segments


class ScraperTests(unittest.TestCase):
    def test_resume_and_offline_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            cache = data / "raw" / "krl"
            cache.mkdir(parents=True)
            (cache / "stations.json").write_text(json.dumps({"data": [{"sta_id": "A", "fg_enable": 1}]}))
            for start, end in windows():
                (cache / f"A_{start.replace(':', '')}_{end.replace(':', '')}.json").write_text('{"data": []}')
            args = SimpleNamespace(data_dir=data, normalize_only=False, impersonate="chrome")
            with patch("legacy.scrapers.scrape_krl.KCIClient") as client:
                run(args)
                client.assert_not_called()
            output = data / "trips.json"
            self.assertEqual(json.loads(output.read_text()), [])
            output.write_text("preserve previous output")
            (cache / "A_0400_0700.json").unlink()
            args.normalize_only = True
            with self.assertRaisesRegex(RuntimeError, "Incomplete cache"):
                run(args)
            self.assertEqual(output.read_text(), "preserve previous output")

    def test_midnight_dedup_and_windows(self):
        self.assertEqual(list(windows())[-1], ("22:00", "24:00"))
        self.assertEqual(len(list(windows())), 7)
        self.assertEqual(minutes("00:12:00"), minutes("24:12"))
        self.assertEqual(active_stations({"data": [{"fg_enable": "0"}]}), [])
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            row = {"train_id": "9", "time_est": "23:55", "route_name": "Bogor"}
            for name in ["A_1900_2200.json", "A_2200_2400.json"]:
                (cache / name).write_text(json.dumps({"data": [row]}))
            (cache / "B_2200_2400.json").write_text(json.dumps({"data": [{**row, "time_est": "00:12"}]}))
            trips = normalize(cache, [{"sta_id": "A"}, {"sta_id": "B"}])
        self.assertEqual(trips[0]["stops"], [{"station_id": "A", "time": "23:55"}, {"station_id": "B", "time": "24:12"}])

    def test_refresh_once(self):
        helper = SimpleNamespace(get_session=Mock(return_value=({"cf_clearance": "test"}, "UA")), invalidate=Mock())
        blocked = SimpleNamespace(status_code=403, text="Just a moment")
        success = Mock(status_code=200, text='{"data": []}')
        success.json.return_value = {"data": []}
        transport = SimpleNamespace(get=Mock(side_effect=[blocked, success]))
        client = KCIClient(helper=helper, transport=transport, sleeper=Mock())
        self.assertEqual(client.get("/stations"), {"data": []})
        helper.invalidate.assert_called_once()
        self.assertEqual(helper.get_session.call_count, 2)
        self.assertEqual(transport.get.call_args.kwargs["impersonate"], "chrome")
        transport.get.side_effect = [blocked, blocked]
        with self.assertRaisesRegex(RuntimeError, "persists"):
            client.get("/stations")

    def test_cookie_cache(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cf_session, "CACHE", Path(directory) / "cache.json"):
            cf_session.CACHE.write_text(json.dumps({"cookies": {"cf_clearance": "test"}, "user_agent": "Chrome/120.0", "expires_at": 9999999999}))
            self.assertEqual(cf_session.get_session(), ({"cf_clearance": "test"}, "Chrome/120.0"))
            cf_session.invalidate()
            self.assertIsNone(cf_session._read_cache())
            cf_session.CACHE.write_text('{"expires_at":0}')
            self.assertIsNone(cf_session._read_cache())

    def test_osm_ambiguity_overrides_and_geometry(self):
        def node(i, name, lat):
            return {"type": "node", "id": i, "lat": lat, "lon": 106.8, "tags": {"railway": "station", "name": name}}
        elements = [node(1, "Stasiun Jakarta Kota", -6.1), node(2, "Twin", -6.2), node(3, "Twin", -6.4)]
        kci = [{"sta_id": "JAKK", "sta_name": "JAKARTA KOTA"}, {"sta_id": "T", "sta_name": "Twin"}, {"sta_id": "X", "sta_name": "Absent"}]
        stations, _, unmatched = convert({"elements": elements}, kci)
        self.assertEqual(stations[0]["id"], "JAKK")
        self.assertEqual(len(unmatched), 2)
        stations, _, unmatched = convert({"elements": elements}, kci, {"T": "node/2"})
        self.assertEqual(len(stations), 2)
        a, b, c, d = [{"lon": i, "lat": 0} for i in range(4)]
        segments = route_segments([{"type": "way", "geometry": [a, b]}, {"type": "way", "geometry": [c, b]}, {"type": "way", "geometry": [d, c], "role": "platform"}])
        self.assertEqual(segments, [[[0, 0], [1, 0], [2, 0]]])
        self.assertEqual(len(route_segments([{"type": "way", "geometry": [a, b]}, {"type": "way", "geometry": [c, d]}])), 2)
        with self.assertRaises(ValueError):
            convert({"elements": [], "remark": "timeout"}, kci)


if __name__ == "__main__":
    unittest.main()
