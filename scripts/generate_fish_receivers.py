#!/usr/bin/env python3
"""Generate LIVE GREAT LAKES FISH ACOUSTIC TELEMETRY RECEIVERS KML.

Multi-source union inventory (no single-source dependency):
  A. USGS CMWSC Real-Time Fish Telemetry summary + station pages (LIVE, hourly)
     https://cm.water.usgs.gov/data/Fish_Tracks_Real_Time/
  B. GLATOS receiver deployments via USGS redhorse data release (DOI 10.5066/P13H22V6,
     Receiver_locs.csv) — Lake Erie / Sandusky / Cuyahoga.
  C. GLATOS receiver deployments via USGS lake-sturgeon data release
     (ScienceBase 65a831f9d34ebad3f34c9aab, recs_release_20241031.csv) —
     Huron-Erie corridor and connected waters.
  D. OTN ERDDAP view_otn_aat_receivers, Great Lakes bbox
     (https://erddap.oceantrack.org/erddap/tabledap/view_otn_aat_receivers)

Consulted but NOT used for placemarks (documented in provenance):
  - GLATOS member Data Portal (login required; receiver locations members-only)
  - RAFT ReceiverMap web app (no public bulk receiver API found)
  - MI DNR Lake Macatawa muskie project (8 receivers announced, no public coords)
  - USGS Atlantic-salmon / whitefish-hypoxia releases (no receiver-location table)

Lifecycle: ONLINE / OFFLINE / DISMANTLED-or-REMOVED. Placemarks persist.
Receiver identity = stable receiver ID (never name/coords alone).

Usage:
  python3 generate_fish_receivers.py --test     # one test placemark (Sandusky Brady's Island)
  python3 generate_fish_receivers.py --full     # union inventory KML
  python3 generate_fish_receivers.py --test --full  # both
  --force  # republish even if the live source version is unchanged
"""
import argparse
import csv
import datetime as dt
import html
import http.cookiejar
import json
import math
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

try:
    from species import resolve as resolve_species, UNRESOLVED
except ImportError:  # allow audit.py import without species side effects
    resolve_species, UNRESOLVED = None, "UNRESOLVED_TAG"


def build_registry_order():
    """Canonical tracked-species order from source/species_registry.json."""
    reg_path = os.path.join(SOURCE, "species_registry.json")
    if not os.path.exists(reg_path):
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from species_registry import build as build_reg
            return build_reg()["order"]
        except Exception:
            return []
    try:
        return json.load(open(reg_path))["order"]
    except (ValueError, KeyError):
        return []

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
SOURCE = os.path.join(BASE, "source")
CACHE = os.path.join(SOURCE, "cache")
KMLDIR = os.path.join(BASE, "kml")

LIVE_SUMMARY = "https://cm.water.usgs.gov/data/Fish_Tracks_Real_Time/"
# Source documents hourly retrieval; freshness threshold is source-specific (spec 13).
EXPECTED_UPDATE_HOURS = 1.0
STALE_AFTER_HOURS = 30.0  # documented per-source threshold for this hourly network

DOI_RED = "10.5066/P13H22V6"
SB_RED = "692dca0bd4be0248224461ed"
SB_STURG = "65a831f9d34ebad3f34c9aab"
OTN_ERDDAP = ("https://erddap.oceantrack.org/erddap/tabledap/view_otn_aat_receivers.csv"
              "?deployment_id,time,latitude,longitude,receiver_model,receiver_reference_id,"
              "array_name,project_reference,recovery_datetime_utc"
              "&latitude>41&latitude<48.6&longitude>-93&longitude<-76")

TEST_RECEIVER_ID = "412109083063800"  # Sandusky River at Brady's Island, Fremont OH

# Published static-asset URL for the icon. KML cannot embed local images, so
# the production KML references the published asset at an absolute URL
# (Google Earth fetches it over HTTP). Mirrors the buoy product's absolute-href
# pattern (generate_live_kml.py:105).
ICON_URL = ("https://raw.githubusercontent.com/wyattleathorn7/"
            "great-lakes-live-fish-telemetry/main/icons/fish_receiver.png")

# Station IDs for unlinked summary rows, resolved from the authoritative USGS
# science page (https://www.usgs.gov/centers/cm-water/science/real-time-fish-telemetry),
# which links these exact station names to these station pages.
UNLINKED_SID = {
    "MISSISSIPPI R AT CREDIT ISLAND NEAR DAVENPORT, IA": "412957090371001",
    "CARTHAGE LAKE NEAR BURLINGTON, IA": "404721091043701",
}
# Authoritative NWIS coordinates for USGS live stations (verified 2026-09-23
# via waterservices.usgs.gov; decoded coordinate-IDs agree within ~170 m and are
# used only where NWIS has no record).
NWIS_COORDS = {
    "05536890": (41.69116667, -87.9637778),
    "05536995": (41.64, -88.0599722),
    "05538010": (41.505, -88.0997222),
    "05538020": (41.5, -88.1069444),
    "05541498": (41.39861111, -88.27055556),
    "412109083063800": (41.3525, -83.1105556),
    "411955088280601": (41.33305556, -88.4669444),
    "412341088161001": (41.3948333, -88.2693611),
    "404721091043701": (40.78902778, -91.0769167),
    "411133091003401": (41.1925, -91.0094444),
    "412957090371001": (41.49916667, -90.6194444),
}


def fetch(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": "great-lakes-live-fish-telemetry/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def decode_coord_station_id(sid):
    """Decode USGS coordinate-derived station IDs (DDMMSS DDDMMSS SS).

    Returns (lat, lon) or None when the pattern is invalid. Validated against
    NWIS for sibling IDs (agreement <= ~170 m); NWIS is preferred when present.
    """
    m = re.fullmatch(r"(\d{2})(\d{2})(\d{2})(\d{3})(\d{2})(\d{2})(\d{2})", sid or "")
    if not m:
        return None
    lat = int(m.group(1)) + int(m.group(2)) / 60 + int(m.group(3)) / 3600
    lon = int(m.group(4)) + int(m.group(5)) / 60 + int(m.group(6)) / 3600
    if not (40 <= lat <= 49 and 70 <= lon <= 95):
        return None
    return (round(lat, 6), round(-lon, 6))


def live_coords(sid):
    if sid in NWIS_COORDS:
        return NWIS_COORDS[sid], "USGS NWIS site coordinates"
    d = decode_coord_station_id(sid)
    if d:
        return d, "USGS coordinate-derived station ID (approx +-1 arcsec; NWIS lookup 404)"
    return None, "no authoritative coordinates"


# ---------------- Source A: USGS live ----------------

def parse_live_summary(page):
    """Parse the live summary table. Returns (page_modified, stations).

    stations: list of dicts {name, href, sid, stamp_text}.
    Rows without links carry explicit statuses (Discontinued / Removed for season).
    """
    text = page.decode("utf-8", errors="ignore")
    mod = None
    m = re.search(r"Page Last Modified:\s*([0-9\-: ]+CDT)", text)
    if m:
        mod = m.group(1).strip()
    stations = []
    # capture anchor rows and plain-text rows of the conditions table
    for tm in re.finditer(
            r'<a\s+href="([^"]+\.html)">([^<]+)</a>\s*</td>\s*<td[^>]*>\s*(?:&nbsp;)?([^<]*)</td>\s*'
            r'<td[^>]*>\s*(?:&nbsp;)?([^<]*)', text):
        href, name, stamp, tags = (html.unescape(x).strip() for x in tm.groups())
        sid = href.rsplit("/", 1)[-1].replace(".html", "")
        stations.append({"name": name, "href": href, "sid": sid,
                         "stamp_text": stamp, "tags_text": tags})
    # explicit-status rows without links
    for tm in re.finditer(
            r'<td[^>]*>\s*([A-Z][^<>]{5,90}?)\s*</td>\s*<td[^>]*>\s*(?:&nbsp;)?([^<>]*)</td>\s*'
            r'<td[^>]*>\s*(?:&nbsp;)?([^<>]*)', text):
        name, stamp, tags = (html.unescape(x).strip() for x in tm.groups())
        if "<a" in name or "Station Name" in name or not name:
            continue
        if any(s["name"] == name for s in stations):
            continue
        stations.append({"name": name, "href": None, "sid": None,
                         "stamp_text": stamp, "tags_text": tags})
    return mod, stations


def classify_live(st, now_utc):
    """Map the authoritative live row to ONLINE / OFFLINE / DISMANTLED."""
    stamp = (st["stamp_text"] or "").strip()
    low = stamp.lower()
    if "discontinued" in low or "discontinued" in (st["tags_text"] or "").lower():
        return "DISMANTLED / REMOVED", "Authoritative source reports: Discontinued"
    if "removed for season" in low:
        return "OFFLINE", "Authoritative source reports: Removed for season"
    if "more than 24 hours old" in low or not stamp or stamp in ("Discontinued",):
        return "OFFLINE", "Authoritative source reports feed older than 24 h / not determined"
    # parse "2026-09-23 10:00:00 EST" style stamps; source tz abbreviations CST/CDT/EST/EDT
    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)\s*([A-Z]{3})?", stamp)
    if not m:
        return "OFFLINE", "Unparseable source timestamp; treated as not reporting"
    off = {"CST": 6, "CDT": 5, "EST": 5, "EDT": 4}.get((m.group(2) or "CST"), 6)
    try:
        local = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S" if len(m.group(1)) > 16 else "%Y-%m-%d %H:%M")
    except ValueError:
        return "OFFLINE", "Unparseable source timestamp; treated as not reporting"
    stamp_utc = local + dt.timedelta(hours=off)
    age_h = (now_utc - stamp_utc.replace(tzinfo=dt.timezone.utc)).total_seconds() / 3600
    if age_h > STALE_AFTER_HOURS:
        return "OFFLINE", f"Feed stale: last source update {age_h:.1f} h ago (threshold {STALE_AFTER_HOURS} h)"
    return "ONLINE", ""


def parse_station_page(page):
    """Parse 5-minute records. Returns (last_update, last_detection, events_24h,
    uniq_24h, tag_counts, all_rows). Rows are (datetime, VRTagCount, [tags])."""
    text = page.decode("utf-8", errors="ignore")
    rows = re.findall(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*</td>\s*<td[^>]*>\s*(\d+)\s*</td>\s*"
        r"<td[^>]*>\s*(-?[\d.]+)\s*</td>\s*<td[^>]*>\s*([\d.NA/]+)\s*</td>\s*"
        r"<td[^>]*>\s*([\d.NA/]+)\s*</td>\s*<td[^>]*>\s*([\d.NA/]+)\s*</td>\s*"
        r"<td[^>]*>\s*([\d.NA/\-]+)\s*</td>\s*<td[^>]*>\s*(-?\d+)\s*</td>\s*"
        r"<td[^>]*>\s*(-?\d+)\s*</td>\s*<td[^>]*>\s*([^<]*)", text)
    recs = []
    for ts, _rec, _dc, _lv, _bv, _tp, _mem, tc, utc, tags in rows:
        try:
            t = dt.datetime.strptime(ts.strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        try:
            tc, utc = int(tc), int(utc)
        except ValueError:
            continue
        taglist = [x.strip() for x in tags.split(",") if x.strip() and re.match(r"A\d+-", x.strip())]
        recs.append((t, tc, utc, taglist))
    if not recs:
        return None, None, 0, 0, {}, []
    recs.sort()
    last_update = recs[-1][0]
    cutoff = last_update - dt.timedelta(hours=24)
    tag_counts, events, det_times = {}, 0, []
    for t, tc, _u, taglist in recs:
        if t >= cutoff and tc >= 0:
            events += tc
            for tag in taglist:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
                det_times.append(t)
    return (last_update, (max(det_times) if det_times else None), events,
            len(tag_counts), tag_counts, recs)


# ---------------- Sources B/C/D ----------------

def load_redhorse():
    path = os.path.join(CACHE, "Receiver_locs.csv")
    if not os.path.exists(path):
        data = fetch(f"https://www.sciencebase.gov/catalog/file/get/{SB_RED}?name=Receiver_locs.csv")
        with open(path, "wb") as f:
            f.write(data)
    out = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                lat, lon = float(r["deploy_lat"]), float(r["deploy_long"])
            except ValueError:
                continue
            if not (40 <= lat <= 49 and -93 <= lon <= -74):
                continue
            s = out.setdefault(r["station"].strip().upper(),
                               {"lat": lat, "lon": lon, "deploys": [], "projects": set()})
            s["deploys"].append(r["deploy_date_time"])
            s["projects"].add("GLATOS/redhorse-2022-2025")
    return out


def load_sturgeon():
    path = os.path.join(CACHE, "recs_release_20241031.csv")
    if not os.path.exists(path):
        data = fetch(f"https://www.sciencebase.gov/catalog/file/get/{SB_STURG}?name=recs_release_20241031.csv")
        with open(path, "wb") as f:
            f.write(data)
    out = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                lat, lon = float(r["deploy_lat"]), float(r["deploy_long"])
            except ValueError:
                continue
            if not (40 <= lat <= 49.5 and -93 <= lon <= -74):
                continue
            key = (r["station"].strip().upper(), (r.get("glatos_array") or "").strip().upper())
            s = out.setdefault(key, {"lat": lat, "lon": lon, "deploys": [], "recovers": [],
                                     "projects": set(), "arrays": set(), "models": set()})
            s["lat"], s["lon"] = lat, lon
            if r.get("deploy_date_time"):
                s["deploys"].append(r["deploy_date_time"])
            if r.get("recover_date_time"):
                s["recovers"].append(r["recover_date_time"])
            if r.get("glatos_project"):
                s["projects"].add(r["glatos_project"].strip())
            if r.get("glatos_array"):
                s["arrays"].add(r["glatos_array"].strip())
            if r.get("ins_model_no"):
                s["models"].add(r["ins_model_no"].strip())
    return out


def load_glatos_map(refresh=True):
    """GLATOS public map pins (authoritative current deployment inventory).

    Returns {identity_key: info} with identity (project, array, station).
    Refreshes via the map's own POST endpoint with a session cookie;
    falls back to the newest cached snapshot in source/cache/.
    """
    def newest_cache():
        cands = sorted(f for f in os.listdir(CACHE) if f.startswith("glatos_map_pins_"))
        return os.path.join(CACHE, cands[-1]) if cands else None

    if refresh:
        cached = newest_cache()
        try:
            stale = True
            if cached:
                age_h = (dt.datetime.now().timestamp() - os.path.getmtime(cached)) / 3600
                stale = age_h > 20
            if not stale:
                d = json.load(open(cached))
                return index_map_pins(d["Pins"]), ""
            cj = http.cookiejar.CookieJar()
            op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            op.open(urllib.request.Request(
                "https://glatos.org/map",
                headers={"User-Agent": "great-lakes-live-fish-telemetry/1.0"}),
                timeout=40).read()
            req = urllib.request.Request(
                "https://glatos.org/map/get", data=b"",
                headers={"User-Agent": "great-lakes-live-fish-telemetry/1.0",
                         "Content-Length": "0"})
            raw = op.open(req, timeout=120).read()
            d = json.loads(raw.decode("utf-8", errors="ignore"))
            if "Pins" in d:
                day = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
                with open(os.path.join(CACHE, f"glatos_map_pins_{day}.json"), "wb") as f:
                    f.write(raw)
                return index_map_pins(d["Pins"]), ""
        except Exception as e:
            cached = newest_cache()
            if cached:
                d = json.load(open(cached))
                return index_map_pins(d["Pins"]), f"map refresh failed ({e}); using {cached}"
            return {}, f"map refresh failed and no cache: {e}"
    cached = newest_cache()
    if cached:
        return index_map_pins(json.load(open(cached))["Pins"]), ""
    return {}, "no map cache available"


def index_map_pins(pins):
    """Collapse deployment pins to station identities.

    identity = (project, array, station). Coords = latest DeployDate pin.
    Status rollup: Ongoing > Unknown > Proposed > Finished (most active wins).
    """
    rank = {"Ongoing": 3, "Unknown - deployed > 2 years without a recovery": 2,
            "Proposed": 1, "Finished": 0}
    groups = {}
    for p in pins:
        try:
            lat, lon = float(p["Lat"]), float(p["Long"])
        except (ValueError, TypeError, KeyError):
            continue
        key = ((p.get("Project") or "").strip(), (p.get("Array") or "").strip().upper(),
               str(p.get("StationNo") or "").strip().upper())
        g = groups.setdefault(key, {"pins": [], "projects": set(), "models": set(),
                                    "seasonal": False, "short_titles": set()})
        g["pins"].append(p)
        g["models"].add(str(p.get("ModelNo") or ""))
        g["short_titles"].add(str(p.get("ShortTitle") or ""))
        if str(p.get("Seasonal")).lower() == "yes":
            g["seasonal"] = True
    out = {}
    for key, g in groups.items():
        g["pins"].sort(key=lambda p: str(p.get("DeployDate") or ""))
        last = g["pins"][-1]
        best = max(g["pins"], key=lambda p: rank.get(p.get("Status"), 0))
        try:
            clat, clon = float(last["Lat"]), float(last["Long"])
        except (ValueError, TypeError):
            continue
        out[key] = {
            "lat": clat, "lon": clon,
            "status": best.get("Status", "Unknown"),
            "seasonal": g["seasonal"],
            "projects": {key[0]} if key[0] else set(),
            "arrays": {key[1]} if key[1] else set(),
            "models": {m for m in g["models"] if m},
            "deploys": sorted({str(p.get("DeployDate") or "") for p in g["pins"] if p.get("DeployDate")}),
            "recovers": sorted({str(p.get("RecoverDate") or "") for p in g["pins"] if p.get("RecoverDate")}),
            "short_titles": {t for t in g["short_titles"] if t},
            "n_pins": len(g["pins"]),
        }
    return out


# ---------------- Union + dedupe ----------------

def load_otn_live():
    """Fetch OTN Great Lakes receiver deployments (public ERDDAP)."""
    try:
        data = fetch(OTN_ERDDAP, timeout=90)
    except Exception as e:
        return {}, f"OTN ERDDAP fetch failed: {e}"
    text = data.decode("utf-8", errors="ignore").splitlines()
    reader = csv.DictReader(text)
    out = {}
    for r in reader:
        try:
            if r["latitude"] in ("degrees_north", "", None):
                continue
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (ValueError, KeyError):
            continue
        ref = (r.get("receiver_reference_id") or "").strip()
        if not ref:
            continue
        s = out.setdefault(ref, {"lat": lat, "lon": lon, "deploys": [], "recovers": [],
                                 "projects": set(), "arrays": set(), "models": set()})
        if r.get("time") and "UTC" not in r["time"]:
            s["deploys"].append(r["time"])
        if r.get("recovery_datetime_utc"):
            s["recovers"].append(r["recovery_datetime_utc"])
        if r.get("project_reference"):
            s["projects"].add("OTN/" + r["project_reference"].strip())
        if r.get("array_name"):
            s["arrays"].add(r["array_name"].strip())
        if r.get("receiver_model"):
            s["models"].add(r["receiver_model"].strip())
    return out, ""


def haversine_m(a, b):
    R = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def station_token(name):
    """(ALPHA, NUMBER) trailing station code.

    'CBG-081' -> ('CBG','81'); 'V2LGLFC_CBG_081' -> ('CBG','81');
    'LEG-615' -> ('LEG','615'). Used only as merge evidence together with
    co-location or identical IDs — never proximity alone.
    """
    if not name:
        return (None, None)
    parts = [p for p in re.split(r"[-_]", name.strip().upper()) if p]
    if not parts:
        return (None, None)
    m = re.fullmatch(r"([A-Z]+)(\d+)", parts[-1])
    if m:
        return (re.sub(r"\d", "", m.group(1)) or None, m.group(2).lstrip("0") or "0")
    if re.fullmatch(r"\d+", parts[-1]) and len(parts) >= 2:
        alpha = re.sub(r"[^A-Z]", "", parts[-2])
        return (alpha or None, parts[-1].lstrip("0") or "0")
    return (None, None)


def geo_bin(lat, lon):
    if 42.2 <= lat <= 43.15 and -83.55 <= lon <= -82.25:
        return "stclair_detroit"
    if 41.3 <= lat <= 42.95 and -83.55 <= lon <= -78.85:
        return "erie"
    if 43.0 <= lat <= 44.25 and -80.0 <= lon <= -76.0:
        return "ontario"
    if 43.0 <= lat <= 46.3 and -84.9 <= lon <= -79.7:
        return "huron"
    if 41.6 <= lat <= 46.15 and -88.2 <= lon <= -85.5:
        return "michigan"
    if 46.35 <= lat <= 48.3 and -92.6 <= lon <= -84.2:
        return "superior"
    if 40.5 <= lat <= 49.5 and -93.5 <= lon <= -73.0:
        return "connected_tributary"
    return "out_of_scope"


def build_inventory(live_stations, refresh_map=True):
    receivers = {}  # rid -> record
    stats = {"exact_merges": 0, "proximity_merges": 0, "excluded_no_coords": [],
             "merge_log": []}

    def add_hist(rid, name, lat, lon, source_tag, source_url, extra):
        if rid in receivers:
            rec = receivers[rid]
            rec["sources"].add(source_tag)
            rec["source_urls"].add(source_url)
            for k in ("projects", "arrays", "models", "deploys", "recovers"):
                rec[k].update(extra.get(k, set()))
            stats["exact_merges"] += 1
            stats["merge_log"].append({"type": "exact-id", "kept": rid,
                                       "evidence": f"identical receiver ID {rid} in {source_tag}"})
            return rec
        rec = {"rid": rid, "name": name, "lat": lat, "lon": lon,
               "coord_method": extra.get("coord_method", "authoritative source file"),
               "network": "GLATOS/OTN (historical deployments)",
               "sources": {source_tag}, "source_urls": {source_url},
               "projects": set(extra.get("projects", set())),
               "arrays": set(extra.get("arrays", set())),
               "models": set(extra.get("models", set())),
               "deploys": set(extra.get("deploys", set())),
               "recovers": set(extra.get("recovers", set())),
               "map_info": [], "live": None}
        receivers[rid] = rec
        return rec

    def absorb(keep, drop, mtype, evidence, dist=None):
        keep["sources"].update(drop["sources"])
        keep["source_urls"].update(drop["source_urls"])
        for k in ("projects", "arrays", "models", "deploys", "recovers"):
            keep[k].update(drop[k])
        keep["map_info"].extend(drop.get("map_info", []))
        keep["name"] = keep["name"] if keep["name"] else drop["name"]
        entry = {"type": mtype, "kept": keep["rid"], "dropped": drop["rid"],
                 "evidence": evidence}
        if dist is not None:
            entry["distance_m"] = round(dist, 1)
        stats["merge_log"].append(entry)

    red = load_redhorse()
    for sid, s in red.items():
        add_hist(f"GLATOS-{sid}", sid, s["lat"], s["lon"], "GLATOS/redhorse",
                 f"https://doi.org/{DOI_RED}",
                 {"projects": s["projects"], "deploys": set(s["deploys"]),
                  "coord_method": "USGS data release Receiver_locs.csv (DOI 10.5066/P13H22V6)"})
    sturg = load_sturgeon()
    for (sid, arr), s in sturg.items():
        # Station ID is the stable identity; array/project metadata aggregates.
        # (Verified: each station ID in this file pairs with exactly one array.)
        add_hist(f"GLATOS-{sid}", sid, s["lat"], s["lon"], "GLATOS/sturgeon-HEC",
                 "https://doi.org/10.5066/P142JQOJ",
                 {"projects": s["projects"], "arrays": s["arrays"], "models": s["models"],
                  "deploys": set(s["deploys"]), "recovers": set(s["recovers"]),
                  "coord_method": "USGS data release recs_release_20241031.csv (DOI 10.5066/P142JQOJ)"})
    otn, otn_err = load_otn_live()
    if otn_err:
        stats["otn_error"] = otn_err
    for ref, s in otn.items():
        add_hist(f"OTN-{ref}", ref, s["lat"], s["lon"], "OTN/ERDDAP",
                 "https://erddap.oceantrack.org/erddap/tabledap/view_otn_aat_receivers",
                 {"projects": s["projects"], "arrays": s["arrays"], "models": s["models"],
                  "deploys": set(s["deploys"]), "recovers": set(s["recovers"]),
                  "coord_method": "OTN ERDDAP view_otn_aat_receivers deployment coordinates"})

    # GLATOS public map ingest: match (array, station) to existing GLATOS rids,
    # else create in-scope identities. Map status evidence stored per project.
    gmap, map_err = load_glatos_map(refresh=refresh_map)
    stats["map_error"] = map_err
    stats["map_identities"] = len(gmap)
    token_index = {}
    for rid, rec in receivers.items():
        if rid.startswith("GLATOS-"):
            token_index.setdefault(station_token(rec["name"]), []).append(rid)
    map_matched, map_added, map_out_of_scope = 0, 0, []
    for (proj, arr, stn), info in gmap.items():
        tok = (arr, (stn or "").lstrip("0") or "0")
        existing = [r for r in token_index.get(tok, []) if r in receivers]
        entry = {"project": proj, "status": info["status"], "seasonal": info["seasonal"],
                 "short_titles": sorted(info["short_titles"])[:3],
                 "n_pins": info["n_pins"]}
        if existing:
            rec = receivers[existing[0]]
            rec["sources"].add("GLATOS/map")
            rec["source_urls"].add("https://glatos.org/map")
            for k in ("projects", "arrays", "models", "deploys", "recovers"):
                rec[k].update(info[k] if k in info else set())
            rec["projects"].update(info["projects"])
            rec["map_info"].append(entry)
            map_matched += 1
        elif geo_bin(info["lat"], info["lon"]) == "out_of_scope":
            map_out_of_scope.append(f"{proj}-{arr}-{stn}")
        else:
            rid = f"GLATOSMAP-{proj}-{arr}-{stn}" if proj else f"GLATOSMAP-{arr}-{stn}"
            receivers[rid] = {
                "rid": rid, "name": f"{arr}-{stn}", "lat": info["lat"], "lon": info["lon"],
                "coord_method": "GLATOS public map latest deployment pin",
                "network": "GLATOS (map inventory)",
                "sources": {"GLATOS/map"}, "source_urls": {"https://glatos.org/map"},
                "projects": set(info["projects"]), "arrays": set(info["arrays"]),
                "models": set(info["models"]), "deploys": set(info["deploys"]),
                "recovers": set(info["recovers"]), "map_info": [entry], "live": None}
            token_index.setdefault(tok, []).append(rid)
            map_added += 1
    stats["map_matched"] = map_matched
    stats["map_added"] = map_added
    stats["map_out_of_scope"] = map_out_of_scope

    # strict token merge: same (ALPHA, NUMBER) station code only, with evidence.
    # Different station numbers are NEVER merged (audit finding: SBI-009 vs SBI-010).
    groups = defaultdict(list)
    for rid, rec in receivers.items():
        if rid.startswith("USGS-LIVE-"):
            continue
        groups[station_token(rec["name"])].append(rid)
    merged_into = {}
    for tok, rids in groups.items():
        if tok == (None, None) or len(rids) < 2:
            continue
        rids = [r for r in rids if r in receivers and r not in merged_into]
        base = None
        for r in rids:
            if receivers[r]["rid"].startswith("GLATOS-") and not receivers[r][
                    "rid"].startswith("GLATOSMAP-"):
                base = r
                break
        base = base or rids[0]
        for r in rids:
            if r == base or r in merged_into:
                continue
            d = haversine_m((receivers[base]["lat"], receivers[base]["lon"]),
                            (receivers[r]["lat"], receivers[r]["lon"]))
            keep, drop = receivers[base], receivers[r]
            if not keep["rid"].startswith("GLATOS-"):
                keep, drop = drop, keep
            if d <= 150:
                absorb(keep, drop, "token-colocated",
                       f"equivalent station code {tok[0]}-{tok[1]} within 150 m across "
                       f"naming conventions ({keep['rid']} / {drop['rid']})", d)
            else:
                absorb(keep, drop, "token-redeployed",
                       f"identical station code {tok[0]}-{tok[1]} at distinct locations; "
                       f"kept {keep['rid']} coords, full history retained", d)
            stats["proximity_merges"] += 1
            merged_into[drop["rid"]] = keep["rid"]
    for rid in merged_into:
        receivers.pop(rid, None)

    # attach live stations (merge by proximity to existing, else new placemark)
    for st in live_stations:
        sid = st["sid"]
        coords, method = live_coords(sid) if sid else (None, "no authoritative coordinates")
        if coords is None:
            stats["excluded_no_coords"].append({"name": st["name"], "sid": sid,
                                                "reason": method})
            continue
        target = None
        for rid, rec in receivers.items():
            if haversine_m(coords, (rec["lat"], rec["lon"])) <= 150:
                target = rec
                break
        if target is None:
            target = {"rid": f"USGS-LIVE-{sid}", "name": st["name"], "lat": coords[0],
                      "lon": coords[1], "coord_method": method,
                      "network": "USGS Real-Time Fish Telemetry",
                      "sources": {"USGS/live"}, "source_urls": {LIVE_SUMMARY},
                      "projects": set(), "arrays": set(), "models": set(),
                      "deploys": set(), "recovers": set(), "live": None}
            receivers[target["rid"]] = target
        else:
            target["sources"].add("USGS/live")
            target["source_urls"].add(LIVE_SUMMARY)
            target["network"] = "USGS Real-Time Fish Telemetry + GLATOS/OTN deployments"
            if target["rid"].startswith("GLATOS") or target["rid"].startswith("OTN"):
                stats["proximity_merges"] += 1
                stats["merge_log"].append({
                    "type": "live-attach", "kept": target["rid"],
                    "live_sid": sid, "live_name": st["name"],
                    "distance_m": round(haversine_m(
                        coords, (target["lat"], target["lon"])), 1),
                    "evidence": "live station coordinates within 150 m of deployment record"})
        target["live"] = st
    return receivers, stats


# ---------------- KML ----------------

def esc(s):
    return html.escape(s or "", quote=False)


def description(rec, status, ctx):
    """User-facing placemark description, rebuilt from scratch per spec."""
    L = ["\U0001F41F LIVE ACOUSTIC TELEMETRY RECEIVER", "",
         f"Receiver: {rec['name']}", f"Status: {status}"]
    d = ctx or {}
    if d.get("last_update"):
        L.append(f"Last source update: {d['last_update']}")
    if d.get("last_detection"):
        L.append(f"Last fish detection: {d['last_detection']}")
    if status == "ONLINE":
        L += ["Reporting period: Previous 24 h", "",
              "FISH SPECIES DETECTIONS"]
        for sp in d.get("species_order", []):
            L.append(f"{sp}: {d['species_counts'].get(sp, 0)}")
        L.append("Counts are tag detection events, not fish abundance.")
        if d.get("unresolved_events"):
            L += ["", (f"Data-quality note: {d['unresolved_events']} detection events "
                       "from transmitters pending species identification are excluded "
                       "above; see live_detections.json.")]
    elif status in ("OFFLINE", "SEASONAL \u2014 CURRENTLY OUT OF SEASON",
                    "DISMANTLED / REMOVED"):
        L += ["", "Current live detections: UNAVAILABLE"]
        if d.get("reason"):
            L.append(f"Reason: {d['reason']}")
        if d.get("last_known"):
            L += ["", "LAST KNOWN LIVE DATA (not current)"] + d["last_known"]
    else:
        if d.get("reason"):
            L += ["", f"Note: {d['reason']}"]
        if d.get("last_deploy"):
            L.append(f"Last documented deployment: {d['last_deploy']}")
        if d.get("last_recover"):
            L.append(f"Last documented recovery: {d['last_recover']}")
        if rec.get("projects"):
            L.append(f"Projects: {', '.join(sorted(rec['projects'])[:4])}")
    L += ["", f"Receiver ID: {rec['rid']}"]
    if rec.get("network"):
        L.append(f"Network: {rec['network']}")
    if rec.get("arrays"):
        L.append(f"Array: {', '.join(sorted(rec['arrays'])[:3])}")
    L.append(f"Coordinates: {rec['lat']:.6f}, {rec['lon']:.6f}")
    L += ["", "Source:"]
    for u in sorted(rec["source_urls"])[:4]:
        L.append(f'<a href="{u}">{link_label(u)}</a>')
    return "\n".join(L)


def link_label(url):
    if "Fish_Tracks_Real_Time" in url:
        return "USGS real-time fish telemetry"
    if url.startswith("https://doi.org/"):
        return "USGS data release " + url.split("doi.org/")[1]
    if "glatos.org/map" in url:
        return "GLATOS public receiver map"
    if "erddap.oceantrack.org" in url:
        return "OTN ERDDAP receivers"
    if "usgs.gov" in url:
        return "USGS science page"
    return url.split("/")[2] if "://" in url else url


def build_kml(receivers, statuses):
    root = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    doc = ET.SubElement(root, "Document")
    ET.SubElement(doc, "name").text = "🐟 LIVE GREAT LAKES FISH ACOUSTIC TELEMETRY RECEIVERS"
    style = ET.SubElement(doc, "Style", id="fishReceiver")
    ist = ET.SubElement(style, "IconStyle")
    ET.SubElement(ist, "scale").text = "1.0"
    ET.SubElement(ET.SubElement(ist, "Icon"), "href").text = ICON_URL
    for rid in sorted(receivers):
        rec = receivers[rid]
        pm = ET.SubElement(doc, "Placemark")
        ET.SubElement(pm, "name").text = f"[{statuses[rid]}] {rec['name']}"
        ET.SubElement(pm, "styleUrl").text = "#fishReceiver"
        desc = ET.SubElement(pm, "description")
        desc.text = "<![CDATA[" + description(rec, statuses[rid], rec.get("desc_ctx")) + "]]>"
        pt = ET.SubElement(pm, "Point")
        ET.SubElement(pt, "coordinates").text = f"{rec['lon']:.6f},{rec['lat']:.6f},0"
    ET.indent(root)
    kml = ET.tostring(root, encoding="unicode", xml_declaration=True)
    # ET escapes CDATA markers and inner HTML; restore real CDATA sections with
    # working hyperlinks (same raw-HTML-in-CDATA pattern as the buoy KML).
    kml = kml.replace("&lt;![CDATA[", "<![CDATA[").replace("]]&gt;", "]]>")
    kml = re.sub(r'&lt;(a href=".*?")&gt;(.*?)&lt;(/a)&gt;',
                 lambda m: f"<{m.group(1)}>{m.group(2)}<{m.group(3)}>", kml)
    return kml


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if not args.test and not args.full:
        args.test = True
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(KMLDIR, exist_ok=True)

    now_utc = dt.datetime.now(dt.timezone.utc)
    try:
        summary = fetch(LIVE_SUMMARY)
    except Exception as e:
        print(f"SOURCE UNAVAILABLE (live summary): {e}; retaining previous outputs.", file=sys.stderr)
        with open(os.path.join(SOURCE, "fetch_failures.log"), "a") as f:
            f.write(f"{now_utc.isoformat()} live summary fetch failed: {e}\n")
        return 2
    page_mod, live_stations = parse_live_summary(summary)
    for st in live_stations:
        if not st["sid"] and st["name"] in UNLINKED_SID:
            st["sid"] = UNLINKED_SID[st["name"]]
            st["href"] = st["sid"] + ".html"

    state_path = os.path.join(DATA, "source_state.json")
    prev = {}
    if os.path.exists(state_path):
        prev = json.load(open(state_path))
    live_version = (page_mod or "") + "|" + "|".join(
        sorted(f"{s.get('sid')}:{s.get('stamp_text')}" for s in live_stations))
    # ensure map freshness before the source-aware skip check
    try:
        load_glatos_map(refresh=True)
    except Exception as e:
        print(f"map pre-refresh note: {e}")
    map_caches = sorted(f for f in os.listdir(CACHE) if f.startswith("glatos_map_pins_"))
    live_version += "|map:" + (map_caches[-1] if map_caches else "none")
    if not args.force and prev.get("live_version") == live_version and not args.test:
        print("Live source unchanged; skipping republish (source-aware update).")
        return 0

    receivers, stats = build_inventory(live_stations)

    failures = []
    # ---- lifecycle classification (evidence-based taxonomy) ----
    # ONLINE / OFFLINE (live-linked w/ reason) / DISMANTLED / REMOVED /
    # ONGOING — NO LIVE FEED / SEASONAL — CURRENTLY OUT OF SEASON /
    # HISTORICAL — NOT CURRENTLY DEPLOYED / UNKNOWN
    statuses = {}
    for rid, rec in receivers.items():
        st = rec.get("live")
        if st is not None:
            status, reason = classify_live(st, now_utc)
            if status == "OFFLINE" and "season" in reason.lower():
                status = "SEASONAL \u2014 CURRENTLY OUT OF SEASON"
            statuses[rid] = status
            rec["status_reason"] = reason
            continue
        mi = rec.get("map_info") or []
        mstats = {m["status"] for m in mi}
        if "Ongoing" in mstats:
            statuses[rid] = "ONGOING \u2014 NO LIVE FEED"
            rec["status_reason"] = ("GLATOS map status Ongoing: receiver deployed, "
                                    "detection stream not publicly exposed")
        elif mstats == {"Proposed"}:
            statuses[rid] = "UNKNOWN"
            rec["status_reason"] = "GLATOS map status Proposed (planned, not yet deployed)"
        elif any(s.startswith("Unknown") for s in mstats):
            statuses[rid] = "UNKNOWN"
            rec["status_reason"] = "GLATOS map: deployed >2 years without a recovery"
        elif "Finished" in mstats:
            if any(m.get("seasonal") for m in mi):
                statuses[rid] = "SEASONAL \u2014 CURRENTLY OUT OF SEASON"
                rec["status_reason"] = "GLATOS map status Finished (seasonal deployment)"
            else:
                statuses[rid] = "HISTORICAL \u2014 NOT CURRENTLY DEPLOYED"
                rec["status_reason"] = "GLATOS map status Finished"
        else:
            recovs = sorted(rec["recovers"])
            if recovs and recovs[-1] < "2024":
                statuses[rid] = "HISTORICAL \u2014 NOT CURRENTLY DEPLOYED"
                rec["status_reason"] = (f"last documented recovery {recovs[-1]}; "
                                        "no current-status evidence")
            else:
                statuses[rid] = "UNKNOWN"
                rec["status_reason"] = "insufficient current-status evidence"

    # ---- live detail + 7-day detection history + species resolution ----
    hist7_path = os.path.join(DATA, "detection_history.json")
    hist7 = json.load(open(hist7_path)) if os.path.exists(hist7_path) else {}
    lastknown_path = os.path.join(DATA, "last_known_species.json")
    lastknown = json.load(open(lastknown_path)) if os.path.exists(lastknown_path) else {}
    live_receivers, live_detections = [], []
    per_rec = {}  # rid -> {species7:set, counts24:Counter, unres24:int, lu, ld}
    for rid, rec in receivers.items():
        st = rec.get("live")
        if st is None:
            continue
        entry = {"receiver_id": rid, "receiver_name": rec["name"],
                 "latitude": rec["lat"], "longitude": rec["lon"],
                 "network": "USGS Real-Time Fish Telemetry",
                 "source_url": LIVE_SUMMARY + (st.get("href") or ""),
                 "current_status": statuses[rid],
                 "status_timestamp": now_utc.isoformat(),
                 "source_update_time": st["stamp_text"],
                 "reason": rec.get("status_reason", ""),
                 "sources": sorted(rec["sources"]),
                 "source_urls": sorted(rec["source_urls"])}
        live_receivers.append(entry)
        if statuses[rid] != "ONLINE":
            rec["_lk"] = lastknown.get(rid)
            rec["desc_ctx"] = {"reason": rec.get("status_reason", ""),
                               "last_update": st["stamp_text"],
                               "last_known": None}  # filled after registry order known
            continue
        try:
            page = fetch(LIVE_SUMMARY + st["href"])
            lu, ld, _events, _uniq, _tags, rows = parse_station_page(page)
        except Exception as e:
            msg = f"{now_utc.isoformat()} station {st.get('href')} fetch failed: {e}"
            failures.append(msg)
            # SOURCE UNAVAILABLE: not evidence the receiver is offline. Keep last
            # known good context; do not zero, do not reclassify.
            lk = lastknown.get(rid, {})
            rec["desc_ctx"] = {"reason": rec.get("status_reason", ""),
                               "last_update": st["stamp_text"],
                               "last_detection": (lk.get("last_detection") or "unavailable "
                                                  "(live source temporarily unreachable)")}
            with open(os.path.join(SOURCE, "fetch_failures.log"), "a") as f:
                f.write(msg + "\n")
            continue
        if lu is None:
            rec["desc_ctx"] = {"reason": rec.get("status_reason", ""),
                               "last_update": st["stamp_text"]}
            continue
        H = hist7.setdefault(rid, [])
        seen = {(h["t"], tuple(h["tags"])) for h in H}
        for (trow, tc, _u, taglist) in rows:
            key = (trow.strftime("%Y-%m-%d %H:%M:%S"), tuple(taglist))
            if key not in seen:
                H.append({"t": key[0], "events": tc, "tags": list(taglist)})
                seen.add(key)
        cutoff7 = lu - dt.timedelta(days=7)
        H[:] = [h for h in H if dt.datetime.strptime(
            h["t"], "%Y-%m-%d %H:%M:%S") >= cutoff7]
        hist7[rid] = H
        per_rec[rid] = {"lu": lu, "rows": H}

    # resolve every tag seen in any 7-day window (single batch; cache persists)
    tags7_all = {tg for v in per_rec.values() for h in v["rows"] for tg in h["tags"]}
    mapping = resolve_species(tags7_all, failures) if resolve_species else {}
    from collections import Counter as _Counter
    system_species_7d = set()
    for rid, v in per_rec.items():
        lu = v["lu"]
        cutoff24 = lu - dt.timedelta(hours=24)
        counts, species7, unres, last_det = _Counter(), set(), 0, None
        for h in v["rows"]:
            trow = dt.datetime.strptime(h["t"], "%Y-%m-%d %H:%M:%S")
            resolved = [(tg, mapping[tg]["common"]) for tg in h["tags"]
                        if mapping.get(tg, {}).get("common") not in (None, UNRESOLVED, "")]
            n_unres = len(h["tags"]) - len(resolved)
            if trow >= cutoff24 and h["events"] >= 0:
                if resolved:
                    share = h["events"] / len(resolved)
                    for _tg, sp in resolved:
                        counts[sp] += share
                        species7.add(sp)
                if n_unres:
                    unres += (h["events"] / len(h["tags"]) if h["tags"] and h["events"]
                              else 0)
            for tg in h["tags"]:
                c = mapping.get(tg, {}).get("common")
                if c and c != UNRESOLVED:
                    species7.add(c)
            if h["tags"]:
                last_det = h["t"] if last_det is None or h["t"] > last_det else last_det
        counts = {sp: int(round(vv)) for sp, vv in counts.items()}
        unres = int(round(unres))
        system_species_7d.update(species7)
        v.update({"species7": species7, "counts": counts, "unres": unres,
                  "last_det": last_det})
        lastknown[rid] = {"counts": counts, "unres": unres, "order": sorted(species7),
                          "period": "previous 24 h",
                          "timestamp": now_utc.isoformat(), "last_detection": last_det}
        live_detections.append({"receiver_id": rid, "reporting_period": "previous 24 h",
                                "species_counts_24h": counts,
                                "unresolved_events_24h": unres,
                                "last_detection": last_det})
    system_order = build_registry_order()
    for rid, rec in receivers.items():
        lk = rec.pop("_lk", None)
        if lk and rec.get("live") is not None and statuses[rid] != "ONLINE":
            rec["desc_ctx"]["last_known"] = (
                [f"Reporting period: {lk['period']}"] +
                [f"{sp}: {lk['counts'].get(sp, 0)}" for sp in system_order
                 if sp in lk["counts"]] or None)
    for rid, v in per_rec.items():
        rec = receivers[rid]
        rec["desc_ctx"] = {
            "last_update": v["lu"].strftime("%Y-%m-%d %H:%M:%S"),
            "last_detection": v["last_det"] or "none in window",
            "species_order": system_order,
            "species_counts": {sp: v["counts"].get(sp, 0) for sp in system_order},
            "unresolved_events": v["unres"]}
    for rid, rec in receivers.items():
        if rec.get("live") is None:
            dep = sorted(rec["deploys"])
            recov = sorted(rec["recovers"])
            rec["desc_ctx"] = {
                "reason": rec.get("status_reason", ""),
                "last_deploy": dep[-1] if dep else None,
                "last_recover": recov[-1] if recov else None}

    # ---- status history (transitions only) ----
    hist_path = os.path.join(DATA, "receiver_status_history.json")
    hist = json.load(open(hist_path)) if os.path.exists(hist_path) else {}
    for rid, s in statuses.items():
        h = hist.setdefault(rid, [])
        if not h or h[-1]["status"] != s:
            h.append({"timestamp": now_utc.isoformat(), "status": s})

    def dump(name, obj):
        with open(os.path.join(DATA, name), "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True, default=list)

    jrec = {}
    for rid, rec in receivers.items():
        jrec[rid] = {**rec, "sources": sorted(rec["sources"]),
                     "source_urls": sorted(rec["source_urls"]),
                     "projects": sorted(rec["projects"]), "arrays": sorted(rec["arrays"]),
                     "models": sorted(rec["models"]), "deploys": sorted(rec["deploys"]),
                     "recovers": sorted(rec["recovers"]),
                     "map_info": rec.get("map_info", []),
                     "current_status": statuses[rid]}
        jrec[rid].pop("live", None)
        jrec[rid].pop("desc_ctx", None)
    dump("live_receivers.json", live_receivers)
    dump("live_detections.json", live_detections)
    dump("receiver_status_history.json", hist)
    dump("detection_history.json", hist7)
    dump("last_known_species.json", lastknown)
    # full receiver audit table (§2 fields for every placemark)
    audit_rows = []
    det_by_rid = {d["receiver_id"]: d for d in live_detections}
    for rid, rec in receivers.items():
        lv = rec.get("live") or {}
        mi = rec.get("map_info") or []
        h = hist.get(rid, [])
        det = det_by_rid.get(rid, {})
        dep = sorted(rec["deploys"])
        recov = sorted(rec["recovers"])
        audit_rows.append({
            "receiver_id": rid, "latitude": rec["lat"], "longitude": rec["lon"],
            "current_status": statuses[rid],
            "previous_status": h[-2]["status"] if len(h) > 1 else None,
            "network": rec.get("network", ""),
            "source_record_id": lv.get("sid") or ";".join(
                f"{m['project']}/{m['status']}" for m in mi[:4]),
            "projects": sorted(rec["projects"]), "arrays": sorted(rec["arrays"]),
            "models": sorted(rec["models"]),
            "deployment_start": dep[0] if dep else None,
            "deployment_end": recov[-1] if recov else None,
            "seasonal": bool(any(m.get("seasonal") for m in mi)),
            "last_known_detection": det.get("last_detection"),
            "last_source_update": lv.get("stamp_text"),
            "source_is_live": lv != {},
            "source_is_historical": bool(recov),
            "source_is_seasonal": bool(any(m.get("seasonal") for m in mi)),
            "source_currently_reports": statuses[rid] == "ONLINE",
            "currently_ongoing": statuses[rid] in (
                "ONLINE", "ONGOING \u2014 NO LIVE FEED"),
            "offline_reason": rec.get("status_reason", ""),
            "evidence_urls": sorted(rec["source_urls"]),
            "species_metadata_available": bool(det.get("species_counts_24h")),
            "species_resolved_detections_24h": sum(
                (det.get("species_counts_24h") or {}).values()),
            "unresolved_detections_24h": det.get("unresolved_events_24h", 0),
        })
    dump("receiver_audit.json", audit_rows)
    if failures:
        with open(os.path.join(SOURCE, "fetch_failures.log"), "a") as f:
            for line in failures:
                f.write(line + "\n")
    with open(os.path.join(SOURCE, "provenance.json"), "w") as f:
        json.dump({
            "generated_utc": now_utc.isoformat(), "live_version": live_version,
            "expected_update_hours": EXPECTED_UPDATE_HOURS,
            "stale_after_hours": STALE_AFTER_HOURS,
            "counting_method": ("per 5-minute row, VRTagCount events split evenly among "
                                "resolved tags present; unresolved share counted separately; "
                                "24 h reporting window; 7-day history retention with aging to 0"),
            "species_method": ("RAFT transmitter lookup > OTN animal releases > USGS "
                               "tag-metadata files; cache data/tag_species_cache.json, "
                               "retried hourly; unresolvable tags stay UNRESOLVED_TAG"),
            "merge_policy": ("exact receiver ID, or equivalent (ALPHA,NUMBER) station code "
                             "across naming conventions with co-location; same code at distance "
                             "kept as redeployed station; distinct station numbers never merged; "
                             "live stations attach to nearest deployment record within 150 m"),
            "dedupe": {"exact_merges": stats["exact_merges"],
                       "token_merges": stats["proximity_merges"],
                       "map_matched": stats.get("map_matched", 0),
                       "map_added": stats.get("map_added", 0)},
            "excluded_no_coords": stats["excluded_no_coords"],
            "map_out_of_scope": stats.get("map_out_of_scope", []),
            "otn_error": stats.get("otn_error", ""),
            "map_error": stats.get("map_error", ""),
            "species_failures_this_run": failures,
            "system_species_7d": system_order,
            "sources": {
                "USGS_live": LIVE_SUMMARY,
                "GLATOS_map": "https://glatos.org/map",
                "GLATOS_redhorse": f"https://doi.org/{DOI_RED}",
                "GLATOS_sturgeon_HEC": "https://doi.org/10.5066/P142JQOJ",
                "OTN_ERDDAP": "https://erddap.oceantrack.org/erddap/tabledap/view_otn_aat_receivers",
                "RAFT_lookup": "https://umesc-gisdb03.er.usgs.gov/raft/TransmitterLookup/SearchTags"},
            "consulted_not_used": {
                "RAFT_map": "web app only; no public bulk receiver API found",
                "MI_DNR_Macatawa": "8 receivers announced without public coordinates",
                "USGS_salmon_10.5066/P1A8ZLWV": "no receiver-location table in release",
                "USGS_whitefish_10.5066/P9CWKQ4D": "no receiver-location table in release"}},
            f, indent=2)
    json.dump({"live_version": live_version}, open(state_path, "w"), indent=2)

    def publish(kml_str, base):
        # KML-only production output: *.kml plus the static image asset.
        # No KMZ/ZIP packaging anywhere in this pipeline.
        kml_path = os.path.join(KMLDIR, base + ".kml")
        changed = True
        if os.path.exists(kml_path):
            changed = open(kml_path, encoding="utf-8").read() != kml_str
        if changed:  # content-aware publish: never rewrite identical output
            open(kml_path, "w", encoding="utf-8").write(kml_str)
        return changed

    if args.test:
        test = None
        for rid, rec in receivers.items():
            lv = rec.get("live") or {}
            if lv.get("sid") == TEST_RECEIVER_ID:
                test = (rid, rec)
                break
        if test:
            rid, rec = test
            kml = build_kml({rid: rec}, {rid: statuses[rid]})
            publish(kml, "TEST_SINGLE_RECEIVER")
            print(f"test KML: {rid} status={statuses[rid]}")
        # minimal icon test: one placemark referencing the published icon, KML only
        mini = ("<?xml version='1.0' encoding='utf-8'?>\n"
                '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
                "<name>ICON TEST</name>"
                '<Style id="fishReceiver"><IconStyle><scale>1.0</scale>'
                f"<Icon><href>{ICON_URL}</href></Icon></IconStyle></Style>"
                "<Placemark><name>icon test</name><styleUrl>#fishReceiver</styleUrl>"
                "<description>icon test</description>"
                "<Point><coordinates>-83.11129,41.35264,0</coordinates></Point>"
                "</Placemark></Document></kml>")
        publish(mini, "ICON_TEST")
        print("icon test KML written")
    if args.full:
        kml = build_kml(receivers, statuses)
        changed = publish(kml, "LIVE_GREAT_LAKES_FISH_ACOUSTIC_TELEMETRY_RECEIVERS")
        from collections import Counter
        print(f"full KML: {len(receivers)} placemarks; {dict(Counter(statuses.values()))}; "
              f"{'published' if changed else 'unchanged, not republished'}")
        print(f"exact merges={stats['exact_merges']} token merges={stats['proximity_merges']} "
              f"map matched={stats.get('map_matched', 0)} added={stats.get('map_added', 0)} "
              f"excluded={len(stats['excluded_no_coords'])} "
              f"system species={len(system_order)} unresolved-tags-seen="
              f"{sum(1 for v in per_rec.values() if v['unres'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
