"""🔔 새 주소 실시간 감시기 — 구(區) 단위 최신순 조회로 새 매물을 찾아 '새 주소'면 카톡 알림 대기열에 넣는다.

홈페이지 파이프라인과 완전히 분리된다:
- 자체 DB `data/watch.db`(watch_seen / watch_locations / watch_alerts / watch_state)만 쓴다.
- 홈페이지 DB(`listings.db`)는 읽기 전용(mode=ro)으로 열어 `seen_locations`만 참조한다.

실측(2026-09-02): `/api/articles`는 구 코드로도 최신순 조회가 돼 서울 한 바퀴가 25요청(약 40초).
구 단위 응답엔 동 이름이 없어 알림 건에 한해 상세 API로 보충한다.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import client as client_mod
from .store import _split_loc, building_keys

log = logging.getLogger("naver_land.watch")
KST = timezone(timedelta(hours=9))
TS = "%Y-%m-%d %H:%M:%S"
ARTICLE_URL = "https://m.land.naver.com/article/info/{no}"
MAX_MSG_CHARS = 200        # PlayMCP 나에게 보내기(MemoChat) 상한
SEEN_KEEP_DAYS = 30        # watch_seen 정리 기준
LOCK_STALE_SEC = 300       # 5분 넘은 잠금 파일은 죽은 프로세스로 간주

SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_seen (
    article_no    TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watch_locations (
    loc_key       TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watch_alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,            -- new_address | notice
    article_no TEXT,
    created_at TEXT NOT NULL,
    message    TEXT NOT NULL,
    sent_at    TEXT,
    expired    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS watch_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class WatchError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 지역
# ---------------------------------------------------------------------------
def gu_list(cache_path: Path) -> list[tuple[str, str]]:
    """동 캐시(data/regions.json)에서 구 목록 [(구코드, 구이름)] 유도. 구코드 = 동코드 앞5자리+00000."""
    p = Path(cache_path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    leaves = data.get("regions", []) if isinstance(data, dict) else []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for leaf in leaves:
        cn = str(leaf.get("cortarNo", ""))
        if len(cn) != 10:
            continue
        code = cn[:5] + "00000"
        if code in seen:
            continue
        seen.add(code)
        out.append((code, leaf.get("gu", "") or ""))
    return out


# ---------------------------------------------------------------------------
# 저장소
# ---------------------------------------------------------------------------
class WatchStore:
    def __init__(self, db_path: str | Path, main_db_path: str | Path | None):
        self.conn = sqlite3.connect(str(db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.main: sqlite3.Connection | None = None
        if main_db_path and Path(main_db_path).exists():
            uri = Path(main_db_path).resolve().as_uri() + "?mode=ro"
            try:
                self.main = sqlite3.connect(uri, uri=True, timeout=30)
                self.main.execute("SELECT 1 FROM seen_locations LIMIT 1")
            except sqlite3.Error as e:
                log.warning("홈페이지 DB 읽기 불가(%s) — 감시기 위치 기록만 사용", e)
                self.main = None

    def close(self):
        self.conn.close()
        if self.main is not None:
            self.main.close()

    # -- 매물 --
    def seen_ids(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT article_no FROM watch_seen")}

    def mark_seen(self, article_nos, ts: str):
        self.conn.executemany(
            "INSERT OR IGNORE INTO watch_seen(article_no, first_seen_at) VALUES (?,?)",
            [(a, ts) for a in article_nos])
        self.conn.commit()

    def prune_seen(self, days: int, now: datetime):
        cutoff = (now - timedelta(days=days)).strftime(TS)
        self.conn.execute("DELETE FROM watch_seen WHERE first_seen_at < ?", (cutoff,))
        self.conn.commit()

    # -- 위치 --
    def known_coords(self) -> list[tuple[str, str]]:
        """홈페이지가 아는 위치(seen_locations, 읽기 전용) + 감시기가 본 위치."""
        out = []
        if self.main is not None:
            out += [_split_loc(r[0]) for r in self.main.execute("SELECT loc_key FROM seen_locations")]
        out += [_split_loc(r[0]) for r in self.conn.execute("SELECT loc_key FROM watch_locations")]
        return out

    def register_locations(self, coords, ts: str):
        self.conn.executemany(
            "INSERT OR IGNORE INTO watch_locations(loc_key, first_seen_at) VALUES (?,?)",
            [(f"{lat},{lng}", ts) for lat, lng in coords])
        self.conn.commit()

    # -- 알림 대기열 --
    def add_alert(self, kind: str, article_no: str | None, message: str, ts: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO watch_alerts(kind, article_no, created_at, message) VALUES (?,?,?,?)",
            (kind, article_no, ts, message))
        self.conn.commit()
        return int(cur.lastrowid)

    def pending(self, limit: int) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id, kind, article_no, created_at, message FROM watch_alerts "
            "WHERE sent_at IS NULL AND expired=0 ORDER BY id LIMIT ?", (int(limit),))
        return [dict(r) for r in cur.fetchall()]

    def pending_count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM watch_alerts WHERE sent_at IS NULL AND expired=0").fetchone()[0])

    def ack(self, ids, ts: str) -> int:
        n = 0
        for i in ids:
            cur = self.conn.execute(
                "UPDATE watch_alerts SET sent_at=? WHERE id=? AND sent_at IS NULL", (ts, int(i)))
            n += cur.rowcount
        self.conn.commit()
        return n

    def expire(self, ttl_min: int, now: datetime) -> int:
        cutoff = (now - timedelta(minutes=ttl_min)).strftime(TS)
        cur = self.conn.execute(
            "UPDATE watch_alerts SET expired=1 WHERE sent_at IS NULL AND expired=0 AND created_at < ?",
            (cutoff,))
        self.conn.commit()
        return cur.rowcount

    def sent_on(self, day: str) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM watch_alerts WHERE sent_at LIKE ?", (day + "%",)).fetchone()[0])

    # -- 상태 --
    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM watch_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str):
        self.conn.execute("INSERT OR REPLACE INTO watch_state(key, value) VALUES (?,?)", (key, value))
        self.conn.commit()


# ---------------------------------------------------------------------------
# 잠금(동시 스캔 방지)
# ---------------------------------------------------------------------------
class _Lock:
    def __init__(self, path: Path, stale_sec: int = LOCK_STALE_SEC):
        self.path = Path(path)
        self.stale_sec = stale_sec
        self.held = False

    def acquire(self) -> bool:
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.held = True
                return True
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                except OSError:
                    continue
                if age > self.stale_sec:
                    log.warning("오래된 잠금(%ds) 제거", age)
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    continue
                return False
        return False

    def release(self):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.held = False


# ---------------------------------------------------------------------------
# 메시지
# ---------------------------------------------------------------------------
def _num(v) -> str:
    if v is None or v == "":
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f <= 0:  # 토지는 연면적 0 으로 온다 → 표시 생략
        return ""
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def _area_text(it: dict) -> str:
    a1, a2 = _num(it.get("area1")), _num(it.get("area2"))
    if a1 and a2:
        return f"대지{a1}/연{a2}㎡"
    if a1:
        return f"대지{a1}㎡"
    if a2:
        return f"연{a2}㎡"
    return ""


def format_message(it: dict, limit: int = MAX_MSG_CHARS) -> str:
    """카톡 메시지 완성본(≤limit자). Claude 는 이 문자열을 수정 없이 그대로 보낸다.

    형식: 🆕 새주소 · 구 동 / 종류 가격 · 면적 · 층 / 특징(예산에 맞춰 잘라 …) / 네이버 링크
    """
    where = " ".join(x for x in (it.get("gu"), it.get("dong")) if x)
    head = f"🆕 새주소 · {where}" if where else "🆕 새주소"
    kind_price = " ".join(x for x in ((it.get("sub_type") or it.get("re_type") or "").strip(),
                                      (it.get("price_text") or "").strip()) if x)
    line2 = " · ".join(x for x in (kind_price, _area_text(it), (it.get("floor") or "").strip()) if x)
    url = ARTICLE_URL.format(no=it["article_no"])
    lines = [x for x in (head, line2) if x]
    fixed = sum(len(x) for x in lines) + len(url) + len(lines)  # 줄바꿈 포함
    desc = " ".join((it.get("feature_desc") or "").split())  # 줄바꿈·이중 공백 정리
    budget = limit - fixed - 1
    if desc and budget >= 5:
        if len(desc) > budget:
            desc = desc[:budget - 1] + "…"
        lines.append(desc)
    lines.append(url)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 스캔
# ---------------------------------------------------------------------------
def _extract(a: dict, gu_code: str, gu_name: str) -> dict:
    return {
        "article_no": str(a.get("articleNo", "")),
        "gu_code": gu_code,
        "gu": gu_name,
        "dong": "",
        "re_type": a.get("realEstateTypeName", "") or "",
        "sub_type": a.get("articleRealEstateTypeName", "") or "",
        "price_text": a.get("dealOrWarrantPrc", "") or "",
        "area1": a.get("area1"),
        "area2": a.get("area2"),
        "floor": a.get("floorInfo", "") or "",
        "feature_desc": (a.get("articleFeatureDesc", "") or "").strip(),
        "confirm_ymd": a.get("articleConfirmYmd", "") or "",
        "cp": a.get("cpName", "") or "",
        "lat": str(a.get("latitude", "") or ""),
        "lng": str(a.get("longitude", "") or ""),
    }


def _fill_dong(client, it: dict):
    """구 단위 목록엔 동이 없다 — 상세로 보충. 실패하면 구 이름만 쓴다."""
    try:
        det = (client.article_detail(it["article_no"]) or {}).get("articleDetail") or {}
    except Exception as e:
        log.warning("상세 조회 실패(%s): %s — 구 이름만 사용", it["article_no"], e)
        return
    if det.get("divisionName"):
        it["gu"] = det["divisionName"]
    if det.get("sectionName"):
        it["dong"] = det["sectionName"]


def _now_kst(now: datetime | None) -> datetime:
    if now is None:
        now = datetime.now(KST)
    if now.tzinfo is not None:
        now = now.astimezone(KST).replace(tzinfo=None)
    return now


def _result(ws: WatchStore, w, now: datetime, **extra) -> dict:
    expired = ws.expire(w.alert_ttl_min, now)
    out = {"ok": True, "time": now.strftime(TS), "warmup": False, "scanned": 0,
           "new_listings": 0, "new_addresses": 0, "queued": 0,
           "alerts": ws.pending(w.max_alerts_per_tick),
           "pending_total": ws.pending_count(), "expired": expired}
    out.update(extra)
    if "error" in extra:
        out["ok"] = False
    return out


def scan(cfg, now: datetime | None = None, client=None) -> dict:
    """한 바퀴 스캔 + 미발송 대기열 반환. 항상 dict 를 돌려준다(예외는 error 로)."""
    now = _now_kst(now)
    now_ts = now.strftime(TS)
    w = cfg.watch
    ws = WatchStore(cfg.watch_db_path, cfg.db_path)
    try:
        blocked_until = ws.get_state("blocked_until")
        if blocked_until and now_ts < blocked_until:
            log.info("차단 휴식 중(%s까지) — 스캔 건너뜀", blocked_until)
            return _result(ws, w, now, skipped=f"blocked_until {blocked_until}")
        lock = _Lock(Path(cfg.data_dir) / "watch.lock")
        if not lock.acquire():
            log.warning("다른 스캔이 진행 중 — 건너뜀")
            return _result(ws, w, now, skipped="scan_running")
        try:
            return _scan_locked(cfg, ws, now, client)
        finally:
            lock.release()
    finally:
        ws.close()


def _scan_locked(cfg, ws: WatchStore, now: datetime, client) -> dict:
    w = cfg.watch
    now_ts = now.strftime(TS)
    gus = gu_list(cfg.regions_cache)
    if not gus:
        return _result(ws, w, now,
                       error="regions cache missing — run.py 를 먼저 한 번 실행해 data/regions.json 을 만드세요")

    last = ws.get_state("last_scan_at")
    warmup = True
    if last:
        try:
            warmup = (now - datetime.strptime(last, TS)) > timedelta(minutes=w.gap_reset_min)
        except ValueError:
            warmup = True
    max_pages = 1 if warmup else max(1, int(w.max_pages))

    if client is None:
        client = client_mod.Client(delay=cfg.crawl.request_delay_sec,
                                   jitter=cfg.crawl.request_jitter_sec,
                                   retries=cfg.crawl.max_retries,
                                   timeout=cfg.crawl.timeout_sec)
    seen = ws.seen_ids()
    new_items: list[dict] = []
    observed: set[str] = set()
    try:
        client.get_token()
        for code, name in gus:
            for page in range(1, max_pages + 1):
                data = client.articles(code, cfg.crawl.real_estate_types,
                                       cfg.crawl.trade_type, cfg.crawl.order, page)
                arts = data.get("articleList") or []
                if not arts:
                    break
                hit_seen = False
                for a in arts:
                    ano = str(a.get("articleNo", "") or "")
                    if not ano:
                        continue
                    if ano in seen:
                        hit_seen = True
                        continue
                    if ano in observed:
                        continue
                    observed.add(ano)
                    new_items.append(_extract(a, code, name))
                if hit_seen or not data.get("isMoreData"):
                    break  # 이미 본 매물이 나온 페이지 아래는 더 오래됨
    except client_mod.BlockedError as e:
        until = (now + timedelta(minutes=w.block_pause_min)).strftime(TS)
        ws.set_state("blocked_until", until)
        log.error("차단 추정 — %s까지 휴식: %s", until, e)
        today = now.strftime("%Y-%m-%d")
        if ws.get_state("block_notified_on") != today:
            ws.add_alert("notice", None,
                         f"⚠️ 네이버 차단 감지({str(e)[:60]}). "
                         f"{w.block_pause_min}분 쉬었다가 재개합니다.", now_ts)
            ws.set_state("block_notified_on", today)
        # 이번에 본 것은 기록하지 않는다 → 재개 후 스캔이 알림을 만든다
        return _result(ws, w, now, error=f"blocked: {e}")

    ws.mark_seen([it["article_no"] for it in new_items], now_ts)

    incoming = [(it["lat"], it["lng"]) for it in new_items if it["lat"] and it["lng"]]
    known = ws.known_coords()
    bkey = building_keys(known + incoming)
    known_buildings = {bkey[c] for c in known}
    new_addr: list[dict] = []
    for it in new_items:
        if not (it["lat"] and it["lng"]):
            continue
        b = bkey[(it["lat"], it["lng"])]
        if b in known_buildings:
            continue
        known_buildings.add(b)   # 같은 새 건물의 두 번째 매물은 알림하지 않음
        new_addr.append(it)
    ws.register_locations(incoming, now_ts)

    queued = 0
    if warmup:
        log.info("워밍업(직전 스캔 %s) — 기록만, 알림 없음: 새 매물 %d / 새 주소 %d",
                 last or "없음", len(new_items), len(new_addr))
    else:
        for it in new_addr:
            _fill_dong(client, it)
            ws.add_alert("new_address", it["article_no"], format_message(it), now_ts)
            queued += 1
    ws.set_state("last_scan_at", now_ts)
    ws.prune_seen(SEEN_KEEP_DAYS, now)
    log.info("스캔 완료: 구 %d개, 새 매물 %d, 새 주소 %d, 알림 추가 %d%s",
             len(gus), len(new_items), len(new_addr), queued, " (워밍업)" if warmup else "")
    return _result(ws, w, now, warmup=warmup, scanned=len(gus),
                   new_listings=len(new_items), new_addresses=len(new_addr), queued=queued)


def ack(cfg, ids, now: datetime | None = None) -> dict:
    now = _now_kst(now)
    ws = WatchStore(cfg.watch_db_path, cfg.db_path)
    try:
        n = ws.ack(ids, now.strftime(TS))
        log.info("발송 완료 처리: %d건 (%s)", n, ",".join(str(i) for i in ids))
        return {"ok": True, "time": now.strftime(TS), "acked": n,
                "pending_total": ws.pending_count()}
    finally:
        ws.close()


def status(cfg, now: datetime | None = None) -> dict:
    now = _now_kst(now)
    ws = WatchStore(cfg.watch_db_path, cfg.db_path)
    try:
        return {"ok": True, "time": now.strftime(TS),
                "last_scan_at": ws.get_state("last_scan_at"),
                "blocked_until": ws.get_state("blocked_until"),
                "pending_total": ws.pending_count(),
                "sent_today": ws.sent_on(now.strftime("%Y-%m-%d")),
                "seen_total": len(ws.seen_ids()),
                "gus": len(gu_list(cfg.regions_cache))}
    finally:
        ws.close()
