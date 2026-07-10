#!/usr/bin/env python3
"""nginx 접속 로그 리포트 — 누가(아이디)/어디서(IP)/언제/얼마나 접속했는지.

VPS에서: python3 deploy/access_report.py [--days 7] [--warn-ips 15]
공유 비밀번호 유출 감시용: 고유 IP가 팀 규모(집·회사·모바일 회선 몇 개)를
크게 넘으면 경고를 띄운다. 표준 라이브러리만 사용.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import re
from collections import defaultdict
from datetime import datetime, timedelta

LOG_GLOB = "/var/log/nginx/naverland_access.log*"
# combined 포맷: IP - 아이디 [시각] "요청" 상태 ...
LINE = re.compile(r'^(\S+) - (\S+) \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3})')


def main():
    ap = argparse.ArgumentParser(description="접속 현황 리포트")
    ap.add_argument("--days", type=int, default=7, help="최근 N일 (기본 7)")
    ap.add_argument("--warn-ips", type=int, default=15,
                    help="고유 IP가 이 수를 넘으면 유출 의심 경고 (기본 15)")
    ap.add_argument("--log-glob", default=LOG_GLOB)
    a = ap.parse_args()

    now = datetime.now().astimezone()
    since = now - timedelta(days=a.days)
    by_key: dict = defaultdict(lambda: {"n": 0, "first": None, "last": None})
    fails = 0
    for path in sorted(glob.glob(a.log_glob)):
        op = gzip.open if path.endswith(".gz") else open
        with op(path, "rt", errors="replace") as f:
            for line in f:
                m = LINE.match(line)
                if not m:
                    continue
                ip, user, ts, _method, _url, status = m.groups()
                try:
                    t = datetime.strptime(ts, "%d/%b/%Y:%H:%M:%S %z")
                except ValueError:
                    continue
                if t < since:
                    continue
                if status == "401":
                    fails += 1  # 로그인 실패(비번 틀림/무단 시도)
                    continue
                if not status.startswith(("2", "3")):
                    continue
                s = by_key[(user, ip)]
                s["n"] += 1
                s["first"] = min(s["first"] or t, t)
                s["last"] = max(s["last"] or t, t)

    print(f"=== 최근 {a.days}일 접속 리포트 ({now:%Y-%m-%d %H:%M}) ===")
    if not by_key:
        print("접속 기록 없음")
        return
    print(f"{'아이디':<8} {'IP':<40} {'요청수':>6}  첫 접속        마지막 접속")
    for (user, ip), s in sorted(by_key.items(),
                                key=lambda kv: kv[1]["last"], reverse=True):
        print(f"{user:<8} {ip:<40} {s['n']:>6}  "
              f"{s['first']:%m-%d %H:%M}    {s['last']:%m-%d %H:%M}")
    ips = {ip for (_u, ip) in by_key}
    print(f"\n고유 IP {len(ips)}개 / 로그인 실패(401) {fails}건")
    if len(ips) > a.warn_ips:
        print(f"⚠️  고유 IP가 {a.warn_ips}개 초과 — 비밀번호가 팀 밖으로 퍼졌을 수 있습니다.")
        print("   비밀번호 교체: sudo htpasswd /etc/nginx/.htpasswd-naverland team")
    else:
        print("정상 범위로 보입니다 (팀 3명 = 집/회사/모바일 회선 몇 개 수준).")


if __name__ == "__main__":
    main()
