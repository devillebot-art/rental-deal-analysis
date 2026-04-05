#!/usr/bin/env python3
"""
Rental Deal Analyzer — HTML Builder
Reads data/properties.json and generates index.html.
"""
import os, json, base64
from datetime import datetime, timezone
from html import escape

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
REPO   = "devillebot-art/rental-deal-analysis"
BRANCH = "main"
DATA_PATH = "data/properties.json"
HTML_PATH = "index.html"
DEFAULT_DP = 150_000

HEADERS = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

# ─── GITHUB ───────────────────────────────────────────────────────────────────

def github(method, path, data=None):
    import urllib.request
    url = f"https://api.github.com/{path.lstrip('/')}"
    body = json.dumps(data).encode() if data else None
    req  = urllib.request.Request(url, data=body, method=method, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def get_file(path):
    d = github("GET", f"repos/{REPO}/contents/{path}?ref={BRANCH}")
    if not d or "content" not in d:
        return None
    return base64.b64decode(d["content"]).decode("utf-8")

def save_file(path, content, msg):
    blob = base64.b64encode(content.encode()).decode()
    existing = github("GET", f"repos/{REPO}/contents/{path}?ref={BRANCH}")
    payload  = {"message": msg, "content": blob, "branch": BRANCH}
    if existing and existing.get("sha"):
        payload["sha"] = existing["sha"]
    r = github("PUT", f"repos/{REPO}/contents/{path}", payload)
    return r.get("commit", {}).get("sha", "")[:8]

def load_properties():
    c = get_file(DATA_PATH)
    return json.loads(c) if c else {"version": 3, "last_updated": "", "properties": []}

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def fmt(n):
    if n is None: return "—"
    return f"${n:,.0f}"

def make_card(p, show_date=False):
    cf  = p.get("cash_flow", {})
    sc  = p.get("scores", {})
    v   = p.get("verdict", "SKIP")
    score = sc.get("overall_score", 0)
    cfv  = cf.get("monthly_cash_flow", 0)
    dp   = cf.get("down_payment", DEFAULT_DP)
    req_dp = cf.get("required_down_payment_for_20pct_cf")
    pos  = cf.get("breakdown", {}).get("positives", [])
    neg  = cf.get("breakdown", {}).get("negatives", [])
    coc  = cf.get("cash_on_cash_return_pct", 0)

    vc = {"BUY":"v-buy","CONDITIONAL":"v-cond","SKIP":"v-skip"}.get(v,"v-skip")
    bc = {"BUY":"green","CONDITIONAL":"yellow","SKIP":"red"}.get(v,"red")
    bar = "high" if score >= 75 else "mid" if score >= 55 else "low"

    sqft = p.get("sqft")
    beds = p.get("beds","?"); baths = p.get("baths","?")
    addr = escape(p.get("address","Unknown"))
    city = escape(p.get("city","") or ""); pt = escape(p.get("property_type",""))
    cond = escape(p.get("condition",""))
    src  = p.get("source","View").upper()
    ts   = p.get("analyzed_at","")[:16].replace("T"," ") if show_date else ""
    pt_label = "Commercial" if p.get("property_type","").lower() in ("commercial","commercial property","office","retail","industrial","multifamily") else ""

    mo_rent  = cf.get("monthly_rent_estimate", 0)
    mo_mort  = cf.get("mortgage_payment", 0)
    mo_tax   = cf.get("monthly_tax", 0)
    mo_ins   = cf.get("monthly_insurance", 0)
    mo_maint = cf.get("monthly_maintenance", 0)
    mo_vac   = cf.get("monthly_vacancy", 0)
    tot_exp  = cf.get("total_monthly_expenses", 0)

    pos_html = "".join(f"<li>{escape(x)}</li>" for x in pos[:6]) if pos else "<li>None identified</li>"
    neg_html = "".join(f"<li>{escape(x)}</li>" for x in neg[:6]) if neg else "<li>None identified</li>"
    cf_class = "cf-pos" if cfv >= 0 else "cf-neg"
    cond_badge = f'<span class="badge bg-muted">{cond}</span>' if cond else ""
    pt_badge   = f'<span class="badge bg-purple">{pt_label}</span>' if pt_label else ""

    if req_dp is not None:
        if req_dp <= dp:
            req_dp_html = f'<span class="reqdp-ok">✅ ${req_dp:,} — you\'re covered at ${dp:,} down</span>'
        else:
            gap = req_dp - dp
            req_dp_html = f'<span class="reqdp-gap">⛔ ~${req_dp:,} needed (+${gap:,} more)</span>'
    else:
        req_dp_html = f'<span class="reqdp-na">⛔ Not achievable at any down payment</span>'

    date_html = f'<span class="date">{ts} UTC</span>' if show_date else ""

    return f'''<div class="card">
  <div class="card-header">
    <div>
      <div class="card-title">{addr}</div>
      <div class="card-sub">{city}{" · "+pt if pt or city else ""}{" · "+pt_label if pt_label else ""}</div>
    </div>
    <span class="badge bg-{bc}">{v}</span>
  </div>

  <div class="price-row">
    <span class="price-big">{fmt(p.get("price"))}</span>
    <span class="beds-baths">{beds}BR / {baths}BA{" · "+str(sqft)+" sqft" if sqft else ""}</span>
  </div>

  <div class="card-badges">
    <span class="badge bg-yellow">Score: {score}</span>
    <span class="badge bg-{"green" if cfv>=0 else "red"}">{fmt(cfv)}/mo CF</span>
    <span class="badge bg-yellow">CoC: {coc}%</span>
    <span class="badge bg-muted">${dp:,} down</span>
    {cond_badge}
    {pt_badge}
  </div>

  <div class="score-bar"><div class="score-fill {bar}" style="width:{score}%"></div></div>

  <div class="cf-block">
    <div class="cf-col">
      <div class="cf-col-header">📊 At Your ${dp:,} Down</div>
      <table class="cf-table">
        <tr><td>Rent</td><td class="cf-pos">+{fmt(mo_rent)}</td></tr>
        <tr><td>Mortgage (P&amp;I @ 7%)</td><td class="cf-neg">−{fmt(mo_mort)}</td></tr>
        <tr><td>Property Tax</td><td class="cf-neg">−{fmt(mo_tax)}</td></tr>
        <tr><td>Insurance</td><td class="cf-neg">−{fmt(mo_ins)}</td></tr>
        <tr><td>Maintenance (10%)</td><td class="cf-neg">−{fmt(mo_maint)}</td></tr>
        <tr><td>Vacancy (8%)</td><td class="cf-neg">−{fmt(mo_vac)}</td></tr>
        <tr class="cf-total"><td>Cash Flow</td><td class="{cf_class}">{fmt(cfv)}/mo</td></tr>
      </table>
    </div>
    <div class="cf-col">
      <div class="cf-col-header">🎯 To Hit 20% Cash Flow</div>
      <div class="reqdp-box">{req_dp_html}</div>
      <table class="cf-table">
        <tr><td>Target CF (20% of expenses)</td><td class="cf-pos">+{fmt(round(tot_exp * 0.20, -1))}</td></tr>
        <tr><td>Non-Mortgage Expenses</td><td class="cf-neg">−{fmt(tot_exp - mo_mort)}</td></tr>
        <tr class="cf-total"><td>Max Mortgage Allowed</td><td class="cf-neg">{fmt(mo_rent - 1.20 * (tot_exp - mo_mort))}/mo</td></tr>
      </table>
    </div>
  </div>

  <div class="breakdown-grid">
    <div>
      <div class="breakdown-header cf-pos">✅ Positives</div>
      <ul class="breakdown-list">{pos_html}</ul>
    </div>
    <div>
      <div class="breakdown-header cf-neg">⚠️ Negatives</div>
      <ul class="breakdown-list">{neg_html}</ul>
    </div>
  </div>

  <div class="verdict-banner {vc}">{v} — {escape(p.get("verdict_reason","") or "No specific reasons noted.")}</div>

  <div class="card-footer">
    <a href="{escape(p.get("url","#"))}" target="_blank">{src} ↗</a>
    {date_html}
  </div>
</div>'''

# ─── CSS ──────────────────────────────────────────────────────────────────────

CSS = """@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root{--bg:#0d1117;--surface:#161b22;--border:#30363d;--text:#e6edf3;--muted:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--purple:#bc8cff}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;padding:1.5rem}
.container{max-width:1200px;margin:0 auto}
header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);padding-bottom:1.5rem;margin-bottom:2rem;flex-wrap:wrap;gap:0.8rem}
h1{font-size:1.6rem;color:var(--accent)}
.updated{color:var(--muted);font-size:0.8rem}
.lead{color:var(--muted);font-size:0.88rem;margin-bottom:1.5rem}
.tabs{display:flex;gap:0;margin-bottom:0;border-bottom:2px solid var(--border);flex-wrap:wrap}
.tab-btn{
  background:none;border:none;padding:0.7rem 1.2rem;font-size:0.9rem;font-weight:600;
  color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;
  transition:color .2s,border-color .2s;border-radius:6px 6px 0 0;white-space:nowrap
}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-panel{display:none}
.tab-panel.active{display:block}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(480px,1fr));gap:1.2rem}
section{margin-bottom:3rem}
h2{font-size:1.2rem;color:var(--accent);margin:0 0 1.2rem;padding-bottom:0.5rem;border-bottom:1px solid var(--border)}
h3{font-size:1rem;margin:1.5rem 0 0.8rem;color:var(--text)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:1.2rem;transition:border-color .2s}
.card:hover{border-color:var(--accent)}
.card-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.7rem;gap:0.5rem}
.card-title{font-size:0.98rem;font-weight:600;line-height:1.3;flex:1}
.card-sub{color:var(--muted);font-size:0.78rem;margin-top:0.2rem}
.price-row{display:flex;align-items:baseline;gap:0.8rem;margin:0.4rem 0;flex-wrap:wrap}
.price-big{font-size:1.35rem;font-weight:700;color:var(--accent)}
.beds-baths{color:var(--muted);font-size:0.82rem}
.card-badges{display:flex;gap:0.35rem;margin:0.5rem 0;flex-wrap:wrap}
.badge{display:inline-block;padding:0.18rem 0.55rem;border-radius:12px;font-size:0.7rem;font-weight:600}
.bg-green{background:rgba(63,185,80,.18);color:var(--green)}
.bg-yellow{background:rgba(210,153,34,.18);color:var(--yellow)}
.bg-red{background:rgba(248,81,73,.18);color:var(--red)}
.bg-muted{background:rgba(139,148,158,.15);color:var(--muted)}
.bg-purple{background:rgba(188,140,255,.18);color:var(--purple)}
.score-bar{background:var(--border);border-radius:4px;height:5px;margin:0.6rem 0;overflow:hidden}
.score-fill{height:100%;border-radius:4px;transition:width .4s}
.score-fill.high{background:var(--green)}.score-fill.mid{background:var(--yellow)}.score-fill.low{background:var(--red)}
.cf-block{display:grid;grid-template-columns:1fr 1fr;gap:0.7rem;margin:0.8rem 0}
.cf-col-header{font-size:0.74rem;font-weight:700;color:var(--accent);margin-bottom:0.4rem;padding-bottom:0.25rem;border-bottom:1px solid var(--border)}
.cf-table{width:100%;border-collapse:collapse;font-size:0.75rem}
.cf-table td{padding:0.25rem 0.45rem;border:1px solid var(--border)}
.cf-table td:last-child{text-align:right;font-weight:600}
.cf-total td{border-top:2px solid var(--border);font-weight:700;font-size:0.8rem}
.cf-pos{color:var(--green)}.cf-neg{color:var(--red)}
.reqdp-box{font-size:0.74rem;font-weight:600;padding:0.45rem 0.6rem;border-radius:6px;margin-bottom:0.4rem;line-height:1.5}
.reqdp-ok{color:var(--green);background:rgba(63,185,80,.12);border:1px solid rgba(63,185,80,.3);display:block;border-radius:6px;padding:0.45rem 0.6rem}
.reqdp-gap{color:var(--yellow);background:rgba(210,153,34,.12);border:1px solid rgba(210,153,34,.3);display:block;border-radius:6px;padding:0.45rem 0.6rem}
.reqdp-na{color:var(--red);background:rgba(248,81,73,.12);border:1px solid rgba(248,81,73,.3);display:block;border-radius:6px;padding:0.45rem 0.6rem}
.breakdown-grid{display:grid;grid-template-columns:1fr 1fr;gap:0.7rem;margin:0.7rem 0}
.breakdown-header{font-size:0.76rem;font-weight:700;margin-bottom:0.35rem}
.breakdown-list{padding-left:1.2rem;font-size:0.76rem;color:var(--muted);line-height:1.5}
.breakdown-list li{margin-bottom:0.15rem}
.verdict-banner{padding:0.6rem 0.9rem;border-radius:6px;margin:0.7rem 0 0.4rem;font-weight:600;font-size:0.8rem;line-height:1.4}
.v-buy{background:rgba(63,185,80,.12);border:1px solid var(--green);color:var(--green)}
.v-cond{background:rgba(210,153,34,.12);border:1px solid var(--yellow);color:var(--yellow)}
.v-skip{background:rgba(248,81,73,.12);border:1px solid var(--red);color:var(--red)}
.card-footer{display:flex;justify-content:space-between;align-items:center;margin-top:0.5rem;gap:0.5rem}
.card-footer a{display:inline-block;background:var(--accent);color:var(--bg);padding:0.3rem 0.8rem;border-radius:6px;text-decoration:none;font-size:0.76rem;font-weight:600}
.card-footer a:hover{opacity:0.85}
.date{color:var(--muted);font-size:0.72rem}
.empty{color:var(--muted);font-style:italic;padding:2.5rem;text-align:center;background:var(--surface);border:1px dashed var(--border);border-radius:8px;font-size:0.9rem}
.how-to{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:1.2rem 1.5rem;margin-top:1rem}
.how-to h3{color:var(--accent);margin-top:0}
.how-to p{color:var(--muted);font-size:0.88rem}
.how-to code{background:var(--bg);padding:0.2rem 0.5rem;border-radius:4px;font-size:0.82rem;color:var(--accent)}
footer{margin-top:4rem;padding-top:1rem;border-top:1px solid var(--border);color:var(--muted);font-size:0.78rem;text-align:center}
footer p{margin-bottom:0.3rem}
.notice{background:rgba(88,166,255,.08);border:1px solid rgba(88,166,255,.25);border-radius:8px;padding:0.8rem 1rem;font-size:0.82rem;color:var(--muted);margin-bottom:1.5rem}

/* ─── Mobile ─────────────────────────────────────────────── */
@media(max-width:600px){
  body{padding:0.8rem}
  h1{font-size:1.3rem}
  header{flex-direction:column;align-items:flex-start;gap:0.5rem}
  .tabs{gap:0;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none}
  .tabs::-webkit-scrollbar{display:none}
  .tab-btn{padding:0.6rem 0.9rem;font-size:0.85rem}
  .cards{grid-template-columns:1fr}
  .cf-block{grid-template-columns:1fr}
  .breakdown-grid{grid-template-columns:1fr}
  .price-big{font-size:1.2rem}
}"""

# ─── JS ───────────────────────────────────────────────────────────────────────

JS = """document.addEventListener('DOMContentLoaded',function(){
  var tabs=document.querySelectorAll('.tab-btn');
  var panels=document.querySelectorAll('.tab-panel');
  tabs.forEach(function(btn){
    btn.addEventListener('click',function(){
      var id=btn.getAttribute('data-tab');
      tabs.forEach(function(t){t.classList.remove('active')});
      panels.forEach(function(p){p.classList.remove('active')});
      btn.classList.add('active');
      document.getElementById(id).classList.add('active');
    });
  });
});"""

# ─── BUILD ─────────────────────────────────────────────────────────────────────

def build_html(props):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    active = [p for p in props if p.get("status") == "active"]

    # Residential vs Commercial
    def is_com(p):
        return p.get("property_type","").lower() in \
            ("commercial","commercial property","office","retail","industrial","multifamily","apartment building")

    res = [p for p in active if not is_com(p)]
    com = [p for p in active if     is_com(p)]

    # Top: best CF among residential, cap at 5
    top_res = sorted(res, key=lambda x: x.get("cash_flow",{}).get("monthly_cash_flow",0), reverse=True)[:5]
    # Recent: last 10 analyzed residential
    rec_res = sorted(res, key=lambda x: x.get("analyzed_at",""), reverse=True)[:10]
    # Commercial
    com_all = sorted(com, key=lambda x: x.get("analyzed_at",""), reverse=True)

    top_res_html  = "\n".join(make_card(p)           for p in top_res) if top_res  else '<div class="empty">No residential properties yet — send a listing link to get started!</div>'
    rec_res_html  = "\n".join(make_card(p,True)       for p in rec_res) if rec_res  else '<div class="empty">No analyses yet.</div>'
    com_html      = "\n".join(make_card(p,True)       for p in com_all) if com_all  else '<div class="empty">No commercial properties yet. Send a LoopNet link to add one.</div>'

    tab_count = len(com_all)
    com_tab_li = f'<button class="tab-btn" data-tab="tab-com">🏢 Commercial {f"({tab_count})" if tab_count else ""}</button>' if True else ""

    how_to = f"""<div class="how-to">
<h3>📧 How to Submit a Property</h3>
<p>Email <strong>devillebot@gmail.com</strong> with any listing link (Zillow, Redfin, Realtor, LoopNet, etc.) — no format needed, just include the link. Commercial links (LoopNet) are automatically grouped in the Commercial tab.</p>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Rental Deal Analyzer — Altamonte Springs / DeBary Corridor</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">
<header>
  <h1>🏠 Rental Deal Analyzer</h1>
  <span class="updated">Updated: {now}</span>
</header>

<div class="notice">
  Analysis defaults to <strong>$150,000 down payment</strong> &nbsp;|&nbsp; Target: <strong>20% cash flow</strong> over all monthly expenses &nbsp;|&nbsp; FL market estimates — verify independently.
</div>

<div class="tabs">
  <button class="tab-btn active" data-tab="tab-top">⭐ Top Opportunities</button>
  <button class="tab-btn" data-tab="tab-rec">📋 Recent Analyses</button>
  {com_tab_li}
</div>

<div id="tab-top" class="tab-panel active">
  <div class="cards">{top_res_html}</div>
  {how_to}
</div>

<div id="tab-rec" class="tab-panel">
  <div class="cards">{rec_res_html}</div>
</div>

<div id="tab-com" class="tab-panel">
  <div class="cards">{com_html}</div>
</div>

<footer>
  <p>⚡ Powered by Johnny5 &nbsp;|&nbsp; Not financial advice — always verify independently.</p>
</footer>
</div>
<script>{JS}</script>
</body>
</html>"""


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    data = load_properties()
    props = data.get("properties", [])

    print(f"Loaded {len(props)} properties from GitHub")

    html = build_html(props)

    sha = save_file(HTML_PATH, html, "Rebuild with tabbed layout — mobile-first")
    print(f"HTML pushed: {sha}")
    print(f"🌐 https://devillebot-art.github.io/rental-deal-analysis/")
