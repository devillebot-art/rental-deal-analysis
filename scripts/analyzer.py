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

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def _extract_from_url(url):
    """Extract property data from URL structure — most reliable method for Zillow/Redfin."""
    import urllib.parse
    parsed = urlparse(url)
    path = parsed.path
    query = urllib.parse.parse_qs(parsed.query)

    result = {}

    # ── Zillow URL patterns ──
    # /homedetails/123-Main-St_City_FL_32771_zpid/123456789/
    if "zillow" in url.lower():
        parts = [p for p in path.split("/") if p and p not in ("", "homedetails", "homes", "listing")]
        for part in parts:
            # Check for zip code (5 digits, in context as zip not zpid prefix)
            zm = re.search(r'\b(\d{5})\b', part)
            if zm:
                z = zm.group(1)
                # Skip if it's part of a zpid (6+ digit number)
                full_num = re.search(r'(\d{6,})', part)
                if full_num and z in full_num.group(0):
                    pass  # skip — it's part of zpid, not a zip
                elif z != '00000' and z != '12345' and not z.startswith('0'):
                    result["zip"] = z
            # Check for beds — pattern: /3-bed/ or /3br/ etc
            bm = re.search(r'(\d+)[- ]?(?:bed|br|bedroom)', part, re.I)
            if bm and "beds" not in result:
                beds = int(bm.group(1))
                if 1 <= beds <= 10:
                    result["beds"] = beds
            # Check for price hint in query string
        if query.get("price"):
            try:
                result["price"] = int(query["price"][0])
            except:
                pass
        # City from URL
        for city in ["Sanford", "Deltona", "Altamonte-Springs", "Lake-Mary", "Longwood", "DeBary", "Oviedo"]:
            if city.lower().replace("-", " ") in path.lower().replace("-", " "):
                result["city"] = city.replace("-", " ")
                break

    # ── Redfin URL pattern ──
    # /FL/City/property-id or /FL/City/street-address/property-id
    if "redfin" in url.lower():
        parts = [p for p in path.split("/") if p and p not in ("", "FL", "home", "property", "fs", "fa", "pid", "listing")]
        for part in parts:
            # Zip
            if re.match(r'\d{5}', part):
                result["zip"] = part[:5]
            # Beds from URL segments like /3-bed/
            bm = re.search(r'(\d+)[- ]?(?:bed|br)', part, re.I)
            if bm and "beds" not in result:
                result["beds"] = int(bm.group(1))
        # Extract city
        for city in ["Sanford", "Deltona", "Altamonte", "Lake Mary", "Longwood", "DeBary", "Oviedo"]:
            if city.lower() in path.lower():
                result["city"] = city
                break

    # ── Realtor URL pattern ──
    if "realtor" in url.lower():
        parts = [p for p in path.split("/") if p]
        for part in parts:
            if re.match(r'\d{5}', part):
                result["zip"] = part[:5]
            bm = re.search(r'(\d+)[- ]?(?:bed|br)', part, re.I)
            if bm and "beds" not in result:
                result["beds"] = int(bm.group(1))

    return result


def _fetch_via_search(url):
    """Use web search to get property details when direct fetch is blocked."""
    import urllib.request
    parsed = urlparse(url)
    query = parsed.netloc.replace("www.", "") + " " + parsed.path.replace("/", " ")
    query = re.sub(r'[^\w\s\-\.,]', ' ', query).strip()[:150]
    try:
        search_url = f"https://duckduckgo.com/html/?q={urllib.request.quote(query)}"
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.I)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.I)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) > 300:
            print(f"    Search got {len(text)} chars")
            return text[:5000]
    except Exception as e:
        print(f"    Search failed: {e}")
    return None


# ─── PROPERTY DATA EXTRACTORS ────────────────────────────────────────────────

def _try_direct_fetch(url):
    """Try to directly fetch listing HTML with rotating User-Agents."""
    import urllib.request

    user_agents = [
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ]

    for ua in user_agents:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ua,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                # Handle gzip compression
                try:
                    import gzip
                    html = gzip.decompress(raw).decode("utf-8", errors="replace")
                except:
                    html = raw.decode("utf-8", errors="replace")

            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.I)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.I)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()

            # Sanity check — should have meaningful text
            if len(text) > 200 and any(k in text for k in ["bed", "bath", "sqft", "price", "$", "for sale", "address"]):
                print(f"    Fetched {len(html)} bytes with UA: {ua[:40]}...")
                return text[:8000]
        except Exception as e:
            continue
    return None


def _fetch_via_search(url):
    """Use web search to get property details when direct fetch is blocked."""
    import urllib.request

    # Extract search query from URL
    parsed = urlparse(url)
    path = parsed.path
    # e.g. /homedetails/123_Main_St_Sanford_FL_32771_zpid/ -> extract address
    address_hint = ""
    if "/homedetails/" in url or "/property/" in url:
        parts = [p for p in path.split("/") if p and p not in ["homedetails", "property", "zpid"]]
        if parts:
            address_hint = parts[0].replace("-", " ") + " "

    # Also try domain-specific extraction
    query = address_hint
    if not query:
        query = parsed.netloc.replace("www.", "") + " " + path.replace("/", " ")

    # Search for the listing
    search_query = f'{query} {url} price beds baths sqft'
    search_query = re.sub(r'[^\w\s\-\.,]', ' ', search_query).strip()[:200]

    try:
        # Use DuckDuckGo HTML (no API key needed)
        search_url = f"https://html.duckduckgo.com/html/?q={urllib.request.quote(search_query)}"
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Extract result snippets
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.I)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.I)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        if len(text) > 300:
            print(f"    Web search got {len(text)} chars of text")
            return text[:6000]
    except Exception as e:
        print(f"    Web search failed: {e}")
    return None


# ─── PROPERTY DATA EXTRACTORS ────────────────────────────────────────────────

def fetch_listing(url):
    """Fetch a property listing page. Extracts data from HTML + URL + web search."""
    import urllib.request

    # ── Step 1: Try direct fetch with rotating User-Agents ──
    html = None
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
                if len(html) > 5000 and any(k in html for k in ["bed", "bath", "sqft", "price", "zpid", "address"]):
                    print(f"    Fetched {len(html)} bytes")
                    break
                html = None
        except Exception:
            continue

    text = None
    if html:
        clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.I)
        clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.I)
        clean = re.sub(r'<noscript[^>]*>.*?</noscript>', '', clean, flags=re.DOTALL | re.I)
        clean = re.sub(r'<[^>]+>', ' ', clean)
        text = re.sub(r'\s+', ' ', clean).strip()

    # ── Step 2: Extract data from URL structure (most reliable for Zillow/Redfin) ──
    url_data = _extract_from_url(url)
    print(f"    URL-extracted: price={url_data.get('price')}, beds={url_data.get('beds')}, baths={url_data.get('baths')}")

    # ── Step 3: If URL had beds/price, use that as primary (avoid search garbage) ──
    domain = urlparse(url).netloc.lower()
    source = "zillow" if "zillow" in domain else \
             "redfin" if "redfin" in domain else \
             "realtor" if "realtor" in domain else "unknown"

    data = {
        "url": url,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    if text and len(text) > 500:
        # ── Try HTML extraction ──
        html_price = extract_price(text)
        html_beds = extract_beds(text)
        html_baths = extract_baths(text)
        html_sqft = extract_sqft(text)
        html_address = extract_address(text, url)

        # Validate HTML data (reject obvious search page results)
        if html_price and 50000 < html_price < 2000000:
            data["price"] = html_price
        if html_beds and 1 <= html_beds <= 7:
            data["beds"] = html_beds
        if html_baths and 1 <= html_baths <= 6:
            data["baths"] = html_baths
        if html_sqft and 400 < html_sqft < 10000:
            data["sqft"] = html_sqft
        if html_address and "pending" not in html_address.lower() and len(html_address) > 8:
            if "99518" not in html_address:  # reject obvious wrong address
                data["address"] = html_address

        data["raw_text_sample"] = text[:3000]
    else:
        data["raw_text_sample"] = ""

    # Apply URL-extracted data as overrides (URL is often more reliable)
    for key in ["price", "beds", "baths", "sqft", "address", "city", "zip"]:
        if url_data.get(key) and (key not in data or not data[key]):
            data[key] = url_data[key]

    # ── Step 4: Try web search for remaining gaps ──
    if not data.get("price") or not data.get("address") or data.get("address") == "Address pending verification":
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

    data["features"] = extract_features(data.get("raw_text_sample", ""))
    data["description"] = extract_description(data.get("raw_text_sample", ""))
    data["property_type"] = extract_property_type(data.get("raw_text_sample", ""))
    data["condition"] = assess_condition(data.get("raw_text_sample", ""))
    data["year_built"] = extract_year_built(data.get("raw_text_sample", ""))
    data["city"] = data.get("city") or extract_city(data.get("raw_text_sample", ""))
    data["zip"] = data.get("zip") or extract_zip(data.get("raw_text_sample", ""))

    return data
    """Fetch a property listing page and extract structured data.
    Falls back to web search if direct fetch is blocked."""
    import urllib.request

    # ── Step 1: Try direct fetch ──
    text = _try_direct_fetch(url)

    # ── Step 2: Web search fallback ──
    if not text:
        print(f"    Direct fetch blocked, trying web search...")
        text = _fetch_via_search(url)

    if not text:
        return {"error": "Could not fetch listing data", "url": url}

    domain = urlparse(url).netloc.lower()
    source = "zillow" if "zillow" in domain else \
             "redfin" if "redfin" in domain else \
             "realtor" if "realtor" in domain else "unknown"

    data = {
        "url": url,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "raw_text_sample": text[:5000],
    }

    # ── Price ──
    price = extract_price(text)
    data["price"] = price

    # ── Beds / Baths ──
    data["beds"] = extract_beds(text)
    data["baths"] = extract_baths(text)
    data["sqft"] = extract_sqft(text)
    data["lot_size"] = extract_lot_size(text)

    # ── Address / Location ──
    data["address"] = extract_address(text, url)
    data["city"] = extract_city(text)
    data["zip"] = extract_zip(text)

    # ── Property details ──
    data["property_type"] = extract_property_type(text)
    data["year_built"] = extract_year_built(text)
    data["condition"] = assess_condition(text)
    data["features"] = extract_features(text)
    data["description"] = extract_description(text)

    # ── MLS / Listing ID ──
    data["mls"] = extract_mls(text)

    return data


def extract_price(text):
    """Extract listing price."""
    patterns = [
        r'\$([1-9]\d{1,2}(?:,\d{3})*(?:\.\d{2})?)',
        r'price[^\d]*\$([1-9]\d{1,2}(?:,\d{3})*)',
        r'listed at[^\$]*\$([1-9]\d{1,2}(?:,\d{3})+)',
    ]
    for pat in patterns:
        m = re.search(pat, text[:3000], re.I)
        if m:
            try:
                return int(m.group(1).replace(",", "").replace("$", ""))
            except:
                pass
    return None


def extract_beds(text):
    """Extract number of bedrooms."""
    patterns = [
        r'(\d+)\s*(?:bed|bedroom|br|beds)\b',
        r'(\d+)\s*(?:bed\s*room)',
        r'bedrooms?[^\d]{0,20}(\d+)',
    ]
    for pat in patterns:
        m = re.search(pat, text[:3000], re.I)
        if m:
            val = int(m.group(1))
            if 0 < val <= 10:
                return val
    return None


def extract_baths(text):
    """Extract number of bathrooms."""
    patterns = [
        r'([\d\.]+)\s*(?:bath|ba|baths|bathroom|bathrooms)\b',
        r'full bath[^\d]{0,20}(\d+)',
    ]
    for pat in patterns:
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
    """Extract square footage."""
    patterns = [
        r'([\d,]+)\s*(?:sq\s*ft|sqft|square\s*feet)',
        r'size[^\d]{0,20}([\d,]+)\s*(?:sq\s*ft|sqft)',
        r'([\d,]+)\s*(?:sq\.?\s*ft)',
        r'living\s*area[^\d]{0,20}([\d,]+)',
    ]
    for pat in patterns:
        m = re.search(pat, text[:3000], re.I)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except:
                pass
    return None


def extract_lot_size(text):
    """Extract lot size in acres."""
    patterns = [
        r'([\d\.]+)\s*(?:acre|acres|ac)\b',
        r'lot[^\d]{0,20}([\d\.]+)\s*(?:acre|acres)',
        r'([\d,]+)\s*(?:sq\s*ft|lot)\b.*?(\d+\.?\d*)\s*acre',
    ]
    for pat in patterns:
        m = re.search(pat, text[:3000], re.I)
        if m:
            try:
                acres = float(m.group(1))
                if 0.01 < acres < 100:
                    return acres
            except:
                pass
    return None


def extract_address(text, url):
    """Extract street address from page text or URL path."""
    # Try text first
    addr_pat = re.compile(r'\d+\s+[A-Za-z]+\s+[A-Za-z]+(?:\s+[A-Za-z]+){0,4},?\s+[A-Z]{2}\s+\d{5}', re.I)
    m = addr_pat.search(text[:3000])
    if m:
        addr = m.group(0).strip().title()
        if len(addr) > 10 and not any(z in addr for z in ['99518', '00000']):
            return addr
    # Parse URL path
    path = urlparse(url).path
    parts = [p for p in path.split('/') if p and p not in ('', 'homedetails', 'property', 'zpid', 'homes', 'home')]
    # Remove zpid suffix from any part
    clean_parts = [re.sub(r'_zpid/.*', '', p, flags=re.I) for p in parts]
    # Remove trailing numeric IDs
    clean_parts = [re.sub(r'_?\d{6,}$', '', p) for p in clean_parts]
    # Find city/state anchor
    city_kw = {'fl', 'sanford', 'deltona', 'altamonte', 'lakemary', 'longwood', 'debary', 'oviedo'}
    FL_kw = {'fl', 'florida'}
    anchor_idx = None
    for i, p in enumerate(clean_parts):
        pl = p.lower().replace('-', '').replace('_', '')
        if any(c in pl for c in city_kw) or any(f in pl for f in FL_kw):
            anchor_idx = i
            break
    if anchor_idx is not None:
        # Get up to 3 segments before anchor (street address)
        start = max(0, anchor_idx - 3)
        street_parts = clean_parts[start:anchor_idx]
        addr = ' '.join(street_parts).replace('-', ' ').strip()
        if addr:
            return addr.title() + f", {clean_parts[anchor_idx].upper()}"
    # Fallback to street-like pattern
    for part in clean_parts:
        words = re.findall(r'[A-Za-z]+', part)
        if len(words) >= 2:
            return ' '.join(words).title() + ', FL'
    return "Address pending verification"


def extract_city(text):
    """Extract city name."""
    cities = ["Altamonte Springs", "Sanford", "Deltona", "Lake Mary", "Longwood",
              "Oviedo", "Winter Springs", "Casselberry", "Orlando", "DeBary"]
    for city in cities:
        if city.lower() in text[:5000].lower():
            return city
    m = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}),\s+FL\s+\d{5}', text[:3000])
    if m:
        return m.group(1).split(',')[0].strip()
    return None


def extract_zip(text):
    """Extract ZIP code."""
    m = re.search(r'\b(\d{5})(?:-\d{4})?\b', text[:3000])
    return m.group(1) if m else None


def extract_property_type(text):
    """Detect property type."""
    if any(k in text.lower()[:2000] for k in ["single family", "single-family", "single family home"]):
        return "Single-Family Home"
    if any(k in text.lower()[:2000] for k in ["duplex", "two-unit", "two unit", "two family"]):
        return "Duplex / Two-Unit"
    if any(k in text.lower()[:2000] for k in ["condo", "condominium"]):
        return "Condo"
    if any(k in text.lower()[:2000] for k in ["townhouse", "townhome", "town house"]):
        return "Townhouse"
    if any(k in text.lower()[:2000] for k in ["multi-family", "multi family", "apartment"]):
        return "Multi-Family"
    return "Single-Family Home"


def extract_year_built(text):
    """Extract year built."""
    m = re.search(r'(?:built|year)[^\d]{0,30}(\d{4})', text[:3000], re.I)
    if m:
        yr = int(m.group(1))
        if 1900 <= yr <= 2026:
            return yr
    return None


def assess_condition(text):
    """Assess property condition from text clues."""
    s = text[:3000].lower()
    excellent = sum([k in s for k in [
        "move-in ready", "mint condition", "like new", "newly renovated",
        "completely renovated", "updated throughout", "fully renovated"
    ]])
    poor = sum([k in s for k in [
        "fixer", "handyman special", "as-is", "needs work", "tlc",
        "cash only", "rehab", "do not disturb", "tenant occupied",
        "poor condition", "major repairs"
    ]])
    if excellent >= 2:
        return "Excellent"
    if poor >= 1:
        return "Poor / Needs Rehab"
    m = re.search(r'condition[^\w]{0,20}(\w+)', s)
    if m:
        cond = m.group(1).lower()
        if cond in ["good", "fair", "excellent", "poor"]:
            return cond.title()
    return "Fair"


def extract_features(text):
    """Extract notable features/amenities."""
    s = text[:5000].lower()
    features = []
    feature_map = {
        "pool": ["pool", "swimming pool"],
        "garage": ["garage", "carport", "covered parking"],
        "fenced yard": ["fenced", "fence", "fence yard"],
        "central ac": ["central air", "central a/c", "hvac", "ac system"],
        "new roof": ["new roof", "roof replaced", "roof < 5 year"],
        "updated kitchen": ["updated kitchen", "new appliances", "stainless", "granite counters"],
        "hardwood floors": ["hardwood", "wood floors", "hardwood floors", "laminate"],
        "updated bath": ["updated bath", "remodeled bath", "new bath"],
        "waterfront": ["waterfront", "on the water", "lake view", "canal"],
        "hoa": ["hoa", "homeowners association"],
        "no hoa": ["no hoa", "no hoa fee", "without hoa"],
    }
    for feature, keywords in feature_map.items():
        if any(kw in s for kw in keywords):
            features.append(feature)
    return features


def extract_description(text):
    """Extract listing description."""
    # Find the longest coherent text block
    s = text[:5000]
    # Remove footer-like noise
    s = re.sub(r'(?:advertisement|api|mls|listing|date|price history|tax|schools?).*', '', s, flags=re.I)
    s = re.sub(r'\s+', ' ', s).strip()
    # Get a meaningful chunk
    start = s.find('. ')
    if start > 0 and start < 500:
        s = s[start+2:]
    return s[:500].strip() + "..." if len(s) > 500 else s.strip()


def extract_mls(text):
    """Extract MLS number."""
    m = re.search(r'(?:mls|listing\s*#|listing\s*id)[^\d]{0,10}(\d{6,10})', text[:3000], re.I)
    return m.group(1) if m else None


# ─── CASH FLOW ANALYSIS ───────────────────────────────────────────────────────

def estimate_rent(prop):
    """Estimate monthly rent based on property specifics."""
    beds = prop.get("beds") or 2
    sqft = prop.get("sqft")
    city = prop.get("city") or "Deltona"
    condition = prop.get("condition", "Fair")
    features = prop.get("features", [])
    price = prop.get("price") or 150000

    # FL market rent estimates by beds + sqft
    base = {1: 1100, 2: 1500, 3: 1850, 4: 2200, 5: 2600}
    rent = base.get(beds, 1500)

    # Size adjustment
    if sqft:
        if sqft > 1800:
            rent += 150
        elif sqft > 2200:
            rent += 250
        elif sqft < 900:
            rent -= 100

    # City adjustment (Deltona/Sanford cheaper, Lake Mary/Altamonte more expensive)
    if city in ["Lake Mary", "Altamonte Springs", "Longwood"]:
        rent *= 1.1
    elif city in ["Deltona", "DeBary"]:
        rent *= 0.95

    # Condition adjustment
    if condition == "Excellent":
        rent = max(rent * 1.1, rent + 150)
    elif condition == "Poor / Needs Rehab":
        rent = min(rent * 0.85, rent - 100)

    # Feature bonuses
    if "pool" in features:
        rent += 150
    if "garage" in features:
        rent += 75
    if "updated kitchen" in features:
        rent += 100
    if "central ac" in features and condition == "Fair":
        rent += 50  # working AC is baseline

    return int(round(rent / 25) * 25)  # round to nearest $25


def estimate_insurance(prop):
    """Estimate annual insurance premium based on property."""
    price = prop.get("price") or 150000
    beds = prop.get("beds") or 2
    city = prop.get("city") or "Deltona"
    features = prop.get("features", [])
    year = prop.get("year_built")

    # FL insurance is expensive — base is roughly $700/mo for $150K home
    base_premium = 7200  # annual

    # Price-based scaling
    premium = base_premium * (price / 150000)

    # Age adjustment (older homes can be higher)
    if year and year < 1990:
        premium *= 1.15
    elif year and year < 1980:
        premium *= 1.25

    # Pool adds significant cost in FL
    if "pool" in features:
        premium *= 1.25

    # New roof credit
    if "new roof" in features:
        premium *= 0.9

    # City (some counties charge more)
    if city in ["Sanford", "Deltona"]:
        premium *= 1.05

    # Bed count
    if beds >= 4:
        premium *= 1.1

    return int(round(premium / 50) * 50)


def estimate_property_tax(prop):
    """Estimate annual property tax."""
    price = prop.get("price") or 150000
    city = prop.get("city") or "Deltona"

    # FL property tax rates vary slightly by county
    rates = {
        "Altamonte Springs": 0.0085,  # Seminole
        "Lake Mary": 0.0085,
        "Longwood": 0.0085,
        "Sanford": 0.0071,  # Seminole
        "Deltona": 0.0085,  # Volusia
        "DeBary": 0.0085,
    }
    rate = rates.get(city, 0.0085)
    return int(round(price * rate / 100) * 100)


def calc_cash_flow(prop):
    """Calculate full cash flow breakdown."""
    price = prop.get("price")
    if not price or price < 10000:
        return {"error": "Cannot analyze: price not found"}

    beds = prop.get("beds") or 2
    sqft = prop.get("sqft")

    monthly_rent = estimate_rent(prop)
    annual_insurance = estimate_insurance(prop)
    monthly_insurance = round(annual_insurance / 12)
    annual_tax = estimate_property_tax(prop)
    monthly_tax = round(annual_tax / 12)

    # Down payment 20%, loan 80%
    down_payment = round(price * 0.20, -3)
    loan_amount = price - down_payment

    # Mortgage at 7% / 30yr
    r = 0.07 / 12
    n = 360
    mortgage = round(loan_amount * (r * (1 + r)**n) / ((1 + r)**n - 1), 2)

    monthly_vacancy = round(monthly_rent * 0.08, -2)
    monthly_maintenance = round(monthly_rent * 0.10, -2)
    monthly_hoa = 0  # no HOA per criteria

    total_expenses = round(
        mortgage + monthly_tax + monthly_insurance + monthly_vacancy +
        monthly_maintenance + monthly_hoa, -1
    )
    monthly_cf = monthly_rent - total_expenses

    # Cash-on-cash return
    if down_payment > 0:
        coc = round((monthly_cf * 12 / down_payment) * 100, 1)
    else:
        coc = 0

    # Build per-line breakdown
    positives = []
    negatives = []

    # Rent
    if beds >= 3:
        positives.append(f"{beds}BR/${monthly_rent}/mo — strong family rental demand")
    else:
        positives.append(f"{beds}BR at ${monthly_rent}/mo — market rate for area")

    # Price
    if price < 150000:
        positives.append(f"Purchase price ${price:,} — below-market for FL corridor")
    elif price > 220000:
        negatives.append(f"Purchase price ${price:,} — expensive for cash flow math")
    else:
        positives.append(f"Purchase price ${price:,} — workable for the area")

    # Condition
    cond = prop.get("condition", "Fair")
    if cond == "Excellent":
        positives.append("Move-in ready — no rehab costs, rents immediately")
    elif cond == "Poor / Needs Rehab":
        negatives.append("Needs rehab — timeline to rent + potential overage costs")

    # Square footage
    if sqft:
        if sqft >= 1400:
            positives.append(f"{sqft:,} sqft — commands premium rent")
        elif sqft < 1000:
            negatives.append(f"{sqft:,} sqft — small, limits tenant pool")

    # Features
    features = prop.get("features", [])
    if "no hoa" in features or "hoa" not in features:
        positives.append("No HOA — keeps monthly costs predictable")
    if "garage" in features:
        positives.append("Garage/carport — desirable in FL heat")
    if "central ac" in features:
        positives.append("Central A/C — essential for FL rental")
    if "new roof" in features:
        positives.append("New roof — reduces insurance risk")
    if "pool" in features:
        positives.append("Pool — adds value but increases insurance/premium")
    if "updated kitchen" in features:
        positives.append("Updated kitchen — justifies higher rent")
    if "updated bath" in features:
        positives.append("Updated bath — reduces turnover risk")

    # City
    city = prop.get("city")
    if city in ["Deltona", "DeBary"]:
        positives.append(f"{city} — lower entry price point, better CF math")
    elif city in ["Lake Mary", "Altamonte Springs"]:
        positives.append(f"{city} — strong rental market, good tenant pool")

    # Insurance
    if annual_insurance > 8400:
        negatives.append(f"Annual insurance ~${annual_insurance:,} (${monthly_insurance}/mo) — FL reality is brutal")
    elif annual_insurance < 6000:
        positives.append(f"Annual insurance ~${annual_insurance:,} — better than typical FL")

    # Vacancy
    if city in ["Deltona", "Sanford"]:
        positives.append("8% vacancy allowance — reasonable for this corridor")
    else:
        negatives.append("8% vacancy reserve — be conservative for this market")

    # Cash flow summary
    if monthly_cf > 0:
        positives.append(f"✅ Positive CF of ${monthly_cf}/mo = ${monthly_cf*12}/yr")
    else:
        negatives.append(f"❌ Negative CF of ${abs(monthly_cf)}/mo — ${abs(monthly_cf*12)}/yr drag")

    return {
        "price": price,
        "down_payment_pct": 20,
        "down_payment": down_payment,
        "loan_amount": loan_amount,
        "interest_rate": 7.0,
        "loan_term_years": 30,
        "mortgage_payment": mortgage,
        "monthly_tax": monthly_tax,
        "annual_tax": annual_tax,
        "annual_insurance": annual_insurance,
        "monthly_insurance": monthly_insurance,
        "monthly_maintenance": monthly_maintenance,
        "monthly_vacancy": monthly_vacancy,
        "monthly_rent_estimate": monthly_rent,
        "total_monthly_expenses": total_expenses,
        "monthly_cash_flow": monthly_cf,
        "cash_on_cash_return_pct": coc,
        "breakdown": {
            "positives": positives,
            "negatives": negatives,
        }
    }


def score_property(prop, cf):
    """Score the overall deal."""
    if "error" in cf:
        return {"cash_flow_score": 0, "deal_quality_score": 0, "overall_score": 0}

    coc = cf.get("cash_on_cash_return_pct", 0)
    cf_val = cf.get("monthly_cash_flow", 0)
    cond = prop.get("condition", "Fair")
    price = prop.get("price", 999999)

    # Cash flow score (0-100) — 12% COC = 80, 18%+ = 100
    cf_score = min(100, max(0, (coc / 0.18) * 100)) if coc > 0 else max(0, 100 + coc * 5)

    # Deal quality
    dq = 50
    # Price point bonus
    if price < 160000: dq += 15
    elif price < 200000: dq += 5
    elif price > 250000: dq -= 20
    # Condition
    if cond == "Excellent": dq += 15
    elif cond == "Poor / Needs Rehab": dq -= 10
    # Features
    feats = prop.get("features", [])
    if "no hoa" in feats: dq += 5
    if "new roof" in feats: dq += 5
    if "pool" in feats: dq -= 5  # liability/cost
    # Negative CF penalty
    if cf_val < 0:
        dq = max(0, dq - abs(cf_val) / 20)
    dq = max(0, min(100, dq))

    overall = round(cf_score * 0.55 + dq * 0.45, 1)

    return {
        "cash_flow_score": round(cf_score, 1),
        "deal_quality_score": round(dq, 1),
        "overall_score": overall
    }


def determine_verdict(prop, cf, scores):
    """Determine BUY / CONDITIONAL / SKIP."""
    if "error" in cf:
        return "SKIP", "Could not calculate — missing property data"
    coc = cf.get("cash_on_cash_return_pct", 0)
    cf_val = cf.get("monthly_cash_flow", 0)
    overall = scores.get("overall_score", 0)

    reasons = []

    if overall >= 75 and cf_val > 0:
        verdict = "BUY"
        reasons.append("Strong overall score and positive cash flow")
    elif overall >= 60 and cf_val > -100:
        verdict = "CONDITIONAL"
        reasons.append("Decent deal if negotiated well")
    elif overall >= 55 and coc >= 8:
        verdict = "CONDITIONAL"
        reasons.append("Acceptable return at asking price")
    else:
        verdict = "SKIP"
        reasons.append("Cash flow math doesn't work at this price point")
        if cf_val < -200:
            reasons.append("Deeply negative CF — would need significant price reduction")

    return verdict, "; ".join(reasons)


def analyze_property(url):
    """Full analysis pipeline for a single URL."""
    print(f"  Fetching: {url}")
    prop = fetch_listing(url)

    if "error" in prop:
        return {"status": "error", "url": url, "error": prop["error"]}

    cf = calc_cash_flow(prop)
    scores = score_property(prop, cf)
    verdict, verdict_reason = determine_verdict(prop, cf, scores)

    result = {
        "status": "ok",
        "property": {
            "id": abs(hash(url)) % (10**9),
            "url": url,
            "source": prop.get("source", "unknown"),
            "address": prop.get("address", "Unknown"),
            "city": prop.get("city"),
            "zip": prop.get("zip"),
            "price": prop.get("price"),
            "beds": prop.get("beds"),
            "baths": prop.get("baths"),
            "sqft": prop.get("sqft"),
            "lot_size": prop.get("lot_size"),
            "property_type": prop.get("property_type"),
            "year_built": prop.get("year_built"),
            "condition": prop.get("condition"),
            "features": prop.get("features", []),
            "description": prop.get("description", ""),
            "mls": prop.get("mls"),
            "monthly_rent_estimate": cf.get("monthly_rent_estimate"),
            "cash_flow": cf,
            "scores": scores,
            "verdict": verdict,
            "verdict_reason": verdict_reason,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "status": "active"
        }
    }
    return result
