"""SQLite 저장 + 신규 판별 + 누적 보관."""
from __future__ import annotations

import logging
import sqlite3
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
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

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
                "SELECT article_no FROM listings WHERE article_no=?", (ano,)
            ).fetchone()
            if row is None:
                self.conn.execute(
                    """INSERT INTO listings
                       (article_no,address,sido,gu,dong,price_text,price_manwon,
                        re_type,article_name,confirm_ymd,feature_desc,area,floor,
                        lat,lng,first_seen_at,last_seen_at,is_active)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (ano, it["address"], it.get("sido"), it.get("gu"), it.get("dong"),
                     it["price_text"], it.get("price_manwon"), it.get("re_type"),
                     it.get("article_name"), it.get("confirm_ymd"), it.get("feature_desc"),
                     it.get("area"), it.get("floor"), it.get("lat"), it.get("lng"),
                     run_ts, run_ts),
                )
                new_ids.append(ano)
            else:
                self.conn.execute(
                    """UPDATE listings SET last_seen_at=?, price_text=?, price_manwon=?,
                       is_active=1 WHERE article_no=?""",
                    (run_ts, it["price_text"], it.get("price_manwon"), ano),
                )
                seen += 1
        self.conn.commit()
        log.info("저장: 신규 %d건, 갱신 %d건", len(new_ids), seen)
        return {"new": new_ids, "new_count": len(new_ids), "seen_count": seen}

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

    def record_run(self, run_ts: str, new_count: int, seen_count: int,
                   total_active: int, ok: bool, note: str = ""):
        self.conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)",
            (run_ts, new_count, seen_count, total_active, 1 if ok else 0, note),
        )
        self.conn.commit()

    def active_listings(self, new_ids: set[str] | None = None) -> list[dict]:
        """표시용 목록. 신규 우선, 그다음 최초 발견 최신순."""
        new_ids = new_ids or set()
        cur = self.conn.execute(
            "SELECT * FROM listings WHERE is_active=1 ORDER BY first_seen_at DESC, price_manwon DESC"
        )
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["is_new"] = r["article_no"] in new_ids
        rows.sort(key=lambda r: (0 if r["is_new"] else 1,), reverse=False)
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
