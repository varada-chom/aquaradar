import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import collect as C  # noqa: E402

NOW = datetime(2026, 10, 2, 8, 0, tzinfo=C.ICT).timestamp()


def wl_row(**kw):
    r = {"station": {"id": 1, "tele_station_name": {"th": "ท"}, "tele_station_lat": 14.0, "tele_station_long": 100.5},
         "geocode": {"province_name": {"th": "P"}, "amphoe_name": {"th": "A"}},
         "waterlevel_datetime": "2026-10-02 07:30", "waterlevel_msl": "3.2",
         "diff_wl_bank": "0.4", "diff_wl_bank_text": "ต่ำกว่าตลิ่ง (ม.)", "situation_level": 2}
    r.update(kw)
    return r


class T(unittest.TestCase):
    def test_records_nested(self):
        self.assertEqual(len(C.records({"a": 1, "b": {"data": [{"x": 1}, {"x": 2}]}})), 2)
        self.assertEqual(C.records({"result": "OK", "data": []}), [])

    def test_ptime_is_thai_time(self):
        self.assertEqual(C.ptime("2026-10-02 08:30"), datetime(2026, 10, 2, 1, 30, tzinfo=timezone.utc).timestamp())
        self.assertIsNone(C.ptime("bad"))
        self.assertIsNone(C.ptime(None))

    def test_wl_over_and_under_bank(self):
        rows = C.norm_wl([wl_row(diff_wl_bank_text="ล้นตลิ่ง (ม.)", diff_wl_bank="0.7", situation_level=None), wl_row()])
        self.assertEqual((rows[0]["sev"], rows[0]["bank"]), (4, 0.7))
        self.assertEqual((rows[1]["sev"], rows[1]["bank"]), (1, -0.4))

    def test_wl_level_map_and_drops(self):
        self.assertEqual(C.norm_wl([wl_row(situation_level=4, diff_wl_bank_text="")])[0]["sev"], 3)
        bad_geo = wl_row()
        bad_geo["station"] = dict(bad_geo["station"], tele_station_lat=0)
        self.assertEqual(C.norm_wl([bad_geo, wl_row(waterlevel_datetime="x")]), [])

    def test_rain_thresholds_and_order(self):
        def row(mm, i):
            return {"station": {"id": i, "tele_station_lat": 14, "tele_station_long": 100}, "geocode": {},
                    "rainfall_datetime": "2026-10-02 07:00", "rain_24h": mm}
        rows = C.norm_rain([row(0.5, 1), row(9.9, 2), row(10, 3), row(35, 4), row(90, 5), row(900, 6)])
        self.assertEqual([r["val"] for r in rows], [90, 35, 10, 9.9])
        self.assertEqual([r["sev"] for r in rows], [3, 2, 1, 0])

    def test_ews_status_and_bbox(self):
        base = {"stn": "S1", "latitude": 14, "longitude": 100, "stn_type": "WL", "wl": "N/A", "date": "2026-10-02 07:00"}
        rows = C.norm_ews([dict(base, status=3), dict(base, status=2), dict(base, status=0),
                           dict(base, status=3, latitude=50)])
        self.assertEqual([r["sev"] for r in rows], [4, 2, 0])
        self.assertIsNone(rows[0]["val"])

    def test_summarize(self):
        s = C.summarize([dict(prov="P", sev=4, kind="wl", bank=0.5, lat=14.0, lon=100.0, val=1),
                         dict(prov="P", sev=1, kind="rain", bank=None, lat=15.0, lon=101.0, val=12.0)])
        p = s[0]
        self.assertEqual((p["n"], p["max"], p["over"], p["rain_max"], p["lat"], p["lon"]), (2, 4, 1, 12.0, 14.5, 100.5))
        self.assertEqual(p["counts"], [0, 1, 0, 0, 1])

    def test_mock_collect_is_valid_json_and_scale(self):
        res = C.collect(mock=True, now=NOW)
        json.dumps(res, ensure_ascii=False)
        self.assertTrue(res["mock"])
        self.assertTrue(all(m["ok"] and m["n"] > 0 for m in res["sources"].values()))
        self.assertTrue(all(0 <= s["sev"] <= 4 for s in res["signals"]))
        self.assertTrue(all(s["age"] is None or s["age"] >= 0 for s in res["signals"]))
        self.assertTrue(all("t" not in s for s in res["signals"]))

    def test_all_sources_fail_exit_1_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(C, "fetch", side_effect=RuntimeError("down")):
            self.assertEqual(C.main(["--out", os.path.join(d, "site")]), 1)
            self.assertFalse(os.path.exists(os.path.join(d, "site")))

    def test_partial_failure_still_writes(self):
        def fake(url, form=None, **k):
            if "rain_24h" in url:
                raise RuntimeError("down")
            return C.mock_payloads(NOW)["wl" if "waterlevel" in url else "ews"]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(C, "fetch", side_effect=fake):
            self.assertEqual(C.main(["--out", d]), 0)
            with open(os.path.join(d, "latest.json"), encoding="utf-8") as f:
                out = json.load(f)
            self.assertFalse(out["sources"]["rain"]["ok"])
            self.assertTrue(out["sources"]["wl"]["ok"])

    def test_archive_appends(self):
        res = C.collect(mock=True, now=NOW)
        with tempfile.TemporaryDirectory() as d:
            C.write_archive(d, res)
            C.write_archive(d, res)
            with open(os.path.join(d, "2026-10-02.json"), encoding="utf-8") as f:
                snaps = json.load(f)
            self.assertEqual(len(snaps), 2)


if __name__ == "__main__":
    unittest.main()
