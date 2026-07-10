"""매물 수집 — 최신순(dateDesc) 페이지네이션 + 이미 본 매물 조기중단."""
from __future__ import annotations

import logging

log = logging.getLogger("naver_land.crawler")


def parse_price_manwon(text: str) -> int | None:
    """'26억 2,000' -> 262000, '67억' -> 670000, '5,000' -> 5000 (단위: 만원).

    숫자로 해석 불가하면 None.
    """
    if not text:
        return None
    t = text.replace(" ", "")
    try:
        if "억" in t:
            a, b = t.split("억", 1)
            eok = int(a.replace(",", "")) if a.replace(",", "").isdigit() else 0
            b = b.strip(",")
            man = int(b.replace(",", "")) if b and b.replace(",", "").isdigit() else 0
            return eok * 10000 + man
        digits = t.replace(",", "")
        if digits.isdigit():
            return int(digits)
    except (ValueError, AttributeError):
        return None
    return None


def _extract(article: dict, region: dict) -> dict:
    price_text = article.get("dealOrWarrantPrc", "") or ""
    return {
        "article_no": str(article.get("articleNo", "")),
        "address": region["address"],
        "sido": region.get("sido", ""),
        "gu": region.get("gu", ""),
        "dong": region.get("dong", ""),
        "price_text": price_text,
        "price_manwon": parse_price_manwon(price_text),
        "re_type": article.get("realEstateTypeName", ""),
        "article_name": article.get("articleName", ""),
        "confirm_ymd": article.get("articleConfirmYmd", "") or "",
        "same_addr_cnt": article.get("sameAddrCnt"),  # 같은 주소 광고 수(1=단독)
        "price_change_state": article.get("priceChangeState", "") or "",  # SAME/INCREASE/DECREASE
        "feature_desc": (article.get("articleFeatureDesc", "") or "").strip(),
        "area": article.get("area1"),
        "floor": article.get("floorInfo", "") or "",
        "lat": article.get("latitude", ""),
        "lng": article.get("longitude", ""),
    }


def crawl(client, cfg, regions: list[dict],
          seen_ids: set[str] | None = None) -> tuple[list[dict], bool]:
    """모든 대상 동을 순회해 매물을 수집.

    반환: (observed_items, full_scan)
      - observed_items: 이번 실행에서 목격한 모든 매물(신규+기존). store 가 신규를
        insert 하고 기존은 last_seen_at 을 갱신하는 데 쓴다.
      - full_scan: 전체 스캔이었는지 여부(early_stop=false면 True). False면 삭제(비활성화)
        판정을 하지 않는다(스캔이 불완전하므로).

    early_stop(config, 기본 False): dateDesc 에서 신규가 없는 페이지에 도달하면 해당
    동의 페이지네이션을 조기중단(요청 최소화, 넓은 범위용). 단 이 경우 목록 하단의
    매물은 목격되지 않아 삭제 판정을 신뢰할 수 없어 비활성화를 생략한다.
    """
    seen_ids = seen_ids or set()
    c = cfg.crawl
    early_stop = bool(getattr(c, "early_stop", False))
    out: list[dict] = []
    observed_ids: set[str] = set()
    total_regions = len(regions)
    for idx, region in enumerate(regions, 1):
        cortar = region["cortarNo"]
        region_total = 0
        region_new = 0
        for page in range(1, c.max_pages_per_region + 1):
            data = client.articles(cortar, c.real_estate_types,
                                    c.trade_type, c.order, page)
            arts = data.get("articleList", []) or []
            if not arts:
                break
            page_new = 0
            for a in arts:
                ano = str(a.get("articleNo", ""))
                if not ano or ano in observed_ids:
                    continue
                observed_ids.add(ano)
                out.append(_extract(a, region))
                region_total += 1
                if ano not in seen_ids:
                    page_new += 1
            region_new += page_new
            if not data.get("isMoreData"):
                break
            if early_stop and page_new == 0:
                break  # 이 페이지 전부 이미 본 매물 → 이후는 더 오래됨
        log.info("[%d/%d] %s(%s): 목격 %d건(신규 %d)",
                 idx, total_regions, region["address"], cortar, region_total, region_new)
    log.info("크롤 완료: 총 %d건 목격(대상 동 %d개, %s)",
             len(out), total_regions, "조기중단" if early_stop else "전체스캔")
    return out, (not early_stop)
