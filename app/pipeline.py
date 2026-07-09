"""파이프라인 오케스트레이션: token → regions → crawl → store → generate → deploy.

각 단계 실패가 사이트를 파괴하지 않도록 격리한다(차단/오류 시 직전 스냅샷 유지).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import client as client_mod
from . import crawler, deploy, generate, regions, store

log = logging.getLogger("naver_land.pipeline")
KST = timezone(timedelta(hours=9))


def run_pipeline(cfg, rebuild_regions: bool = False, dry_run: bool | None = None) -> dict:
    now = datetime.now(KST)
    run_ts = now.strftime("%Y-%m-%d %H:%M:%S")
    summary = {"ok": False, "new_count": 0, "total": 0, "note": ""}

    c = client_mod.Client(delay=cfg.crawl.request_delay_sec,
                          jitter=cfg.crawl.request_jitter_sec,
                          retries=cfg.crawl.max_retries,
                          timeout=cfg.crawl.timeout_sec)

    # 1) 토큰 — 실패 시 사이트 손대지 않고 종료
    try:
        c.get_token()
    except Exception as e:
        log.error("토큰 획득 실패 — 중단(기존 사이트 유지): %s", e)
        summary["note"] = f"token_fail: {e}"
        _record(cfg, run_ts, summary)
        return summary

    # 2) 지역
    try:
        region_list = regions.build(c, cfg.regions, cache_path=cfg.regions_cache,
                                    use_cache=not rebuild_regions)
    except Exception as e:
        log.error("지역 전개 실패 — 중단: %s", e)
        summary["note"] = f"regions_fail: {e}"
        _record(cfg, run_ts, summary)
        return summary

    st = store.Store(cfg.db_path)
    try:
        seen = st.existing_ids()
        # 3) 크롤 — 차단이면 예외 → 사이트 유지
        try:
            items, full_scan = crawler.crawl(c, cfg, region_list, seen_ids=seen)
        except client_mod.BlockedError as e:
            log.error("차단 추정으로 크롤 중단 — 기존 사이트 유지: %s", e)
            summary["note"] = f"blocked: {e}"
            st.record_run(run_ts, 0, 0, st.count_active(), False, summary["note"])
            return summary

        # 4) 저장 + 신규판별
        res = st.upsert(items, run_ts)
        # 삭제(비활성화) 판정: 전체 스캔일 때만(early_stop 스캔은 불완전 → 생략).
        # keep_days>0 이면 마지막 목격 후 그 일수만큼 유예. keep_days=0 이면 누적 보관.
        if full_scan:
            st.deactivate_stale(cfg.site.keep_days, run_ts)
        new_ids = set(res["new"])
        rows = st.active_listings(new_ids=new_ids)

        # 5) 사이트 생성
        generated = generate.generate(cfg, rows, res["new_count"], run_dt=now)

        # 6) 배포
        if generated:
            deploy.deploy(cfg, dry_run=dry_run)

        total = st.count_active()
        st.record_run(run_ts, res["new_count"], res["seen_count"], total, True,
                      "generated" if generated else "skip_empty")
        summary.update(ok=True, new_count=res["new_count"], total=total,
                       note="generated" if generated else "skip_empty")
        log.info("파이프라인 완료: 신규 %d / 총 %d", res["new_count"], total)
        return summary
    finally:
        st.close()


def regenerate(cfg, dry_run: bool | None = None) -> dict:
    """크롤 없이 기존 DB로 사이트만 재생성 + 배포(템플릿 변경 즉시 반영용)."""
    now = datetime.now(KST)
    st = store.Store(cfg.db_path)
    try:
        new_ids = st.latest_batch_ids()
        rows = st.active_listings(new_ids=new_ids)
        generated = generate.generate(cfg, rows, len(new_ids), run_dt=now)
        if generated:
            deploy.deploy(cfg, dry_run=dry_run)
        total = st.count_active()
        log.info("재생성 완료: 총 %d건 (최근배치 %d건 NEW)", total, len(new_ids))
        return {"ok": True, "new_count": len(new_ids), "total": total,
                "note": "regenerated" if generated else "skip_empty"}
    finally:
        st.close()


def _record(cfg, run_ts: str, summary: dict):
    try:
        st = store.Store(cfg.db_path)
        st.record_run(run_ts, 0, 0, st.count_active(), False, summary.get("note", ""))
        st.close()
    except Exception:
        pass
