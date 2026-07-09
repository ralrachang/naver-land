"""대상 지역(구)을 동 단위까지 전개하고 캐시.

법정동코드(cortarNo, 10자리) 규칙:
  시도  = 끝 8자리 0  (예: 서울 1100000000)
  구    = 끝 5자리 0  (예: 강남구 1168000000)
  동    = 끝 5자리 != 00000 (예: 개포동 1168010300)  → leaf(크롤 대상)
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("naver_land.regions")


def _is_container(cortar_no: str) -> bool:
    """끝 5자리가 00000 이면 상위(시도/구) 컨테이너 → 자식 전개 필요."""
    return cortar_no.endswith("00000")


def _signature(targets: list[dict]) -> str:
    """대상 지역 집합의 서명 — config 가 바뀌면 캐시를 무효화하기 위함."""
    return ",".join(sorted(str(t["cortarNo"]) for t in targets))


def build(client, targets: list[dict], cache_path=None, use_cache: bool = True,
          max_depth: int = 3) -> list[dict]:
    """targets: config 의 [[regions]] 항목 리스트.

    반환: leaf(동) 목록 [{cortarNo, sido, gu, dong, address}]
    """
    sig = _signature(targets)
    if use_cache and cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            # 신형식({sig, regions}) 이면서 서명이 일치할 때만 재사용
            if isinstance(cached, dict) and cached.get("sig") == sig and cached.get("regions"):
                regs = cached["regions"]
                log.info("지역 캐시 사용: %d개 동 (%s)", len(regs), cache_path.name)
                return regs
            log.info("지역 캐시 무효(대상 변경/구형식) — 재구축")
        except Exception:
            log.warning("지역 캐시 로드 실패 — 재구축")

    leaves: list[dict] = []

    def descend(cortar_no: str, sido: str, gu: str, depth: int):
        if depth > max_depth:
            return
        if not _is_container(cortar_no):
            # 동(leaf) — 하지만 seed 로 동을 직접 준 경우 gu 가 이미 채워져 있음
            return
        for child in client.regions(cortar_no):
            cn = child["cortarNo"]
            name = child.get("cortarName", "")
            if _is_container(cn):
                # 구 레벨: gu 이름을 확정하고 한 단계 더
                descend(cn, sido, name, depth + 1)
            else:
                # 동 leaf
                cur_gu = gu or name  # 방어
                leaves.append({
                    "cortarNo": cn,
                    "sido": sido,
                    "gu": cur_gu,
                    "dong": name,
                    "address": " ".join(x for x in (sido, cur_gu, name) if x),
                })

    for t in targets:
        cortar_no = str(t["cortarNo"])
        sido = t.get("sido", "")
        gu = t.get("gu", "")
        if _is_container(cortar_no):
            descend(cortar_no, sido, gu, 0)
        else:
            # config 가 동을 직접 지정한 경우
            leaves.append({
                "cortarNo": cortar_no,
                "sido": sido,
                "gu": gu,
                "dong": t.get("dong", ""),
                "address": " ".join(x for x in (sido, gu, t.get("dong", "")) if x),
            })

    # 중복 제거
    seen = set()
    uniq = []
    for l in leaves:
        if l["cortarNo"] in seen:
            continue
        seen.add(l["cortarNo"])
        uniq.append(l)

    log.info("지역 전개 완료: %d개 동", len(uniq))
    if cache_path:
        cache_path.write_text(
            json.dumps({"sig": sig, "regions": uniq}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    return uniq
