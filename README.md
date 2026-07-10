# 네이버 부동산 매매 매물 모니터 (토지·건물)

매일 정해진 시각에 네이버 부동산의 **매매(토지·건물)** 매물을 자동 수집하여,
**주소와 매매가만** 개인 웹사이트(GitHub Pages)에 올려주는 도구입니다.
신규 매물은 상단에 `NEW` 배지로 강조하고, 지난 매물도 누적 보관합니다.

- 기본 대상: **서울 전체 25개 구** (config로 특정 구/수도권 조정 가능)
- 사이트: 상단 **구(區) 필터** + 💎정밀단독/🎯단독매물/🆕새 주소/💰가격인하 토글 + "더 보기"
  페이지네이션, 각 매물에 네이버 매물 페이지 링크
- **💎 정밀단독(기본 켜짐)**: 최근 30일 광고 이력 전체(목록에서 빠진 광고 포함)에서 같은
  좌표 광고가 1개뿐인 매물. 면적으로 물건을 나누지 않음 — 같은 건물도 중개업소마다
  대지면적을 다르게 적는 게 실측으로 확인됨(연면적은 동일).
- **💰 가격 인하 감지(급매 신호)**: 재목격 시 저장된 가격과 비교해 내려갔으면
  이전가→현재가와 할인율(%)을 표시. 네이버 `priceChangeState=DECREASE` 신호도 반영.
  최근 인하된 매물은 올라온 지 오래됐어도 피드에 유지됩니다.
- 실행: 하루 2회(기본 09:00 / 16:00 KST), Hostinger VPS의 cron
- 데이터 소스: `new.land.naver.com` 내부 API (로그인 불필요)
- 스택: **Python 표준 라이브러리만** (서드파티 의존성 0). git 필요.

> ⚠️ 이 도구는 네이버 부동산의 **비공식 내부 API**를 사용합니다. 개인·비상업·저빈도
> 사용을 전제로 하며, 네이버 이용약관상 회색지대입니다. API 구조 변경 시 동작이
> 멈출 수 있습니다. 사용에 따른 책임은 사용자에게 있습니다.

---

## 동작 원리

```
run.py
  → 토큰   : GET https://new.land.naver.com/ 의 HTML에서 Bearer 토큰(JWT) 추출
  → 지역   : /api/regions/list 로 대상 구 → 동 목록 전개(캐시)
  → 크롤   : /api/articles (realEstateType=TJ:GM, tradeType=A1, order=dateDesc)
             최신순 조회 + 이미 본 매물 도달 시 조기중단(요청 최소화)
  → 저장   : SQLite(data/listings.db)에 upsert + 신규 판별 + 누적 보관
  → 생성   : site/index.html + site/data.json (주소+매매가, NEW 배지)
  → 배포   : site/ 를 GitHub Pages 저장소로 git push
```

## 로컬에서 빠르게 확인

```bash
python run.py               # 수집 → 사이트 생성 (배포는 config에서 켤 때만)
# 결과: site/index.html 을 브라우저로 열어 확인
python -m unittest discover tests -v   # 단위 테스트
```

## 설정 (`config.toml`)

- `[crawl]` : 매물종류(`real_estate_types`), 지연/재시도, 동별 최대 페이지 등
  - `early_stop` : `false`(기본, 권장) = 전체 스캔 → 신규 감지 + 삭제(더 이상 없는) 매물 정리 모두 정확.
    `true` = 신규 없는 페이지에서 조기중단(요청 최소화, 넓은 범위용). 단 스캔이 불완전해져
    삭제 판정을 생략합니다(누적만). 강남3구 같은 좁은 범위는 `false`가 맞습니다.
- `[[regions]]` : 대상 지역. 구(區) `cortarNo`를 넣으면 동까지 자동 전개
  - 강남구 `1168000000`, 서초구 `1165000000`, 송파구 `1171000000`
  - **수도권 확장**: 원하는 구를 추가하거나, 시도 코드(서울 `1100000000`,
    경기 `4100000000`, 인천 `2800000000`)를 넣으면 구→동까지 전개됩니다.
    (범위가 넓을수록 요청량·차단 위험이 커집니다. 점진 확장을 권장합니다.)
- `[deploy]` : `enabled=true` 로 켜고 `repo`(예: `user/site-repo`), `branch` 설정
- `[site]` : 제목/부제/`keep_days`
  - `keep_days = 0`(기본) = 삭제된 매물도 계속 **누적 보관**(목록에서 빼지 않음).
  - `keep_days = N`(>0) = 마지막으로 목격된 지 N일 지나면(=삭제 추정) 목록에서 제외.
    (전체 스캔 모드 `early_stop=false`일 때만 적용됩니다.)

매물종류 코드: 토지 `TJ`, 건물(통건물) `GM`, 아파트 `APT`, 오피스텔 `OPST`,
빌라 `VL`, 상가 `SG`, 사무실 `SMS` 등.

---

## Hostinger VPS 배포 가이드

### 1. 준비
```bash
sudo apt update && sudo apt install -y python3 git
python3 --version        # 3.11 이상이어야 함(tomllib 필요)
sudo timedatectl set-timezone Asia/Seoul   # KST로 맞추면 cron이 간단
```

### 2. 코드 배치
```bash
sudo mkdir -p /opt/naver-land && sudo chown $USER /opt/naver-land
# 이 프로젝트 파일을 /opt/naver-land 로 복사 (scp/git clone 등)
cd /opt/naver-land
```

### 3. GitHub Pages 저장소 준비
1. GitHub에 사이트용 저장소 생성 (예: `yourname/naver-land-site`)
2. **Settings → Pages** 에서 소스 브랜치를 `gh-pages`(또는 `docs/`)로 지정
3. **Personal Access Token** 발급 (Fine-grained, 해당 repo의 *Contents: Read and write*)
4. `config.toml` 의 `[deploy]` 설정:
   ```toml
   [deploy]
   enabled = true
   repo = "yourname/naver-land-site"
   branch = "gh-pages"
   ```
5. 토큰을 환경변수로:
   ```bash
   echo 'GH_TOKEN=github_pat_xxx' | sudo tee -a /etc/environment
   ```

### 4. 동작 확인 (푸시 없이 커밋까지만)
```bash
cd /opt/naver-land && python3 run.py --dry-run
```
정상이면 `site/index.html` 생성 + 로그에 `[DRY_RUN] 커밋 생성됨`.

### 5. cron 등록 (하루 2회)
```bash
crontab -e
```
`deploy/crontab.txt` 의 한 줄을 붙여넣기:
```
0 9,16 * * *  cd /opt/naver-land && python3 run.py >> logs/cron.log 2>&1
```
(VPS가 UTC면 `0 0,7 * * *`)

### 6. (선택) Docker
```bash
docker build -f deploy/Dockerfile -t naver-land .
docker run --rm -e GH_TOKEN=$GH_TOKEN -v $PWD/data:/opt/naver-land/data naver-land
```

---

## ⚠️ 데이터센터 IP 차단 대응 (중요)

Hostinger VPS는 **데이터센터 IP**라, 네이버가 봇으로 판단해 차단(401/403/429)할
가능성이 주거용 IP보다 높습니다. 이 도구는 다음으로 위험을 낮춥니다:

- 브라우저 유사 헤더 + 정상 토큰 사용
- 요청 간 지연(≥1초)+지터, 지수 백오프 재시도
- **최신순 조회 + 이미 본 매물 조기중단**으로 일일 요청량 최소화
- 하루 2회 저빈도 실행

그럼에도 차단되면 로그에 `차단 추정`이 남고 **사이트는 직전 정상본을 유지**합니다.
차단 시 대응:
1. 실행 빈도/범위를 줄인다(구 1~2개부터).
2. `config.toml` 의 `request_delay_sec` 를 2~3초로 늘린다.
3. 그래도 막히면 **주거용 IP에서 수집**(집 PC/NAS에서 크롤 → 생성물만 VPS/GH로)하거나
   신뢰할 수 있는 프록시를 고려한다.

## 주소 정밀도에 대해

네이버는 토지/건물 매물의 정확한 지번(번지)을 공개하지 않습니다. 따라서 주소는
**시·구·동 단위**로 표시됩니다(예: `서울특별시 강남구 삼성동`). 매매가는 등록된
광고가 기준입니다.

## 파일 구조

```
config.toml         설정
run.py              엔트리포인트
app/                config·client·regions·crawler·store·generate·deploy·pipeline
data/               regions.json(지역캐시), listings.db(SQLite)
site/               생성물(index.html, data.json) → GitHub Pages로 배포
logs/               실행 로그
deploy/             crontab 예시, Dockerfile
tests/              단위 테스트
```
