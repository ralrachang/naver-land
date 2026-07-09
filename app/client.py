"""네이버 부동산 내부 API HTTP 클라이언트.

검증된 동작(Phase 0):
- GET https://new.land.naver.com/ 로 쿠키 세팅 + HTML 안의 '"token":{"token":"<JWT>"}' 추출
- 그 JWT 를 `Authorization: Bearer <token>` 로 /api/regions/list, /api/articles 호출
- JWT payload = {"id":"REALESTATE","iat":...,"exp":...}, exp ≈ iat + 3시간
"""
from __future__ import annotations

import base64
import json
import logging
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar

log = logging.getLogger("naver_land.client")

BASE = "https://new.land.naver.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TOKEN_RE = re.compile(r'"token"\s*:\s*\{\s*"token"\s*:\s*"([^"]+)"')


class NaverLandError(RuntimeError):
    pass


class BlockedError(NaverLandError):
    """차단/인증 실패로 추정(401/403/429/빈응답)."""


class Client:
    def __init__(self, delay=1.0, jitter=0.7, retries=3, timeout=20):
        self.delay = delay
        self.jitter = jitter
        self.retries = retries
        self.timeout = timeout
        self._cj = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cj))
        self._token: str | None = None
        self._token_exp: int = 0

    # ---- low level -------------------------------------------------------
    def _sleep(self):
        time.sleep(self.delay + random.uniform(0, self.jitter))

    def _raw_get(self, url: str, headers: dict) -> tuple[int, str]:
        h = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9",
             "Accept": "application/json, text/plain, */*"}
        h.update(headers or {})
        req = urllib.request.Request(url, headers=h)
        with self._opener.open(req, timeout=self.timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")

    # ---- token -----------------------------------------------------------
    @staticmethod
    def _jwt_exp(token: str) -> int:
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
            return int(data.get("exp", 0))
        except Exception:
            return 0

    def get_token(self, force: bool = False) -> str:
        """토큰을 획득/캐시. 만료 60초 전이면 재획득."""
        now = int(time.time())
        if not force and self._token and now < self._token_exp - 60:
            return self._token
        status, html = self._raw_get(BASE + "/", {"Accept": "text/html,*/*"})
        m = TOKEN_RE.search(html)
        if not m:
            raise BlockedError(
                "홈페이지 HTML 에서 토큰을 찾지 못함 — 페이지 구조 변경 또는 차단 가능성")
        self._token = m.group(1)
        self._token_exp = self._jwt_exp(self._token) or (now + 3000)
        log.info("토큰 획득 (exp=%s, %ds 남음)", self._token_exp, self._token_exp - now)
        return self._token

    def _auth_headers(self) -> dict:
        return {"Authorization": "Bearer " + self.get_token(),
                "Referer": BASE + "/"}

    # ---- API with retry/backoff -----------------------------------------
    def get_json(self, path: str, params: dict | None = None) -> dict:
        qs = ("?" + urllib.parse.urlencode(params, safe=":")) if params else ""
        url = BASE + path + qs
        last_err: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                # _opener 에 기본 HTTPErrorProcessor 가 있어 비2xx 는 HTTPError 로 예외 발생.
                # 따라서 여기 도달하면 status==200.
                _, body = self._raw_get(url, self._auth_headers())
                return json.loads(body)
            except urllib.error.HTTPError as e:
                code = e.code
                text = e.read().decode("utf-8", "replace")[:120]
                if code == 401:
                    # 인증 실패 = 차단/토큰 문제. 남은 시도 있으면 토큰 재획득.
                    last_err = BlockedError(f"401 unauthorized: {text}")
                    if attempt < self.retries:
                        log.warning("401 — 토큰 재획득 후 재시도 (%d/%d)", attempt, self.retries)
                        self.get_token(force=True)
                elif code in (403, 429):
                    raise BlockedError(f"{code} 차단 추정: {text}") from e
                else:
                    last_err = NaverLandError(f"HTTP {code}: {text}")
            except urllib.error.URLError as e:
                last_err = NaverLandError(f"네트워크 오류: {e}")
            # 지수 백오프
            backoff = self.delay * (2 ** (attempt - 1)) + random.uniform(0, self.jitter)
            time.sleep(backoff)
        raise last_err or NaverLandError("알 수 없는 오류")

    # ---- domain helpers --------------------------------------------------
    def regions(self, cortar_no: str) -> list[dict]:
        data = self.get_json("/api/regions/list", {"cortarNo": cortar_no})
        self._sleep()  # 지역 열거도 정중하게(수도권 재구축 시 버스트 방지)
        return data.get("regionList", [])

    def articles(self, cortar_no: str, real_estate_types: list[str],
                 trade_type: str, order: str, page: int) -> dict:
        params = {
            "cortarNo": cortar_no,
            "realEstateType": ":".join(real_estate_types),
            "tradeType": trade_type,
            "order": order,
            "page": page,
        }
        result = self.get_json("/api/articles", params)
        self._sleep()  # 정중한 크롤링
        return result
