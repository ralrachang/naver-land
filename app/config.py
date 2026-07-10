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
    max_pages_per_region: int = 25
    max_retries: int = 3
    timeout_sec: int = 20
    early_stop: bool = False


@dataclass
class SiteCfg:
    title: str = "네이버 부동산 매매 매물"
    subtitle: str = ""
    timezone: str = "Asia/Seoul"
    keep_days: int = 30
    solo_window_days: int = 30       # 💎정밀단독: 최근 N일 목격 이력 전체로 광고 수 판정
    mega_coord_threshold: int = 10   # 한 좌표에 광고 N개 이상 = 위치 마스킹 의심 → 판정 제외


@dataclass
class DeployCfg:
    enabled: bool = False
    repo: str = ""
    branch: str = "gh-pages"
    subdir: str = "."
    commit_name: str = "naver-land-bot"
    commit_email: str = "bot@example.com"


@dataclass
class Config:
    crawl: CrawlCfg
    site: SiteCfg
    deploy: DeployCfg
    regions: list[dict]
    root: Path = ROOT

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

    # 환경변수 오버라이드(민감정보/운영 토글)
    if os.environ.get("DEPLOY_ENABLED"):
        deploy.enabled = os.environ["DEPLOY_ENABLED"].lower() in ("1", "true", "yes")
    if os.environ.get("DEPLOY_REPO"):
        deploy.repo = os.environ["DEPLOY_REPO"]

    regions = raw.get("regions", [])
    cfg = Config(crawl=crawl, site=site, deploy=deploy, regions=regions)
    for d in (cfg.data_dir, cfg.site_dir, cfg.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
