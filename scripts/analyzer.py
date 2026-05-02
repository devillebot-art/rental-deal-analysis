#!/usr/bin/env python3
"""
Rental Property Analyzer
Fetches real listing data, generates property-specific cash flow analysis.
"""

import re
import json
import os
import math
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
DEFAULT_DOWN_PAYMENT = 125_000  # flat $125K, not percentage
INTEREST_RATE        = 0.07     # 7% annual
LOAN_TERM_MONTHS    = 360       # 30-year
VACANCY_RATE        = 0.08      # 8% vacancy allowance
MAINTENANCE_RATE    = 0.10      # 10% maintenance allowance
TARGET_CF_PCT       = 0.20      # target: CF = 20% of total monthly expenses (rent must be 1.2x expenses)

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


# ─── FETCH HELPERS ──────────────────────────────────────────────────────────

def _extract_from_url(url):
    """Extract property data from URL structure — most reliable method for Zillow/Redfin."""
    import urllib.parse
    parsed = urlparse(url)
    result = {}
    path = parsed.path
    query = urllib.parse.parse_qs(parsed.query)

    # Zillow
    if "zillow" in url.lower():
        parts = [p for p in path.split("/") if p and p not in ("", "homedetails", "homes", "listing")]
        for part in parts:
            zm = re.search(r'\b(\d{5})\b', part)
            if zm:
                z = zm.group(1)
                full_num = re.search(r'(\d{6,})', part)
                if not (full_num and z in full_num.group(0)):
                    if z != '00000' and z != '12345' and not z.startswith('0'):
                        result["zip"] = z
            bm = re.search(r'(\d+)[- ]?(?:bed|br|bedroom)', part, re.I)
            if bm and "beds" not in result:
                beds = int(bm.group(1))
                if 1 <= beds <= 10:
                    result["beds"] = beds
        if query.get("price"):
            try:
                result["price"] = int(query["price"][0])
            except:
                pass
        for city in ["Sanford","Deltona","Altamonte-Springs","Lake-Mary","Longwood","DeBary","Oviedo"]:
            if city.lower().replace("-"," ") in path.lower().replace("-"," "):
                result["city"] = city.replace("-"," ")
                break

    # Redfin
    if "redfin" in url.lower():
        parts = [p for p in path.split("/") if p and p not in ("","FL","home","property","fs","fa","pid","listing")]
        for part in parts:
            if re.match(r'\d{5}', part):
                result["zip"] = part[:5]
            bm = re.search(r'(\d+)[- ]?(?:bed|br)', part, re.I)
            if bm and "beds" not in result:
                result["beds"] = int(bm.group(1))
        for city in ["Sanford","Deltona","Altamonte","Lake Mary","Longwood","DeBary","Oviedo"]:
            if city.lower() in path.lower():
                result["city"] = city
                break

    # Realtor
    if "realtor" in url.lower():
        parts = [p for p in path.split("/") if p]
        for part in parts:
            if re.match(r'\d{5}', part):
                result["zip"] = part[:5]
            bm = re.search(r'(\d+)[- ]?(?:bed|br)', part, re.I)
            if bm and "beds" not in result:
                result["beds"] = int(bm.group(1))

    return result


def _try_direct_fetch(url):
    """Try direct fetch with rotating User-Agents."""
    import urllib.request
    for ua in USER_AGENTS:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ua,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                try:
                    import gzip
                    html = gzip.decompress(raw).decode("utf-8", errors="replace")
                except:
                    html = raw.decode("utf-8", errors="replace")
                if len(html) > 5000 and any(k in html for k in ["bed","bath","sqft","price","zpid","address"]):
                    print(f"    Fetched {len(html)} bytes")
                    return html
        except Exception:
            continue
    return None


def _fetch_via_search(url):
    """Web search fallback for blocked listings."""
    import urllib.request
    parsed = urlparse(url)
    query = parsed.netloc.replace("www.","") + " " + parsed.path.replace("/"," ")
    query = re.sub(r'[^\w\s\-\.,]', ' ', query).strip()[:150]
    try:
        search_url = f"https://duckduckgo.com/html/?q={urllib.request.quote(query)}"
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL|re.I)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL|re.I)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) > 300:
            print(f"    Search got {len(text)} chars")
            return text[:5000]
    except Exception as e:
        print(f"    Search failed: {e}")
    return None


# ─── PROPERTY DATA EXTRACTORS ────────────────────────────────────────────────

def fetch_listing(url):
    """Fetch and extract listing data from HTML + URL + search fallback."""
    html = _try_direct_fetch(url)
    text = None
    if html:
        clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL|re.I)
        clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL|re.I)
        clean = re.sub(r'<noscript[^>]*>.*?</noscript>', '', clean, flags=re.DOTALL|re.I)
        clean = re.sub(r'<[^>]+>', ' ', clean)
        text = re.sub(r'\s+', ' ', clean).strip()

    url_data = _extract_from_url(url)
    print(f"    URL-extracted: price={url_data.get('price')}, beds={url_data.get('beds')}")

    domain = urlparse(url).netloc.lower()
    source = ("zillow" if "zillow" in domain else
              "redfin" if "redfin" in domain else
              "realtor" if "realtor" in domain else "unknown")

    data = {"url": url, "source": source, "fetched_at": datetime.now(timezone.utc).isoformat()}

    if text and len(text) > 500:
        html_price = extract_price(text)
        html_beds  = extract_beds(text)
        html_baths = extract_baths(text)
        html_sqft  = extract_sqft(text)
        html_addr  = extract_address(text, url)

        if html_price and 50000 < html_price < 2000000:
            data["price"] = html_price
        if html_beds and 1 <= html_beds <= 7:
            data["beds"] = html_beds
        if html_baths and 1 <= html_baths <= 6:
            data["baths"] = html_baths
        if html_sqft and 400 < html_sqft < 10000:
            data["sqft"] = html_sqft
        if html_addr and "pending" not in html_addr.lower() and len(html_addr) > 8:
            if "99518" not in html_addr:
                data["address"] = html_addr
        data["raw_text_sample"] = text[:3000]
    else:
        data["raw_text_sample"] = ""

    for key in ["price","beds","baths","sqft","address","city","zip"]:
        if url_data.get(key) and (key not in data or not data[key]):
            data[key] = url_data[key]

    if not data.get("price") or not data.get("address") or data["address"] == "Address pending verification":
        search_text = _fetch_via_search(url)
        if search_text:
            sp = extract_price(search_text)
            sb = extract_beds(search_text)
            sa = extract_address(search_text, url)
            if sp and 50000 < sp < 2000000 and not data.get("price"):
                data["price"] = sp
            if sb and not data.get("beds"):
                data["beds"] = sb
            if sa and "pending" not in sa.lower() and len(sa) > 10 and sa != data.get("address"):
                data["address"] = sa

    if not data.get("price"):
        return {"error": "Could not determine price", "url": url}

    data["features"]       = extract_features(data.get("raw_text_sample",""))
    data["description"]    = extract_description(data.get("raw_text_sample",""))
    data["property_type"] = extract_property_type(data.get("raw_text_sample",""))
    data["condition"]     = assess_condition(data.get("raw_text_sample",""))
    data["year_built"]     = extract_year_built(data.get("raw_text_sample",""))
    data["city"]  = data.get("city") or extract_city(data.get("raw_text_sample",""))
    data["zip"]   = data.get("zip")  or extract_zip(data.get("raw_text_sample",""))

    return data


def extract_price(text):
    for pat in [
        r'\$([1-9]\d{1,2}(?:,\d{3})*(?:\.\d{2})?)',
        r'price[^\d]*\$([1-9]\d{1,2}(?:,\d{3})*)',
        r'listed at[^\$]*\$([1-9]\d{1,2}(?:,\d{3})+)',
    ]:
        m = re.search(pat, text[:3000], re.I)
        if m:
            try:
                return int(m.group(1).replace(",","").replace("$",""))
            except:
                pass
    return None


def extract_beds(text):
    for pat in [r'(\d+)\s*(?:bed|bedroom|br|beds)\b', r'(\d+)\s*(?:bed\s*room)']:
        m = re.search(pat, text[:3000], re.I)
        if m:
            val = int(m.group(1))
            if 0 < val <= 10:
                return val
    return None


def extract_baths(text):
    for pat in [r'([\d\.]+)\s*(?:bath|ba|baths|bathroom|bathrooms)\b']:
        m = re.search(pat, text[:3000], re.I)
        if m:
            try:
                val = float(m.group(1))
                if 0 < val <= 10:
                    return val
            except:
                pass
    return None


def extract_sqft(text):
    for pat in [r'([\d,]+)\s*(?:sq\s*ft|sqft|square\s*feet)', r'([\d,]+)\s*(?:sq\.?\s*ft)']:
        m = re.search(pat, text[:3000], re.I)
        if m:
            try:
                return int(m.group(1).replace(",",""))
            except:
                pass
    return None


def extract_address(text, url):
    addr_pat = re.compile(r'\d+\s+[A-Za-z]+\s+[A-Za-z]+(?:\s+[A-Za-z]+){0,4},?\s+[A-Z]{2}\s+\d{5}', re.I)
    m = addr_pat.search(text[:3000])
    if m:
        addr = m.group(0).strip().title()
        if len(addr) > 10 and not any(z in addr for z in ['99518','00000']):
            return addr
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p and p not in ("","homedetails","property","zpid","homes","home")]
    clean_parts = [re.sub(r'_zpid/.*', '', p, flags=re.I) for p in parts]
    clean_parts = [re.sub(r'_?\d{6,}$', '', p) for p in clean_parts]
    city_kw = {'fl','sanford','deltona','altamonte','lakemary','longwood','debary','oviedo'}
    anchor_idx = None
    for i, p in enumerate(clean_parts):
        pl = p.lower().replace('-','').replace('_','')
        if any(c in pl for c in city_kw) or pl in {'fl','florida'}:
            anchor_idx = i; break
    if anchor_idx is not None:
        start = max(0, anchor_idx - 3)
        street_parts = clean_parts[start:anchor_idx]
        addr = ' '.join(street_parts).replace('-',' ').strip()
        if addr:
            return addr.title() + f", {clean_parts[anchor_idx].upper()}"
    for part in clean_parts:
        words = re.findall(r'[A-Za-z]+', part)
        if len(words) >= 2:
            return ' '.join(words).title() + ', FL'
    return "Address pending verification"


def extract_city(text):
    cities = ["Altamonte Springs","Sanford","Deltona","Lake Mary","Longwood",
              "Oviedo","Winter Springs","Casselberry","Orlando","DeBary"]
    for city in cities:
        if city.lower() in text[:5000].lower():
            return city
    m = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}),\s+FL\s+\d{5}', text[:3000])
    if m:
        return m.group(1).split(',')[0].strip()
    return None


def extract_zip(text):
    m = re.search(r'\b(\d{5})(?:-\d{4})?\b', text[:3000])
    return m.group(1) if m else None


def extract_property_type(text):
    s = text[:2000].lower()
    if any(k in s for k in ["single family","single-family","single family home"]): return "Single-Family Home"
    if any(k in s for k in ["duplex","two-unit","two unit","two family"]): return "Duplex / Two-Unit"
    if any(k in s for k in ["condo","condominium"]): return "Condo"
    if any(k in s for k in ["townhouse","townhome","town house"]): return "Townhouse"
    if any(k in s for k in ["multi-family","multi family","apartment"]): return "Multi-Family"
    return "Single-Family Home"


def extract_year_built(text):
    m = re.search(r'(?:built|year)[^\d]{0,30}(\d{4})', text[:3000], re.I)
    if m:
        yr = int(m.group(1))
        if 1900 <= yr <= 2026:
            return yr
    return None


def assess_condition(text):
    s = text[:3000].lower()
    excellent = sum(k in s for k in ["move-in ready","mint condition","like new","newly renovated","completely renovated","updated throughout","fully renovated"])
    poor = sum(k in s for k in ["fixer","handyman special","as-is","needs work","tlc","cash only","rehab","do not disturb","tenant occupied","poor condition","major repairs"])
    if excellent >= 2: return "Excellent"
    if poor >= 1: return "Poor / Needs Rehab"
    m = re.search(r'condition[^\w]{0,20}(\w+)', s)
    if m:
        cond = m.group(1).lower()
        if cond in ["good","fair","excellent","poor"]:
            return cond.title()
    return "Fair"


def extract_features(text):
    s = text[:5000].lower()
    features = []
    fm = {
        "pool": ["pool","swimming pool"], "garage": ["garage","carport","covered parking"],
        "fenced yard": ["fenced","fence","fence yard"], "central ac": ["central air","central a/c","hvac"],
        "new roof": ["new roof","roof replaced","roof < 5 year"],
        "updated kitchen": ["updated kitchen","new appliances","stainless","granite counters"],
        "hardwood floors": ["hardwood","wood floors","hardwood floors","laminate"],
        "updated bath": ["updated bath","remodeled bath","new bath"],
        "waterfront": ["waterfront","on the water","lake view","canal"],
    }
    for feat, kws in fm.items():
        if any(kw in s for kw in kws):
            features.append(feat)
    if "hoa" not in s or "no hoa" in s:
        features.append("no hoa")
    return features


def extract_description(text):
    s = text[:5000]
    s = re.sub(r'(?:advertisement|api|mls|listing|date|price history|tax|schools?).*', '', s, flags=re.I)
    s = re.sub(r'\s+', ' ', s).strip()
    start = s.find('. ')
    if start > 0 and start < 500:
        s = s[start+2:]
    return s[:500].strip() + "..." if len(s) > 500 else s.strip()


# ─── CASH FLOW ENGINE ─────────────────────────────────────────────────────────

def estimate_rent(prop):
    beds = prop.get("beds") or 2
    sqft = prop.get("sqft")
    city = prop.get("city") or "Deltona"
    cond = prop.get("condition", "Fair")
    feats = prop.get("features", [])
    base = {1:1100, 2:1500, 3:1850, 4:2200, 5:2600}
    rent = base.get(beds, 1500)
    if sqft:
        rent += 150 if sqft > 1800 else 0
        rent += 250 if sqft > 2200 else 0
        rent -= 100 if sqft < 900 else 0
    if city in ["Lake Mary","Altamonte Springs","Longwood"]: rent *= 1.1
    elif city in ["Deltona","DeBary"]: rent *= 0.95
    if cond == "Excellent": rent = max(rent * 1.1, rent + 150)
    elif cond == "Poor / Needs Rehab": rent = min(rent * 0.85, rent - 100)
    if "pool" in feats: rent += 150
    if "garage" in feats: rent += 75
    if "updated kitchen" in feats: rent += 100
    if "central ac" in feats and cond == "Fair": rent += 50
    return int(round(rent / 25) * 25)


def estimate_insurance(prop):
    price = prop.get("price") or 150000
    beds  = prop.get("beds") or 2
    city  = prop.get("city") or "Deltona"
    feats = prop.get("features", [])
    year  = prop.get("year_built")
    premium = 7200 * (price / 150000)
    if year and year < 1990: premium *= 1.15
    elif year and year < 1980: premium *= 1.25
    if "pool" in feats: premium *= 1.25
    if "new roof" in feats: premium *= 0.90
    if city in ["Sanford","Deltona"]: premium *= 1.05
    if beds >= 4: premium *= 1.10
    return int(round(premium / 50) * 50)


def estimate_property_tax(prop):
    price = prop.get("price") or 150000
    city  = prop.get("city") or "Deltona"
    rates = {"Altamonte Springs":0.0085,"Lake Mary":0.0085,"Longwood":0.0085,
             "Sanford":0.0071,"Deltona":0.0085,"DeBary":0.0085}
    return int(round(price * rates.get(city, 0.0085) / 100) * 100)


def monthly_payment(principal, annual_rate=INTEREST_RATE, months=LOAN_TERM_MONTHS):
    """Standard amortization formula."""
    if principal <= 0:
        return 0.0
    r = annual_rate / 12
    return round(principal * (r * (1 + r)**months) / ((1 + r)**months - 1), 2)


def calc_cash_flow(prop, down_payment=None):
    """
    Calculate cash flow at a given down payment.
    Also computes the 'required down payment' to hit TARGET_CF_PCT (20%) over expenses.
    """
    price = prop.get("price")
    if not price or price < 10000:
        return {"error": "Cannot analyze: price not found"}

    dp = down_payment if down_payment is not None else DEFAULT_DOWN_PAYMENT
    loan_amount = max(0, price - dp)
    mortgage    = monthly_payment(loan_amount)
    annual_ins  = estimate_insurance(prop)
    monthly_ins = round(annual_ins / 12)
    annual_tax  = estimate_property_tax(prop)
    monthly_tax = round(annual_tax / 12)

    monthly_rent     = estimate_rent(prop)
    monthly_vacancy  = round(monthly_rent * VACANCY_RATE, -2)
    monthly_maint    = round(monthly_rent * MAINTENANCE_RATE, -2)

    total_expenses = round(mortgage + monthly_tax + monthly_ins + monthly_vacancy + monthly_maint, -1)
    monthly_cf = monthly_rent - total_expenses

    # Required down payment to hit 20% CF over expenses
    # Target: CF = TARGET_CF_PCT * total_expenses  =>  rent = 1.20 * total_expenses
    # => mortgage = 1.20 * (mortgage + non_mort) - non_mort  =  0.20 * non_mort
    non_mort = monthly_tax + monthly_ins + monthly_vacancy + monthly_maint
    target_cf = round(total_expenses * TARGET_CF_PCT, -1)
    target_mortgage = monthly_rent - non_mort - target_cf

    required_dp = None
    if target_mortgage > 0:
        # Solve for loan amount that gives target_mortgage payment
        r = INTEREST_RATE / 12
        n = LOAN_TERM_MONTHS
        numerator   = target_mortgage * ((1 + r)**n - 1)
        denominator = r * (1 + r)**n
        required_loan = numerator / denominator if denominator > 0 else 0
        required_dp = round(price - required_loan, -3)
        # Can't require negative down payment
        if required_dp < 0:
            required_dp = 0
    else:
        # Even at 100% down (no mortgage), can't hit target CF
        required_dp = None

    coc = round((monthly_cf * 12 / dp) * 100, 1) if dp > 0 else 0

    # Build line-item notes
    positives, negatives = [], []

    beds  = prop.get("beds") or 2
    sqft  = prop.get("sqft")
    cond  = prop.get("condition", "Fair")
    city  = prop.get("city")
    feats = prop.get("features", [])

    if beds >= 3:
        positives.append(f"{beds}BR/${monthly_rent}/mo — strong family rental demand")
    else:
        positives.append(f"{beds}BR at ${monthly_rent}/mo — market rate for area")

    if price < 150000:
        positives.append(f"Purchase price ${price:,} — below-market for FL corridor")
    elif price > 220000:
        negatives.append(f"Purchase price ${price:,} — expensive for cash flow math")

    if cond == "Excellent":
        positives.append("Move-in ready — no rehab costs, rents immediately")
    elif cond == "Poor / Needs Rehab":
        negatives.append("Needs rehab — timeline to rent + potential overage costs")

    if sqft:
        if sqft >= 1400: positives.append(f"{sqft:,} sqft — commands premium rent")
        elif sqft < 1000: negatives.append(f"{sqft:,} sqft — small, limits tenant pool")

    if "no hoa" in feats: positives.append("No HOA — keeps monthly costs predictable")
    if "garage" in feats: positives.append("Garage/carport — desirable in FL heat")
    if "central ac" in feats: positives.append("Central A/C — essential for FL rental")
    if "new roof" in feats: positives.append("New roof — reduces insurance risk")
    if "pool" in feats: negatives.append("Pool — adds insurance cost and maintenance burden")
    if "updated kitchen" in feats: positives.append("Updated kitchen — justifies higher rent")
    if "updated bath" in feats: positives.append("Updated bath — reduces turnover risk")

    if city:
        if city in ["Deltona","DeBary"]:
            positives.append(f"{city} — lower entry price point, better CF math")
        elif city in ["Lake Mary","Altamonte Springs"]:
            positives.append(f"{city} — strong rental market, good tenant pool")

    if annual_ins > 8400:
        negatives.append(f"Annual insurance ~${annual_ins:,} — FL reality is brutal on this property")
    elif annual_ins < 6000:
        positives.append(f"Annual insurance ~${annual_ins:,} — better than typical FL")

    if monthly_cf > 0:
        positives.append(f"✅ Positive CF of ${monthly_cf}/mo = ${monthly_cf*12}/yr at ${dp:,} down")
    else:
        negatives.append(f"❌ Negative CF of ${abs(monthly_cf)}/mo at ${dp:,} down — ${abs(monthly_cf)*12}/yr drag")

    if required_dp is not None:
        if required_dp <= dp:
            gap = dp - required_dp
            positives.append(f"✅ Already above required down payment (${dp:,} vs ${required_dp:,} min)")
        else:
            negatives.append(f"Need ~${required_dp:,} down to hit 20% CF target (+${required_dp - dp:,} more)")
    else:
        negatives.append("20% CF target not achievable at any down payment — price likely too high")

    return {
        "price": price,
        "down_payment": dp,
        "loan_amount": loan_amount,
        "interest_rate": INTEREST_RATE * 100,
        "loan_term_years": int(LOAN_TERM_MONTHS / 12),
        "mortgage_payment": mortgage,
        "monthly_tax": monthly_tax,
        "annual_tax": annual_tax,
        "annual_insurance": annual_ins,
        "monthly_insurance": monthly_ins,
        "monthly_maintenance": monthly_maint,
        "monthly_vacancy": monthly_vacancy,
        "monthly_rent_estimate": monthly_rent,
        "total_monthly_expenses": total_expenses,
        "monthly_cash_flow": monthly_cf,
        "cash_on_cash_return_pct": coc,
        "required_down_payment_for_20pct_cf": required_dp,
        "breakdown": {"positives": positives, "negatives": negatives}
    }


def score_property(prop, cf):
    """Score overall deal quality."""
    if "error" in cf:
        return {"cash_flow_score": 0, "deal_quality_score": 0, "overall_score": 0}
    coc  = cf.get("cash_on_cash_return_pct", 0)
    cfv  = cf.get("monthly_cash_flow", 0)
    req_dp = cf.get("required_down_payment_for_20pct_cf")
    price = prop.get("price", 999999)
    cond = prop.get("condition", "Fair")
    feats = prop.get("features", [])

    # CF score: 12% CoC = 80pts, 18%+ = 100pts
    cf_score = min(100, max(0, (coc / 0.18) * 100)) if coc > 0 else max(0, 100 + coc * 5)

    dq = 50
    if price < 160000: dq += 15
    elif price < 200000: dq += 5
    elif price > 250000: dq -= 20
    if cond == "Excellent": dq += 15
    elif cond == "Poor / Needs Rehab": dq -= 10
    if "no hoa" in feats: dq += 5
    if "new roof" in feats: dq += 5
    if "pool" in feats: dq -= 5
    # Down payment achievability
    if req_dp is not None and req_dp <= DEFAULT_DOWN_PAYMENT:
        dq += 10  # bonus: achievable at our standard $125K
    elif req_dp is not None and req_dp > DEFAULT_DOWN_PAYMENT:
        dq -= 10  # penalty: requires more than our $125K standard
    if cfv < 0: dq = max(0, dq - abs(cfv) / 20)
    dq = max(0, min(100, dq))

    overall = round(cf_score * 0.55 + dq * 0.45, 1)
    return {"cash_flow_score": round(cf_score, 1), "deal_quality_score": round(dq, 1), "overall_score": overall}


def determine_verdict(prop, cf, scores):
    if "error" in cf:
        return "SKIP", "Could not calculate — missing property data"
    coc    = cf.get("cash_on_cash_return_pct", 0)
    cf_val = cf.get("monthly_cash_flow", 0)
    req_dp = cf.get("required_down_payment_for_20pct_cf")
    overall = scores.get("overall_score", 0)
    price   = prop.get("price", 999999)

    if overall >= 75 and cf_val > 0:
        return "BUY", "Strong overall score and positive cash flow at $125K down"
    if overall >= 60 and cf_val > -100:
        return "CONDITIONAL", "Decent deal at $125K down — negotiate price down if possible"
    if overall >= 55 and coc >= 8:
        return "CONDITIONAL", "Acceptable return at $125K down"
    if req_dp is not None and req_dp <= DEFAULT_DOWN_PAYMENT and cf_val > 0:
        return "CONDITIONAL", f"Can hit 20% CF target at ${req_dp:,} down — within our $125K standard"
    if overall >= 50 and price < 150000:
        return "CONDITIONAL", "Low entry price compensates for CF challenges"
    return "SKIP", "Cash flow math doesn't work at $125K down — price too high for the corridor"


def analyze_property(url, down_payment=None):
    dp = down_payment if down_payment is not None else DEFAULT_DOWN_PAYMENT
    print(f"  Fetching: {url}")
    prop = fetch_listing(url)
    if "error" in prop:
        return {"status": "error", "url": url, "error": prop["error"]}

    cf     = calc_cash_flow(prop, dp)
    scores = score_property(prop, cf)
    verdict, verdict_reason = determine_verdict(prop, cf, scores)

    result = {
        "status": "ok",
        "property": {
            "id": abs(hash(url)) % (10**9),
            "url": url,
            "source":     prop.get("source","unknown"),
            "address":    prop.get("address","Unknown"),
            "city":       prop.get("city"),
            "zip":        prop.get("zip"),
            "price":      prop.get("price"),
            "beds":       prop.get("beds"),
            "baths":      prop.get("baths"),
            "sqft":       prop.get("sqft"),
            "lot_size":   prop.get("lot_size"),
            "property_type": prop.get("property_type"),
            "year_built": prop.get("year_built"),
            "condition":  prop.get("condition"),
            "features":   prop.get("features", []),
            "description": prop.get("description",""),
            "mls":        prop.get("mls"),
            "monthly_rent_estimate": cf.get("monthly_rent_estimate"),
            "down_payment_used": dp,
            "cash_flow":  cf,
            "scores":     scores,
            "verdict":    verdict,
            "verdict_reason": verdict_reason,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "status":     "active"
        }
    }
    return result
