# 🆕 새 주소 "최근 2일" + 수집분별 그룹 헤더 — 설계

작성일: 2026-07-13
대상: `naver land` (네이버 부동산 신규 단독 매물 모니터)

## 배경 / 문제

현재 **🆕 새 주소** 뷰(기본 화면)는 `store.latest_location_batch()` 로 **가장 최근 수집 1회분**의
새 위치만 표시한다. 하루 2회(09:00·16:00 KST) 수집하는데, 특정 수집에서 새 위치가 1개뿐이면
화면에 딱 1건만 뜬다(예: 오늘 오전 1건). 사용자는 "이전 수집분까지 최근 2일 정도"를 함께
보고 싶어 한다.

핵심: **데이터는 이미 다 있다.** `seen_locations` 는 모든 위치의 최초 목격 시각(`first_seen_at`)을
영구 보관하고, 매물도 `keep_days=14` 로 2주치가 살아있다. 따라서 **재수집 없이 조회 범위만
넓히면** 최근 2일치 새 주소를 보여줄 수 있다.

## 목표 / 비목표

**목표**
- 🆕 새 주소 표시 범위를 "최근 정상 수집 1회분" → "실제 수집 배치 기준 최근 N일"(기본 2일)로 확장.
- 🆕 필터가 켜졌을 때 매물을 **수집 배치별로 그룹핑**하고 날짜 헤더(`── 7.13 오전 (n) ──`)로 구분.
- 재수집 불필요: 기존 DB로 `run.py --regenerate` 한 번이면 반영·배포.

**비목표(YAGNI)**
- "새 주소 = 이전엔 없던 좌표" **판정 로직 자체는 절대 변경하지 않는다.** 바뀌는 것은 오직 표시 범위.
- 알림, N일(기본 2) 초과 히스토리 UI, 그룹 접기/펼치기, 별도 페이지.

## 급소: 가짜 새주소 재발 방지

`--rebaseline`(전체 재스캔)은 그동안 페이지 상한에 잘렸던 꼬리 매물의 위치를 백필하며,
`seen_locations.first_seen_at` 을 **네이버 '매물 확인일'**(예: `2026-07-12 00:00:00`)로 기록한다.
단순히 "first_seen_at 이 최근 2일" 로 거르면 이 백필 위치가 **가짜 새주소**로 되살아난다
(과거 실제로 겪은 오판 — `naver-land-tech-facts` 참고).

**해결:** 실제 정상 수집 배치만 "새 주소 배치"로 인정한다.
- 배치 후보 = `seen_locations.first_seen_at` 값 중 `runs.run_at` 과 **정확히 일치**하고
  그 run 의 `note IN ('generated','skip_empty')` 인 것(= 정상 수집 run).
- 정상 수집은 `register_locations(items, run_ts)` 와 `record_run(run_ts, ...)` 가 **같은 run_ts** 를
  쓰므로 first_seen_at == run_at 로 일치한다.
- rebaseline 백필은 first_seen_at 이 '확인일(00:00:00)' 이라 어떤 run_at 과도 일치하지 않아
  자동 배제된다. rebaseline 자신의 run 은 `runs` 에 기록되지만(note='rebaseline'), 그 run_ts 로
  등록된 seen_location 이 없어 배치로 잡히지 않는다.

기준 시점(anchor)은 **가장 최근 정상 배치 시각**으로 잡고 거기서 N일 역산한다(수집이 잠시 멈춰도
안정적 — 💎정밀단독의 `solo_window` 와 동일한 방식).

## 변경 상세

### 1) `app/store.py`

새 메서드:

```python
def recent_location_batches(self, days: int) -> dict[str, str]:
    """실제 정상 수집 배치 기준 최근 N일의 새 위치 → 배치시각 맵.

    반환: {loc_key: batch_ts}. 가짜 새주소(rebaseline 백필) 배제를 위해
    first_seen_at 이 정상 수집 run_at 과 일치하는 위치만 포함한다.
    """
```

동작:
1. 정상 배치 시각 집합 조회:
   `SELECT run_at FROM runs WHERE note IN ('generated','skip_empty')`
2. anchor = 그중 MAX(run_at). 없으면 빈 dict 반환(첫 수집 전 등).
3. cutoff = anchor 에서 `days` 일 역산(`julianday` 비교).
4. `seen_locations` 에서 `first_seen_at >= cutoff` **AND** `first_seen_at IN (정상 run_at 집합)`
   인 (loc_key, first_seen_at) 을 뽑아 `{loc_key: first_seen_at}` 로 반환.

`active_listings` 시그니처 확장:
- 기존 `new_loc_keys: set` 파라미터를 `new_loc_batches: dict[str,str] | set | None` 로 받도록 확장.
  하위호환을 위해 set 이 들어오면 배치시각 없이 `is_new_location` 만 세팅.
- 각 행에 세팅:
  - `is_new_location`: `f"{lat},{lng}"` 가 맵/셋에 존재.
  - `new_location_batch`: 맵일 때 해당 loc 의 배치시각(문자열), 아니면 `None`.
- 그 외 정렬·판정 로직은 **불변**.

기존 `latest_location_batch()` 는 제거하지 않고 남겨둔다(테스트/하위호환). 호출부만 교체.

### 2) `app/pipeline.py`

세 경로 모두 표시용 new_loc 소스를 `recent_location_batches(window)` 로 교체.
`window = cfg.site.new_location_window_days`.

- `run_pipeline`: 새 위치 **기록**은 기존대로 `st.register_locations(items, run_ts)` 유지(부작용 필요).
  표시용으로만 `batches = st.recent_location_batches(window)` 를 새로 조회해
  `active_listings(new_ids=..., new_loc_batches=batches, ...)` 로 전달.
- `regenerate`: `latest_location_batch()` → `recent_location_batches(window)`.
- `rebaseline`: `latest_location_batch()` → `recent_location_batches(window)`.
  (rebaseline 이 배지를 만들지 않는 성질은 그대로 유지 — 백필 위치가 배치로 안 잡히므로.)

### 3) `app/generate.py`

`_build_listings` 에서 새 위치 매물에 필드 추가:
- `nlb`: 배치 라벨. `new_location_batch`(예: `"2026-07-13 09:00:03"`)를 `"7.13 오전"` 으로 포맷.
  - 날짜: `M.D`(0 패딩 없음). 오전/오후: run 시각 hour < 12 → "오전", else "오후".
  - `new_location_batch` 가 없으면(set 경로/구 데이터) 빈 문자열.
- `nlbt`: 정렬용 원본 시각 문자열(그대로). 최신 배치가 위로 오도록 내림차순 정렬 키.

포맷 헬퍼는 generate.py 안에 작은 함수로 추가(`_batch_label(ts) -> str`).

### 4) 템플릿 JS(generate.py 내 `TEMPLATE`)

- 🆕 필터(`newLocOnly`)가 **ON** 일 때만 그룹 헤더 렌더링:
  - 필터링된 목록을 `nlbt` 내림차순으로 안정 정렬.
  - 렌더 루프에서 직전 행과 `nlb` 가 달라지면 `<div class="batch">── {nlb} ({그룹내 개수}) ──</div>` 헤더 삽입.
  - 그룹 개수는 **현재 필터(구/💎/🎯/💰) 적용 후** 각 배치의 행 수.
- 🆕 필터 OFF 면 헤더 없이 지금과 동일한 평면 리스트.
- 헤더 스타일(`.batch`)은 기존 톤(muted, 작은 글씨, sticky 아님)로 CSS 소량 추가.
- 기존 정렬(신규→인하→나머지)은 🆕 OFF 경로에서 그대로. 🆕 ON 경로는 배치 그룹 정렬이 우선.

### 5) 설정

- `app/config.py` `SiteCfg` 에 `new_location_window_days: int = 2` 추가(dataclass 필드만 추가하면
  `load()` 의 필터가 자동 반영).
- `config.toml` `[site]` 에 주석과 함께 `new_location_window_days = 2` 추가.

## 데이터 흐름 요약

```
seen_locations(first_seen_at 영구) + runs(정상 배치 run_at)
        └── recent_location_batches(days) ──▶ {loc_key: batch_ts}
active_listings(new_loc_batches) ──▶ 행에 is_new_location + new_location_batch
_build_listings ──▶ 행에 nl / nlb("7.13 오전") / nlbt(정렬키)
템플릿 JS: 🆕 ON → nlbt desc 정렬 + nlb 바뀔 때 헤더 삽입
```

## 테스트(tests/test_core.py 확장)

- `recent_location_batches`:
  - 정상 배치 2개(예: 오늘/어제) + rebaseline 백필 위치 1개(확인일) 를 심어놓고,
    반환 맵에 정상 2배치 위치만 포함되고 **백필 위치는 제외**되는지(가짜 새주소 방지 회귀 테스트).
  - anchor 로부터 N일 밖 배치는 제외되는지.
  - runs 가 비었을 때 빈 dict.
- `_batch_label`: 09:xx → "오전", 16:xx → "오후", M.D 포맷.
- `active_listings`: dict 전달 시 `new_location_batch` 세팅, set 전달 시 하위호환(배치 None).

## 검증(수동)

로컬에서 소규모 시드 DB로 `--regenerate` 실행 → 생성된 `site/index.html` 을 브라우저로 열어
🆕 필터 ON 시 배치 헤더가 최신순으로 뜨고, 구 필터와 조합되며, OFF 시 평면 리스트로 돌아오는지 확인.

## 배포

코드 변경 후 VPS 반영은 수동:
`cd /opt/naver-land && git pull && set -a; . ./.env.sh; set +a && python3 run.py --regenerate`
(재수집 불필요 — 기존 DB로 즉시 반영·배포. `--regenerate` 는 배포 설정 로딩 주의: `naver-land-project` 메모리 참고.)
