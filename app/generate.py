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
:root{--bg:#f7f7f8;--card:#fff;--fg:#1a1a1a;--muted:#6b7280;--line:#e5e7eb;--new:#e11d48;--tag:#eef2ff;--tagfg:#4338ca;--price:#0f766e;--link:#2563eb;--cut:#d97706;--ps:#7c3aed;--chip:#fff;--chipactive:#111}
@media (prefers-color-scheme:dark){:root{--bg:#0b0d10;--card:#15181d;--fg:#e8eaed;--muted:#9aa3af;--line:#242a31;--new:#fb7185;--tag:#1e2340;--tagfg:#a5b4fc;--price:#5eead4;--link:#7dd3fc;--cut:#fbbf24;--ps:#a78bfa;--chip:#15181d;--chipactive:#e8eaed}}
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
.fbtn.newloc{border-color:var(--link);color:var(--link);font-weight:600}
.fbtn.newloc.active{background:var(--link);color:#fff;border-color:var(--link)}
.fbtn.newloc.active span{color:#fff;opacity:.85}
.newloc-badge{background:var(--link);color:#fff;border-radius:6px;padding:1px 7px;font-size:.7rem;font-weight:700;margin-left:6px}
a.row.newloc{border-color:var(--link)}
.fbtn.ps{border-color:var(--ps);color:var(--ps);font-weight:600}
.fbtn.ps.active{background:var(--ps);color:#fff;border-color:var(--ps)}
.fbtn.ps.active span{color:#fff;opacity:.85}
.ps-chip{background:var(--ps);color:#fff;border-radius:6px;padding:1px 7px;font-size:.72rem;font-weight:700}
.fbtn.cut{border-color:var(--cut);color:var(--cut);font-weight:600}
.fbtn.cut.active{background:var(--cut);color:#fff;border-color:var(--cut)}
.fbtn.cut.active span{color:#fff;opacity:.85}
.cut-badge{background:var(--cut);color:#fff;border-radius:6px;padding:1px 7px;font-size:.7rem;font-weight:700;margin-left:6px}
a.row.cut{border-color:var(--cut)}
.oldprice{color:var(--muted);font-size:.78rem;text-decoration:line-through}
.pct{color:var(--cut);font-size:.78rem;font-weight:700}
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
.batch{margin:16px 4px 4px;color:var(--muted);font-size:.82rem;font-weight:700}
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
<div class="note">💡 <b>🆕 새 주소</b> = 이전엔 없던 위치에 새로 등장한 매물(가장 최근 수집분, 기본 켜짐) · <b>💎 정밀단독</b> = 최근 30일 광고 이력 전체(목록에서 빠진 광고 포함)에서 같은 위치 광고가 1개뿐 · <b>🎯 단독</b> = 현재 목록 기준 같은 위치 광고 1개 · <b>💰 가격인하</b> = 등록 후 가격이 내려간 매물(급매 신호). 네이버가 지번은 공개 안 해 주소는 동 단위이며, 매물을 눌러 상세를 확인하세요.</div>
<div class="filters" id="filters"></div>
<main id="list"></main>
<div class="more" id="more" style="display:none"><button type="button">더 보기</button></div>
<footer>네이버 부동산 매물 정보(개인용). 매매가는 등록 광고가 기준이며 실제 거래·정확도를 보장하지 않습니다.</footer>
<script id="data" type="application/json">@@DATA@@</script>
<script>
(function(){
  var L = JSON.parse(document.getElementById('data').textContent);
  var PAGE = 300, curGu = '전체', psOnly = false, soloOnly = false, newLocOnly = true, cutOnly = false, shown = PAGE;
  function esc(s){ s = s==null?'':(''+s); return s.replace(/[&<>"]/g,function(m){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m];}); }
  var psCnt = 0, soloCnt = 0, newLocCnt = 0, cutCnt = 0, gusSet = {};
  for (var i=0;i<L.length;i++){ gusSet[L[i].g]=1; if(L[i].ps) psCnt++; if(L[i].s===1) soloCnt++; if(L[i].nl) newLocCnt++; if(L[i].pc) cutCnt++; }
  var gus = Object.keys(gusSet).sort();
  var filtersEl = document.getElementById('filters');
  var listEl = document.getElementById('list');
  var moreEl = document.getElementById('more');
  var moreBtn = moreEl.querySelector('button');
  function passToggles(x){ return (!psOnly || x.ps) && (!soloOnly || x.s===1) && (!newLocOnly || x.nl) && (!cutOnly || x.pc); }
  function btn(g,n){ return '<button type="button" class="fbtn'+(g===curGu?' active':'')+'" data-gu="'+esc(g)+'">'+esc(g)+' <span>'+n+'</span></button>'; }
  function renderFilters(){
    var c = {}, total = 0;
    for (var i=0;i<L.length;i++){ if(passToggles(L[i])){ c[L[i].g]=(c[L[i].g]||0)+1; total++; } }
    var h = '<button type="button" class="fbtn newloc'+(newLocOnly?' active':'')+'" data-newloc="1">🆕 새 주소만 <span>'+newLocCnt+'</span></button>';
    h += '<button type="button" class="fbtn ps'+(psOnly?' active':'')+'" data-ps="1">💎 정밀단독만 <span>'+psCnt+'</span></button>';
    h += '<button type="button" class="fbtn solo'+(soloOnly?' active':'')+'" data-solo="1">🎯 단독매물만 <span>'+soloCnt+'</span></button>';
    h += '<button type="button" class="fbtn cut'+(cutOnly?' active':'')+'" data-cut="1">💰 가격인하만 <span>'+cutCnt+'</span></button>';
    h += btn('전체', total);
    for (var k=0;k<gus.length;k++){ h += btn(gus[k], c[gus[k]]||0); }
    filtersEl.innerHTML = h;
    var bs = filtersEl.querySelectorAll('button');
    for (var j=0;j<bs.length;j++){ bs[j].onclick = function(){
      if (this.getAttribute('data-ps')){ psOnly = !psOnly; }
      else if (this.getAttribute('data-solo')){ soloOnly = !soloOnly; }
      else if (this.getAttribute('data-newloc')){ newLocOnly = !newLocOnly; }
      else if (this.getAttribute('data-cut')){ cutOnly = !cutOnly; }
      else { curGu = this.getAttribute('data-gu'); }
      shown = PAGE; render(); window.scrollTo(0,0);
    }; }
  }
  function filtered(){
    var f = [];
    for (var i=0;i<L.length;i++){ var x=L[i]; if((curGu==='전체'||x.g===curGu) && passToggles(x)) f.push(x); }
    return f;
  }
  function rowHtml(x){
    var cls = x.nl?' newloc':(x.n?' new':(x.pc?' cut':''));
    var badge = x.nl?'<span class="badge newloc-badge">🆕 새주소</span>':(x.n?'<span class="badge">NEW</span>':'');
    if (x.pc) badge += '<span class="badge cut-badge">💰 인하</span>';
    var priceLine = '<div class="price">'+esc(x.p)+'</div>';
    if (x.pc && x.pp) priceLine = '<div><span class="oldprice">'+esc(x.pp)+'</span> <span class="pct">-'+x.pd+'%</span></div>' + priceLine;
    return '<a class="row'+cls+'" href="'+esc(x.u)+'" target="_blank" rel="noopener">'
      + '<div class="left"><div class="addr">'+esc(x.a)+badge+'</div>'
      + '<div class="meta"><span class="tag">'+esc(x.t)+'</span>'+(x.ps?'<span class="ps-chip">💎 정밀단독</span>':(x.s===1?'<span class="solo-chip">단독</span>':(x.s>1?'<span class="cnt">광고 '+x.s+'개</span>':'')))+'<span>확인 '+esc(x.c)+'</span></div>'
      + (x.f?'<div class="feature">'+esc(x.f)+'</div>':'')
      + '</div><div class="right">'+priceLine+'<div class="link">네이버에서 보기 ›</div></div></a>';
  }
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
  moreBtn.onclick = function(){ shown += PAGE; render(); };
  render();
})();
</script>
</body>
</html>
"""


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


def _fmt_confirm(ymd: str) -> str:
    if ymd and len(ymd) == 8:
        return f"{ymd[:4]}.{ymd[4:6]}.{ymd[6:]}"
    return ymd or "-"


def _build_listings(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        prev, cur = r.get("prev_price_manwon"), r.get("price_manwon")
        own_cut = prev and cur and cur < prev
        out.append({
            "a": r.get("address") or "-",
            "p": r.get("price_text") or "가격문의",
            "t": r.get("re_type") or "",
            "c": _fmt_confirm(r.get("confirm_ymd") or ""),
            "n": bool(r.get("is_new")),
            "nl": bool(r.get("is_new_location")),  # 이전엔 없던 위치에 새로 등장
            "nlb": _batch_label(r.get("new_location_batch")),  # 수집분 라벨 "7.13 오전"
            "nlbt": r.get("new_location_batch") or "",         # 그룹 정렬키(원본 시각)
            "u": ARTICLE_URL.format(no=r.get("article_no")),
            "g": r.get("gu") or "기타",
            "s": r.get("loc_count"),  # 같은 위경도(=같은 건물) 광고 수. 1=진짜 단독
            "f": (r.get("feature_desc") or "").strip(),
            "ps": 1 if r.get("is_precise_solo") else 0,  # 💎정밀단독(이력전체+면적+마스킹 제외)
            "pc": 1 if r.get("is_price_cut") else 0,  # 가격인하(급매 신호)
            "pp": (r.get("prev_price_text") or "") if own_cut else "",  # 인하 전 가격
            "pd": round((prev - cur) / prev * 100) if own_cut else 0,   # 할인율 %
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
