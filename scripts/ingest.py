#!/usr/bin/env python3
"""
Rental Analysis Pipeline — Gmail Ingestion Script
Polls Gmail for new property emails, analyzes them, updates data + HTML on GitHub.
"""

import os, re, json, base64
from datetime import datetime, timezone

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO   = "devillebot-art/rental-deal-analysis"
GITHUB_BRANCH = "main"
MATON_KEY     = os.environ.get("MATON_API_KEY")
DATA_PATH     = "data/properties.json"
HTML_PATH     = "index.html"
EMAIL_QUERY   = "subject:new property OR subject:property OR subject:rental after:2026/04/05"

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
    property_domains = ["zillow","redfin","realtor.com","trulia","homes.com","movoto","REA","rltre","loopnet","commercialcss"]
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
                print(f"  ✅ {prop['verdict']} (CF: ${cfv:,}/mo | req_dp: {fmt(req)}) — {prop['address']}")
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
