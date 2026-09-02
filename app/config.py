"""설정 로딩 (config.toml, 표준 tomllib 사용)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"


@dataclass
class CrawlCfg:
    trade_type: str = "A1"
    real_estate_types: list[str] = field(default_factory=lambda: ["TJ", "GM"])
    order: str = "dateDesc"
    request_delay_sec: float = 1.0
    request_jitter_sec: float = 0.7
    max_pages_per_region: int = 75
    max_retries: int = 3
    timeout_sec: int = 20
    early_stop: bool = False


@dataclass
class SiteCfg:
    title: str = "네이버 부동산 매매 매물"
    subtitle: str = ""
    timezone: str = "Asia/Seoul"
    keep_days: int = 30
    solo_window_days: int = 30       # 💎정밀단독: 최근 N일 목격 이력 전체로 좌표별 광고 수 판정
    new_location_window_days: int = 2  # 🆕 새 주소: 최근 N일 정상 수집 배치의 새 위치를 그룹으로 표시


@dataclass
class DeployCfg:
    enabled: bool = False
    repo: str = ""
    branch: str = "gh-pages"
    subdir: str = "."
    commit_name: str = "naver-land-bot"
    commit_email: str = "bot@example.com"


@dataclass
class WatchCfg:
    """🔔 새 주소 실시간 감시기(watch.py) 설정. 요청 간격은 [crawl] 값을 재사용."""
    max_pages: int = 5            # 구당 최대 페이지(이미 본 매물이 있는 페이지에서 중단)
    gap_reset_min: int = 60       # 직전 스캔이 이보다 오래됐으면 워밍업(기록만, 알림 없음)
    alert_ttl_min: int = 180      # 이 시간 넘게 못 보낸 알림은 폐기
    max_alerts_per_tick: int = 15  # 한 번에 내보낼 알림 상한(홍수 방지)
    block_pause_min: int = 30     # 403/429 차단 감지 시 휴식


@dataclass
class Config:
    crawl: CrawlCfg
    site: SiteCfg
    deploy: DeployCfg
    regions: list[dict]
    root: Path = ROOT
    watch: WatchCfg = field(default_factory=WatchCfg)

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def site_dir(self) -> Path:
        return self.root / "site"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "listings.db"

    @property
    def regions_cache(self) -> Path:
        return self.data_dir / "regions.json"

    @property
    def watch_db_path(self) -> Path:
        """감시기 자체 DB — 홈페이지 DB(listings.db)와 분리."""
        return self.data_dir / "watch.db"


def load(path: str | os.PathLike | None = None) -> Config:
    p = Path(path) if path else DEFAULT_CONFIG
    with open(p, "rb") as f:
        raw = tomllib.load(f)

    crawl = CrawlCfg(**{k: v for k, v in raw.get("crawl", {}).items()
                        if k in CrawlCfg.__dataclass_fields__})
    site = SiteCfg(**{k: v for k, v in raw.get("site", {}).items()
                      if k in SiteCfg.__dataclass_fields__})
    deploy = DeployCfg(**{k: v for k, v in raw.get("deploy", {}).items()
                          if k in DeployCfg.__dataclass_fields__})
    watch = WatchCfg(**{k: v for k, v in raw.get("watch", {}).items()
                        if k in WatchCfg.__dataclass_fields__})

    # 환경변수 오버라이드(민감정보/운영 토글)
    if os.environ.get("DEPLOY_ENABLED"):
        deploy.enabled = os.environ["DEPLOY_ENABLED"].lower() in ("1", "true", "yes")
    if os.environ.get("DEPLOY_REPO"):
        deploy.repo = os.environ["DEPLOY_REPO"]

    regions = raw.get("regions", [])
    cfg = Config(crawl=crawl, site=site, deploy=deploy, regions=regions, watch=watch)
    for d in (cfg.data_dir, cfg.site_dir, cfg.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
