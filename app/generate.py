"""정적 사이트 생성 — 주소 + 매매가 + 매물 직접 링크. 외부 의존 없음(self-contained).

주의: 네이버는 토지/건물 매물의 지번(번지)을 공개하지 않습니다(isLocationShow=False).
따라서 주소는 동 단위로 표기하고, 각 매물에 네이버 매물 페이지 링크
(https://m.land.naver.com/article/info/{articleNo})를 붙여 클릭 시 지도 위치·사진·
중개업소 연락처 등 상세를 볼 수 있게 한다.
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("naver_land.generate")

KST = timezone(timedelta(hours=9))
ARTICLE_URL = "https://m.land.naver.com/article/info/{no}"

PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title}</title>
<style>
:root{{--bg:#f7f7f8;--card:#fff;--fg:#1a1a1a;--muted:#6b7280;--line:#e5e7eb;--new:#e11d48;--tag:#eef2ff;--tagfg:#4338ca;--price:#0f766e;--link:#2563eb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0b0d10;--card:#15181d;--fg:#e8eaed;--muted:#9aa3af;--line:#242a31;--new:#fb7185;--tag:#1e2340;--tagfg:#a5b4fc;--price:#5eead4;--link:#7dd3fc}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Malgun Gothic","Apple SD Gothic Neo",sans-serif;line-height:1.5}}
header{{padding:20px 16px 12px;max-width:860px;margin:0 auto}}
h1{{font-size:1.3rem;margin:0 0 4px}}
.sub{{color:var(--muted);font-size:.85rem}}
.stats{{max-width:860px;margin:8px auto 0;padding:0 16px;display:flex;gap:14px;flex-wrap:wrap;font-size:.85rem;color:var(--muted)}}
.stats b{{color:var(--fg)}}
.note{{max-width:860px;margin:6px auto 0;padding:0 16px;font-size:.75rem;color:var(--muted)}}
main{{max-width:860px;margin:12px auto 40px;padding:0 12px}}
a.row{{text-decoration:none;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:8px 0;display:flex;justify-content:space-between;align-items:flex-start;gap:12px;transition:border-color .15s,transform .05s}}
a.row:hover{{border-color:var(--link)}}
a.row:active{{transform:scale(.995)}}
a.row.new{{border-color:var(--new)}}
.left{{min-width:0}}
.addr{{font-weight:600;font-size:1rem;word-break:keep-all}}
.meta{{color:var(--muted);font-size:.8rem;margin-top:3px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.feature{{color:var(--muted);font-size:.78rem;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:52ch}}
.tag{{background:var(--tag);color:var(--tagfg);border-radius:6px;padding:1px 7px;font-size:.72rem;font-weight:600}}
.badge{{background:var(--new);color:#fff;border-radius:6px;padding:1px 7px;font-size:.7rem;font-weight:700;margin-left:6px}}
.right{{text-align:right;white-space:nowrap;flex-shrink:0}}
.price{{color:var(--price);font-weight:700;font-size:1.05rem}}
.link{{color:var(--link);font-size:.75rem;margin-top:4px}}
.empty{{text-align:center;color:var(--muted);padding:48px 0}}
footer{{max-width:860px;margin:0 auto;padding:16px;color:var(--muted);font-size:.75rem;text-align:center}}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="sub">{subtitle}</div>
</header>
<div class="stats">
  <span>마지막 수집 <b>{updated}</b> KST</span>
  <span>총 <b>{total}</b>건</span>
  <span>이번 신규 <b style="color:var(--new)">{new_count}</b>건</span>
</div>
<div class="note">💡 네이버가 지번(번지)은 공개하지 않아 주소는 동 단위입니다. 각 매물을 눌러 네이버 페이지에서 지도 위치·사진·중개업소를 확인하세요.</div>
<main>
{rows}
</main>
<footer>
  네이버 부동산 매물 정보(개인용). 매매가는 등록 광고가 기준이며 실제 거래·정확도를 보장하지 않습니다.
</footer>
</body>
</html>
"""

ROW = """<a class="row{new_cls}" href="{url}" target="_blank" rel="noopener">
  <div class="left">
    <div class="addr">{address}{badge}</div>
    <div class="meta"><span class="tag">{re_type}</span>{extra}<span>확인 {confirm}</span></div>
    {feature}
  </div>
  <div class="right">
    <div class="price">{price}</div>
    <div class="link">네이버에서 보기 ›</div>
  </div>
</a>"""


def _fmt_confirm(ymd: str) -> str:
    if ymd and len(ymd) == 8:
        return f"{ymd[:4]}.{ymd[4:6]}.{ymd[6:]}"
    return ymd or "-"


def _render_rows(rows: list[dict]) -> str:
    if not rows:
        return '<div class="empty">표시할 매물이 없습니다.</div>'
    out = []
    for r in rows:
        is_new = r.get("is_new")
        ano = str(r.get("article_no") or "")
        extra = ""
        area = r.get("area")
        if area:
            try:
                extra = f'<span>{float(area):,.0f}㎡</span>'
            except (ValueError, TypeError):
                extra = ""
        feat = (r.get("feature_desc") or "").strip()
        feature = f'<div class="feature">{html.escape(feat)}</div>' if feat else ""
        out.append(ROW.format(
            new_cls=" new" if is_new else "",
            url=ARTICLE_URL.format(no=html.escape(ano)),
            address=html.escape(r.get("address") or "-"),
            badge='<span class="badge">NEW</span>' if is_new else "",
            re_type=html.escape(r.get("re_type") or ""),
            extra=extra,
            confirm=html.escape(_fmt_confirm(r.get("confirm_ymd") or "")),
            feature=feature,
            price=html.escape(r.get("price_text") or "가격문의"),
        ))
    return "\n".join(out)


def generate(cfg, rows: list[dict], new_count: int, run_dt: datetime | None = None) -> bool:
    """site/index.html + site/data.json 생성.

    AC11: rows 가 비었고 기존 site/index.html 이 있으면 덮어쓰지 않는다.
    반환: 실제로 생성했으면 True.
    """
    index_path = cfg.site_dir / "index.html"
    if not rows and index_path.exists():
        log.warning("수집 결과 0건 — 기존 사이트 유지(덮어쓰기 안 함)")
        return False

    now = run_dt or datetime.now(KST)
    updated = now.astimezone(KST).strftime("%Y-%m-%d %H:%M")

    page = PAGE.format(
        title=html.escape(cfg.site.title),
        subtitle=html.escape(cfg.site.subtitle),
        updated=updated,
        total=len(rows),
        new_count=new_count,
        rows=_render_rows(rows),
    )
    index_path.write_text(page, encoding="utf-8")

    data = {
        "updated": updated,
        "total": len(rows),
        "new_count": new_count,
        "listings": [
            {"address": r.get("address"), "price": r.get("price_text"),
             "price_manwon": r.get("price_manwon"), "type": r.get("re_type"),
             "confirm_ymd": r.get("confirm_ymd"), "is_new": bool(r.get("is_new")),
             "url": ARTICLE_URL.format(no=r.get("article_no"))}
            for r in rows
        ],
    }
    (cfg.site_dir / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("사이트 생성: %d건 (신규 %d) → %s", len(rows), new_count, index_path)
    return True
