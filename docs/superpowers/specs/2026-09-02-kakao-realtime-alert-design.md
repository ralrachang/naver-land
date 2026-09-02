# 🔔 새 주소 실시간 카톡 알림 (VPS 감시기 + PlayMCP 루프) — 설계

작성일: 2026-09-02
대상: `naver land` (네이버 부동산 신규 단독 매물 모니터)

## 배경 / 문제

홈페이지는 하루 3회(09/13/16시) 수집이라 경쟁팀보다 한발씩 늦다. 사용자는 **새 주소
매물이 올라오면 10분 안에 카카오톡 나와의 채팅으로 링크**를 받고 싶어 한다.
업무 시간(08~17시)에만 쓰고, 카카오 개발자 앱은 만들지 않기로 했다(PlayMCP 사용).

## 실측 근거 (2026-09-02)

- `/api/articles`는 **구(區) 코드**(예: 강남구 1168000000)로도 최신순 조회가 된다.
  서울 25개 구 × 1페이지 = 25요청/바퀴(약 40초). 서울 코드(1100000000)는 0건.
- 구 단위 응답엔 동 이름이 없다. 상세 `GET /api/articles/{no}`의
  `articleDetail.divisionName/sectionName`("강남구"/"청담동")으로 보충(알림 건당 1회).
- PlayMCP `KakaotalkChat-MemoChat`은 **Claude 세션 안에서만** 호출 가능, `message` 200자 상한.
  클라우드 예약 실행은 최소 1시간 간격이라 10분 주기는 **이 PC의 Claude Code `/loop 10m`**로만 가능.
- 이 PC → VPS SSH(`sise-vps` = srv1730165.hstgr.cloud) 접속 확인됨.
- 최근 이틀 새 주소는 수집당 8~29곳 → 하루 40~55건 알림 예상.

## 목표 / 비목표

**목표**
- VPS 감시기 `watch.py --scan`: 구 단위 최신순으로 새 매물을 찾고, **새 주소**면 카톡 메시지를
  만들어 대기열에 넣고, 미발송 대기열을 JSON으로 돌려준다.
- 이 PC의 `/watch-tick` 명령: SSH로 `--scan` → 알림 `message`를 PlayMCP로 발송 → `--ack`.
  사용자는 아침에 `/watch` 한 번(= `/loop 10m /watch-tick`).
- 홈페이지 파이프라인(listings.db, 하루 3회 수집, 🆕/NEW 판정)은 **한 글자도 안 바뀐다.**

**비목표(YAGNI)**
- 가격/유형/구 필터, 홈페이지 실시간 섹션, 카카오 REST(개발자 앱) 발송, 주말 자동 기동.
  (발송 출구는 대기열 구조라 나중에 REST notifier를 붙여도 감시기 재작업 없음.)

## 아키텍처

```
[이 PC · Claude Code]  /loop 10m /watch-tick
   │ ssh sise-vps "python3 watch.py --scan"  ──►  [VPS] app/watch.py
   │ ◄── stdout JSON {alerts:[{id,message}], ...}      ├ data/watch.db (자체 DB)
   │ MemoChat(message) × N                             └ data/listings.db  읽기 전용(seen_locations)
   └ ssh sise-vps "python3 watch.py --ack 1,2,3"
```

- 감시기는 **자체 DB `data/watch.db`**만 쓴다. 홈페이지 DB는 `mode=ro`로 열어 `seen_locations`만 읽는다.
- 스캔은 루프가 부를 때만 돈다(VPS cron 없음). 루프가 꺼지면 스캔도 멈춰 주말 누적이 없다.

## 판정 규칙

- **새 매물**: `watch_seen`에 없는 articleNo.
- **새 주소**: 좌표의 건물키(`store.building_keys`, 1.1m 지터 병합)가
  `seen_locations`(홈페이지, 읽기 전용) ∪ `watch_locations`(감시기) 어디에도 없음.
  같은 스캔에서 같은 새 건물의 두 번째 매물은 알림하지 않는다.
- **워밍업**: 직전 스캔이 없거나 `gap_reset_min`(60분) 넘게 지났으면 이번 스캔은 **기록만**
  하고 알림을 만들지 않는다(구당 1페이지). 아침 첫 틱은 조용하고 두 번째 틱부터 알린다.
  밤새 올라온 매물은 09시 홈페이지가 보여준다.
- **페이지**: 한 페이지에 이미 본 매물이 하나라도 있으면 그 구는 중단. 최대 `max_pages`(5).
- **차단(403/429)**: `blocked_until = now + block_pause_min`(30분) 기록, 안내 알림 1건(하루 1회).
  그 사이 `--scan`은 즉시 skip(대기열만 반환). 차단 시 그 스캔의 관측은 기록하지 않는다(다음 스캔이 알림).
- **대기열**: `alert_ttl_min`(180분) 넘긴 미발송은 expired. `--scan` 응답엔 오래된 순으로
  최대 `max_alerts_per_tick`(15)건. `--ack`가 `sent_at`을 찍는다(발송 실패분은 다음 틱 재시도).
- `watch_seen`은 30일 지난 행 정리. 동시 실행은 `data/watch.lock`(5분 stale)로 막는다.

## 메시지 (≤200자, 감시기가 완성본을 만들고 Claude는 그대로 전달)

```
🆕 새주소 · 강남구 청담동
상가건물 230억 · 대지255/연868㎡ · 5/B1
압구정로데오역 도보 1분 삼거리 노출 건물
https://m.land.naver.com/article/info/2647384345
```
특징 설명은 200자 예산에 맞춰 잘라 넣고(말줄임 …), 토지는 연면적/층이 없으면 생략.
동 조회 실패 시 구 이름만 쓴다.

## 데이터 (`data/watch.db`)

- `watch_seen(article_no PK, first_seen_at)`
- `watch_locations(loc_key PK, first_seen_at)`
- `watch_alerts(id PK, kind new_address|notice, article_no, created_at, message, sent_at, expired)`
- `watch_state(key PK, value)` — `last_scan_at`, `blocked_until`, `block_notified_on`

## CLI / 설정

- `python3 watch.py --scan | --ack 1,2,3 | --status [--verbose]` — **stdout엔 JSON 한 줄만**,
  로그는 `logs/watch-YYYYMMDD.log`(`--verbose`면 stderr에도).
- `config.toml [watch]`: `max_pages=5, gap_reset_min=60, alert_ttl_min=180,
  max_alerts_per_tick=15, block_pause_min=30`. 요청 간격은 `[crawl]` 값 재사용.
- 구 목록은 `data/regions.json`(동 캐시)의 동 코드 앞 5자리 + `00000`으로 유도.
- `client.Client.article_detail(no)` 추가(상세 조회 + 정중한 지연).

## 이 PC 명령 (`.claude/commands/`)

- `/watch` — `/loop 10m /watch-tick` 기동.
- `/watch-tick` — 고정 절차: ①KST 08:00~17:00 밖이면 건너뜀 ②`ssh --scan` ③`alerts[].message`를
  **그대로** MemoChat 발송 ④성공분만 `--ack` ⑤한 줄 요약. 질문·창작 금지.

## 오류 처리

- ssh 실패/JSON 아님 → 한 줄 보고 후 종료, 다음 틱 재시도.
- MemoChat 실패 → ack 안 함 → TTL 안에서 다음 틱 재발송.
- 지역 캐시 없음 → 오류 JSON(`run.py`를 먼저 한 번 돌리라는 안내).

## 테스트 / 검증

- `tests/test_watch.py`(unittest, 가짜 클라이언트): 구 유도, 워밍업 무알림, 새 주소 판정
  (알려진 좌표·지터·같은 건물 2건), 페이지 중단 규칙, gap 재워밍업, 차단 처리, TTL/ack/상한, 메시지 길이, 잠금.
- 실검증: 로컬 2회 스캔(실 네이버), VPS 2회 스캔, MemoChat 테스트 발송 1건.

## 배포

git push → `ssh sise-vps "cd /opt/naver-land && git pull"`. VPS는 `run.py`가 이미 돌아 `regions.json`이 있다.
