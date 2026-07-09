"""정적 사이트 생성 — 구 필터 + 페이지네이션 클라이언트 앱. 외부 의존 없음.

데이터를 페이지에 임베드하고, 상단 구(區) 버튼으로 필터링하며 "더 보기"로 조금씩
렌더링한다(서울 전체 수만 건도 가볍게 표시). 각 매물은 네이버 매물 페이지로 링크.

주의: 네이버는 토지/건물 지번(번지)을 공개하지 않아(isLocationShow=False) 주소는 동
단위이며, 링크로 상세(지도·사진·중개업소)를 확인한다.
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("naver_land.generate")

KST = timezone(timedelta(hours=9))
ARTICLE_URL = "https://m.land.naver.com/article/info/{no}"

TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>@@TITLE@@</title>
<style>
:root{--bg:#f7f7f8;--card:#fff;--fg:#1a1a1a;--muted:#6b7280;--line:#e5e7eb;--new:#e11d48;--tag:#eef2ff;--tagfg:#4338ca;--price:#0f766e;--link:#2563eb;--chip:#fff;--chipactive:#111}
@media (prefers-color-scheme:dark){:root{--bg:#0b0d10;--card:#15181d;--fg:#e8eaed;--muted:#9aa3af;--line:#242a31;--new:#fb7185;--tag:#1e2340;--tagfg:#a5b4fc;--price:#5eead4;--link:#7dd3fc;--chip:#15181d;--chipactive:#e8eaed}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Malgun Gothic","Apple SD Gothic Neo",sans-serif;line-height:1.5}
header{padding:18px 16px 8px;max-width:900px;margin:0 auto}
h1{font-size:1.3rem;margin:0 0 4px}
.sub{color:var(--muted);font-size:.85rem}
.stats{max-width:900px;margin:8px auto 0;padding:0 16px;display:flex;gap:14px;flex-wrap:wrap;font-size:.85rem;color:var(--muted)}
.stats b{color:var(--fg)}
.note{max-width:900px;margin:6px auto 0;padding:0 16px;font-size:.75rem;color:var(--muted)}
.filters{position:sticky;top:0;background:var(--bg);z-index:5;max-width:900px;margin:10px auto 0;padding:8px 12px;display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.fbtn{cursor:pointer;border:1px solid var(--line);background:var(--chip);color:var(--fg);border-radius:999px;padding:5px 11px;font-size:.82rem;font-family:inherit}
.fbtn span{color:var(--muted);font-size:.72rem;margin-left:3px}
.fbtn.active{background:var(--chipactive);color:var(--bg);border-color:var(--chipactive)}
.fbtn.active span{color:var(--bg);opacity:.7}
.fbtn.solo{border-color:var(--price);color:var(--price);font-weight:600}
.fbtn.solo.active{background:var(--price);color:#fff;border-color:var(--price)}
.fbtn.solo.active span{color:#fff;opacity:.85}
.solo-chip{background:var(--price);color:#fff;border-radius:6px;padding:1px 7px;font-size:.72rem;font-weight:700}
.cnt{color:var(--muted);font-size:.75rem}
main{max-width:900px;margin:8px auto 20px;padding:0 12px}
a.row{text-decoration:none;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:8px 0;display:flex;justify-content:space-between;align-items:flex-start;gap:12px;transition:border-color .15s}
a.row:hover{border-color:var(--link)}
a.row.new{border-color:var(--new)}
.left{min-width:0}
.addr{font-weight:600;font-size:1rem;word-break:keep-all}
.meta{color:var(--muted);font-size:.8rem;margin-top:3px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.feature{color:var(--muted);font-size:.78rem;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:56ch}
.tag{background:var(--tag);color:var(--tagfg);border-radius:6px;padding:1px 7px;font-size:.72rem;font-weight:600}
.badge{background:var(--new);color:#fff;border-radius:6px;padding:1px 7px;font-size:.7rem;font-weight:700;margin-left:6px}
.right{text-align:right;white-space:nowrap;flex-shrink:0}
.price{color:var(--price);font-weight:700;font-size:1.05rem}
.link{color:var(--link);font-size:.75rem;margin-top:4px}
.empty{text-align:center;color:var(--muted);padding:48px 0}
.more{max-width:900px;margin:0 auto 40px;padding:0 12px;text-align:center}
.more button{cursor:pointer;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:10px;padding:10px 20px;font-size:.9rem;font-family:inherit}
footer{max-width:900px;margin:0 auto;padding:16px;color:var(--muted);font-size:.75rem;text-align:center}
</style>
</head>
<body>
<header>
  <h1>@@TITLE@@</h1>
  <div class="sub">@@SUBTITLE@@</div>
</header>
<div class="stats">
  <span>마지막 수집 <b>@@UPDATED@@</b> KST</span>
  <span>표시 <b id="curcount">@@TOTAL@@</b>건 / 전체 @@TOTAL@@건</span>
  <span>이번 신규 <b style="color:var(--new)">@@NEWCOUNT@@</b>건</span>
</div>
<div class="note">💡 기본은 <b>단독 매물(광고 1개)</b>만 표시합니다 — 상단 🎯 버튼을 끄면 전체가 보입니다. 네이버가 지번은 공개하지 않아 주소는 동 단위이며, 매물을 눌러 상세(지도·사진·중개업소)를 확인하세요.</div>
<div class="filters" id="filters"></div>
<main id="list"></main>
<div class="more" id="more" style="display:none"><button type="button">더 보기</button></div>
<footer>네이버 부동산 매물 정보(개인용). 매매가는 등록 광고가 기준이며 실제 거래·정확도를 보장하지 않습니다.</footer>
<script id="data" type="application/json">@@DATA@@</script>
<script>
(function(){
  var L = JSON.parse(document.getElementById('data').textContent);
  var PAGE = 300, curGu = '전체', soloOnly = true, shown = PAGE;
  function esc(s){ s = s==null?'':(''+s); return s.replace(/[&<>"]/g,function(m){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m];}); }
  var countsAll = {}, countsSolo = {}, soloCnt = 0;
  for (var i=0;i<L.length;i++){ var g0=L[i].g; countsAll[g0]=(countsAll[g0]||0)+1; if(L[i].s===1){ countsSolo[g0]=(countsSolo[g0]||0)+1; soloCnt++; } }
  var gus = Object.keys(countsAll).sort();
  var filtersEl = document.getElementById('filters');
  var listEl = document.getElementById('list');
  var moreEl = document.getElementById('more');
  var moreBtn = moreEl.querySelector('button');
  function btn(g,n){ return '<button type="button" class="fbtn'+(g===curGu?' active':'')+'" data-gu="'+esc(g)+'">'+esc(g)+' <span>'+n+'</span></button>'; }
  function renderFilters(){
    var cmap = soloOnly ? countsSolo : countsAll;
    var h = '<button type="button" class="fbtn solo'+(soloOnly?' active':'')+'" data-solo="1">🎯 단독매물만 <span>'+soloCnt+'</span></button>';
    h += btn('전체', soloOnly ? soloCnt : L.length);
    for (var i=0;i<gus.length;i++){ h += btn(gus[i], cmap[gus[i]]||0); }
    filtersEl.innerHTML = h;
    var bs = filtersEl.querySelectorAll('button');
    for (var j=0;j<bs.length;j++){ bs[j].onclick = function(){
      if (this.getAttribute('data-solo')){ soloOnly = !soloOnly; }
      else { curGu = this.getAttribute('data-gu'); }
      shown = PAGE; render(); window.scrollTo(0,0);
    }; }
  }
  function filtered(){
    var f = curGu==='전체' ? L : L.filter(function(x){ return x.g===curGu; });
    if (soloOnly) f = f.filter(function(x){ return x.s===1; });
    return f;
  }
  function rowHtml(x){
    return '<a class="row'+(x.n?' new':'')+'" href="'+esc(x.u)+'" target="_blank" rel="noopener">'
      + '<div class="left"><div class="addr">'+esc(x.a)+(x.n?'<span class="badge">NEW</span>':'')+'</div>'
      + '<div class="meta"><span class="tag">'+esc(x.t)+'</span>'+(x.s===1?'<span class="solo-chip">단독</span>':(x.s>1?'<span class="cnt">광고 '+x.s+'개</span>':''))+'<span>확인 '+esc(x.c)+'</span></div>'
      + (x.f?'<div class="feature">'+esc(x.f)+'</div>':'')
      + '</div><div class="right"><div class="price">'+esc(x.p)+'</div><div class="link">네이버에서 보기 ›</div></div></a>';
  }
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
  moreBtn.onclick = function(){ shown += PAGE; render(); };
  render();
})();
</script>
</body>
</html>
"""


def _fmt_confirm(ymd: str) -> str:
    if ymd and len(ymd) == 8:
        return f"{ymd[:4]}.{ymd[4:6]}.{ymd[6:]}"
    return ymd or "-"


def _build_listings(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({
            "a": r.get("address") or "-",
            "p": r.get("price_text") or "가격문의",
            "t": r.get("re_type") or "",
            "c": _fmt_confirm(r.get("confirm_ymd") or ""),
            "n": bool(r.get("is_new")),
            "u": ARTICLE_URL.format(no=r.get("article_no")),
            "g": r.get("gu") or "기타",
            "s": r.get("same_addr_cnt"),  # 같은 주소 광고 수(1=단독)
            "f": (r.get("feature_desc") or "").strip(),
        })
    return out


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
    listings = _build_listings(rows)
    # <script> 안전 임베드: JSON 내부의 '<' 를 < 로(=</script> 방지). JSON.parse 시 복원됨.
    data_embed = json.dumps(listings, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")

    page = (TEMPLATE
            .replace("@@TITLE@@", html.escape(cfg.site.title))
            .replace("@@SUBTITLE@@", html.escape(cfg.site.subtitle))
            .replace("@@UPDATED@@", updated)
            .replace("@@NEWCOUNT@@", str(new_count))
            .replace("@@TOTAL@@", str(len(rows)))
            .replace("@@DATA@@", data_embed))
    index_path.write_text(page, encoding="utf-8")

    data = {"updated": updated, "total": len(rows), "new_count": new_count,
            "listings": listings}
    (cfg.site_dir / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("사이트 생성: %d건 (신규 %d) → %s", len(rows), new_count, index_path)
    return True
