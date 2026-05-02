#!/usr/bin/env python3
"""
Rental Analysis Pipeline — Gmail Ingestion Script
Polls Gmail for new property emails, analyzes them, updates data + HTML on GitHub,
and sends confirmation replies to submitters.
"""

import os, re, json, base64, smtplib, ssl
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO   = "devillebot-art/rental-deal-analysis"
GITHUB_BRANCH = "main"
MATON_KEY     = os.environ.get("MATON_API_KEY")
DATA_PATH     = "data/properties.json"
HTML_PATH     = "index.html"
EMAIL_QUERY   = "subject:new property OR subject:property OR subject:rental after:2026/04/05"
SMTP_EMAIL    = "devillebot@gmail.com"
SMTP_APP_PWD = "mfaksffweflzqxpr"

HEADERS = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyzer import analyze_property
from rebuild_html import build_html


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
        return {"version": 3, "last_updated": "", "properties": []}
    return json.loads(content)


def save_properties(data):
    content = json.dumps(data, indent=2)
    return save_file(DATA_PATH, content, f"Update properties — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")


# ─── GMAIL HELPERS ───────────────────────────────────────────────────────────

def gmail_fetch():
    import urllib.request
    url = (f"https://gateway.maton.ai/google-mail/gmail/v1/users/me/messages"
           f"?q={urllib.request.quote(EMAIL_QUERY)}&maxResults=10")
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {MATON_KEY}")
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return resp.get("messages", [])


def gmail_get_message(msg_id):
    """Returns (body_text, sender_email) from a Gmail message."""
    import urllib.request
    url = f"https://gateway.maton.ai/google-mail/gmail/v1/users/me/messages/{msg_id}?format=full"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {MATON_KEY}")
    msg = json.loads(urllib.request.urlopen(req, timeout=30).read())

    # Get body
    payload = msg.get("payload", {})
    body = ""
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            break
    if not body:
        body = msg.get("snippet", "")

    # Get sender
    headers = payload.get("headers", [])
    sender = ""
    for h in headers:
        if h["name"].lower() == "from":
            m = re.search(r'<(.+@.+)>', h["value"])
            sender = m.group(1) if m else h["value"].strip()
            break

    return body, sender


def extract_links(text):
    property_domains = ["zillow","redfin","realtor.com","trulia","homes.com",
                        "movoto","REA","rltre","loopnet","commercialcss","propertytype=commercial"]
    url_pattern = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+', re.IGNORECASE)
    found = []
    for url in url_pattern.findall(text):
        url = url.strip().rstrip('.,;:')
        if any(d in url.lower() for d in property_domains):
            found.append(url)
    seen = set()
    for u in found:
        if u not in seen:
            seen.add(u); found[found.index(u)] = u
    return list(seen)


# ─── EMAIL CONFIRMATION ─────────────────────────────────────────────────────

def send_confirmation(to_email, properties):
    """Send a confirmation email to the submitter for each new property."""
    if not to_email or not properties:
        return

    SITE_URL = "https://devillebot-art.github.io/rental-deal-analysis/"

    # Build one section per property
    def prop_section(p, num):
        cf = p.get("cash_flow", {})
        sc = p.get("scores", {})
        v  = p.get("verdict", "SKIP")
        score = sc.get("overall_score", 0)
        cfv   = cf.get("monthly_cash_flow", 0)
        dp    = cf.get("down_payment", 150_000)
        req_dp = cf.get("required_down_payment_for_20pct_cf")
        coc   = cf.get("cash_on_cash_return_pct", 0)
        mo_rent = cf.get("monthly_rent_estimate", 0)

        verdict_icon = {"BUY":"✅","CONDITIONAL":"🟡","SKIP":"❌"}.get(v,"❌")
        verdict_color = {"BUY":"#3fb950","CONDITIONAL":"#d29922","SKIP":"#f85149"}.get(v,"#f85149")

        req_dp_text = (
            f"✅ Already covered at ${dp:,} down"
            if req_dp is not None and req_dp <= dp
            else f"⛔ ~${req_dp:,} needed" if req_dp
            else "⛔ Not achievable at any down payment"
        )

        return f"""<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:1.2rem;margin-bottom:1rem">
  <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem">
    <span style="font-size:1.2rem">{verdict_icon}</span>
    <strong style="font-size:1rem;color:#e6edf3">{p.get("address","Unknown")}</strong>
  </div>
  <div style="color:#8b949e;font-size:0.85rem;margin-bottom:0.8rem">
    {p.get("city","")} · {p.get("beds","?")}BR / {p.get("baths","?")}BA
    {f' · {p.get("sqft")} sqft' if p.get("sqft") else ""}
    · {p.get("property_type","")}
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;font-size:0.85rem">
    <div style="background:#0d1117;padding:0.6rem;border-radius:6px">
      <div style="color:#8b949e;margin-bottom:0.2rem">Price</div>
      <div style="color:#58a6ff;font-weight:700;font-size:1rem">{fmt(p.get("price"))}</div>
    </div>
    <div style="background:#0d1117;padding:0.6rem;border-radius:6px">
      <div style="color:#8b949e;margin-bottom:0.2rem">Est. Rent</div>
      <div style="color:#3fb950;font-weight:700;font-size:1rem">{fmt(mo_rent)}/mo</div>
    </div>
    <div style="background:#0d1117;padding:0.6rem;border-radius:6px">
      <div style="color:#8b949e;margin-bottom:0.2rem">Cash Flow (at $125K)</div>
      <div style="color:{'#3fb950' if cfv>=0 else '#f85149'};font-weight:700;font-size:1rem">{fmt(cfv)}/mo</div>
    </div>
    <div style="background:#0d1117;padding:0.6rem;border-radius:6px">
      <div style="color:#8b949e;margin-bottom:0.2rem">Verdict</div>
      <div style="color:{verdict_color};font-weight:700;font-size:1rem">{verdict_icon} {v}</div>
    </div>
  </div>
  <div style="margin-top:0.7rem;padding:0.5rem;background:#0d1117;border-radius:6px;font-size:0.82rem">
    <span style="color:#8b949e">Required DP for 20% CF:</span>
    <span style="color:{'#3fb950' if req_dp and req_dp<=dp else '#d29922'};font-weight:600">{req_dp_text}</span>
  </div>
  <div style="margin-top:0.7rem;font-size:0.82rem;color:#8b949e">
    Score: <span style="color:#d29922">{score}</span> &nbsp;|&nbsp;
    CoC: <span style="color:#d29922">{coc}%</span> &nbsp;|&nbsp;
    <a href="{p.get("url","#")}" style="color:#58a6ff">View Listing ↗</a>
  </div>
</div>"""

    sections = "\n".join(prop_section(p, i+1) for i, p in enumerate(properties))

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:#0d1117;color:#e6edf3;padding:2rem">
<div style="max-width:620px;margin:0 auto">
  <div style="border-bottom:1px solid #30363d;padding-bottom:1rem;margin-bottom:1.5rem">
    <h1 style="color:#58a6ff;font-size:1.5rem;margin:0">🏠 Property Added!</h1>
    <p style="color:#8b949e;margin:0.3rem 0 0;font-size:0.9rem">
      Your submission{'s' if len(properties)>1 else ''} ha{'ve' if len(properties)>1 else 's'} been analyzed and added to the Rental Deal Analyzer.
    </p>
  </div>
  {sections}
  <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:1rem;text-align:center">
    <p style="margin:0 0 0.5rem;font-size:0.9rem">
      <a href="{SITE_URL}" style="background:#58a6ff;color:#0d1117;padding:0.6rem 1.2rem;border-radius:6px;text-decoration:none;font-weight:600;display:inline-block">
        View All Properties →
      </a>
    </p>
    <p style="color:#8b949e;font-size:0.8rem;margin:0">
      Analyzed by Johnny5 ⚡ · FL Altamonte Springs / DeBary Corridor
    </p>
  </div>
</div>
</body></html>"""

    plain = f"""RENTAL DEAL ANALYZER — Property Added! ✅

Your submission{'s' if len(properties)>1 else ''} ha{'ve' if len(properties)>1 else 's'} been analyzed and added.
View all properties: {SITE_URL}

"""
    for i, p in enumerate(properties, 1):
        cf = p.get("cash_flow", {})
        cfv = cf.get("monthly_cash_flow", 0)
        plain += f"""{'─'*50}
{i}. {p.get("address","Unknown")}
   Price: {fmt(p.get("price"))} | Rent: {fmt(cf.get("monthly_rent_estimate",0))}/mo
   Cash Flow (at $125K): {fmt(cfv)}/mo | Verdict: {p.get("verdict","?")}
   Listing: {p.get("url","#")}
"""

    plain += f"""
—
Analyzed by Johnny5 ⚡ · {SITE_URL}
"""

    subject_prefix = "✅" if all(p.get("verdict")=="BUY" for p in properties) else "📋"
    subject = f"{subject_prefix} {len(properties)} property(ies) added to Rental Deal Analyzer"

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = SMTP_EMAIL
    msg['To'] = to_email
    msg.attach(MIMEText(plain, 'plain'))
    msg.attach(MIMEText(html, 'html'))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
            server.login(SMTP_EMAIL, SMTP_APP_PWD)
            server.sendmail(SMTP_EMAIL, [to_email], msg.as_string())
        print(f"  📧 Confirmation sent to {to_email}")
        return True
    except Exception as e:
        print(f"  ❌ Failed to send confirmation to {to_email}: {e}")
        return False


# ─── PIPELINE ───────────────────────────────────────────────────────────────

def fmt(n):
    if n is None: return "—"
    return f"${n:,.0f}"


def run():
    print(f"[{datetime.now().isoformat()}] Starting pipeline...")

    messages = gmail_fetch()
    print(f"  Found {len(messages)} matching emails")

    if not messages:
        print("  No new emails. Exiting.")
        return

    data = load_properties()
    existing_urls = {p["url"] for p in data["properties"]}

    # Per-email: {msg_id: {"sender": email, "new_props": []}}
    processed_emails = {}   # msg_id → {sender, new_props}

    for msg_ref in messages:
        msg_id = msg_ref["id"]
        body, sender = gmail_get_message(msg_id)
        links = extract_links(body)
        print(f"  Email from {sender}: {len(links)} link(s) found")

        if not links:
            print(f"  No property links...")
            continue

        processed_emails[msg_id] = {"sender": sender, "new_props": []}

        for url in links:
            if url in existing_urls:
                print(f"    Already analyzed: {url[:60]}...")
                continue
            result = analyze_property(url)
            if result["status"] == "ok":
                prop = result["property"]
                data["properties"].append(prop)
                processed_emails[msg_id]["new_props"].append(prop)
                existing_urls.add(url)
                cfv = prop["cash_flow"].get("monthly_cash_flow", 0)
                req = prop["cash_flow"].get("required_down_payment_for_20pct_cf")
                print(f"    ✅ {prop['verdict']} | CF: {fmt(cfv)}/mo | req_dp: {fmt(req)} | {prop['address'][:50]}")
            else:
                print(f"    ❌ Error: {result.get('error')}")

    # Collect all new properties across all emails
    all_new_props = []
    for info in processed_emails.values():
        all_new_props.extend(info["new_props"])

    if not all_new_props:
        print("  No new properties. Done.")
        return

    # Save updated data
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    data["version"] = data.get("version", 0) + 1

    print("  Saving properties JSON...")
    r1 = save_properties(data)
    print(f"  JSON saved: {r1.get('commit',{}).get('sha','')[:8] if r1 else 'FAILED'}")

    print("  Rebuilding HTML...")
    html = build_html(data["properties"])
    r2 = save_file(HTML_PATH, html, f"Rebuild — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  HTML saved: {r2.get('commit',{}).get('sha','')[:8] if r2 else 'FAILED'}")

    # Send confirmation emails — one per original sender
    for msg_id, info in processed_emails.items():
        if info["new_props"]:
            send_confirmation(info["sender"], info["new_props"])

    print(f"  ✅ Done — {len(all_new_props)} new property(ies) added")
    print(f"  🌐 https://devillebot-art.github.io/rental-deal-analysis/")


if __name__ == "__main__":
    run()
