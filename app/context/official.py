"""Official, open and narrative evidence adapters (research context only).

Each ``collect_*`` function fetches with app.context.fetch.Fetcher, stores
raw evidence first, then parses with a pure ``parse_*`` function. It returns
a section dict with ``status`` (AVAILABLE / UNAVAILABLE / BLOCKED), the
``provenance`` and parsed records. A failure is recorded with its stable code;
nothing is reused from earlier runs or invented.

Evidence classes (kept separate end to end):
- OFFICIAL_FACT: government, central bank, exchange or regulator publication;
- REFERENCE_RATE: a published reference or aggregate, not a tradable quote;
- MEDIA_NARRATIVE: headlines (GDELT); attention and context, never facts
  about markets, never a signal.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from http.cookiejar import CookieJar
from pathlib import Path
from urllib import parse, request
from zoneinfo import ZoneInfo

from app.context.fetch import FetchError, provenance, store_raw

CAIRO, NEW_YORK = ZoneInfo("Africa/Cairo"), ZoneInfo("America/New_York")
EGX_BASE = "https://beta.egx.com.eg"
EGX_HEADERS = {"Accept": "application/json", "x-egx-bff-request": "1", "x-egx-bff-client": "web",
               "User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
DISCLOSURE_SECTIONS = [3, 4, 5, 6, 7, 8, 16]
LISTING_SECTIONS = [11, 12, 13]


class ParseError(ValueError):
    pass


def _fail(code, **extra):
    return {"status": "UNAVAILABLE", "reason": code, **extra}


def _decimal(text):
    try:
        value = Decimal(str(text).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise ParseError(f"not a number: {text!r}") from None
    if not value.is_finite():
        raise ParseError("non-finite")
    return value


def _acquire(fetcher, data_root, source_id, url, ext, **kwargs):
    payload, headers = fetcher.get(url, **kwargs)
    digest, _ = store_raw(data_root, source_id, payload, ext)
    return payload, provenance(source_id, url, digest, headers=headers)


# --- USD/EGP reference (ExchangeRate-API open access) -------------------------------------------

ER_API_URL = "https://open.er-api.com/v6/latest/USD"


def parse_er_api(payload):
    document = json.loads(payload)
    if document.get("result") != "success" or document.get("base_code") != "USD":
        raise ParseError("er-api result not success/USD")
    updated = datetime.fromtimestamp(int(document["time_last_update_unix"]), timezone.utc)
    rate = _decimal(document["rates"]["EGP"])
    if rate <= 0:
        raise ParseError("non-positive rate")
    return {"usd_egp": str(rate), "updated_at": updated.isoformat(), "date": updated.date().isoformat(),
            "terms": document.get("terms_of_use"), "attribution": document.get("provider")}


def collect_er_api(fetcher, data_root):
    try:
        payload, prov = _acquire(fetcher, data_root, "er_api_usd", ER_API_URL, "json")
        return {"status": "AVAILABLE", "evidence": "REFERENCE_RATE", "provenance": prov, **parse_er_api(payload)}
    except (FetchError, ParseError, KeyError, ValueError) as exc:
        return _fail(getattr(exc, "code", None) or f"PARSE:{exc}")


# --- IMF IFS: Egypt interest rates (official, lagged) --------------------------------------------

IMF_URL = "https://api.imf.org/external/sdmx/2.1/data/IMF.STA,MFS_IR/EGY..M?startPeriod={start}"
IMF_INDICATORS = {"DISR_RT_PT_A_PT": "CBE discount rate", "GSTBILY_RT_PT_A_PT": "Egypt treasury bill yield"}


def parse_imf(payload):
    root = ET.fromstring(payload)
    result = {}
    for series in root.iter():
        if not series.tag.endswith("Series"):
            continue
        indicator = series.attrib.get("INDICATOR")
        if indicator not in IMF_INDICATORS:
            continue
        observations = []
        for obs in series:
            period, value = obs.attrib.get("TIME_PERIOD"), obs.attrib.get("OBS_VALUE")
            match = re.fullmatch(r"(\d{4})-M(\d{2})", period or "")
            if not match or value in (None, ""):
                continue
            observations.append((f"{match.group(1)}-{match.group(2)}", str(_decimal(value))))
        result[indicator] = {"title": IMF_INDICATORS[indicator], "unit": "%", "frequency": "monthly",
                             "observations": sorted(observations)[-24:]}
    return result


def collect_imf_egypt(fetcher, data_root, *, today):
    url = IMF_URL.format(start=f"{today.year - 3}-01")
    try:
        payload, prov = _acquire(fetcher, data_root, "imf_ifs_egy", url, "xml")
        series = parse_imf(payload)
    except (FetchError, ParseError, ET.ParseError) as exc:
        return _fail(getattr(exc, "code", None) or f"PARSE:{exc}")
    for item in series.values():
        last = item["observations"][-1] if item["observations"] else None
        item["latest"] = last
        if last:
            year, month = map(int, last[0].split("-"))
            item["age_months"] = (today.year - year) * 12 + today.month - month
            item["freshness"] = "CURRENT" if item["age_months"] <= 2 else "STALE"
    return {"status": "AVAILABLE" if series else "UNAVAILABLE", "evidence": "OFFICIAL_FACT",
            "publisher": "IMF International Financial Statistics (reported by the Central Bank of Egypt)",
            "provenance": prov, "series": series}


# --- BIS policy rate (verification of the Fed target) ---------------------------------------------

BIS_URL = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.US?startPeriod={start}&format=csv"


def parse_bis(payload):
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    observations = [(row["TIME_PERIOD"], str(_decimal(row["OBS_VALUE"]))) for row in rows
                    if row.get("OBS_VALUE") not in (None, "", "NaN")]
    if not observations:
        raise ParseError("no BIS observations")
    return sorted(observations)


def collect_bis_us(fetcher, data_root, *, today):
    url = BIS_URL.format(start=(today - timedelta(days=60)).isoformat())
    try:
        payload, prov = _acquire(fetcher, data_root, "bis_cbpol_us", url, "csv")
        observations = parse_bis(payload)
    except (FetchError, ParseError, KeyError) as exc:
        return _fail(getattr(exc, "code", None) or f"PARSE:{exc}")
    return {"status": "AVAILABLE", "evidence": "OFFICIAL_FACT", "provenance": prov,
            "publisher": "BIS central bank policy rates (Fed target midpoint)", "latest": observations[-1]}


# --- Federal Reserve press releases (monetary policy) ---------------------------------------------

FED_PRESS_URL = "https://www.federalreserve.gov/json/ne-press.json"


def _fed_time(text):
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=NEW_YORK)
        except ValueError:
            continue
    return None


def parse_fed_press(payload, *, limit=12):
    items = json.loads(payload.decode("utf-8-sig"))
    out, seen = [], set()
    for item in items:
        if item.get("pt") != "Monetary Policy" or item.get("l") in seen:
            continue
        seen.add(item.get("l"))
        stamp = _fed_time(item.get("d") or "")
        if stamp is None:
            continue  # undatable entry: skipped, never guessed
        out.append({"published_at": stamp.isoformat(), "title": item["t"].strip(),
                    "url": "https://www.federalreserve.gov" + item["l"]})
    out.sort(key=lambda row: row["published_at"], reverse=True)
    return out[:limit]


def collect_fed_press(fetcher, data_root):
    try:
        payload, prov = _acquire(fetcher, data_root, "fed_press", FED_PRESS_URL, "json")
        releases = parse_fed_press(payload)
    except (FetchError, ParseError, KeyError, ValueError) as exc:
        return _fail(getattr(exc, "code", None) or f"PARSE:{exc}")
    return {"status": "AVAILABLE", "evidence": "OFFICIAL_FACT", "provenance": prov,
            "publisher": "Board of Governors of the Federal Reserve System", "releases": releases}


# --- EGX official disclosures and financial statements --------------------------------------------

def _strip(html):
    text = re.sub(r"<br\s*/?>", "\n", html or "", flags=re.I)
    return unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ")


def _field(text, label):
    match = re.search(rf"{label}\s*:\s*([^\n]+)", text, flags=re.I)
    return match.group(1).strip() if match else None


def _egx_time(stamp):
    return datetime.fromisoformat(stamp).replace(tzinfo=CAIRO).isoformat()


def _links(html):
    return [EGX_BASE + href if href.startswith("/") else href
            for href in re.findall(r'href="([^"]+\.pdf)"', html or "", flags=re.I)]


def parse_egx_disclosures(documents):
    out, seen = [], set()
    for document in documents:
        if not document.get("success"):
            raise ParseError("EGX feed reported failure")
        for item in document.get("data") or []:
            code = item.get("code")
            if code in seen or code is None:
                continue
            seen.add(code)
            text = _strip(item.get("content"))
            out.append({"code": code, "published_at": _egx_time(item["dateStamp"]),
                        "heading": (item.get("heading") or "").strip(),
                        "section": (item.get("section") or "").strip(), "isin": _field(text, "ISIN Code"),
                        "reuters": _field(text, "Reuters Code"), "documents": _links(item.get("content"))})
    out.sort(key=lambda row: row["published_at"], reverse=True)
    return out


PERIOD = re.compile(r"F/S\s*\(?(\w*)\)?\s*Period\s*:\s*From\s+(\d\d)/(\d\d)/(\d{4})\s+To\s+(\d\d)/(\d\d)/(\d{4})",
                    re.I)
RESULT = re.compile(r"Net\s+(Comparative\s+)?(Profit|Loss)\s*:\s*\(?(-?[\d,\.]+)\)?\s*(Value\s+In\s+\w+)?", re.I)
UNITS = {"value in thousand": Decimal(1000), "value in million": Decimal(1_000_000)}


def parse_egx_financials(documents):
    out, seen = [], set()
    for document in documents:
        if not document.get("success"):
            raise ParseError("EGX feed reported failure")
        for item in document.get("data") or []:
            code = item.get("code")
            if code in seen or code is None:
                continue
            seen.add(code)
            text = _strip(item.get("content"))
            periods, results = PERIOD.findall(text), RESULT.findall(text)
            record = {"code": code, "published_at": _egx_time(item["dateStamp"]),
                      "heading": (item.get("heading") or "").strip(), "isin": _field(text, "ISIN Code"),
                      "currency": _field(text, "Currency"), "audit_status": _field(text, "Audit Status"),
                      "documents": _links(item.get("content")), "parse_status": "PARSED"}
            current = next((r for r in results if not r[0]), None)
            comparative = next((r for r in results if r[0]), None)
            if len(periods) < 2 or current is None or comparative is None:
                record["parse_status"] = "UNPARSED_LAYOUT"
                out.append(record)
                continue

            def amount(row):
                value = _decimal(row[2]) * UNITS.get((row[3] or "").strip().lower(), Decimal(1))
                return -abs(value) if row[1].lower() == "loss" else value

            (basis, d1, m1, y1, d2, m2, y2), comp = periods[0], periods[1]
            now_value, prior_value = amount(current), amount(comparative)
            record.update(basis=basis.upper() or "UNSPECIFIED", period_start=f"{y1}-{m1}-{d1}", period_end=f"{y2}-{m2}-{d2}",
                          comparative_period_end=f"{comp[6]}-{comp[5]}-{comp[4]}",
                          net_result=str(now_value), comparative_net_result=str(prior_value),
                          unit_note=(current[3] or "").strip() or None,
                          net_result_change_pct=(str(((now_value - prior_value) / abs(prior_value) * 100)
                                                     .quantize(Decimal("0.1"))) if prior_value else None))
            out.append(record)
    out.sort(key=lambda row: row["published_at"], reverse=True)
    return out


def _egx_post(opener, endpoint, body):
    req = request.Request(f"{EGX_BASE}/api/bff/egx/{endpoint}", data=json.dumps(body).encode(),
                          headers=EGX_HEADERS, method="POST")
    with opener.open(req, timeout=30) as response:
        payload = response.read(8 * 1024 * 1024 + 1)
    if len(payload) > 8 * 1024 * 1024:
        raise ParseError("EGX response too large")
    return payload


def collect_egx(data_root, *, pages=2, opener=None):
    """EGX disclosures (official exchange feed) and financial-statement disclosures."""
    if opener is None:
        opener = request.build_opener(request.HTTPCookieProcessor(CookieJar()))
        try:
            opener.open(request.Request(EGX_BASE + "/en", headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()
        except OSError as exc:
            failure = _fail(f"NETWORK_{type(exc).__name__}")
            return failure, failure
    sections = {}
    for key, endpoint, base in (
            ("disclosures", "news-search", {"marketSessionNews": False,
                                             "secIds": DISCLOSURE_SECTIONS + LISTING_SECTIONS, "count": 50}),
            ("financials", "financial-statements-filter", {})):
        documents, provs = [], []
        try:
            for page in range(1, pages + 1):
                payload = _egx_post(opener, endpoint, {**base, "interval": 50, "pageNumber": page, "pageSize": 50})
                digest, _ = store_raw(data_root, f"egx_{key}", payload, "json")
                provs.append(provenance(f"egx_{key}", f"{EGX_BASE}/api/bff/egx/{endpoint}?page={page}", digest))
                documents.append(json.loads(payload))
            records = (parse_egx_disclosures if key == "disclosures" else parse_egx_financials)(documents)
        except (OSError, ParseError, ValueError, KeyError) as exc:
            sections[key] = _fail(getattr(exc, "code", None) or f"{type(exc).__name__}:{str(exc)[:80]}")
            continue
        sections[key] = {"status": "AVAILABLE", "evidence": "OFFICIAL_FACT", "publisher": "The Egyptian Exchange",
                         "provenance": provs, "records": records}
    return sections["disclosures"], sections["financials"]


# --- IMF PortWatch chokepoints (Suez / Red Sea / Hormuz) -----------------------------------------

PORTWATCH_URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/"
                 "FeatureServer/0/query")
CHOKEPOINTS = {"chokepoint1": "Suez Canal", "chokepoint4": "Bab el-Mandeb Strait",
               "chokepoint6": "Strait of Hormuz", "chokepoint7": "Cape of Good Hope"}


def parse_portwatch(payload, port_id):
    document = json.loads(payload)
    if "error" in document:
        raise ParseError("arcgis error")
    rows = sorted(((date.fromisoformat(f["attributes"]["date"][:10]), int(f["attributes"]["n_total"]),
                    int(f["attributes"]["n_tanker"])) for f in document.get("features", [])
                   if f["attributes"].get("portid") == port_id and f["attributes"].get("n_total") is not None))
    if not rows:
        raise ParseError("no PortWatch rows")
    latest = rows[-1][0]

    def mean_between(start, end):
        values = [n for d, n, _ in rows if start <= d <= end]
        return (round(sum(values) / len(values), 1), len(values)) if values else (None, 0)

    last7, n7 = mean_between(latest - timedelta(days=6), latest)
    last90, n90 = mean_between(latest - timedelta(days=89), latest)
    year_ago, n_ago = mean_between(latest - timedelta(days=371), latest - timedelta(days=365))
    return {"latest_date": latest.isoformat(), "latest_transits": rows[-1][1], "latest_tankers": rows[-1][2],
            "mean_7d": last7, "days_7d": n7, "mean_90d": last90, "days_90d": n90,
            "mean_7d_year_ago": year_ago, "days_year_ago": n_ago,
            "change_7d_vs_90d_pct": (round((last7 / last90 - 1) * 100, 1) if last7 is not None and last90 else None),
            "change_7d_vs_year_ago_pct": (round((last7 / year_ago - 1) * 100, 1)
                                           if last7 is not None and year_ago else None)}


def collect_portwatch(fetcher, data_root, *, today):
    start = (today - timedelta(days=400)).isoformat()
    out, provs = {}, []
    for port_id, name in CHOKEPOINTS.items():
        query = parse.urlencode({"where": f"portid='{port_id}' AND date >= DATE '{start}'",
                                 "outFields": "date,portid,n_total,n_tanker", "orderByFields": "date",
                                 "resultRecordCount": 2000, "f": "json"})
        try:
            payload, prov = _acquire(fetcher, data_root, "imf_portwatch", f"{PORTWATCH_URL}?{query}", "json")
            out[name] = {"status": "AVAILABLE", **parse_portwatch(payload, port_id)}
            provs.append(prov)
        except (FetchError, ParseError, KeyError, ValueError) as exc:
            out[name] = _fail(getattr(exc, "code", None) or f"PARSE:{exc}")
        if out[name]["status"] == "AVAILABLE":
            age = (today - date.fromisoformat(out[name]["latest_date"])).days
            out[name].update(age_days=age, freshness="CURRENT" if age <= 10 else "STALE")
    available = any(item["status"] == "AVAILABLE" for item in out.values())
    return {"status": "AVAILABLE" if available else "UNAVAILABLE", "evidence": "OFFICIAL_FACT",
            "publisher": "IMF PortWatch (daily chokepoint transit calls from AIS)", "provenance": provs,
            "chokepoints": out}


# --- OFAC SDN list ---------------------------------------------------------------------------------

OFAC_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"


def parse_ofac(payload):
    reader = csv.reader(io.StringIO(payload.decode("latin-1")))
    programs, total = Counter(), 0
    for row in reader:
        if len(row) < 4 or not row[0].strip().isdigit():
            continue
        total += 1
        for program in re.findall(r"[A-Z0-9][A-Z0-9_\-]+", row[3].replace("] [", " ")):
            programs[program] += 1
    if total == 0:
        raise ParseError("empty SDN list")
    return {"entries": total, "top_programs": programs.most_common(12)}


def collect_ofac(data_root, *, fetcher):
    try:
        payload, prov = _acquire(fetcher, data_root, "ofac_sdn", OFAC_URL, "csv")
        parsed = parse_ofac(payload)
    except (FetchError, ParseError) as exc:
        return _fail(getattr(exc, "code", None) or f"PARSE:{exc}")
    return {"status": "AVAILABLE", "evidence": "OFFICIAL_FACT", "provenance": prov,
            "publisher": "U.S. Treasury OFAC (Specially Designated Nationals list)", **parsed,
            "note": "Counts only; no entity screening is performed or claimed."}


# --- GDELT headlines (media narrative) -------------------------------------------------------------

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_THEMES = {
    "Suez / Red Sea shipping": '("Suez Canal" OR "Red Sea" OR "Bab el-Mandeb") (shipping OR transit OR attack)',
    "Egypt economy and CBE": '("Central Bank of Egypt" OR "Egyptian pound" OR "Egypt inflation")',
    "Oil supply": '(OPEC OR "oil supply" OR "Brent crude")',
    "Sanctions": '(sanctions OR OFAC) (oil OR Iran OR Russia)',
}


def parse_gdelt(payload, *, limit=8):
    document = json.loads(payload)
    out, seen = [], set()
    for article in document.get("articles") or []:
        key = (article.get("url") or "").split("?")[0]
        title = (article.get("title") or "").strip()
        if not key or key in seen or not title:
            continue
        seen.add(key)
        stamp = article.get("seendate")
        seen_at = (datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
                   if stamp else None)
        out.append({"title": title, "url": article.get("url"), "domain": article.get("domain"),
                    "language": article.get("language"), "seen_at": seen_at})
    return out[:limit]


def collect_gdelt(fetcher, data_root):
    themes, provs, failures = {}, [], 0
    for theme, query in GDELT_THEMES.items():
        url = GDELT_URL + "?" + parse.urlencode({"query": query + " sourcelang:english", "mode": "artlist",
                                                 "format": "json", "maxrecords": 25, "timespan": "48h",
                                                 "sort": "datedesc"})
        try:
            payload, prov = _acquire(fetcher, data_root, "gdelt_doc", url, "json")
            themes[theme] = {"status": "AVAILABLE", "articles": parse_gdelt(payload)}
            provs.append(prov)
        except (FetchError, ValueError) as exc:
            failures += 1
            themes[theme] = _fail(getattr(exc, "code", None) or "PARSE")
            if getattr(exc, "code", "") == "HTTP_429" and failures >= 2:
                for rest in GDELT_THEMES:
                    themes.setdefault(rest, _fail("SKIPPED_AFTER_RATE_LIMIT"))
                break
    available = any(item["status"] == "AVAILABLE" for item in themes.values())
    return {"status": "AVAILABLE" if available else "UNAVAILABLE", "evidence": "MEDIA_NARRATIVE",
            "publisher": "GDELT Project DOC 2.0 (open; citation required)", "provenance": provs,
            "themes": themes, "note": "Headlines are narrative context, not verified facts and never a signal."}


# --- SEC (contact-gated) ---------------------------------------------------------------------------

SEC_CONTACT_ENV = "EGX_SEC_CONTACT"
SEC_CONTACT_FILE = "/home/egx-agent/er1-autopilot/state/config/sec-contact.txt"


def sec_contact(env=os.environ, path=SEC_CONTACT_FILE):
    """The operator's SEC fair-access contact (an e-mail address), or None.

    Only an explicit operator setting counts: never git configuration, account
    metadata, other environment variables or a guessed value.
    """
    value = (env.get(SEC_CONTACT_ENV) or "").strip()
    if not value and Path(path).is_file():
        value = Path(path).read_text().strip()
    return value if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value or "") else None


def sec_status(contact):
    if contact:
        return {"status": "CONFIGURED", "evidence": "OFFICIAL_FACT"}
    return {"status": "BLOCKED", "code": "NO_OPERATOR_CONTACT_EMAIL",
            "reason": "SEC fair-access policy requires a User-Agent with a contact e-mail; the operator has not "
                      "provided one (docs/PROVIDER_ARCHITECTURE.md)."}
