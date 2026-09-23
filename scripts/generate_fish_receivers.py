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
import json
import math
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

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
    """Parse 5-minute records. Returns (last_update, last_detection, events_24h, uniq_24h, tag_counts)."""
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
        return None, None, 0, 0, {}
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
    return last_update, (max(det_times) if det_times else None), events, len(tag_counts), tag_counts


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


# ---------------- Union + dedupe ----------------

def haversine_m(a, b):
    R = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def build_inventory(live_stations):
    receivers = {}  # rid -> record
    stats = {"exact_merges": 0, "proximity_merges": 0, "excluded_no_coords": []}

    def add_hist(rid, name, lat, lon, source_tag, source_url, extra):
        if rid in receivers:
            rec = receivers[rid]
            rec["sources"].add(source_tag)
            rec["source_urls"].add(source_url)
            for k in ("projects", "arrays", "models", "deploys", "recovers"):
                rec[k].update(extra.get(k, set()))
            stats["exact_merges"] += 1
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
               "live": None}
        receivers[rid] = rec
        return rec

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

    # proximity merge via grid hashing (150 m): OTN<->GLATOS and live<->GLATOS/OTN
    cell = defaultdict(list)
    for rid, rec in receivers.items():
        cell[(round(rec["lat"], 2), round(rec["lon"], 2))].append(rid)
    merged_into = {}
    dure = list(receivers)
    for rid in dure:
        if rid in merged_into:
            continue
        rec = receivers[rid]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in cell.get((round(rec["lat"], 2) + dx * 0.01,
                                       round(rec["lon"], 2) + dy * 0.01), []):
                    if other == rid or other in merged_into:
                        continue
                    o = receivers[other]
                    if haversine_m((rec["lat"], rec["lon"]), (o["lat"], o["lon"])) <= 150:
                        # keep GLATOS named receiver as primary when present
                        keep, drop = (rec, o) if rec["rid"].startswith("GLATOS") else (o, rec)
                        keep["sources"].update(drop["sources"])
                        keep["source_urls"].update(drop["source_urls"])
                        for k in ("projects", "arrays", "models", "deploys", "recovers"):
                            keep[k].update(drop[k])
                        keep["name"] = keep["name"] if keep["name"] else drop["name"]
                        merged_into[drop["rid"]] = keep["rid"]
                        stats["proximity_merges"] += 1
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
        target["live"] = st
    return receivers, stats


# ---------------- KML ----------------

def esc(s):
    return html.escape(s or "", quote=False)


def description(rec, status, live_detail):
    L = []
    L.append("🐟 LIVE ACOUSTIC TELEMETRY RECEIVER")
    L.append("")
    L.append(f"Receiver: {rec['name']}")
    L.append(f"Status: {status}")
    if live_detail and status == "ONLINE":
        d = live_detail
        L.append(f"Last source update: {d['last_update']} (source tz, see source)")
        L.append(f"Last fish detection: {d['last_detection'] or 'none in window'}")
        L.append(f"Reporting period: previous 24 h ending {d['last_update']}")
        L.append("")
        L.append("FISH DETECTIONS (live TagID level; species attribution via RAFT/subscription)")
        L.append(f"Unique tags (24 h): {d['uniq']} / detection events (24 h): {d['events']}")
        for tag in sorted(d["tag_counts"]):
            L.append(f"{tag}: {d['tag_counts'][tag]} detections (tag detection events, NOT fish)")
        if not d["tag_counts"]:
            for tag in ("(no tag detections in reporting period) (= 0)",):
                L.append(tag)
    elif live_detail and status == "OFFLINE":
        d = live_detail
        L.append("")
        L.append("Current live detections: UNAVAILABLE")
        L.append(f"Last live update: {d.get('last_update', 'unknown')}")
        if d.get("reason"):
            L.append(f"Reason: {d['reason']}")
        if d.get("tag_counts"):
            L.append("")
            L.append("LAST KNOWN LIVE DATA (not current)")
            L.append(f"Reporting period: {d.get('period', 'previous 24 h')}")
            for tag in sorted(d["tag_counts"]):
                L.append(f"{tag}: {d['tag_counts'][tag]} detections")
    else:
        dep = sorted(rec["deploys"]) if rec["deploys"] else []
        recov = sorted(rec["recovers"]) if rec["recovers"] else []
        L.append("")
        L.append("Current live detections: UNAVAILABLE")
        if rec.get("reason"):
            L.append(f"Reason: {rec['reason']}")
        if dep:
            L.append(f"Last documented deployment: {dep[-1]}")
        if recov:
            L.append(f"Last documented recovery: {recov[-1]}")
        if rec["projects"]:
            L.append(f"Projects: {', '.join(sorted(rec['projects'])[:6])}")
    L.append("")
    L.append("RECEIVER")
    L.append(f"Receiver ID: {rec['rid']}")
    L.append(f"Network: {rec['network']}")
    if rec["arrays"]:
        L.append(f"Array: {', '.join(sorted(rec['arrays'])[:4])}")
    L.append(f"Waterbody: {rec.get('waterbody', 'Great Lakes basin / connected waters')}")
    L.append(f"Coordinates: {rec['lat']:.6f}, {rec['lon']:.6f} ({rec['coord_method'][:80]})")
    L.append("")
    L.append("SOURCE")
    for u in sorted(rec["source_urls"]):
        L.append(u)
    return "\n".join(L)


def build_kml(receivers, statuses):
    root = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    doc = ET.SubElement(root, "Document")
    ET.SubElement(doc, "name").text = "🐟 LIVE GREAT LAKES FISH ACOUSTIC TELEMETRY RECEIVERS"
    style = ET.SubElement(doc, "Style", id="fishReceiver")
    ist = ET.SubElement(style, "IconStyle")
    ET.SubElement(ist, "scale").text = "1.0"
    ET.SubElement(ET.SubElement(ist, "Icon"), "href").text = "../icons/fish_receiver.png"
    for rid in sorted(receivers):
        rec = receivers[rid]
        pm = ET.SubElement(doc, "Placemark")
        ET.SubElement(pm, "name").text = f"[{statuses[rid]}] {rec['name']}"
        ET.SubElement(pm, "styleUrl").text = "#fishReceiver"
        desc = ET.SubElement(pm, "description")
        desc.text = "<![CDATA[" + description(rec, statuses[rid], rec.get("live_detail")) + "]]>"
        pt = ET.SubElement(pm, "Point")
        ET.SubElement(pt, "coordinates").text = f"{rec['lon']:.6f},{rec['lat']:.6f},0"
    ET.indent(root)
    kml = ET.tostring(root, encoding="unicode", xml_declaration=True)
    # ET escapes CDATA markers; restore real CDATA sections for Google Earth HTML.
    return kml.replace("&lt;![CDATA[", "<![CDATA[").replace("]]&gt;", "]]>")


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
    if not args.force and prev.get("live_version") == live_version and not args.test:
        print("Live source unchanged; skipping republish (source-aware update).")
        return 0

    receivers, stats = build_inventory(live_stations)

    # classify + fetch live details for live-linked receivers
    statuses, live_receivers, live_detections = {}, [], []
    for rid, rec in receivers.items():
        st = rec.get("live")
        if st is None:
            statuses[rid] = "OFFLINE"
            rec["reason"] = ("No live feed in the USGS real-time network; "
                             "last documented deployment retained below (not current).")
            continue
        status, reason = classify_live(st, now_utc)
        statuses[rid] = status
        coords, _m = live_coords(st["sid"])
        entry = {"receiver_id": rid, "receiver_name": rec["name"], "latitude": rec["lat"],
                 "longitude": rec["lon"], "network": "USGS Real-Time Fish Telemetry",
                 "source_url": LIVE_SUMMARY + (st["href"] or ""),
                 "current_status": status, "status_timestamp": now_utc.isoformat(),
                 "source_update_time": st["stamp_text"], "reason": reason,
                 "sources": sorted(rec["sources"]), "source_urls": sorted(rec["source_urls"])}
        detail = {"reason": reason}
        if status == "ONLINE" and st.get("href"):
            try:
                page = fetch(LIVE_SUMMARY + st["href"])
                lu, ld, events, uniq, tags = parse_station_page(page)
                detail.update({"last_update": lu.strftime("%Y-%m-%d %H:%M:%S") if lu else st["stamp_text"],
                               "last_detection": ld.strftime("%Y-%m-%d %H:%M:%S") if ld else None,
                               "events": events, "uniq": uniq, "tag_counts": tags,
                               "period": "previous 24 h"})
                rec["live_detail"] = detail
                live_detections.append({"receiver_id": rid, "reporting_period": detail["period"],
                                        "unique_tags_24h": uniq, "detection_events_24h": events,
                                        "tag_counts": tags})
            except Exception as e:
                detail.update({"last_update": st["stamp_text"], "events": 0, "uniq": 0,
                               "tag_counts": {}, "fetch_error": str(e)})
                rec["live_detail"] = detail
        elif status == "OFFLINE":
            detail.update({"last_update": st["stamp_text"], "tag_counts": {}})
            rec["live_detail"] = detail
        else:  # dismantled
            rec["reason"] = reason
        live_receivers.append(entry)

    # status history (append transitions only)
    hist_path = os.path.join(DATA, "receiver_status_history.json")
    hist = json.load(open(hist_path)) if os.path.exists(hist_path) else {}
    for rid, s in statuses.items():
        h = hist.setdefault(rid, [])
        if not h or h[-1]["status"] != s:
            h.append({"timestamp": now_utc.isoformat(), "status": s})

    def dump(name, obj):
        with open(os.path.join(DATA, name), "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True, default=list)

    # JSON-serializable receivers
    jrec = {}
    for rid, rec in receivers.items():
        jrec[rid] = {**rec, "sources": sorted(rec["sources"]),
                     "source_urls": sorted(rec["source_urls"]),
                     "projects": sorted(rec["projects"]), "arrays": sorted(rec["arrays"]),
                     "models": sorted(rec["models"]), "deploys": sorted(rec["deploys"]),
                     "recovers": sorted(rec["recovers"]),
                     "current_status": statuses[rid]}
        jrec[rid].pop("live", None)
        jrec[rid].pop("live_detail", None)
    dump("live_receivers.json", live_receivers)
    dump("live_detections.json", live_detections)
    dump("receiver_status_history.json", hist)
    with open(os.path.join(SOURCE, "provenance.json"), "w") as f:
        json.dump({"generated_utc": now_utc.isoformat(), "live_version": live_version,
                   "expected_update_hours": EXPECTED_UPDATE_HOURS,
                   "stale_after_hours": STALE_AFTER_HOURS,
                   "dedupe": {"exact_merges": stats["exact_merges"],
                              "proximity_merges_150m": stats["proximity_merges"]},
                   "excluded_no_coords": stats["excluded_no_coords"],
                   "otn_error": stats.get("otn_error", ""),
                   "sources": {
                       "USGS_live": LIVE_SUMMARY,
                       "GLATOS_redhorse": f"https://doi.org/{DOI_RED}",
                       "GLATOS_sturgeon_HEC": "https://doi.org/10.5066/P142JQOJ",
                       "OTN_ERDDAP": "https://erddap.oceantrack.org/erddap/tabledap/view_otn_aat_receivers"},
                   "consulted_not_used": {
                       "GLATOS_portal": "members-only login; no public bulk receiver file",
                       "RAFT_map": "web app only; no public bulk receiver API found",
                       "MI_DNR_Macatawa": "8 receivers announced without public coordinates",
                       "USGS_salmon_10.5066/P1A8ZLWV": "no receiver-location table in release",
                       "USGS_whitefish_10.5066/P9CWKQ4D": "no receiver-location table in release"}},
                  f, indent=2)
    json.dump({"live_version": live_version}, open(state_path, "w"), indent=2)

    if args.test:
        # single test placemark: Brady's Island live receiver (merged record if present)
        test = None
        for rid, rec in receivers.items():
            lv = rec.get("live") or {}
            if lv.get("sid") == TEST_RECEIVER_ID:
                test = (rid, rec)
                break
        if test:
            rid, rec = test
            kml = build_kml({rid: rec}, {rid: statuses[rid]})
            with open(os.path.join(KMLDIR, "TEST_SINGLE_RECEIVER.kml"), "w") as f:
                f.write(kml)
            print(f"test KML: {rid} status={statuses[rid]}")
    if args.full:
        kml = build_kml(receivers, statuses)
        with open(os.path.join(KMLDIR, "LIVE_GREAT_LAKES_FISH_ACOUSTIC_TELEMETRY_RECEIVERS.kml"), "w") as f:
            f.write(kml)
        from collections import Counter
        print(f"full KML: {len(receivers)} placemarks; {dict(Counter(statuses.values()))}")
        print(f"exact merges={stats['exact_merges']} proximity merges={stats['proximity_merges']} "
              f"excluded={len(stats['excluded_no_coords'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
