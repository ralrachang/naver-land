#!/usr/bin/env python3
"""🔔 새 주소 실시간 감시기 CLI. stdout 엔 JSON 한 줄만 찍는다(루프가 파싱). 로그는 logs/watch-YYYYMMDD.log.

    python3 watch.py --scan          # 한 바퀴 스캔 + 미발송 알림 대기열 JSON
    python3 watch.py --ack 12,13     # 카톡 발송 완료 처리
    python3 watch.py --status        # 상태 JSON
    python3 watch.py --vum-seed      # 밸류맵 전체 주소 이력 1회 시딩(알림 없음, 수 분 소요)
    python3 watch.py --scan --verbose   # 로그를 stderr 에도

이 PC 의 Claude Code 에서 `/watch`(= /loop 10m /watch-tick) 가 SSH 로 이 명령을 부른다.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config as config_mod  # noqa: E402
from app import watch  # noqa: E402

KST = timezone(timedelta(hours=9))


def setup_logging(logs_dir: Path, verbose: bool):
    logs_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(KST).strftime("%Y%m%d")
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [
        logging.FileHandler(logs_dir / f"watch-{day}.log", encoding="utf-8")]
    if verbose:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="🔔 새 주소 실시간 감시기")
    ap.add_argument("--config", default=None)
    ap.add_argument("--scan", action="store_true", help="한 바퀴 스캔 + 대기열 반환")
    ap.add_argument("--ack", default=None, help="발송 완료 처리할 알림 id(쉼표 구분)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--vum-seed", action="store_true", help="밸류맵 현재 전체 주소를 '아는 주소'로 기록")
    ap.add_argument("--verbose", action="store_true", help="로그를 stderr 에도 출력")
    args = ap.parse_args(argv)

    cfg = config_mod.load(args.config)
    setup_logging(cfg.logs_dir, args.verbose)
    log = logging.getLogger("naver_land.watch.cli")
    try:
        if args.ack is not None:
            ids = [int(x) for x in args.ack.split(",") if x.strip()]
            out = watch.ack(cfg, ids)
        elif args.scan:
            out = watch.scan(cfg)
        elif args.status:
            out = watch.status(cfg)
        elif args.vum_seed:
            out = watch.vum_seed(cfg)
        else:
            ap.print_help(sys.stderr)
            return 2
    except Exception as e:  # 어떤 경우에도 JSON 한 줄은 내보낸다
        log.exception("감시기 오류: %s", e)
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
