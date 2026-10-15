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

    def seed(self, groups):
        with mock.patch.object(watch_vum, "fetch_gu_all", return_value=groups):
            return watch_vum.seed(self.ws, [("1168000000", "강남구")], "2026-10-14 08:00:00", sleep_sec=0)

    def test_only_never_seen_address_alerts(self):
        A, B, C = "1168010100100010001", "1168010100100020002", "1168010100100030003"
        self.seed([grp(10, pnu=A), grp(20, pnu=B)])      # 시딩 시점에 이미 올라와 있던 주소들
        # id 가 아무리 커져도(광고 갱신/추가) 아는 주소면 알리지 않는다 — 2026-10-15 오탐 재현
        r = self.run_scan([grp(9000, cnt=8, pnu=A), grp(9001, cnt=4, pnu=B)])
        self.assertEqual((r["vum_new"], r["vum_queued"]), (0, 0))
        # 처음 보는 주소만 알린다
        r = self.run_scan([grp(9002, pnu=C), grp(9003, cnt=2, pnu=A)])
        self.assertEqual((r["vum_new"], r["vum_queued"]), (1, 1))
        al = self.ws.pending(10)
        self.assertEqual([x["kind"] for x in al], ["vum_new"])
        self.assertEqual(al[0]["article_no"], C)
        r = self.run_scan([grp(9004, pnu=C)])             # 같은 주소 재알림 없음
        self.assertEqual(r["vum_queued"], 0)

    def test_no_alert_before_seeding(self):
        r = self.run_scan([grp(1, pnu="1168010100100010001")])
        self.assertEqual((r["vum_new"], r["vum_queued"], r["vum_seeded"]), (1, 0, False))
        self.assertEqual(self.ws.pending_count(), 0)
        # 시딩 전이라도 기록은 남아 이후에 같은 주소가 알림으로 새지 않는다
        self.seed([])
        r = self.run_scan([grp(2, pnu="1168010100100010001")])
        self.assertEqual((r["vum_new"], r["vum_queued"]), (0, 0))

    def test_warmup_records_without_alert(self):
        self.seed([])
        r = self.run_scan([grp(1, pnu="1168010100100010001")], warmup=True)
        self.assertEqual((r["vum_new"], r["vum_queued"]), (1, 0))
        r = self.run_scan([grp(2, pnu="1168010100100010001")])
        self.assertEqual(r["vum_queued"], 0)

    def test_seed_failure_leaves_unseeded(self):
        with mock.patch.object(watch_vum, "fetch_gu_all", side_effect=RuntimeError("x")):
            with self.assertRaises(RuntimeError):
                watch_vum.seed(self.ws, [("1168000000", "강남구")], "2026-10-14 08:00:00", sleep_sec=0)
        self.assertIsNone(self.ws.get_state("vum_pnu_seeded"))

    def test_all_gu_failure_raises(self):
        with mock.patch.object(watch_vum, "fetch_gu", side_effect=RuntimeError("x")):
            with self.assertRaises(RuntimeError):
                watch_vum.scan(self.ws, [("1168000000", "강남구")], 40, "2026-10-14 09:00:00",
                               False, sleep_sec=0)


if __name__ == "__main__":
    unittest.main()
