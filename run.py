#!/usr/bin/env python3
"""엔트리포인트. 사용법:
    python run.py                 # 수집→저장→사이트생성(→배포)
    python run.py --dry-run       # 배포는 커밋까지만(푸시 안 함)
    python run.py --rebuild-regions   # 지역 캐시 재생성
    python run.py --config other.toml
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config as config_mod  # noqa: E402
from app import pipeline  # noqa: E402

KST = timezone(timedelta(hours=9))


def setup_logging(logs_dir: Path):
    logs_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(KST).strftime("%Y%m%d")
    logfile = logs_dir / f"run-{day}.log"
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stdout),
                logging.FileHandler(logfile, encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def main(argv=None):
    ap = argparse.ArgumentParser(description="네이버 부동산 매물 모니터")
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rebuild-regions", action="store_true")
    ap.add_argument("--regenerate", action="store_true",
                    help="크롤 없이 기존 DB로 사이트만 재생성+배포(템플릿 변경 즉시 반영)")
    args = ap.parse_args(argv)

    cfg = config_mod.load(args.config)
    setup_logging(cfg.logs_dir)
    log = logging.getLogger("naver_land.run")
    log.info("=== 실행 시작 (%s KST) ===", datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"))
    try:
        if args.regenerate:
            summary = pipeline.regenerate(cfg, dry_run=args.dry_run)
        else:
            summary = pipeline.run_pipeline(cfg, rebuild_regions=args.rebuild_regions,
                                            dry_run=args.dry_run)
    except Exception as e:
        log.exception("치명적 오류: %s", e)
        return 2
    log.info("=== 실행 종료: %s ===", summary)
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
