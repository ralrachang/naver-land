"""GitHub Pages 배포 — site/ 를 대상 저장소에 git push.

인증: 환경변수 GH_TOKEN (Personal Access Token, repo/contents write 권한).
저장소/브랜치/경로: config.toml [deploy] 또는 환경변수.
실패는 파이프라인을 중단시키지 않고 로그로 남긴다.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("naver_land.deploy")


def _run(cmd: list[str], cwd: str) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out.strip()


def _scrub(text: str, token: str) -> str:
    """로그 등에 토큰이 새지 않도록 마스킹."""
    return text.replace(token, "***") if token else text


def deploy(cfg, dry_run: bool | None = None) -> bool:
    d = cfg.deploy
    if dry_run is None:
        dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

    if not d.enabled:
        log.info("배포 비활성화(config deploy.enabled=false) — 건너뜀")
        return False
    if not d.repo:
        log.warning("deploy.repo 미설정 — 건너뜀")
        return False

    token = os.environ.get("GH_TOKEN", "")
    if not token and not dry_run:
        log.warning("GH_TOKEN 환경변수 없음 — 배포 건너뜀")
        return False

    src = cfg.site_dir
    if not (src / "index.html").exists():
        log.warning("site/index.html 없음 — 배포할 것이 없음")
        return False

    # 기본은 github.com. GH_REMOTE 로 오버라이드 가능(GH Enterprise/테스트용).
    remote = os.environ.get(
        "GH_REMOTE", f"https://x-access-token:{token}@github.com/{d.repo}.git")
    work = tempfile.mkdtemp(prefix="ghpages_")
    try:
        # 대상 브랜치를 얕은 클론. 실패(브랜치 없음/빈 저장소)면 깨끗한 디렉터리에서
        # 새 브랜치를 init 한다(더럽혀진 디렉터리에 재클론하지 않음).
        rc, _ = _run(["git", "clone", "--depth", "1", "--branch", d.branch,
                      remote, work], cwd=".")
        if rc != 0:
            log.info("브랜치 %s 클론 실패 → 새 브랜치 생성", d.branch)
            shutil.rmtree(work, ignore_errors=True)
            work = tempfile.mkdtemp(prefix="ghpages_")
            for cmd in (["git", "init", "-q", "-b", d.branch],
                        ["git", "remote", "add", "origin", remote]):
                _run(cmd, cwd=work)

        dest = Path(work) / (d.subdir if d.subdir not in (".", "") else "")
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("index.html", "data.json"):
            f = src / name
            if f.exists():
                shutil.copy2(f, dest / name)
        (Path(work) / ".nojekyll").touch()

        _run(["git", "config", "user.name", d.commit_name], cwd=work)
        _run(["git", "config", "user.email", d.commit_email], cwd=work)
        _run(["git", "add", "-A"], cwd=work)
        rc, _ = _run(["git", "diff", "--cached", "--quiet"], cwd=work)
        if rc == 0:
            log.info("변경 없음 — 배포 생략")
            return False

        _run(["git", "commit", "-q", "-m", "update listings"], cwd=work)
        if dry_run:
            log.info("[DRY_RUN] 커밋 생성됨(푸시 안 함)")
            return True
        rc, out = _run(["git", "push", "origin", d.branch], cwd=work)
        if rc != 0:
            log.error("git push 실패: %s", _scrub(out, token)[:300])
            return False
        log.info("배포 완료 → %s@%s", d.repo, d.branch)
        return True
    except Exception as e:  # 배포 실패가 전체를 죽이지 않도록
        log.error("배포 예외: %s", _scrub(str(e), token))
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)
