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

    def test_keep_days_zero_accumulates(self):
        st = Store(self.db)
        st.upsert([self._item("A"), self._item("B")], "2026-06-01 09:00:00")
        st.upsert([self._item("A")], "2026-07-06 09:00:00")  # B 미목격
        st.deactivate_stale(0, "2026-07-06 09:00:00")  # 0 = 누적 보관(비활성화 안 함)
        ids = {r["article_no"] for r in st.active_listings()}
        self.assertEqual(ids, {"A", "B"})
        st.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
