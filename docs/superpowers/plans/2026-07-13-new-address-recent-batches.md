# 🆕 새 주소 최근 2일 + 수집분별 그룹 헤더 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 🆕 새 주소 뷰가 "가장 최근 수집 1회분"이 아니라 "실제 정상 수집 배치 기준 최근 N일(기본 2일)"을 수집분별 날짜 헤더로 그룹핑해 보여주도록 확장한다.

**Architecture:** 데이터는 이미 DB(`seen_locations` 영구 + `listings` 14일 보관)에 있으므로 **재수집 없이 조회 범위만 확장**한다. store 에 `recent_location_batches(days, current_ts)` 를 추가해 `{loc_key: 배치시각}` 를 만들고(가짜 새주소 방지를 위해 정상 수집 `run_at` 과 일치하는 배치만 인정), generate 가 각 새 위치 행에 배치 라벨(`nlb`)·정렬키(`nlbt`)를 실어, 템플릿 JS 가 🆕 필터 ON 일 때만 배치별 헤더로 그룹핑한다. pipeline 3개 경로(run_pipeline/regenerate/rebaseline)가 이 조회를 사용한다.

**Tech Stack:** 순수 Python 표준라이브러리(sqlite3/tomllib/datetime), 바닐라 JS 임베드 템플릿, `unittest`(stdlib). 서드파티 0.

## Global Constraints

- **판정 로직 불변:** "새 주소 = 이전엔 없던 좌표" 정의·계산은 절대 바꾸지 않는다. 이 작업은 오직 **표시 범위**만 넓힌다.
- **가짜 새주소 재발 방지(회귀 필수):** `first_seen_at` 이 정상 수집 `run_at`(`runs.note IN ('generated','skip_empty')`)과 정확히 일치하는 배치만 새주소 배치로 인정한다. `--rebaseline` 백필(확인일=`YYYY-MM-DD 00:00:00` 로 등록)은 배치로 잡히면 안 된다.
- **서드파티 금지:** 표준 라이브러리만. 새 의존성 추가 금지.
- **테스트 러너:** `python -m unittest tests.test_core -v` (프로젝트 루트 `D:\claude_project\naver land` 에서).
- **하위호환:** `active_listings` 의 `new_loc_keys` 파라미터 이름 유지(set 또는 dict 수용). 기존 테스트가 `new_loc_keys=` 키워드를 쓴다.
- **커밋 메시지:** 한국어 요약 한 줄 + 본문 끝에 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

- `app/config.py` — `SiteCfg` 에 `new_location_window_days` 필드 추가(로딩 자동).
- `config.toml` — `[site]` 에 `new_location_window_days = 2` 추가.
- `app/store.py` — `recent_location_batches()` 신규 + `active_listings()` 에 `new_location_batch` 세팅.
- `app/generate.py` — `_batch_label()` 신규 + `_build_listings()` 에 `nlb`/`nlbt` + `TEMPLATE` JS/CSS 그룹 헤더.
- `app/pipeline.py` — 3개 경로에서 new_loc 소스를 `recent_location_batches()` 로 교체.
- `tests/test_core.py` — 각 태스크의 테스트 추가.

---

## Task 1: config — `new_location_window_days`

**Files:**
- Modify: `app/config.py` (SiteCfg 데이터클래스)
- Modify: `config.toml` (`[site]` 섹션)
- Test: `tests/test_core.py` (새 `TestConfig` 클래스)

**Interfaces:**
- Consumes: 없음.
- Produces: `cfg.site.new_location_window_days: int` (기본 2). 이후 pipeline 태스크가 사용.

- [ ] **Step 1: Write the failing test**

`tests/test_core.py` 맨 아래에 클래스 추가(파일 상단 import 에 이미 `tempfile`, `Path` 있음):

```python
class TestConfig(unittest.TestCase):
    def _write(self, body: str) -> Path:
        d = tempfile.mkdtemp()
        p = Path(d) / "c.toml"
        p.write_text(body, encoding="utf-8")
        return p

    def test_new_location_window_default_is_2(self):
        from app import config as config_mod
        cfg = config_mod.load(self._write('[site]\ntitle = "t"\n'))
        self.assertEqual(cfg.site.new_location_window_days, 2)

    def test_new_location_window_override(self):
        from app import config as config_mod
        cfg = config_mod.load(self._write(
            '[site]\ntitle = "t"\nnew_location_window_days = 3\n'))
        self.assertEqual(cfg.site.new_location_window_days, 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_core.TestConfig -v`
Expected: FAIL — `AttributeError: 'SiteCfg' object has no attribute 'new_location_window_days'`

- [ ] **Step 3: Add the field**

`app/config.py` — `SiteCfg` 의 `solo_window_days` 줄 바로 아래에 추가:

```python
@dataclass
class SiteCfg:
    title: str = "네이버 부동산 매매 매물"
    subtitle: str = ""
    timezone: str = "Asia/Seoul"
    keep_days: int = 30
    solo_window_days: int = 30       # 💎정밀단독: 최근 N일 목격 이력 전체로 좌표별 광고 수 판정
    new_location_window_days: int = 2  # 🆕 새 주소: 최근 N일 정상 수집 배치의 새 위치를 그룹으로 표시
```

`config.toml` — `[site]` 의 `solo_window_days = 30 ...` 블록 다음 줄에 추가:

```toml
new_location_window_days = 2      # 🆕 새 주소를 최근 N일 수집분까지 수집분별 헤더로 그룹 표시.
                                  # 정상 수집(run) 배치만 인정 → --rebaseline 백필은 새주소로 안 뜸.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_core.TestConfig -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/config.py config.toml tests/test_core.py
git commit -m "새 주소 표시 창 설정 new_location_window_days(기본 2) 추가

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: store — `recent_location_batches()` + `active_listings` 배치 필드

**Files:**
- Modify: `app/store.py` (`Store` 클래스에 메서드 추가, `active_listings` 수정)
- Test: `tests/test_core.py` (새 `TestRecentBatches` 클래스)

**Interfaces:**
- Consumes: 기존 `Store.upsert`, `Store.register_locations`, `Store.register_locations_baseline`, `Store.record_run`.
- Produces:
  - `Store.recent_location_batches(days: int, current_ts: str | None = None) -> dict[str, str]` — `{loc_key: batch_ts}`.
  - `Store.active_listings(..., new_loc_keys: set | dict | None, ...)` — 각 행에 `is_new_location: bool` 과 `new_location_batch: str | None` 세팅.

- [ ] **Step 1: Write the failing tests**

`tests/test_core.py` 에 클래스 추가:

```python
class TestRecentBatches(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "t.db"

    def _item(self, ano, lat, lng, confirm="20260708"):
        return {"article_no": ano, "address": "서울특별시 강남구 개포동",
                "sido": "서울특별시", "gu": "강남구", "dong": "개포동",
                "price_text": "10억", "price_manwon": 100000, "re_type": "건물",
                "article_name": "빌딩", "confirm_ymd": confirm, "same_addr_cnt": None,
                "feature_desc": "", "area": 100, "floor": "", "lat": lat, "lng": lng}

    def _normal_batch(self, st, ano, lat, lng, ts):
        it = self._item(ano, lat, lng)
        st.upsert([it], ts)
        st.register_locations([it], ts)
        st.record_run(ts, 1, 0, st.count_active(), True, "generated")

    def test_includes_normal_runs_within_window(self):
        st = Store(self.db)
        self._normal_batch(st, "A", "37.1", "127.1", "2026-07-12 09:00:00")
        self._normal_batch(st, "B", "37.2", "127.2", "2026-07-13 09:00:00")
        self.assertEqual(
            st.recent_location_batches(2),
            {"37.1,127.1": "2026-07-12 09:00:00",
             "37.2,127.2": "2026-07-13 09:00:00"})
        st.close()

    def test_rebaseline_backfill_excluded(self):
        # 급소 회귀: 확인일로 백필된 위치는 새주소 배치로 잡히면 안 됨
        st = Store(self.db)
        self._normal_batch(st, "A", "37.1", "127.1", "2026-07-13 09:00:00")
        tail = self._item("T", "37.9", "127.9", confirm="20260713")
        st.register_locations_baseline([tail])
        st.record_run("2026-07-13 16:00:00", 0, 1, st.count_active(), True, "rebaseline")
        batches = st.recent_location_batches(2)
        self.assertIn("37.1,127.1", batches)
        self.assertNotIn("37.9,127.9", batches)
        st.close()

    def test_batch_beyond_window_excluded(self):
        st = Store(self.db)
        self._normal_batch(st, "OLD", "37.1", "127.1", "2026-07-01 09:00:00")
        self._normal_batch(st, "NEW", "37.2", "127.2", "2026-07-13 09:00:00")
        self.assertEqual(set(st.recent_location_batches(2)), {"37.2,127.2"})
        st.close()

    def test_empty_runs_returns_empty(self):
        st = Store(self.db)
        self.assertEqual(st.recent_location_batches(2), {})
        st.close()

    def test_current_ts_included_before_record_run(self):
        # run_pipeline 은 record_run 을 표시 이후 호출 → current_ts 로 이번 배치를 인정
        st = Store(self.db)
        it = self._item("A", "37.1", "127.1")
        st.upsert([it], "2026-07-13 09:00:00")
        st.register_locations([it], "2026-07-13 09:00:00")
        # 아직 record_run 안 함
        self.assertEqual(st.recent_location_batches(2), {})
        self.assertEqual(
            st.recent_location_batches(2, current_ts="2026-07-13 09:00:00"),
            {"37.1,127.1": "2026-07-13 09:00:00"})
        st.close()

    def test_active_listings_sets_batch_from_dict(self):
        st = Store(self.db)
        self._normal_batch(st, "A", "37.1", "127.1", "2026-07-13 09:00:00")
        batches = st.recent_location_batches(2)
        rows = {r["article_no"]: r for r in st.active_listings(new_loc_keys=batches)}
        self.assertTrue(rows["A"]["is_new_location"])
        self.assertEqual(rows["A"]["new_location_batch"], "2026-07-13 09:00:00")
        st.close()

    def test_active_listings_set_backward_compat(self):
        st = Store(self.db)
        it = self._item("A", "37.1", "127.1")
        st.upsert([it], "2026-07-13 09:00:00")
        newloc = st.register_locations([it], "2026-07-13 09:00:00")
        rows = {r["article_no"]: r for r in st.active_listings(new_loc_keys=newloc)}
        self.assertTrue(rows["A"]["is_new_location"])
        self.assertIsNone(rows["A"]["new_location_batch"])
        st.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_core.TestRecentBatches -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'recent_location_batches'` (그리고 `new_location_batch` KeyError)

- [ ] **Step 3: Add `recent_location_batches` to `Store`**

`app/store.py` — `latest_location_batch` 메서드 바로 다음에 추가:

```python
    def recent_location_batches(self, days: int,
                                current_ts: str | None = None) -> dict:
        """실제 정상 수집 배치 기준 최근 N일의 새 위치 → 배치시각 맵 {loc_key: batch_ts}.

        가짜 새주소(rebaseline 백필) 배제: first_seen_at 이 정상 수집 run_at
        (runs.note IN ('generated','skip_empty'))과 정확히 일치하는 위치만 포함.
        current_ts: 진행 중인 이번 수집의 run_ts(아직 runs 에 없음)를 정상 배치로 인정.
        anchor(최근 정상 배치)에서 days 만큼 역산한 창 안의 위치만 반환.
        """
        batch_ts = [r[0] for r in self.conn.execute(
            "SELECT run_at FROM runs WHERE note IN ('generated','skip_empty')")]
        if current_ts:
            batch_ts.append(current_ts)
        if not batch_ts:
            return {}
        anchor = max(batch_ts)
        batch_set = set(batch_ts)
        out: dict = {}
        for r in self.conn.execute(
            "SELECT loc_key, first_seen_at FROM seen_locations "
            "WHERE julianday(?) - julianday(first_seen_at) <= ?",
            (anchor, days),
        ):
            if r["first_seen_at"] in batch_set:
                out[r["loc_key"]] = r["first_seen_at"]
        return out
```

- [ ] **Step 4: Wire batch into `active_listings`**

`app/store.py` — `active_listings` 시그니처의 타입 힌트를 넓히고 배치 필드를 세팅한다.

시그니처 변경:

```python
    def active_listings(self, new_ids: set[str] | None = None,
                        new_loc_keys: set | dict | None = None,
                        solo_window_days: int = 30) -> list[dict]:
```

메서드 상단 `new_loc_keys = new_loc_keys or set()` 다음 줄에 추가:

```python
        new_ids = new_ids or set()
        new_loc_keys = new_loc_keys or set()
        loc_batch = new_loc_keys if isinstance(new_loc_keys, dict) else {}
```

행 루프에서 `r["is_new_location"] = f"{r['lat']},{r['lng']}" in new_loc_keys` 줄 **다음에** 추가:

```python
            r["is_new_location"] = f"{r['lat']},{r['lng']}" in new_loc_keys
            r["new_location_batch"] = loc_batch.get(f"{r['lat']},{r['lng']}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_core.TestRecentBatches -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Run full suite (no regressions)**

Run: `python -m unittest tests.test_core -v`
Expected: PASS (기존 테스트 전부 그대로 통과 — `active_listings` 하위호환 유지)

- [ ] **Step 7: Commit**

```bash
git add app/store.py tests/test_core.py
git commit -m "store: recent_location_batches(정상 배치만) + active_listings 배치 필드

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: generate — `_batch_label()` + `_build_listings` 배치 필드

**Files:**
- Modify: `app/generate.py` (`_batch_label` 신규, `_build_listings` 필드 추가)
- Test: `tests/test_core.py` (새 `TestBatchLabel` 클래스)

**Interfaces:**
- Consumes: `active_listings` 행의 `new_location_batch: str | None` (Task 2).
- Produces: `_build_listings` 출력 dict 에 `nlb: str`(라벨, 예 "7.13 오전"), `nlbt: str`(정렬키, 원본 시각 또는 "").

- [ ] **Step 1: Write the failing tests**

`tests/test_core.py` 에 추가:

```python
class TestBatchLabel(unittest.TestCase):
    def test_morning_afternoon(self):
        from app.generate import _batch_label
        self.assertEqual(_batch_label("2026-07-13 09:00:03"), "7.13 오전")
        self.assertEqual(_batch_label("2026-07-12 16:00:00"), "7.12 오후")

    def test_empty_or_bad(self):
        from app.generate import _batch_label
        self.assertEqual(_batch_label(None), "")
        self.assertEqual(_batch_label(""), "")
        self.assertEqual(_batch_label("bad"), "")

    def test_build_listings_carries_batch(self):
        from app.generate import _build_listings
        rows = [{"address": "서울 강남", "price_text": "10억", "re_type": "건물",
                 "confirm_ymd": "20260708", "is_new": False, "is_new_location": True,
                 "article_no": "A", "gu": "강남구", "loc_count": 1, "feature_desc": "",
                 "is_precise_solo": False, "is_price_cut": False,
                 "new_location_batch": "2026-07-13 09:00:03"}]
        out = _build_listings(rows)
        self.assertEqual(out[0]["nlb"], "7.13 오전")
        self.assertEqual(out[0]["nlbt"], "2026-07-13 09:00:03")

    def test_build_listings_no_batch(self):
        from app.generate import _build_listings
        rows = [{"address": "서울 강남", "price_text": "10억", "re_type": "건물",
                 "confirm_ymd": "20260708", "is_new": False, "is_new_location": False,
                 "article_no": "A", "gu": "강남구", "loc_count": 1, "feature_desc": "",
                 "is_precise_solo": False, "is_price_cut": False,
                 "new_location_batch": None}]
        out = _build_listings(rows)
        self.assertEqual(out[0]["nlb"], "")
        self.assertEqual(out[0]["nlbt"], "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_core.TestBatchLabel -v`
Expected: FAIL — `ImportError: cannot import name '_batch_label'`

- [ ] **Step 3: Add `_batch_label`**

`app/generate.py` — `_fmt_confirm` 함수 바로 위(또는 아래)에 추가. `datetime` 은 파일 상단에서 이미 import 됨:

```python
def _batch_label(ts: str | None) -> str:
    """배치시각 '2026-07-13 09:00:03' → '7.13 오전'(hour<12=오전, else 오후). 불량이면 ''."""
    if not ts:
        return ""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return ""
    ampm = "오전" if dt.hour < 12 else "오후"
    return f"{dt.month}.{dt.day} {ampm}"
```

- [ ] **Step 4: Add fields to `_build_listings`**

`app/generate.py` — `_build_listings` 의 out.append({...}) 딕셔너리에서 `"nl": bool(r.get("is_new_location")),` 줄 **다음에** 추가:

```python
            "nl": bool(r.get("is_new_location")),  # 이전엔 없던 위치에 새로 등장
            "nlb": _batch_label(r.get("new_location_batch")),  # 수집분 라벨 "7.13 오전"
            "nlbt": r.get("new_location_batch") or "",         # 그룹 정렬키(원본 시각)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_core.TestBatchLabel -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add app/generate.py tests/test_core.py
git commit -m "generate: 배치 라벨(_batch_label) + 매물에 nlb/nlbt 필드

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: generate — 템플릿 JS 그룹 헤더 + CSS

**Files:**
- Modify: `app/generate.py` (`TEMPLATE` 문자열 내 CSS 1줄 + `render()` JS)
- Test: `tests/test_core.py` (새 `TestGenerateGrouping` 클래스)

**Interfaces:**
- Consumes: 임베드 데이터의 `nl`/`nlb`/`nlbt` (Task 3), 기존 `newLocOnly` 토글 상태.
- Produces: 🆕 필터 ON 시 배치별 `<div class="batch">── {nlb} ({n}) ──</div>` 헤더가 최신 배치순으로 삽입된 리스트. OFF 시 기존과 동일한 평면 리스트.

- [ ] **Step 1: Write the failing test**

`tests/test_core.py` 에 추가. 실제 `site/` 를 덮지 않도록 **임시 root 로 Config 를 직접 구성**한다:

```python
class TestGenerateGrouping(unittest.TestCase):
    def _cfg(self):
        from app.config import Config, CrawlCfg, SiteCfg, DeployCfg
        root = Path(tempfile.mkdtemp())
        for sub in ("data", "site", "logs"):
            (root / sub).mkdir()
        return Config(crawl=CrawlCfg(),
                      site=SiteCfg(title="t", subtitle="s"),
                      deploy=DeployCfg(enabled=False), regions=[], root=root)

    def test_batch_header_js_and_label_embedded(self):
        from datetime import datetime, timezone, timedelta
        from app import generate
        cfg = self._cfg()
        rows = [{"address": "서울 강남", "price_text": "10억", "re_type": "건물",
                 "confirm_ymd": "20260708", "is_new": False, "is_new_location": True,
                 "article_no": "A", "gu": "강남구", "loc_count": 1, "feature_desc": "",
                 "is_precise_solo": False, "is_price_cut": False,
                 "new_location_batch": "2026-07-13 09:00:03"}]
        ok = generate.generate(cfg, rows, 1, run_dt=datetime(
            2026, 7, 13, 9, 0, tzinfo=timezone(timedelta(hours=9))))
        self.assertTrue(ok)
        html_txt = (cfg.site_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="batch"', html_txt)  # 그룹 헤더 렌더 JS 존재
        self.assertIn("7.13 오전", html_txt)        # 배치 라벨 임베드
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_core.TestGenerateGrouping -v`
Expected: FAIL — `AssertionError: 'class="batch"' not found` (아직 그룹 JS 없음)

- [ ] **Step 3: Add `.batch` CSS**

`app/generate.py` — `TEMPLATE` 의 `<style>` 안, `.empty{...}` 줄 다음에 한 줄 추가:

```css
.empty{text-align:center;color:var(--muted);padding:48px 0}
.batch{margin:16px 4px 4px;color:var(--muted);font-size:.82rem;font-weight:700}
```

- [ ] **Step 4: Update `render()` JS for grouping**

`app/generate.py` — `TEMPLATE` 안의 기존 `render()` 함수 전체를 아래로 교체한다.

기존:

```javascript
  function render(){
    renderFilters();
    var f = filtered();
    document.getElementById('curcount').textContent = f.length;
    if (!f.length){ listEl.innerHTML = '<div class="empty">표시할 매물이 없습니다.</div>'; moreEl.style.display='none'; return; }
    var slice = f.slice(0, shown), h = '';
    for (var i=0;i<slice.length;i++){ h += rowHtml(slice[i]); }
    listEl.innerHTML = h;
    if (f.length > shown){ moreEl.style.display='block'; moreBtn.textContent = '더 보기 ('+(f.length-shown)+'건 남음)'; }
    else { moreEl.style.display='none'; }
  }
```

교체:

```javascript
  function render(){
    renderFilters();
    var f = filtered();
    document.getElementById('curcount').textContent = f.length;
    if (!f.length){ listEl.innerHTML = '<div class="empty">표시할 매물이 없습니다.</div>'; moreEl.style.display='none'; return; }
    var grouped = newLocOnly;  // 🆕 필터 ON 일 때만 수집분별 헤더
    if (grouped){
      f = f.slice().sort(function(a,b){ var x=a.nlbt||'', y=b.nlbt||''; return x<y?1:(x>y?-1:0); });  // 최신 배치 먼저
    }
    var bcount = {};
    if (grouped){ for (var k=0;k<f.length;k++){ var bk=f[k].nlbt||''; bcount[bk]=(bcount[bk]||0)+1; } }
    var slice = f.slice(0, shown), h = '', lastB = null;
    for (var i=0;i<slice.length;i++){
      var x = slice[i];
      if (grouped){
        var bk2 = x.nlbt||'';
        if (bk2 !== lastB){ lastB = bk2; h += '<div class="batch">── '+esc(x.nlb||'이전 수집')+' ('+(bcount[bk2]||0)+') ──</div>'; }
      }
      h += rowHtml(x);
    }
    listEl.innerHTML = h;
    if (f.length > shown){ moreEl.style.display='block'; moreBtn.textContent = '더 보기 ('+(f.length-shown)+'건 남음)'; }
    else { moreEl.style.display='none'; }
  }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest tests.test_core.TestGenerateGrouping -v`
Expected: PASS (1 test)

- [ ] **Step 6: Manual browser check (그룹 헤더 실제 렌더 확인)**

임시 시드로 사이트를 만들어 브라우저로 연다(실제 `site/` 를 덮지 않음). 프로젝트 루트에서:

```bash
python - <<'PY'
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
from app.config import Config, CrawlCfg, SiteCfg, DeployCfg
from app import generate
root = Path(tempfile.mkdtemp()); [ (root/s).mkdir() for s in ("data","site","logs") ]
cfg = Config(crawl=CrawlCfg(), site=SiteCfg(title="미리보기", subtitle="배치 그룹 확인"),
             deploy=DeployCfg(enabled=False), regions=[], root=root)
def row(no, gu, batch, nl=True):
    return {"address":"서울 "+gu,"price_text":"10억","re_type":"건물","confirm_ymd":"20260708",
            "is_new":False,"is_new_location":nl,"article_no":no,"gu":gu,"loc_count":1,
            "feature_desc":"","is_precise_solo":False,"is_price_cut":False,
            "new_location_batch":batch}
rows = [row("A","강남구","2026-07-13 09:00:00"),
        row("B","서초구","2026-07-12 16:00:00"), row("C","송파구","2026-07-12 16:00:00"),
        row("D","마포구","2026-07-12 09:00:00")]
generate.generate(cfg, rows, 1, run_dt=datetime(2026,7,13,9,0,tzinfo=timezone(timedelta(hours=9))))
print(cfg.site_dir/"index.html")
PY
```

출력된 경로의 `index.html` 을 브라우저로 열어 확인:
- 🆕 필터가 기본 ON → `── 7.13 오전 (1) ──`, `── 7.12 오후 (2) ──`, `── 7.12 오전 (1) ──` 순서로 헤더가 뜨는지.
- 구 필터(예: 서초구) 누르면 해당 그룹 개수가 갱신되는지.
- 🆕 필터를 끄면 헤더가 사라지고 평면 리스트로 돌아오는지.

- [ ] **Step 7: Commit**

```bash
git add app/generate.py tests/test_core.py
git commit -m "generate: 🆕 필터 ON 시 수집분별 그룹 헤더 렌더 + .batch 스타일

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: pipeline — 3개 경로 배치 조회로 교체

**Files:**
- Modify: `app/pipeline.py` (`run_pipeline`, `regenerate`, `rebaseline`)
- Test: `tests/test_core.py` (새 `TestPipelineRegenerate` 클래스)

**Interfaces:**
- Consumes: `Store.recent_location_batches(days, current_ts)` (Task 2), `cfg.site.new_location_window_days` (Task 1), `_build_listings` 필드(Task 3).
- Produces: 세 경로 모두 최근 N일 배치 그룹을 표시. run_pipeline 은 `current_ts=run_ts` 를 넘겨 이번 배치를 포함.

- [ ] **Step 1: Write the failing test**

`tests/test_core.py` 에 추가(`regenerate` 는 네트워크 불필요, deploy 는 enabled=False 로 no-op):

```python
class TestPipelineRegenerate(unittest.TestCase):
    def _cfg(self):
        from app.config import Config, CrawlCfg, SiteCfg, DeployCfg
        root = Path(tempfile.mkdtemp())
        for sub in ("data", "site", "logs"):
            (root / sub).mkdir()
        return Config(crawl=CrawlCfg(),
                      site=SiteCfg(title="t", subtitle="s", new_location_window_days=2),
                      deploy=DeployCfg(enabled=False), regions=[], root=root)

    def test_regenerate_uses_recent_batches(self):
        import json
        from app import pipeline
        cfg = self._cfg()
        st = Store(cfg.db_path)
        it = {"article_no": "A", "address": "서울 강남", "sido": "서울특별시",
              "gu": "강남구", "dong": "개포동", "price_text": "10억",
              "price_manwon": 100000, "re_type": "건물", "article_name": "빌딩",
              "confirm_ymd": "20260708", "same_addr_cnt": None, "feature_desc": "",
              "area": 100, "floor": "", "lat": "37.1", "lng": "127.1"}
        st.upsert([it], "2026-07-13 09:00:00")
        st.register_locations([it], "2026-07-13 09:00:00")
        st.record_run("2026-07-13 09:00:00", 1, 0, 1, True, "generated")
        st.close()
        res = pipeline.regenerate(cfg, dry_run=True)
        self.assertTrue(res["ok"])
        data = json.loads((cfg.site_dir / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(data["listings"][0]["nlb"], "7.13 오전")
        self.assertEqual(data["listings"][0]["nl"], True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_core.TestPipelineRegenerate -v`
Expected: FAIL — `data.json` 의 `nlb` 가 `""` (regenerate 가 아직 `latest_location_batch()`(set 반환)를 써서 배치시각이 안 실림)

- [ ] **Step 3: Update `regenerate`**

`app/pipeline.py` — `regenerate` 안:

```python
        new_ids = st.latest_batch_ids()
        new_loc_keys = st.latest_location_batch()
```

를 아래로 교체:

```python
        new_ids = st.latest_batch_ids()
        new_loc_keys = st.recent_location_batches(cfg.site.new_location_window_days)
```

- [ ] **Step 4: Update `rebaseline`**

`app/pipeline.py` — `rebaseline` 안의 동일한 두 줄:

```python
        new_ids = st.latest_batch_ids()
        new_loc_keys = st.latest_location_batch()
```

를 아래로 교체:

```python
        new_ids = st.latest_batch_ids()
        new_loc_keys = st.recent_location_batches(cfg.site.new_location_window_days)
```

- [ ] **Step 5: Update `run_pipeline`**

`app/pipeline.py` — `run_pipeline` 안:

```python
        res = st.upsert(items, run_ts)
        new_loc_keys = st.register_locations(items, run_ts)
```

를 아래로 교체(등록은 유지, 표시는 최근 배치로 — current_ts 로 이번 배치 포함):

```python
        res = st.upsert(items, run_ts)
        st.register_locations(items, run_ts)  # 새 위치 기록(부작용)
        new_loc_keys = st.recent_location_batches(
            cfg.site.new_location_window_days, current_ts=run_ts)
```

(그 아래 `rows = st.active_listings(new_ids=new_ids, new_loc_keys=new_loc_keys, ...)` 는 그대로 두면 됨 — 이제 dict 를 받는다.)

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m unittest tests.test_core.TestPipelineRegenerate -v`
Expected: PASS (1 test)

- [ ] **Step 7: Run full suite (no regressions)**

Run: `python -m unittest tests.test_core -v`
Expected: PASS (전체 통과)

- [ ] **Step 8: Smoke test — 실제 엔트리포인트 import/서명 확인**

빈 로컬 DB 로 실행해 import·서명 오류가 없는지(예외 없이 종료) 확인:

Run: `python run.py --regenerate`
Expected: 예외 없이 종료. 로컬 DB 가 비어 있으면 "수집 결과 0건 — 기존 사이트 유지" 또는 "총 0건" 로그. (실제 데이터 반영은 VPS 에서.)

- [ ] **Step 9: Commit**

```bash
git add app/pipeline.py tests/test_core.py
git commit -m "pipeline: run/regenerate/rebaseline 새주소 표시를 최근 N일 배치로 전환

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 문서(README/note) — 새 동작 반영

**Files:**
- Modify: `app/generate.py` (`TEMPLATE` 의 `.note` 안내 문구 — 🆕 설명 갱신)
- Modify: `README.md` (해당 기능 설명이 있으면 최근 2일 그룹으로 갱신)

**Interfaces:**
- Consumes: 없음(문구/문서만).
- Produces: 사용자 안내 문구가 새 동작("최근 2일 수집분, 수집분별 그룹")과 일치.

- [ ] **Step 1: Update the in-page note**

`app/generate.py` — `TEMPLATE` 안 `.note` div 의 🆕 설명 부분:

```
<b>🆕 새 주소</b> = 이전엔 없던 위치에 새로 등장한 매물(가장 최근 수집분, 기본 켜짐)
```

를 아래로 교체:

```
<b>🆕 새 주소</b> = 이전엔 없던 위치에 새로 등장한 매물(최근 2일 수집분, 수집분별로 묶어 표시, 기본 켜짐)
```

- [ ] **Step 2: Update README if it documents the 🆕 filter**

Run: `grep -n "새 주소\|새주소\|latest_location_batch\|가장 최근 수집" README.md`

- 매칭이 있으면 해당 설명을 "최근 2일 수집분을 수집분별 헤더로 그룹 표시"로 갱신.
- 매칭이 없으면 이 스텝은 건너뛴다(README 에 관련 서술이 없으면 추가하지 않음 — YAGNI).

- [ ] **Step 3: Run full suite (문구 변경이 테스트를 깨지 않는지)**

Run: `python -m unittest tests.test_core -v`
Expected: PASS (전체 통과)

- [ ] **Step 4: Commit**

```bash
git add app/generate.py README.md
git commit -m "docs: 🆕 새 주소 안내 문구를 최근 2일 수집분 그룹으로 갱신

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 배포(수동, VPS)

코드 머지 후 VPS 반영(재수집 불필요 — 기존 DB 로 즉시 반영·배포):

```bash
cd /opt/naver-land && git pull
set -a; . ./.env.sh; set +a && python3 run.py --regenerate
```

성공 로그: `배포 완료 → ralrachang/naver-land@gh-pages`.
주의: `--regenerate` 는 배포 설정을 `.env.sh` 에서 읽으므로 위처럼 env 를 먼저 로드해야 푸시된다
(안 하면 "배포 비활성화" 로 조용히 스킵 — `naver-land-project` 메모리 참고).

---

## Self-Review 결과

- **Spec coverage:** store 배치 조회(Task 2) · 가짜 새주소 방지 회귀(Task 2 `test_rebaseline_backfill_excluded`) · 라벨/필드(Task 3) · 그룹 헤더 UI(Task 4) · pipeline 3경로(Task 5) · 설정(Task 1) · 안내 문구(Task 6) 모두 태스크로 커버.
- **Placeholder scan:** 모든 코드 스텝에 실제 코드 포함, 명령·기대출력 명시. 플레이스홀더 없음.
- **Type consistency:** `recent_location_batches(days, current_ts=None) -> dict` 는 Task 2 정의 → Task 5 에서 동일 시그니처로 호출. `active_listings(new_loc_keys: set|dict)` 는 dict 수용 → Task 5 가 dict 전달. `nlb`/`nlbt` 는 Task 3 생성 → Task 4 JS 가 소비 → Task 5 테스트가 검증. 이름 일치 확인.
