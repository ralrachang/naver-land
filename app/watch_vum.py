"""🏢 밸류맵(valueupmap.com) 신규 매물 감시 — watch.py 틱에 얹혀 같은 알림 대기열로 카톡을 보낸다.

비로그인 내부 API `/api/plus/item-groups`(구 코드 + sort=LATEST)를 구 단위로 부른다(실측 2026-10-14).
항목은 '건물(필지) 그룹'이다 — id = 그룹, itemCount = 그 건물의 매매 광고 수.
알림 조건: 지금까지 본 최대 그룹 id 보다 큰 새 그룹(= 이전에 광고가 없던 새 건물)만.
재등장한 옛 건물·광고 추가·가격인하는 알리지 않는다.
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
CREATE TABLE IF NOT EXISTS vum_seen (
    group_id      TEXT PRIMARY KEY,
    item_count    INTEGER,
    first_seen_at TEXT NOT NULL
);
"""


def fetch_gu(code: str, size: int, timeout: int = 20, retries: int = 2) -> list[dict]:
    q = urllib.parse.urlencode({
        "areaUnitType": "PYEONG", "dealTypes": "SALES", "districtCode": code,
        "isSearched": "false", "isSelectedThreeDepth": "false",
        "page": 0, "size": size, "sort": "LATEST"})
    last: Exception | None = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(f"{API}?{q}",
                                         headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8")).get("contents") or []
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{code}: {last}")


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


def scan(ws, gus, size: int, now_ts: str, warmup: bool, sleep_sec: float = 1.0) -> dict:
    """ws: watch.WatchStore. 알림은 ws.add_alert 로 큐에 넣는다(warmup 이면 기록만).

    '새 주소' = 건물 그룹 id 가 지금까지 본 최대 id(vum_max_id)보다 큰 것만. 그룹 id 는 생성 순으로
    늘어나므로, 예전에 광고 이력이 있던 건물이 최신순 목록에 다시 올라와도(낮은 id) 알리지 않는다.
    기존 건물에 광고가 추가된 경우도 알리지 않는다.
    """
    ws.conn.executescript(SCHEMA)
    known = {r[0]: r[1] for r in ws.conn.execute("SELECT group_id, item_count FROM vum_seen")}
    max_id = int(ws.get_state("vum_max_id") or 0)
    if not max_id and known:
        max_id = max(int(k) for k in known if k.isdigit())
    new_groups: list[dict] = []
    skipped_old = 0
    upserts: dict[str, int] = {}
    failed = 0
    for code, name in gus:
        try:
            contents = fetch_gu(code, size)
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.warning("밸류맵 조회 실패 %s(%s): %s", name, code, e)
            continue
        for g in contents:
            gid = str(g.get("id", ""))
            if not gid or gid in upserts:
                continue
            if gid not in known:
                if gid.isdigit() and int(gid) > max_id:
                    new_groups.append(g)
                else:
                    skipped_old += 1   # 이미 이력이 있던 건물이 다시 올라온 것 — 새 주소 아님
            upserts[gid] = int(g.get("itemCount") or 0)
        time.sleep(sleep_sec)
    if gus and failed == len(gus):
        raise RuntimeError("밸류맵 전 구 조회 실패")
    ws.conn.executemany(
        "INSERT INTO vum_seen(group_id, item_count, first_seen_at) VALUES (?,?,?) "
        "ON CONFLICT(group_id) DO UPDATE SET item_count=excluded.item_count",
        [(gid, c, now_ts) for gid, c in upserts.items()])
    ids = [int(k) for k in upserts if k.isdigit()]
    if ids:
        ws.set_state("vum_max_id", str(max(max_id, max(ids))))
    ws.conn.commit()
    queued = 0
    if not warmup:
        for g in new_groups:
            ws.add_alert("vum_new", str(g["id"]), format_message(g), now_ts)
            queued += 1
    log.info("밸류맵 스캔: 구 %d(실패 %d), 새 건물 %d, 재등장(제외) %d, 알림 %d%s",
             len(gus), failed, len(new_groups), skipped_old, queued, " (워밍업)" if warmup else "")
    return {"vum_new": len(new_groups), "vum_added": 0,
            "vum_queued": queued, "vum_failed": failed}
