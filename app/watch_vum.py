"""🏢 밸류맵(valueupmap.com) 신규 매물 감시 — watch.py 틱에 얹혀 같은 알림 대기열로 카톡을 보낸다.

비로그인 내부 API `/api/plus/item-groups`(구 코드 + sort=LATEST)를 구 단위로 부른다(실측 2026-10-14).
항목은 '건물(필지) 그룹'이고 itemCount = 그 건물의 매매 광고 수다. 주의: `id` 는 건물 고유 id 가 아니라
광고가 갱신·추가될 때마다 새로 붙는 값이다(2026-10-15 실측: id 가 방금 생성돼도 광고 8개·조회수 1813).
알림 조건: 지금까지 한 번도 본 적 없는 주소(pnu)만. pnu 이력은 `--vum-seed` 로 전체를 한 번 심어 둔다.
광고 갱신/추가·재등장한 옛 건물·가격인하는 알리지 않는다.
지번은 pnu(19자리: 법정동10 + 구분1 + 본번4 + 부번4)에서 복원한다 — 네이버와 달리 번지까지 나온다.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

log = logging.getLogger("naver_land.watch.vum")

API = "https://www.valueupmap.com/api/plus/item-groups"
DETAIL_URL = "https://www.valueupmap.com/properties/rental-items/{pnu}?dealType=SALES"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")

SCHEMA = """
CREATE TABLE IF NOT EXISTS vum_pnu (
    pnu           TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vum_seen (
    group_id      TEXT PRIMARY KEY,
    item_count    INTEGER,
    first_seen_at TEXT NOT NULL
);
"""


def _get_page(code: str, size: int, page: int, timeout: int = 20, retries: int = 2) -> dict:
    q = urllib.parse.urlencode({
        "areaUnitType": "PYEONG", "dealTypes": "SALES", "districtCode": code,
        "isSearched": "false", "isSelectedThreeDepth": "false",
        "page": page, "size": size, "sort": "LATEST"})
    last: Exception | None = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(f"{API}?{q}",
                                         headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{code}: {last}")


def fetch_gu(code: str, size: int, timeout: int = 20, retries: int = 2) -> list[dict]:
    """구의 최신순 첫 페이지."""
    return _get_page(code, size, 0, timeout, retries).get("contents") or []


def fetch_gu_all(code: str, size: int = 200, sleep_sec: float = 0.5, max_pages: int = 60) -> list[dict]:
    """구의 전체 목록(hasNext 가 꺼질 때까지 페이징). 시딩 전용 — 평시 틱에선 쓰지 않는다."""
    out: list[dict] = []
    for page in range(max_pages):
        d = _get_page(code, size, page)
        out.extend(d.get("contents") or [])
        if not d.get("hasNext"):
            break
        time.sleep(sleep_sec)
    return out


def jibun(pnu: str) -> str:
    if len(pnu) != 19 or not pnu.isdigit():
        return ""
    bon, bu = int(pnu[11:15]), int(pnu[15:19])
    s = f"{bon}-{bu}" if bu else f"{bon}"
    return ("산" if pnu[10] == "2" else "") + s


def _eok(won) -> str:
    try:
        man = int(round(float(won) / 10000))
    except (TypeError, ValueError):
        return ""
    if man >= 10000:
        e, r = divmod(man, 10000)
        return f"{e}억" + (f"{r:,}" if r else "")
    return f"{man:,}만"


def format_message(g: dict, added: int = 0, limit: int = 200) -> str:
    """added=0 → 새 건물, added>0 → 기존 건물에 광고 N개 추가."""
    where = (g.get("shortAddress") or "").strip()
    jb = jibun(str(g.get("pnu", "")))
    if jb:
        where = f"{where} {jb}".strip()
    head = f"🏢 밸류맵 +{added} · {where}" if added else f"🏢 밸류맵 신규 · {where}"
    attrs = {a.get("title"): a.get("content") for a in g.get("summaryAttributes") or []}
    parts = [(g.get("propertyType2") or {}).get("label", ""), _eok(g.get("price"))]
    extra = []
    if attrs.get("대지면적"):
        extra.append(f"대지{attrs['대지면적']}평")
    if attrs.get("연면적"):
        extra.append(f"연{attrs['연면적']}평")
    if attrs.get("규모"):
        extra.append(attrs["규모"])
    mt = {t.get("label") for t in g.get("dealMethodTypes") or []}
    if "전속매물" in mt:
        extra.append("전속")
    line2 = " · ".join(x for x in [" ".join(p for p in parts if p)] + extra if x)
    if g.get("itemCount") and int(g["itemCount"]) > 1:
        line2 += f" · 광고{g['itemCount']}"
    url = DETAIL_URL.format(pnu=g.get("pnu", ""))
    msg = "\n".join(x for x in (head, line2, url) if x)
    if len(msg) <= limit:
        return msg
    room = max(0, limit - len(head) - len(url) - 3)
    return "\n".join((head, line2[:room] + "…", url))


def seed(ws, gus, now_ts: str, size: int = 200, sleep_sec: float = 0.5) -> dict:
    """현재 밸류맵에 올라 있는 모든 주소(pnu)를 '이미 아는 주소'로 기록한다(알림 없음).

    한 구라도 실패하면 seeded 표시를 하지 않고 예외를 던진다(다시 돌리면 이어서 채운다).
    """
    ws.conn.executescript(SCHEMA)
    total = 0
    failed: list[str] = []
    for code, name in gus:
        try:
            contents = fetch_gu_all(code, size, sleep_sec)
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            log.warning("밸류맵 시딩 실패 %s(%s): %s", name, code, e)
            continue
        ws.conn.executemany(
            "INSERT OR IGNORE INTO vum_pnu(pnu, first_seen_at) VALUES (?,?)",
            [(str(g["pnu"]), now_ts) for g in contents if g.get("pnu")])
        ws.conn.commit()
        total += len(contents)
        time.sleep(sleep_sec)
    if failed:
        raise RuntimeError("밸류맵 시딩 실패 구: " + ",".join(failed))
    ws.set_state("vum_pnu_seeded", now_ts)
    n = ws.conn.execute("SELECT COUNT(*) FROM vum_pnu").fetchone()[0]
    log.info("밸류맵 시딩 완료: 구 %d, 그룹 %d, 주소 %d", len(gus), total, n)
    return {"ok": True, "gus": len(gus), "groups": total, "pnu_total": n}


def scan(ws, gus, size: int, now_ts: str, warmup: bool, sleep_sec: float = 1.0) -> dict:
    """ws: watch.WatchStore. 알림은 ws.add_alert 로 큐에 넣는다(warmup 이면 기록만).

    '새 주소' = 지금까지 한 번도 본 적 없는 pnu. 그룹 id 는 광고가 갱신될 때마다 새로 붙어 신규 판정에
    쓸 수 없다. 시딩(`--vum-seed`)이 끝나기 전에는 이력이 불완전하므로 기록만 하고 알리지 않는다.
    """
    ws.conn.executescript(SCHEMA)
    seeded = bool(ws.get_state("vum_pnu_seeded"))
    known = {r[0] for r in ws.conn.execute("SELECT pnu FROM vum_pnu")}
    new_groups: list[dict] = []
    skipped_old = 0
    found: set[str] = set()
    failed = 0
    for code, name in gus:
        try:
            contents = fetch_gu(code, size)
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.warning("밸류맵 조회 실패 %s(%s): %s", name, code, e)
            continue
        for g in contents:
            pnu = str(g.get("pnu", ""))
            if not pnu or pnu in found:
                continue
            found.add(pnu)
            if pnu in known:
                skipped_old += 1   # 이미 광고가 있던 주소 — 광고 갱신/추가일 뿐 새 주소 아님
            else:
                new_groups.append(g)
        time.sleep(sleep_sec)
    if gus and failed == len(gus):
        raise RuntimeError("밸류맵 전 구 조회 실패")
    ws.conn.executemany("INSERT OR IGNORE INTO vum_pnu(pnu, first_seen_at) VALUES (?,?)",
                        [(p, now_ts) for p in found])
    ws.conn.commit()
    queued = 0
    if not warmup and seeded:
        for g in new_groups:
            ws.add_alert("vum_new", str(g["pnu"]), format_message(g), now_ts)
            queued += 1
    log.info("밸류맵 스캔: 구 %d(실패 %d), 새 주소 %d, 기존 주소(제외) %d, 알림 %d%s%s",
             len(gus), failed, len(new_groups), skipped_old, queued,
             " (워밍업)" if warmup else "", "" if seeded else " (시딩 전 — 알림 보류)")
    return {"vum_new": len(new_groups), "vum_added": 0,
            "vum_queued": queued, "vum_failed": failed, "vum_seeded": seeded}
