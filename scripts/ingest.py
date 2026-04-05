#!/usr/bin/env python3
"""
Rental Analysis Pipeline — Gmail Ingestion Script
Polls Gmail for new property emails, analyzes them, updates data + HTML on GitHub.
"""

import os, re, json, base64
from datetime import datetime, timezone
from html import escape

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO   = "devillebot-art/rental-deal-analysis"
GITHUB_BRANCH = "main"
MATON_KEY     = os.environ.get("MATON_API_KEY")
DATA_PATH     = "data/properties.json"
HTML_PATH     = "index.html"
EMAIL_QUERY   = "subject:new property OR subject:property OR subject:rental after:2026/04/05"
DEFAULT_DP    = 150_000

HEADERS = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyzer import analyze_property


# ─── GITHUB HELPERS ──────────────────────────────────────────────────────────

def github_api(method, path, data=None):
    import urllib.request
    url = f"https://api.github.com/{path.lstrip('/')}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  [!] GitHub API error: {e}")
        return None


def get_file(path):
    data = github_api("GET", f"repos/{GITHUB_REPO}/contents/{path}?ref={GITHUB_BRANCH}")
    if not data or "content" not in data:
        return None
    return base64.b64decode(data["content"]).decode("utf-8")


def save_file(path, content, msg):
    blob = base64.b64encode(content.encode()).decode()
    existing = github_api("GET", f"repos/{GITHUB_REPO}/contents/{path}?ref={GITHUB_BRANCH}")
    payload = {"message": msg, "content": blob, "branch": GITHUB_BRANCH}
    if existing and existing.get("sha"):
        payload["sha"] = existing["sha"]
    return github_api("PUT", f"repos/{GITHUB_REPO}/contents/{path}", payload)


def load_properties():
    content = get_file(DATA_PATH)
    if not content:
        return {"version": 1, "last_updated": "", "properties": []}
    return json.loads(content)


def save_properties(data):
    content = json.dumps(data, indent=2)
    return save_file(DATA_PATH, content, f"Update properties — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")


# ─── GMAIL HELPERS ───────────────────────────────────────────────────────────

def gmail_fetch():
    import urllib.request
    url = f"https://gateway.maton.ai/google-mail/gmail/v1/users/me/messages?q={urllib.request.quote(EMAIL_QUERY)}&maxResults=10"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {MATON_KEY}")
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return resp.get("messages", [])


def gmail_get_body(msg_id):
    import urllib.request
    url = f"https://gateway.maton.ai/google-mail/gmail/v1/users/me/messages/{msg_id}?format=full"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {MATON_KEY}")
    msg = json.loads(urllib.request.urlopen(req, timeout=30).read())
    payload = msg.get("payload", {})
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            raw = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            return raw
    return msg.get("snippet", "")


def extract_links(text):
    property_domains = ["zillow","redfin","realtor.com","trulia","homes.com","movoto","REA","rltre"]
    url_pattern = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+', re.IGNORECASE)
    found = []
    for url in url_pattern.findall(text):
        url = url.strip().rstrip('.,;:')
        if any(d in url.lower() for d in property_domains):
            found.append(url)
    seen = set()
    unique = []
    for u in found:
        if u not in seen:
            seen.add(u); unique.append(u)
    return unique


# ─── HTML BUILDER ─────────────────────────────────────────────────────────────

def fmt_currency(n):
    if n is None: return "—"
    return f"${n:,.0f}"


def build_html(properties):
    now = datetime.now().strftime("%Y-%m-%d %H:%M %Z")

    # Top: sorted by cash flow (highest first) at $150K down
    active = [p for p in properties if p.get("status") == "active"]
    top = sorted(active, key=lambda x: x.get("cash_flow", {}).get("monthly_cash_flow", 0), reverse=True)[:5]
    recent = sorted(active, key=lambda x: x.get("analyzed_at", ""), reverse=True)[:10]

    def make_card(p, show_date=False):
        cf  = p.get("cash_flow", {})
        sc  = p.get("scores", {})
        v   = p.get("verdict", "SKIP")
        score = sc.get("overall_score", 0)
        cfv = cf.get("monthly_cash_flow", 0)
        dp  = cf.get("down_payment", DEFAULT_DP)
        req_dp = cf.get("required_down_payment_for_20pct_cf")
        pos = cf.get("breakdown", {}).get("positives", [])
        neg = cf.get("breakdown", {}).get("negatives", [])
        coc = cf.get("cash_on_cash_return_pct", 0)

        vc  = {"BUY":"v-buy","CONDITIONAL":"v-cond","SKIP":"v-skip"}.get(v,"v-skip")
        bc  = {"BUY":"green","CONDITIONAL":"yellow","SKIP":"red"}.get(v,"red")
        bar = "high" if score >= 75 else "mid" if score >= 55 else "low"
        sqft = p.get("sqft"); beds = p.get("beds","?"); baths = p.get("baths","?")
        addr = escape(p.get("address","Unknown"))
        city = escape(p.get("city","") or ""); pt = escape(p.get("property_type",""))
        cond = escape(p.get("condition",""))
        src  = p.get("source","View").upper()
        ts   = p.get("analyzed_at","")[:16].replace("T"," ") if show_date else ""

        mo_rent = cf.get("monthly_rent_estimate", 0)
        mo_mort = cf.get("mortgage_payment", 0)
        mo_tax  = cf.get("monthly_tax", 0)
        mo_ins  = cf.get("monthly_insurance", 0)
        mo_maint = cf.get("monthly_maintenance", 0)
        mo_vac  = cf.get("monthly_vacancy", 0)
        tot_exp = cf.get("total_monthly_expenses", 0)

        pos_html = "".join(f"<li>{escape(x)}</li>" for x in pos[:6])
        neg_html = "".join(f"<li>{escape(x)}</li>" for x in neg[:6])
        cf_class = "cf-pos" if cfv >= 0 else "cf-neg"
        cond_badge = f'<span class="badge bg-muted">{cond}</span>' if cond else ""

        # Required DP section
        if req_dp is not None:
            if req_dp <= dp:
                req_dp_html = f'<span class="reqdp-ok">✅ ${req_dp:,} — you\'re covered at ${dp:,}</span>'
            else:
                gap = req_dp - dp
                req_dp_html = f'<span class="reqdp-gap">⛔ ~${req_dp:,} needed (+${gap:,} more)</span>'
        else:
            req_dp_html = f'<span class="reqdp-na">⛔ Not achievable — price too high for the corridor</span>'

        return f'''<div class="card">
  <div class="card-header">
    <div>
      <div class="card-title">{addr}</div>
      <div class="card-sub">{city}{" · "+pt if pt or city else ""}</div>
    </div>
    <span class="badge bg-{bc}">{v}</span>
  </div>

  <div class="price-row">
    <span class="price-big">{fmt_currency(p.get("price"))}</span>
    <span class="beds-baths">{beds}BR / {baths}BA{" · "+str(sqft)+" sqft" if sqft else ""}</span>
  </div>

  <div class="card-badges">
    <span class="badge bg-yellow">Score: {score}</span>
    <span class="badge bg-{"green" if cfv>=0 else "red"}">{fmt_currency(cfv)}/mo CF</span>
    <span class="badge bg-yellow">CoC: {coc}%</span>
    <span class="badge bg-muted">${dp:,} down</span>
    {cond_badge}
  </div>

  <div class="score-bar"><div class="score-fill {bar}" style="width:{score}%"></div></div>

  <div class="cf-block">
    <div class="cf-col">
      <div class="cf-col-header">📊 At Your ${dp:,} Down</div>
      <table class="cf-table">
        <tr><td>Rent</td><td class="cf-pos">+{fmt_currency(mo_rent)}</td></tr>
        <tr><td>Mortgage (P&amp;I @ 7%)</td><td class="cf-neg">−{fmt_currency(mo_mort)}</td></tr>
        <tr><td>Property Tax</td><td class="cf-neg">−{fmt_currency(mo_tax)}</td></tr>
        <tr><td>Insurance</td><td class="cf-neg">−{fmt_currency(mo_ins)}</td></tr>
        <tr><td>Maintenance (10%)</td><td class="cf-neg">−{fmt_currency(mo_maint)}</td></tr>
        <tr><td>Vacancy (8%)</td><td class="cf-neg">−{fmt_currency(mo_vac)}</td></tr>
        <tr class="cf-total"><td>Cash Flow</td><td class="{cf_class}">{fmt_currency(cfv)}/mo</td></tr>
      </table>
    </div>
    <div class="cf-col">
      <div class="cf-col-header">🎯 Down Payment to Hit 20% CF</div>
      <div class="reqdp-box">{req_dp_html}</div>
      <table class="cf-table">
        <tr><td>Target CF (20% of rent)</td><td class="cf-pos">+{fmt_currency(round(mo_rent * 0.20, -1))}</td></tr>
        <tr><td>Monthly Expenses</td><td class="cf-neg">−{fmt_currency(tot_exp - mo_mort)}</td></tr>
        <tr class="cf-total"><td>Max Mortgage Payment</td><td class="cf-neg">{fmt_currency(mo_rent * 1.20 - (tot_exp - mo_mort))}/mo</td></tr>
      </table>
    </div>
  </div>

  <div class="breakdown-grid">
    <div>
      <div class="breakdown-header cf-pos">✅ Positives</div>
      <ul class="breakdown-list">{"<li>None identified</li>" if not pos_html else pos_html}</ul>
    </div>
    <div>
      <div class="breakdown-header cf-neg">⚠️ Negatives</div>
      <ul class="breakdown-list">{"<li>None identified</li>" if not neg_html else neg_html}</ul>
    </div>
  </div>

  <div class="verdict-banner {vc}">{v} — {escape(p.get("verdict_reason",""))}</div>

  <div class="card-footer">
    <a href="{escape(p.get("url","#"))}" target="_blank">{src} ↗</a>
    {f'<span class="date">{ts} UTC</span>' if show_date else ""}
  </div>
</div>'''

    top_html = "\n".join(make_card(p) for p in top) if top else '<div class="empty">No properties yet — email a listing link to get started!</div>'
    rec_html = "\n".join(make_card(p, show_date=True) for p in recent) if recent else '<div class="empty">No analyses yet.</div>'

    CSS = """@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root{--bg:#0d1117;--surface:#161b22;--border:#30363d;--text:#e6edf3;--muted:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;padding:2rem}
.container{max-width:1200px;margin:0 auto}
header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);padding-bottom:1.5rem;margin-bottom:2.5rem;flex-wrap:wrap;gap:1rem}
h1{font-size:1.8rem;color:var(--accent)}.updated{color:var(--muted);font-size:0.8rem}
section{margin-bottom:3.5rem}
h2{font-size:1.3rem;color:var(--accent);margin:0 0 1.5rem;padding-bottom:0.5rem;border-bottom:1px solid var(--border)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(520px,1fr));gap:1.2rem}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:1.3rem;transition:border-color .2s}
.card:hover{border-color:var(--accent)}
.card-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.8rem;gap:0.5rem}
.card-title{font-size:1rem;font-weight:600;line-height:1.3;flex:1}
.card-sub{color:var(--muted);font-size:0.8rem;margin-top:0.2rem}
.price-row{display:flex;align-items:baseline;gap:1rem;margin:0.4rem 0;flex-wrap:wrap}
.price-big{font-size:1.4rem;font-weight:700;color:var(--accent)}
.beds-baths{color:var(--muted);font-size:0.85rem}
.card-badges{display:flex;gap:0.4rem;margin:0.5rem 0;flex-wrap:wrap}
.badge{display:inline-block;padding:0.2rem 0.6rem;border-radius:12px;font-size:0.72rem;font-weight:600}
.bg-green{background:rgba(63,185,80,.18);color:var(--green)}
.bg-yellow{background:rgba(210,153,34,.18);color:var(--yellow)}
.bg-red{background:rgba(248,81,73,.18);color:var(--red)}
.bg-muted{background:rgba(139,148,158,.15);color:var(--muted)}
.score-bar{background:var(--border);border-radius:4px;height:6px;margin:0.6rem 0;overflow:hidden}
.score-fill{height:100%;border-radius:4px;transition:width .4s}
.score-fill.high{background:var(--green)}.score-fill.mid{background:var(--yellow)}.score-fill.low{background:var(--red)}
.cf-block{display:grid;grid-template-columns:1fr 1fr;gap:0.8rem;margin:0.8rem 0}
.cf-col-header{font-size:0.75rem;font-weight:700;color:var(--accent);margin-bottom:0.4rem;padding-bottom:0.3rem;border-bottom:1px solid var(--border)}
.cf-table{width:100%;border-collapse:collapse;font-size:0.76rem}
.cf-table td{padding:0.28rem 0.5rem;border:1px solid var(--border)}
.cf-table td:last-child{text-align:right;font-weight:600}
.cf-total td{border-top:2px solid var(--border);font-weight:700;font-size:0.82rem}
.cf-pos{color:var(--green)}.cf-neg{color:var(--red)}
.reqdp-box{font-size:0.76rem;font-weight:600;padding:0.5rem 0.6rem;border-radius:6px;margin-bottom:0.4rem;line-height:1.5}
.reqdp-ok{color:var(--green);background:rgba(63,185,80,.12);border:1px solid rgba(63,185,80,.3);display:block;border-radius:6px;padding:0.5rem}
.reqdp-gap{color:var(--yellow);background:rgba(210,153,34,.12);border:1px solid rgba(210,153,34,.3);display:block;border-radius:6px;padding:0.5rem}
.reqdp-na{color:var(--red);background:rgba(248,81,73,.12);border:1px solid rgba(248,81,73,.3);display:block;border-radius:6px;padding:0.5rem}
.breakdown-grid{display:grid;grid-template-columns:1fr 1fr;gap:0.8rem;margin:0.8rem 0}
.breakdown-header{font-size:0.78rem;font-weight:700;margin-bottom:0.4rem}
.breakdown-list{padding-left:1.2rem;font-size:0.78rem;color:var(--muted);line-height:1.5}
.breakdown-list li{margin-bottom:0.2rem}
.verdict-banner{padding:0.7rem 1rem;border-radius:6px;margin:0.8rem 0 0.5rem;font-weight:600;font-size:0.82rem;line-height:1.4}
.v-buy{background:rgba(63,185,80,.12);border:1px solid var(--green);color:var(--green)}
.v-cond{background:rgba(210,153,34,.12);border:1px solid var(--yellow);color:var(--yellow)}
.v-skip{background:rgba(248,81,73,.12);border:1px solid var(--red);color:var(--red)}
.card-footer{display:flex;justify-content:space-between;align-items:center;margin-top:0.6rem;gap:0.5rem}
.card-footer a{display:inline-block;background:var(--accent);color:var(--bg);padding:0.35rem 0.9rem;border-radius:6px;text-decoration:none;font-size:0.78rem;font-weight:600}
.card-footer a:hover{opacity:0.85}
.date{color:var(--muted);font-size:0.72rem}
.empty{color:var(--muted);font-style:italic;padding:2.5rem;text-align:center;background:var(--surface);border:1px dashed var(--border);border-radius:8px}
footer{margin-top:4rem;padding-top:1rem;border-top:1px solid var(--border);color:var(--muted);font-size:0.8rem;text-align:center}
footer p{margin-bottom:0.3rem}
@media(max-width:600px){body{padding:1rem}.cards{grid-template-columns:1fr}.cf-block{grid-template-columns:1fr}.breakdown-grid{grid-template-columns:1fr}}"""

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
  <span class="updated">Last updated: {now} &nbsp;|&nbsp; Auto-updated from email submissions</span>
</header>

<section>
  <h2>⭐ Top Opportunities — Ranked by Cash Flow at $150K Down</h2>
  <div class="cards">{top_html}</div>
</section>

<section>
  <h2>📋 Recent Analyses</h2>
  <div class="cards">{rec_html}</div>
</section>

<footer>
  <p>⚡ Powered by Johnny5 &nbsp;|&nbsp; FL market estimates — verify independently</p>
  <p>Analysis defaults to $150,000 down payment &nbsp;|&nbsp; Target: 20% cash flow over expenses &nbsp;|&nbsp; Not financial advice.</p>
</footer>
</div>
</body>
</html>"""


# ─── PIPELINE ────────────────────────────────────────────────────────────────

def run():
    print(f"[{datetime.now().isoformat()}] Starting pipeline...")

    messages = gmail_fetch()
    print(f"  Found {len(messages)} matching emails")

    if not messages:
        print("  No new emails. Exiting.")
        return

    data = load_properties()
    existing_urls = {p["url"] for p in data["properties"]}
    new_props = []

    for msg_ref in messages:
        msg_id = msg_ref["id"]
        body = gmail_get_body(msg_id)
        links = extract_links(body)

        if not links:
            print(f"  No property links in {msg_id[:8]}...")
            continue

        for url in links:
            if url in existing_urls:
                print(f"  Already analyzed: {url[:60]}...")
                continue
            result = analyze_property(url)
            if result["status"] == "ok":
                prop = result["property"]
                data["properties"].append(prop)
                new_props.append(prop)
                existing_urls.add(url)
                cfv = prop["cash_flow"].get("monthly_cash_flow", 0)
                req = prop["cash_flow"].get("required_down_payment_for_20pct_cf")
                print(f"  ✅ {prop['verdict']} (CF: ${cfv:,}/mo | req_dp: {fmt_currency(req)}) — {prop['address']}")
            else:
                print(f"  ❌ Error analyzing {url[:60]}: {result.get('error')}")

    if not new_props:
        print("  No new properties. Done.")
        return

    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    data["version"] = data.get("version", 0) + 1

    print("  Saving properties JSON...")
    r1 = save_properties(data)
    print(f"  JSON saved: {r1.get('commit',{}).get('sha','')[:8] if r1 else 'FAILED'}")

    print("  Rebuilding HTML...")
    html = build_html(data["properties"])
    r2 = save_file(HTML_PATH, html, f"Rebuild — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  HTML saved: {r2.get('commit',{}).get('sha','')[:8] if r2 else 'FAILED'}")

    print(f"  ✅ Done — {len(new_props)} new property(ies) added")
    print(f"  🌐 https://devillebot-art.github.io/rental-deal-analysis/")


if __name__ == "__main__":
    run()
