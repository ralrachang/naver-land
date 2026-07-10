"""SQLite 저장 + 신규 판별 + 누적 보관."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("naver_land.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    article_no   TEXT PRIMARY KEY,
    address      TEXT,
    sido         TEXT,
    gu           TEXT,
    dong         TEXT,
    price_text   TEXT,
    price_manwon INTEGER,
    re_type      TEXT,
    article_name TEXT,
    confirm_ymd  TEXT,
    same_addr_cnt INTEGER,
    price_change_state TEXT,
    prev_price_manwon INTEGER,
    prev_price_text  TEXT,
    price_changed_at TEXT,
    feature_desc TEXT,
    area         REAL,
    floor        TEXT,
    lat          TEXT,
    lng          TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_first_seen ON listings(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_active ON listings(is_active);

CREATE TABLE IF NOT EXISTS runs (
    run_at    TEXT PRIMARY KEY,
    new_count INTEGER,
    seen_count INTEGER,
    total_active INTEGER,
    ok        INTEGER,
    note      TEXT
);

-- 우리가 지금껏 광고를 본 모든 위치(위경도). 매물이 만료돼도 지워지지 않아
-- '이전엔 없던 위치' 판정의 기준이 된다.
CREATE TABLE IF NOT EXISTS seen_locations (
    loc_key       TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """기존 DB에 없는 컬럼 추가 + 위치 baseline 백필(하위호환)."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(listings)")}
        if "same_addr_cnt" not in cols:
            self.conn.execute("ALTER TABLE listings ADD COLUMN same_addr_cnt INTEGER")
        for col, typ in (("price_change_state", "TEXT"),
                         ("prev_price_manwon", "INTEGER"),
                         ("prev_price_text", "TEXT"),
                         ("price_changed_at", "TEXT")):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {typ}")
        # seen_locations 가 비어있고 기존 매물이 있으면, 현재 매물의 위치를 baseline 으로
        # 저장(각 위치의 최초 목격 시각). 이후 이 목록에 없는 위치만 '새 주소'가 된다.
        has_loc = self.conn.execute("SELECT 1 FROM seen_locations LIMIT 1").fetchone()
        if not has_loc:
            self.conn.execute(
                "INSERT OR IGNORE INTO seen_locations(loc_key, first_seen_at) "
                "SELECT lat||','||lng, MIN(first_seen_at) FROM listings "
                "WHERE lat IS NOT NULL AND lat!='' GROUP BY lat||','||lng"
            )

    def close(self):
        self.conn.close()

    def existing_ids(self) -> set[str]:
        cur = self.conn.execute("SELECT article_no FROM listings")
        return {r[0] for r in cur.fetchall()}

    def upsert(self, items: list[dict], run_ts: str) -> dict:
        """items 를 저장. 반환: {'new': [...], 'new_count', 'seen_count'}"""
        new_ids: list[str] = []
        seen = 0
        for it in items:
            ano = it["article_no"]
            if not ano:
                continue
            row = self.conn.execute(
                "SELECT price_manwon, price_text FROM listings WHERE article_no=?", (ano,)
            ).fetchone()
            if row is None:
                self.conn.execute(
                    """INSERT INTO listings
                       (article_no,address,sido,gu,dong,price_text,price_manwon,
                        re_type,article_name,confirm_ymd,same_addr_cnt,price_change_state,
                        feature_desc,area,floor,
                        lat,lng,first_seen_at,last_seen_at,is_active)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (ano, it["address"], it.get("sido"), it.get("gu"), it.get("dong"),
                     it["price_text"], it.get("price_manwon"), it.get("re_type"),
                     it.get("article_name"), it.get("confirm_ymd"), it.get("same_addr_cnt"),
                     it.get("price_change_state"),
                     it.get("feature_desc"), it.get("area"), it.get("floor"),
                     it.get("lat"), it.get("lng"), run_ts, run_ts),
                )
                new_ids.append(ano)
            else:
                # 가격 변동 감지: 재목격 시 저장된 가격과 다르면 이전가·시점을 기록.
                # (인하/인상 모두 기록하고, '인하' 여부는 표시 단계에서 판정)
                old_man, new_man = row["price_manwon"], it.get("price_manwon")
                if old_man is not None and new_man is not None and new_man != old_man:
                    self.conn.execute(
                        """UPDATE listings SET last_seen_at=?, price_text=?, price_manwon=?,
                           same_addr_cnt=?, price_change_state=?,
                           prev_price_manwon=?, prev_price_text=?, price_changed_at=?,
                           is_active=1 WHERE article_no=?""",
                        (run_ts, it["price_text"], new_man, it.get("same_addr_cnt"),
                         it.get("price_change_state"), old_man, row["price_text"],
                         run_ts, ano),
                    )
                else:
                    self.conn.execute(
                        """UPDATE listings SET last_seen_at=?, price_text=?, price_manwon=?,
                           same_addr_cnt=?, price_change_state=?, is_active=1
                           WHERE article_no=?""",
                        (run_ts, it["price_text"], new_man, it.get("same_addr_cnt"),
                         it.get("price_change_state"), ano),
                    )
                seen += 1
        self.conn.commit()
        log.info("저장: 신규 %d건, 갱신 %d건", len(new_ids), seen)
        return {"new": new_ids, "new_count": len(new_ids), "seen_count": seen}

    def register_locations(self, items: list[dict], run_ts: str) -> set:
        """관측된 매물의 위치를 seen_locations 에 등록. 처음 보는 위치만 run_ts 로 기록.
        반환: 이번에 처음 등장한 위치(loc_key) 집합 = '새 주소'."""
        new_keys = set()
        for it in items:
            lat, lng = it.get("lat"), it.get("lng")
            if not (lat and lng):
                continue
            key = f"{lat},{lng}"
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO seen_locations(loc_key, first_seen_at) VALUES (?,?)",
                (key, run_ts))
            if cur.rowcount:
                new_keys.add(key)
        self.conn.commit()
        log.info("새 주소(이전에 없던 위치): %d개", len(new_keys))
        return new_keys

    def latest_location_batch(self) -> set:
        """가장 최근에 처음 등장한 위치 집합(재생성 시 '새 주소' 표시용)."""
        row = self.conn.execute(
            "SELECT MAX(first_seen_at) FROM seen_locations").fetchone()
        if not row or not row[0]:
            return set()
        cur = self.conn.execute(
            "SELECT loc_key FROM seen_locations WHERE first_seen_at=?", (row[0],))
        return {r[0] for r in cur.fetchall()}

    def deactivate_stale(self, keep_days: int, now_ts: str):
        """삭제(더 이상 목격 안 됨) 매물 비활성화.

        전제: 전체 스캔 시 present 매물은 upsert 로 last_seen_at 이 now_ts 로 갱신됨.
        따라서 last_seen_at 이 오래된 항목 = 이번에 목격 안 된 항목(삭제 추정).
        keep_days<=0 이면 비활성화하지 않고 계속 누적 보관한다.
        """
        if keep_days and keep_days > 0:
            self.conn.execute(
                """UPDATE listings SET is_active=0
                   WHERE julianday(?) - julianday(last_seen_at) > ?""",
                (now_ts, keep_days),
            )
            self.conn.commit()

    def deactivate_by_age(self, keep_days: int, now_ts: str):
        """올라온 지(=first_seen) keep_days 초과한 매물을 비활성화(신규 피드 유지용).

        early_stop 모드에서 사용: 전체를 스캔하지 않으므로 '삭제' 판정 대신 '최근 N일'
        기준으로 목록을 롤링한다. keep_days<=0 이면 무제한 누적.
        단, 최근 N일 내 가격 변동(인하 감지)이 있던 매물은 오래됐어도 피드에 유지한다.
        """
        if keep_days and keep_days > 0:
            self.conn.execute(
                """UPDATE listings SET is_active=0
                   WHERE julianday(?) - julianday(first_seen_at) > ?
                     AND (price_changed_at IS NULL
                          OR julianday(?) - julianday(price_changed_at) > ?)""",
                (now_ts, keep_days, now_ts, keep_days),
            )
            self.conn.commit()

    def record_run(self, run_ts: str, new_count: int, seen_count: int,
                   total_active: int, ok: bool, note: str = ""):
        self.conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)",
            (run_ts, new_count, seen_count, total_active, 1 if ok else 0, note),
        )
        self.conn.commit()

    def active_listings(self, new_ids: set[str] | None = None,
                        new_loc_keys: set | None = None,
                        solo_window_days: int = 30,
                        mega_coord_threshold: int = 10) -> list[dict]:
        """표시용 목록. 신규 우선, 그다음 최초 발견 최신순.

        - loc_count: 같은 위경도(=같은 건물/토지)의 '활성' 광고 수(단순 판정, 🎯단독).
          네이버 sameAddrCnt는 토지/건물에서 부정확하므로 위경도로 직접 센다.
        - is_precise_solo(💎정밀단독): 최근 solo_window_days일 내 목격된 광고 '이력
          전체'(비활성 포함)를 좌표+면적으로 묶어 광고 1개일 때만 True. 좌표에 광고가
          mega_coord_threshold개 이상 겹치면 위치 마스킹(대표좌표) 의심 → 판정 제외.
          면적 미상인 광고가 같은 좌표에 있으면 같은 물건일 수 있어 광고 수에 합산(보수적).
        - is_new_location: 이전엔 없던 위치(seen_locations 기준)에 처음 등장한 매물.
        - is_price_cut: 가격 인하(급매 신호). 자체 감지(재목격 시 가격이 내려감) 또는
          네이버 priceChangeState=DECREASE.
        """
        new_ids = new_ids or set()
        new_loc_keys = new_loc_keys or set()
        loc_count: dict = {}
        for r in self.conn.execute(
            "SELECT lat, lng, COUNT(*) c FROM listings "
            "WHERE is_active=1 AND lat IS NOT NULL AND lat!='' GROUP BY lat, lng"
        ):
            loc_count[(r["lat"], r["lng"])] = r["c"]
        # 정밀단독용 카운트 — 기준 시각은 DB의 최근 목격 시각(재생성 시에도 동일 판정).
        coord_total: dict = {}
        group_count: dict = {}
        noarea_count: dict = {}
        ref = self.conn.execute("SELECT MAX(last_seen_at) FROM listings").fetchone()[0]
        cutoff = ""
        if ref:
            ref_dt = datetime.strptime(ref, "%Y-%m-%d %H:%M:%S")
            cutoff = (ref_dt - timedelta(days=solo_window_days)).strftime(
                "%Y-%m-%d %H:%M:%S")
            for r in self.conn.execute(
                "SELECT lat, lng, area FROM listings "
                "WHERE lat IS NOT NULL AND lat!='' "
                "AND julianday(?) - julianday(last_seen_at) <= ?",
                (ref, solo_window_days),
            ):
                k = (r["lat"], r["lng"])
                coord_total[k] = coord_total.get(k, 0) + 1
                if r["area"] is None:
                    noarea_count[k] = noarea_count.get(k, 0) + 1
                else:
                    g = (r["lat"], r["lng"], round(r["area"]))
                    group_count[g] = group_count.get(g, 0) + 1
        cur = self.conn.execute(
            "SELECT * FROM listings WHERE is_active=1 ORDER BY first_seen_at DESC, price_manwon DESC"
        )
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["is_new"] = r["article_no"] in new_ids
            r["loc_count"] = loc_count.get((r["lat"], r["lng"]), 1)
            r["is_new_location"] = f"{r['lat']},{r['lng']}" in new_loc_keys
            lat, lng, area = r["lat"], r["lng"], r["area"]
            ps = False
            # 자신도 윈도우 안이어야 함(밖이면 카운트에 자신이 빠져 오판 가능)
            if lat and lng and area is not None and r["last_seen_at"] >= cutoff:
                k = (lat, lng)
                if coord_total.get(k, 0) < mega_coord_threshold:
                    n = (group_count.get((lat, lng, round(area)), 0)
                         + noarea_count.get(k, 0))
                    ps = (n == 1)
            r["is_precise_solo"] = ps
            own_cut = (r.get("prev_price_manwon") is not None
                       and r.get("price_manwon") is not None
                       and r["price_manwon"] < r["prev_price_manwon"])
            state_cut = (r.get("price_change_state") or "").upper() in ("DECREASE", "DOWN")
            r["is_price_cut"] = own_cut or state_cut
        # 정렬: 신규 → 가격인하 → 나머지 (그룹 내에서는 first_seen 최신순 유지)
        rows.sort(key=lambda r: 0 if r["is_new"] else (1 if r["is_price_cut"] else 2))
        return rows

    def count_active(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_active=1").fetchone()[0]

    def latest_batch_ids(self) -> set[str]:
        """가장 최근에 처음 발견된 배치의 article_no 집합(재생성 시 NEW 표시용)."""
        row = self.conn.execute(
            "SELECT MAX(first_seen_at) FROM listings WHERE is_active=1").fetchone()
        if not row or not row[0]:
            return set()
        cur = self.conn.execute(
            "SELECT article_no FROM listings WHERE is_active=1 AND first_seen_at=?",
            (row[0],))
        return {r[0] for r in cur.fetchall()}
