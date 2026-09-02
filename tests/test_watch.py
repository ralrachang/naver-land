"""🔔 새 주소 실시간 감시기(app/watch.py) 단위 테스트 — 가짜 클라이언트, stdlib unittest."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import watch as W  # noqa: E402
from app.client import BlockedError, NaverLandError  # noqa: E402
from app.config import Config, CrawlCfg, DeployCfg, SiteCfg, WatchCfg  # noqa: E402
from app.store import Store  # noqa: E402

KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 9, 3, 8, 0, 0, tzinfo=KST)
GN, SC = "1168000000", "1165000000"   # 강남구, 서초구
KNOWN = ("37.500000", "127.030000")   # 홈페이지 DB(seen_locations)가 이미 아는 좌표


def art(no, lat, lng, **kw):
    base = {"articleNo": no, "latitude": lat, "longitude": lng,
            "realEstateTypeName": "건물", "articleRealEstateTypeName": "상가건물",
            "dealOrWarrantPrc": "230억", "area1": 255, "area2": 868, "floorInfo": "5/B1",
            "articleFeatureDesc": "압구정로데오역 도보 1분 삼거리 노출 건물",
            "articleConfirmYmd": "20260902", "cpName": "공실클럽", "isLocationShow": True}
    base.update(kw)
    return base


class FakeClient:
    """구 코드별 페이지 목록을 돌려주는 가짜 네이버 클라이언트."""

    def __init__(self, pages=None, details=None, block_gus=()):
        self.pages = pages or {}        # gu_code -> [page1_list, page2_list, ...]
        self.details = details or {}    # article_no -> articleDetail dict
        self.block_gus = set(block_gus)
        self.calls = []
        self.detail_calls = []

    def get_token(self, force=False):
        return "token"

    def articles(self, cortar, types, trade, order, page):
        if cortar in self.block_gus:
            raise BlockedError("403 차단 추정")
        self.calls.append((cortar, page))
        pg = self.pages.get(cortar, [])
        arts = pg[page - 1] if page - 1 < len(pg) else []
        return {"articleList": arts, "isMoreData": page < len(pg)}

    def article_detail(self, no):
        self.detail_calls.append(no)
        if no not in self.details:
            raise NaverLandError("HTTP 500")
        return {"articleDetail": self.details[no]}


def make_cfg(tmp: str) -> Config:
    cfg = Config(crawl=CrawlCfg(request_delay_sec=0, request_jitter_sec=0),
                 site=SiteCfg(), deploy=DeployCfg(), regions=[], root=Path(tmp),
                 watch=WatchCfg())
    for d in (cfg.data_dir, cfg.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    leaves = [
        {"cortarNo": "1168010300", "sido": "서울특별시", "gu": "강남구", "dong": "개포동"},
        {"cortarNo": "1168010400", "sido": "서울특별시", "gu": "강남구", "dong": "청담동"},
        {"cortarNo": "1165010100", "sido": "서울특별시", "gu": "서초구", "dong": "방배동"},
    ]
    cfg.regions_cache.write_text(json.dumps({"sig": "x", "regions": leaves}), encoding="utf-8")
    st = Store(cfg.db_path)
    st.conn.execute("INSERT INTO seen_locations(loc_key, first_seen_at) VALUES (?,?)",
                    (",".join(KNOWN), "2026-08-01 09:00:00"))
    st.conn.commit()
    st.close()
    return cfg


class WatchBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def warmup(self, pages=None):
        """첫 스캔(워밍업). 기본으로 강남구에 매물 A 하나."""
        pages = pages or {GN: [[art("A", "37.510000", "127.040000")]]}
        res = W.scan(self.cfg, now=T0, client=FakeClient(pages))
        self.assertTrue(res["warmup"])
        self.assertEqual(res["alerts"], [])
        return res


class TestGuList(unittest.TestCase):
    def test_derive_gu_from_dong_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(tmp)
            self.assertEqual(W.gu_list(cfg.regions_cache), [(GN, "강남구"), (SC, "서초구")])

    def test_missing_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(W.gu_list(Path(tmp) / "none.json"), [])


class TestScan(WatchBase):
    def test_first_scan_is_warmup_one_page_no_alert(self):
        client = FakeClient({GN: [[art("A", "37.510000", "127.040000")],
                                  [art("B", "37.511000", "127.041000")]]})
        res = W.scan(self.cfg, now=T0, client=client)
        self.assertTrue(res["ok"] and res["warmup"])
        self.assertEqual(res["new_listings"], 1)         # 2페이지는 안 감
        self.assertEqual(res["alerts"], [])
        self.assertEqual(client.calls, [(GN, 1), (SC, 1)])
        ws = W.WatchStore(self.cfg.watch_db_path, self.cfg.db_path)
        self.assertEqual(ws.seen_ids(), {"A"})
        ws.close()

    def test_second_scan_alerts_only_new_buildings(self):
        self.warmup()
        pages = {GN: [[
            art("N1", "37.520000", "127.050000"),            # 처음 보는 건물 → 알림
            art("K1", *KNOWN),                              # 홈페이지가 아는 좌표 → X
            art("J1", "37.510001", "127.040001"),            # A 의 지터 좌표 → X
            art("N2a", "37.530000", "127.060000"),           # 새 건물, 첫 매물 → 알림
            art("N2b", "37.530001", "127.060000"),           # 같은 새 건물 두 번째 → X
            art("A", "37.510000", "127.040000"),             # 이미 봄 → 페이지 중단
        ]]}
        client = FakeClient(pages, details={
            "N1": {"divisionName": "강남구", "sectionName": "청담동",
                   "exposureAddress": "서울시 강남구 청담동"}})
        res = W.scan(self.cfg, now=T0 + timedelta(minutes=10), client=client)
        self.assertFalse(res["warmup"])
        self.assertEqual(res["new_listings"], 5)
        self.assertEqual(res["new_addresses"], 2)
        self.assertEqual(len(res["alerts"]), 2)
        m1, m2 = res["alerts"][0]["message"], res["alerts"][1]["message"]
        self.assertIn("강남구 청담동", m1)
        self.assertTrue(m1.endswith("https://m.land.naver.com/article/info/N1"))
        self.assertIn("상가건물 230억", m1)
        self.assertIn("🆕 새주소 · 강남구\n", m2)              # 상세 실패 → 구만
        self.assertTrue(m2.endswith("/article/info/N2a"))
        self.assertEqual(client.detail_calls, ["N1", "N2a"])
        for a in res["alerts"]:
            self.assertEqual(a["kind"], "new_address")
            self.assertLessEqual(len(a["message"]), W.MAX_MSG_CHARS)

    def test_page_stops_at_first_seen_item_and_caps(self):
        self.warmup()
        pages = {GN: [[art("N1", "37.520000", "127.050000"), art("N2", "37.521000", "127.051000")],
                      [art("N3", "37.522000", "127.052000"), art("A", "37.510000", "127.040000")],
                      [art("N4", "37.523000", "127.053000")]]}
        client = FakeClient(pages)
        res = W.scan(self.cfg, now=T0 + timedelta(minutes=10), client=client)
        self.assertEqual([c for c in client.calls if c[0] == GN], [(GN, 1), (GN, 2)])
        self.assertEqual(res["new_listings"], 3)
        # max_pages 상한
        self.cfg.watch.max_pages = 2
        pages = {GN: [[art(f"P{i}{j}", f"37.6{i}{j}000", "127.000000")] for i in range(4)
                      for j in range(1)]}
        client = FakeClient(pages)
        W.scan(self.cfg, now=T0 + timedelta(minutes=20), client=client)
        self.assertEqual([c for c in client.calls if c[0] == GN], [(GN, 1), (GN, 2)])

    def test_gap_resets_to_warmup(self):
        self.warmup()
        client = FakeClient({GN: [[art("N1", "37.520000", "127.050000")]]})
        res = W.scan(self.cfg, now=T0 + timedelta(hours=2), client=client)
        self.assertTrue(res["warmup"])
        self.assertEqual(res["alerts"], [])
        # 그 다음 틱부터는 알림
        client = FakeClient({GN: [[art("N2", "37.540000", "127.070000"),
                                   art("N1", "37.520000", "127.050000")]]})
        res = W.scan(self.cfg, now=T0 + timedelta(hours=2, minutes=10), client=client)
        self.assertFalse(res["warmup"])
        self.assertEqual(len(res["alerts"]), 1)

    def test_blocked_pauses_and_notifies_once(self):
        self.warmup()
        t1 = T0 + timedelta(minutes=10)
        client = FakeClient({GN: [[art("N1", "37.520000", "127.050000")]]}, block_gus={SC})
        res = W.scan(self.cfg, now=t1, client=client)
        self.assertFalse(res["ok"])
        self.assertIn("blocked", res["error"])
        self.assertEqual(len(res["alerts"]), 1)
        self.assertEqual(res["alerts"][0]["kind"], "notice")
        self.assertIn("차단", res["alerts"][0]["message"])
        # 휴식 중엔 스캔 안 함(대기열만), 안내 중복 없음
        client2 = FakeClient({GN: [[art("N1", "37.520000", "127.050000")]]})
        res = W.scan(self.cfg, now=t1 + timedelta(minutes=5), client=client2)
        self.assertTrue(res["skipped"].startswith("blocked_until"))
        self.assertEqual(client2.calls, [])
        self.assertEqual(len(res["alerts"]), 1)
        # 휴식 끝 → 재개, 차단 직전에 봤던 N1 은 기록 안 됐으므로 이제 알림
        res = W.scan(self.cfg, now=t1 + timedelta(minutes=31), client=client2)
        self.assertTrue(res["ok"])
        self.assertEqual(res["new_addresses"], 1)
        kinds = [a["kind"] for a in res["alerts"]]
        self.assertEqual(kinds, ["notice", "new_address"])

    def test_lock_prevents_concurrent_scan(self):
        self.warmup()
        lock = self.cfg.data_dir / "watch.lock"
        lock.write_text("123")
        client = FakeClient({GN: [[art("N1", "37.520000", "127.050000")]]})
        res = W.scan(self.cfg, now=T0 + timedelta(minutes=10), client=client)
        self.assertEqual(res["skipped"], "scan_running")
        self.assertEqual(client.calls, [])
        old = time.time() - 600
        os.utime(lock, (old, old))                       # 5분 넘은 잠금은 stale
        res = W.scan(self.cfg, now=T0 + timedelta(minutes=10), client=client)
        self.assertTrue(res["ok"])
        self.assertFalse(lock.exists())

    def test_no_region_cache_is_error(self):
        self.cfg.regions_cache.unlink()
        res = W.scan(self.cfg, now=T0, client=FakeClient())
        self.assertFalse(res["ok"])
        self.assertIn("regions", res["error"])


class TestQueue(WatchBase):
    def test_pending_cap_ack_expire(self):
        ws = W.WatchStore(self.cfg.watch_db_path, self.cfg.db_path)
        ts = T0.strftime("%Y-%m-%d %H:%M:%S")
        ids = [ws.add_alert("new_address", str(i), f"msg{i}", ts) for i in range(20)]
        pend = ws.pending(15)
        self.assertEqual([p["id"] for p in pend], ids[:15])
        self.assertEqual(ws.ack(ids[:15], ts), 15)
        self.assertEqual([p["id"] for p in ws.pending(15)], ids[15:])
        self.assertEqual(ws.expire(180, T0 + timedelta(minutes=181)), 5)
        self.assertEqual(ws.pending(15), [])
        ws.close()

    def test_ack_and_status_api(self):
        self.warmup()
        client = FakeClient({GN: [[art("N1", "37.520000", "127.050000"),
                                   art("A", "37.510000", "127.040000")]]})
        res = W.scan(self.cfg, now=T0 + timedelta(minutes=10), client=client)
        aid = res["alerts"][0]["id"]
        self.assertEqual(W.ack(self.cfg, [aid], now=T0 + timedelta(minutes=11))["acked"], 1)
        st = W.status(self.cfg, now=T0 + timedelta(minutes=12))
        self.assertEqual(st["pending_total"], 0)
        self.assertEqual(st["sent_today"], 1)
        self.assertEqual(st["last_scan_at"], (T0 + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"))


class TestMessage(unittest.TestCase):
    def item(self, **kw):
        base = {"article_no": "123", "gu": "강남구", "dong": "청담동", "re_type": "건물",
                "sub_type": "상가건물", "price_text": "230억", "area1": 255, "area2": 868,
                "floor": "5/B1", "feature_desc": "압구정로데오역 도보 1분 삼거리 노출 건물"}
        base.update(kw)
        return base

    def test_full(self):
        msg = W.format_message(self.item())
        self.assertEqual(msg.split("\n"), [
            "🆕 새주소 · 강남구 청담동",
            "상가건물 230억 · 대지255/연868㎡ · 5/B1",
            "압구정로데오역 도보 1분 삼거리 노출 건물",
            "https://m.land.naver.com/article/info/123"])

    def test_long_desc_is_trimmed_to_limit(self):
        msg = W.format_message(self.item(feature_desc="가" * 500))
        self.assertLessEqual(len(msg), W.MAX_MSG_CHARS)
        self.assertTrue(msg.endswith("https://m.land.naver.com/article/info/123"))
        self.assertIn("…", msg)

    def test_land_without_area2_floor_dong(self):
        msg = W.format_message(self.item(dong="", sub_type="", re_type="토지", area2=0,
                                         floor="", feature_desc=""))
        self.assertEqual(msg.split("\n"), [
            "🆕 새주소 · 강남구",
            "토지 230억 · 대지255㎡",
            "https://m.land.naver.com/article/info/123"])


if __name__ == "__main__":
    unittest.main()
