import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import watch, watch_vum  # noqa: E402


def grp(gid, cnt=1, pnu="1168010100106580021", price=9_800_000_000):
    return {"id": gid, "pnu": pnu, "shortAddress": "강남구 역삼동", "price": price, "itemCount": cnt,
            "propertyType2": {"label": "일반건물"},
            "summaryAttributes": [{"title": "대지면적", "content": "89.9"},
                                  {"title": "연면적", "content": "148.3"},
                                  {"title": "규모", "content": "3F/B1"}],
            "dealMethodTypes": [{"label": "실매물"}, {"label": "전속매물"}]}


class VumTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = watch.WatchStore(Path(self.tmp.name) / "w.db", None)

    def tearDown(self):
        self.ws.close()
        self.tmp.cleanup()

    def run_scan(self, groups, warmup=False):
        with mock.patch.object(watch_vum, "fetch_gu", return_value=groups):
            return watch_vum.scan(self.ws, [("1168000000", "강남구")], 40, "2026-10-14 09:00:00",
                                  warmup, sleep_sec=0)

    def test_jibun_from_pnu(self):
        self.assertEqual(watch_vum.jibun("1168010100106580021"), "658-21")
        self.assertEqual(watch_vum.jibun("1168010100106580000"), "658")
        self.assertEqual(watch_vum.jibun("1168010100206580000"), "산658")

    def test_message_fields(self):
        m = watch_vum.format_message(grp(1))
        self.assertIn("강남구 역삼동 658-21", m)
        self.assertIn("98억", m)
        self.assertIn("전속", m)
        self.assertLessEqual(len(m), 200)
        self.assertTrue(m.endswith("?dealType=SALES"))

    def test_warmup_records_without_alert_then_new_and_grown(self):
        r = self.run_scan([grp(1), grp(2)], warmup=True)
        self.assertEqual(r["vum_queued"], 0)
        self.assertEqual(self.ws.pending_count(), 0)
        r = self.run_scan([grp(1), grp(2, cnt=2), grp(3)])
        self.assertEqual((r["vum_new"], r["vum_added"], r["vum_queued"]), (1, 1, 2))
        kinds = sorted(a["kind"] for a in self.ws.pending(10))
        self.assertEqual(kinds, ["vum_added", "vum_new"])
        r = self.run_scan([grp(1), grp(2, cnt=2), grp(3)])   # 변화 없음 → 재알림 없음
        self.assertEqual(r["vum_queued"], 0)

    def test_all_gu_failure_raises(self):
        with mock.patch.object(watch_vum, "fetch_gu", side_effect=RuntimeError("x")):
            with self.assertRaises(RuntimeError):
                watch_vum.scan(self.ws, [("1168000000", "강남구")], 40, "2026-10-14 09:00:00",
                               False, sleep_sec=0)


if __name__ == "__main__":
    unittest.main()
