"""단위 테스트 — 가격 파싱, 신규 판별(store), 지역 재귀 규칙. stdlib unittest."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.crawler import parse_price_manwon
from app.regions import _is_container, build
from app.store import Store


class TestPrice(unittest.TestCase):
    def test_eok_and_man(self):
        self.assertEqual(parse_price_manwon("26억 2,000"), 262000)
        self.assertEqual(parse_price_manwon("9억 5,000"), 95000)

    def test_eok_only(self):
        self.assertEqual(parse_price_manwon("67억"), 670000)
        self.assertEqual(parse_price_manwon("1억"), 10000)

    def test_man_only(self):
        self.assertEqual(parse_price_manwon("5,000"), 5000)
        self.assertEqual(parse_price_manwon("500"), 500)

    def test_no_space(self):
        self.assertEqual(parse_price_manwon("12억5,000"), 125000)

    def test_invalid(self):
        self.assertIsNone(parse_price_manwon(""))
        self.assertIsNone(parse_price_manwon("가격문의"))


class TestRegionRule(unittest.TestCase):
    def test_container_detection(self):
        self.assertTrue(_is_container("1100000000"))   # 서울
        self.assertTrue(_is_container("1168000000"))   # 강남구
        self.assertFalse(_is_container("1168010300"))  # 개포동(leaf)

    def test_build_with_seed_dong(self):
        # 동을 직접 seed 로 주면 API 호출 없이 leaf 로 처리
        leaves = build(client=None,
                       targets=[{"cortarNo": "1168010300", "sido": "서울특별시",
                                 "gu": "강남구", "dong": "개포동"}],
                       cache_path=None, use_cache=False)
        self.assertEqual(len(leaves), 1)
        self.assertEqual(leaves[0]["address"], "서울특별시 강남구 개포동")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "t.db"

    def _item(self, ano, price="10억", addr="서울특별시 강남구 개포동"):
        return {"article_no": ano, "address": addr, "sido": "서울특별시",
                "gu": "강남구", "dong": "개포동", "price_text": price,
                "price_manwon": 100000, "re_type": "건물", "article_name": "빌딩",
                "confirm_ymd": "20260708", "feature_desc": "", "area": 100,
                "floor": "", "lat": "37.0", "lng": "127.0"}

    def test_new_then_seen(self):
        st = Store(self.db)
        r1 = st.upsert([self._item("A"), self._item("B")], "2026-07-09 09:00:00")
        self.assertEqual(r1["new_count"], 2)
        self.assertEqual(sorted(r1["new"]), ["A", "B"])
        # 2회차: A 다시 + C 신규 → C만 new
        r2 = st.upsert([self._item("A"), self._item("C")], "2026-07-09 16:00:00")
        self.assertEqual(r2["new_count"], 1)
        self.assertEqual(r2["new"], ["C"])
        self.assertEqual(r2["seen_count"], 1)
        self.assertEqual(st.count_active(), 3)
        st.close()

    def test_active_listings_new_first(self):
        st = Store(self.db)
        st.upsert([self._item("A"), self._item("B")], "2026-07-09 09:00:00")
        r = st.upsert([self._item("C")], "2026-07-09 16:00:00")
        rows = st.active_listings(new_ids=set(r["new"]))
        self.assertTrue(rows[0]["is_new"])  # 신규가 맨 앞
        self.assertEqual(rows[0]["article_no"], "C")
        st.close()


class TestDelisting(unittest.TestCase):
    """블로커 회귀: full-scan 재목격 시 last_seen 이 갱신되어 삭제판정이 올바른지."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "t.db"

    def _item(self, ano):
        return {"article_no": ano, "address": "서울특별시 강남구 개포동", "sido": "서울특별시",
                "gu": "강남구", "dong": "개포동", "price_text": "10억", "price_manwon": 100000,
                "re_type": "건물", "article_name": "빌딩", "confirm_ymd": "20260708",
                "feature_desc": "", "area": 100, "floor": "", "lat": "37.0", "lng": "127.0"}

    def _last_seen(self, st, ano):
        return st.conn.execute(
            "SELECT last_seen_at FROM listings WHERE article_no=?", (ano,)).fetchone()[0]

    def test_last_seen_advances_on_reobserve(self):
        st = Store(self.db)
        st.upsert([self._item("A"), self._item("B")], "2026-06-01 09:00:00")
        # 전체 스캔 재실행 — A,B 여전히 존재(둘 다 items 에 포함)
        st.upsert([self._item("A"), self._item("B")], "2026-06-02 09:00:00")
        self.assertEqual(self._last_seen(st, "A"), "2026-06-02 09:00:00")
        st.close()

    def test_deactivation_drops_only_delisted(self):
        st = Store(self.db)
        st.upsert([self._item("A"), self._item("B")], "2026-06-01 09:00:00")
        # 35일 뒤: A만 목격(B는 삭제됨) → keep_days=30 이면 B만 비활성화
        st.upsert([self._item("A")], "2026-07-06 09:00:00")
        st.deactivate_stale(30, "2026-07-06 09:00:00")
        ids = {r["article_no"] for r in st.active_listings()}
        self.assertIn("A", ids)
        self.assertNotIn("B", ids)
        st.close()

    def test_deactivate_by_age_rolling_feed(self):
        # early_stop 모드: 올라온 지 keep_days 지난 매물은 빠지고 최근 것만 유지
        st = Store(self.db)
        st.upsert([self._item("OLD")], "2026-06-01 09:00:00")   # 오래됨
        st.upsert([self._item("NEW")], "2026-07-06 09:00:00")   # 최근
        st.deactivate_by_age(14, "2026-07-06 09:00:00")         # 14일 초과 제외
        ids = {r["article_no"] for r in st.active_listings()}
        self.assertIn("NEW", ids)
        self.assertNotIn("OLD", ids)
        st.close()

    def test_keep_days_zero_accumulates(self):
        st = Store(self.db)
        st.upsert([self._item("A"), self._item("B")], "2026-06-01 09:00:00")
        st.upsert([self._item("A")], "2026-07-06 09:00:00")  # B 미목격
        st.deactivate_stale(0, "2026-07-06 09:00:00")  # 0 = 누적 보관(비활성화 안 함)
        ids = {r["article_no"] for r in st.active_listings()}
        self.assertEqual(ids, {"A", "B"})
        st.close()


class TestLocations(unittest.TestCase):
    """위경도 기반 광고수(loc_count) + '이전엔 없던 위치'(새 주소) 판정."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "t.db"

    def _item(self, ano, lat, lng):
        return {"article_no": ano, "address": "서울특별시 강남구 개포동", "sido": "서울특별시",
                "gu": "강남구", "dong": "개포동", "price_text": "10억", "price_manwon": 100000,
                "re_type": "건물", "article_name": "빌딩", "confirm_ymd": "20260708",
                "same_addr_cnt": None, "feature_desc": "", "area": 100, "floor": "",
                "lat": lat, "lng": lng}

    def test_loc_count_and_new_location(self):
        st = Store(self.db)
        # A,B = 같은 건물(같은 좌표), C = 다른 좌표
        items = [self._item("A", "37.1", "127.1"), self._item("B", "37.1", "127.1"),
                 self._item("C", "37.2", "127.2")]
        st.upsert(items, "2026-07-09 09:00:00")
        newloc = st.register_locations(items, "2026-07-09 09:00:00")
        self.assertEqual(newloc, {"37.1,127.1", "37.2,127.2"})  # 위치 2곳 다 처음
        rows = {r["article_no"]: r for r in st.active_listings(new_loc_keys=newloc)}
        self.assertEqual(rows["A"]["loc_count"], 2)   # 같은 좌표 2개 → 단독 아님
        self.assertEqual(rows["C"]["loc_count"], 1)   # 단독
        self.assertTrue(rows["C"]["is_new_location"])

    def test_previously_seen_location_not_new(self):
        st = Store(self.db)
        st.upsert([self._item("A", "37.1", "127.1")], "2026-07-09 09:00:00")
        st.register_locations([self._item("A", "37.1", "127.1")], "2026-07-09 09:00:00")
        # 다음날: 같은 위치 D(기존) + 새 위치 E
        d, e = self._item("D", "37.1", "127.1"), self._item("E", "37.9", "127.9")
        st.upsert([d, e], "2026-07-10 09:00:00")
        newloc2 = st.register_locations([d, e], "2026-07-10 09:00:00")
        self.assertEqual(newloc2, {"37.9,127.9"})     # 기존 위치는 새 주소 아님
        st.close()


class TestPreciseSolo(unittest.TestCase):
    """💎 정밀단독: 이력 전체(비활성 포함)+면적 결합+마스킹 좌표 제외."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "t.db"

    def _item(self, ano, lat="37.1", lng="127.1", area=100):
        return {"article_no": ano, "address": "서울특별시 강남구 개포동", "sido": "서울특별시",
                "gu": "강남구", "dong": "개포동", "price_text": "10억",
                "price_manwon": 100000, "re_type": "건물", "article_name": "빌딩",
                "confirm_ymd": "20260708", "same_addr_cnt": None,
                "price_change_state": "", "feature_desc": "", "area": area,
                "floor": "", "lat": lat, "lng": lng}

    def _flags(self, st, **kw):
        return {r["article_no"]: r["is_precise_solo"] for r in st.active_listings(**kw)}

    def test_same_coord_diff_area_not_solo(self):
        # 같은 좌표+다른 면적 = 같은 건물의 중복 광고(실측: 중개업소마다 대지면적을
        # 다르게 적음, 연면적은 동일) → 면적으로 나누지 않고 단독 아님으로 판정
        st = Store(self.db)
        st.upsert([self._item("A", area=187), self._item("B", area=434)],
                  "2026-07-09 09:00:00")
        f = self._flags(st)
        self.assertFalse(f["A"])
        self.assertFalse(f["B"])
        st.close()

    def test_same_coord_same_area_not_solo(self):
        st = Store(self.db)
        st.upsert([self._item("A"), self._item("B")], "2026-07-09 09:00:00")
        f = self._flags(st)
        self.assertFalse(f["A"])
        self.assertFalse(f["B"])
        st.close()

    def test_alone_at_coord_is_solo(self):
        # 좌표에 광고가 자기 자신뿐이면 정밀단독(면적 미상이어도 무관)
        st = Store(self.db)
        st.upsert([self._item("A", lat="37.1", lng="127.1", area=None),
                   self._item("B", lat="37.2", lng="127.2")],
                  "2026-07-09 09:00:00")
        f = self._flags(st)
        self.assertTrue(f["A"])
        self.assertTrue(f["B"])
        st.close()

    def test_inactive_history_still_counts(self):
        # 롤링으로 목록에서 빠진 옛 광고도 윈도우 안이면 광고 수에 포함(가짜 단독 방지)
        st = Store(self.db)
        st.upsert([self._item("OLD")], "2026-06-20 09:00:00")
        st.upsert([self._item("NEW2")], "2026-07-09 09:00:00")
        st.deactivate_by_age(14, "2026-07-09 09:00:00")  # OLD 비활성화
        rows = {r["article_no"]: r for r in st.active_listings(solo_window_days=30)}
        self.assertNotIn("OLD", rows)                      # 목록에는 없지만
        self.assertEqual(rows["NEW2"]["loc_count"], 1)     # 단순 판정은 단독인데
        self.assertFalse(rows["NEW2"]["is_precise_solo"])  # 정밀 판정은 아님
        st.close()

    def test_history_beyond_window_ignored(self):
        # 윈도우(30일) 밖의 옛 광고는 만료로 보고 무시 → 정밀단독 인정
        st = Store(self.db)
        st.upsert([self._item("OLD")], "2026-05-01 09:00:00")
        st.upsert([self._item("NEW2")], "2026-07-09 09:00:00")
        st.deactivate_by_age(14, "2026-07-09 09:00:00")
        f = self._flags(st, solo_window_days=30)
        self.assertTrue(f["NEW2"])
        st.close()

    def test_masked_coord_stack_not_solo(self):
        # 위치 마스킹으로 한 좌표에 광고가 대량으로 쌓여도(면적 제각각) 전부 단독 아님
        st = Store(self.db)
        items = [self._item(f"M{i}", area=100 + i) for i in range(10)]
        st.upsert(items, "2026-07-09 09:00:00")
        f = self._flags(st)
        self.assertFalse(any(f.values()))
        st.close()


class TestPriceCut(unittest.TestCase):
    """가격 인하 감지: 재목격 시 가격 비교 + 네이버 priceChangeState."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "t.db"

    def _item(self, ano, price_text="10억", manwon=100000, state=""):
        return {"article_no": ano, "address": "서울특별시 강남구 개포동", "sido": "서울특별시",
                "gu": "강남구", "dong": "개포동", "price_text": price_text,
                "price_manwon": manwon, "re_type": "건물", "article_name": "빌딩",
                "confirm_ymd": "20260708", "same_addr_cnt": None,
                "price_change_state": state, "feature_desc": "", "area": 100,
                "floor": "", "lat": "37.0", "lng": "127.0"}

    def _row(self, st, ano):
        rows = {r["article_no"]: r for r in st.active_listings()}
        return rows[ano]

    def test_price_drop_detected(self):
        st = Store(self.db)
        st.upsert([self._item("A")], "2026-07-01 09:00:00")
        st.upsert([self._item("A", "9억", 90000)], "2026-07-05 09:00:00")
        r = self._row(st, "A")
        self.assertEqual(r["prev_price_manwon"], 100000)
        self.assertEqual(r["prev_price_text"], "10억")
        self.assertEqual(r["price_changed_at"], "2026-07-05 09:00:00")
        self.assertTrue(r["is_price_cut"])
        st.close()

    def test_price_same_no_change_recorded(self):
        st = Store(self.db)
        st.upsert([self._item("A")], "2026-07-01 09:00:00")
        st.upsert([self._item("A")], "2026-07-05 09:00:00")
        r = self._row(st, "A")
        self.assertIsNone(r["prev_price_manwon"])
        self.assertFalse(r["is_price_cut"])
        st.close()

    def test_price_increase_not_cut(self):
        st = Store(self.db)
        st.upsert([self._item("A")], "2026-07-01 09:00:00")
        st.upsert([self._item("A", "11억", 110000)], "2026-07-05 09:00:00")
        r = self._row(st, "A")
        self.assertEqual(r["prev_price_manwon"], 100000)  # 변동은 기록되지만
        self.assertFalse(r["is_price_cut"])               # 인하는 아님
        st.close()

    def test_second_drop_overwrites_prev(self):
        st = Store(self.db)
        st.upsert([self._item("A")], "2026-07-01 09:00:00")
        st.upsert([self._item("A", "9억", 90000)], "2026-07-03 09:00:00")
        st.upsert([self._item("A", "8억", 80000)], "2026-07-05 09:00:00")
        r = self._row(st, "A")
        self.assertEqual(r["prev_price_manwon"], 90000)   # 직전 가격 기준
        self.assertEqual(r["price_changed_at"], "2026-07-05 09:00:00")
        st.close()

    def test_naver_state_decrease_is_cut(self):
        st = Store(self.db)
        st.upsert([self._item("A", state="DECREASE")], "2026-07-01 09:00:00")
        self.assertTrue(self._row(st, "A")["is_price_cut"])
        st.close()

    def test_cut_keeps_listing_in_rolling_feed(self):
        # 올라온 지 오래돼도 최근 가격인하가 있으면 신규 피드에 유지
        st = Store(self.db)
        st.upsert([self._item("OLD")], "2026-06-01 09:00:00")
        st.upsert([self._item("OLD", "9억", 90000)], "2026-07-05 09:00:00")
        st.deactivate_by_age(14, "2026-07-06 09:00:00")
        ids = {r["article_no"] for r in st.active_listings()}
        self.assertIn("OLD", ids)
        # 인하마저 오래되면 제외
        st.deactivate_by_age(14, "2026-08-01 09:00:00")
        ids = {r["article_no"] for r in st.active_listings()}
        self.assertNotIn("OLD", ids)
        st.close()

    def test_sort_cut_after_new_before_rest(self):
        st = Store(self.db)
        st.upsert([self._item("PLAIN"), self._item("CUT")], "2026-07-01 09:00:00")
        st.upsert([self._item("CUT", "9억", 90000)], "2026-07-04 09:00:00")
        r = st.upsert([self._item("NEWB")], "2026-07-05 09:00:00")
        order = [x["article_no"] for x in st.active_listings(new_ids=set(r["new"]))]
        self.assertEqual(order, ["NEWB", "CUT", "PLAIN"])
        st.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
