# 🔔 새 주소 실시간 카톡 알림 Implementation Plan

> 설계: `docs/superpowers/specs/2026-09-02-kakao-realtime-alert-design.md`. 단계마다 체크박스로 추적.

**Goal:** VPS 감시기(`watch.py --scan/--ack`)와 이 PC의 `/watch`(=`/loop 10m /watch-tick`) 명령으로, 업무 시간에 새 주소 매물이 뜨면 10분 안에 카톡 나와의 채팅으로 링크를 보낸다.

**Architecture:** 감시기는 구 단위 최신순 25요청/바퀴로 새 매물을 찾고, 홈페이지 DB의 `seen_locations`(읽기 전용)+자체 `watch_locations`로 새 주소를 판정해 메시지를 대기열(`data/watch.db`)에 넣는다. Claude 루프가 SSH로 스캔을 트리거하고 `message`를 PlayMCP MemoChat으로 그대로 보낸 뒤 ack 한다. 홈페이지 파이프라인은 불변.

**Tech Stack:** Python 표준라이브러리(sqlite3/urllib/tomllib), `unittest`. 서드파티 0.

## Global Constraints
- 홈페이지 파이프라인·`listings.db` 스키마·판정 로직 **불변**. 감시기는 `listings.db`를 `mode=ro`로만 연다.
- stdout엔 JSON 한 줄만(루프가 파싱). 로그는 파일(+`--verbose`면 stderr).
- 메시지 200자 상한(PlayMCP). 감시기가 완성본을 만들고 Claude는 수정 없이 전달.
- 테스트 러너: `python -m unittest tests.test_watch tests.test_core -v`

## Tasks
- [ ] 1. `app/config.py` `WatchCfg` + `Config.watch`/`watch_db_path`; `config.toml [watch]` 추가
- [ ] 2. `app/client.py` `article_detail(no)` 추가
- [ ] 3. `tests/test_watch.py` 작성(가짜 클라이언트) — 먼저 실패 확인
- [ ] 4. `app/watch.py`: `gu_list`, `WatchStore`, `format_message`, `scan`, `ack`, `status`, 잠금
- [ ] 5. `watch.py` CLI(`--scan/--ack/--status/--verbose`) — 테스트 통과
- [ ] 6. `.claude/commands/watch.md`, `.claude/commands/watch-tick.md`
- [ ] 7. README 섹션 + 메모리 갱신
- [ ] 8. 로컬 실검증(실 네이버 2회 스캔) → 커밋·푸시 → VPS `git pull` → VPS 2회 스캔 → MemoChat 테스트 1건
