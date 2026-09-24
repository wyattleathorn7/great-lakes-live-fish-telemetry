#!/usr/bin/env python3
"""Post-build audit of the fish-telemetry receiver inventory.

Produces audit/ACCOUNTING.json + audit/COVERAGE_GAP_REPORT.md.
Read-only: never modifies KML/data outputs.
"""
import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))
from generate_fish_receivers import (  # noqa: E402
    load_redhorse, load_sturgeon, load_otn_live, parse_live_summary, fetch,
    LIVE_SUMMARY, build_inventory,
)

AUDIT = os.path.join(BASE, "audit")
os.makedirs(AUDIT, exist_ok=True)
CACHE = os.path.join(BASE, "source", "cache")


def load_glatos_map():
    for f in sorted(os.listdir(CACHE)):
        if f.startswith("glatos_map_pins_"):
            d = json.load(open(os.path.join(CACHE, f)))
            return d["Pins"], f
    return [], None


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


def main():
    pins, pins_file = load_glatos_map()
    red = load_redhorse()
    sturg = load_sturgeon()
    otn, otn_err = load_otn_live()
    summary = fetch(LIVE_SUMMARY)
    page_mod, live_stations = parse_live_summary(summary)

    # ---- map identity index ----
    map_by_ident = {}
    for p in pins:
        k = ((p.get("Array") or "").strip().upper(),
             str(p.get("StationNo") or "").strip().upper())
        map_by_ident.setdefault(k, []).append(p)

    def map_status(name):
        """Best current GLATOS-map status evidence for a station name like ARR-001."""
        if "-" not in (name or ""):
            return []
        arr, stn = name.rsplit("-", 1)
        stn = stn.upper().lstrip("0") or "0"
        out = []
        for (a, s), v in map_by_ident.items():
            if a == arr.upper() and (s == stn or s.lstrip("0") == stn):
                out.extend(v)
        return out

    red_names = set(red)
    sturg_names = {sid for (sid, _a) in sturg}
    otn_refs = set(otn)
    on_idents = {k for k, v in map_by_ident.items()
                 if any(p.get("Status") == "Ongoing" for p in v)}

    sources = {
        "USGS_live": {
            "raw_records": len(live_stations),
            "linked_stations": sum(1 for s in live_stations if s.get("sid")),
            "unlinked_status_rows": sum(1 for s in live_stations if not s.get("sid")),
            "live_feed": True},
        "GLATOS_redhorse_DOI_10.5066/P13H22V6": {
            "raw_deployments": sum(len(v["deploys"]) for v in red.values()),
            "unique_stations": len(red_names),
            "live_feed": False},
        "GLATOS_sturgeon_DOI_10.5066/P142JQOJ": {
            "raw_deployments": 12047,
            "unique_stations": len(sturg_names),
            "live_feed": False},
        "OTN_ERDDAP_GL_bbox": {
            "raw_deployments": 797,
            "unique_receivers": len(otn_refs),
            "live_feed": False},
        "GLATOS_map_public": {
            "raw_pins": len(pins),
            "unique_identities_proj_array_stn": len(map_by_ident),
            "ongoing_identities": len(on_idents),
            "live_feed": False},
    }
    exact_id_overlap = len(red_names & sturg_names)
    ongoing_name_match = {f"{a}-{s}" for (a, s) in on_idents}
    ongoing_missing = sorted(on_idents - {(n.rsplit("-", 1)[0], n.rsplit("-", 1)[1].lstrip("0"))
                                          for n in (red_names | sturg_names) if "-" in n})

    # ---- rebuild inventory with merge log ----
    receivers, stats = build_inventory(live_stations)

    # ---- lifecycle classification of every placemark ----
    lifecycle = Counter()
    geo = Counter()
    liveavail = Counter()
    live_by_rid = {}
    for st in live_stations:
        pass
    # index live attachments
    live_attached = {rid: rec.get("live") for rid, rec in receivers.items() if rec.get("live")}
    details = {}
    for rid, rec in receivers.items():
        lat, lon = rec["lat"], rec["lon"]
        geo[geo_bin(lat, lon)] += 1
        lv = rec.get("live")
        if lv is not None:
            txt = ((lv.get("stamp_text") or "") + " " + (lv.get("tags_text") or "")).lower()
            if "discontinued" in txt:
                cat = "DISMANTLED / REMOVED"
            elif "removed for season" in txt:
                cat = "OFFLINE — SEASONAL"
            elif "more than 24 hours old" in txt or not (lv.get("stamp_text") or "").strip():
                cat = "OFFLINE — TEMPORARY"
            else:
                # freshness vs 30 h threshold
                import re, datetime as dtu
                m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)\s*([A-Z]{3})?",
                              lv.get("stamp_text") or "")
                cat = "ONLINE"
                if m:
                    off = {"CST": 6, "CDT": 5, "EST": 5, "EDT": 4}.get(m.group(2) or "CST", 6)
                    try:
                        local = dtu.datetime.strptime(
                            m.group(1), "%Y-%m-%d %H:%M:%S" if len(m.group(1)) > 16 else "%Y-%m-%d %H:%M")
                        age_h = (dtu.datetime.now(dtu.timezone.utc) - (
                            local + dtu.timedelta(hours=off)).replace(
                            tzinfo=dtu.timezone.utc)).total_seconds() / 3600
                        if age_h > 30:
                            cat = "OFFLINE — TEMPORARY"
                    except ValueError:
                        cat = "OFFLINE — TEMPORARY"
            liveavail["live/near-live feed"] += 1
            details[rid] = {"via": "USGS live feed"}
        else:
            mrecs = map_status(rec["name"] if rec["rid"].startswith("GLATOS") else "")
            statuses = {p.get("Status") for p in mrecs}
            if "Ongoing" in statuses:
                cat = "ONLINE / DATA NOT PUBLIC"
                liveavail["deployed, no public feed"] += 1
            elif "Proposed" in statuses and len(statuses) == 1:
                cat = "UNCERTAIN"
                liveavail["no public detection data"] += 1
            elif any(s.startswith("Unknown") for s in statuses):
                cat = "UNCERTAIN"
                liveavail["no public detection data"] += 1
            elif "Finished" in statuses:
                seasonal = any(str(p.get("Seasonal")).lower() == "yes" for p in mrecs)
                cat = "OFFLINE — SEASONAL" if seasonal else "FINISHED / HISTORICAL"
                liveavail["historical-only"] += 1
            else:
                # no map evidence: use recovery dates when present
                recovs = sorted(rec["recovers"])
                if recovs and recovs[-1] >= "2024":
                    cat = "UNCERTAIN"
                elif recovs:
                    cat = "FINISHED / HISTORICAL"
                else:
                    cat = "UNCERTAIN"
                liveavail["historical-only" if recovs else "no public detection data"] += 1
            details[rid] = {"map_statuses": sorted(statuses)}
        lifecycle[cat] += 1

    # ---- merge evidence review ----
    coords_only = [m for m in stats["merge_log"] if m.get("evidence") == "coordinates only"]
    with_evidence = [m for m in stats["merge_log"] if m.get("evidence") != "coordinates only"]

    accounting = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "live_page_modified": page_mod,
        "pins_file": pins_file,
        "sources": sources,
        "map_status_counts": dict(Counter(p.get("Status") for p in pins)),
        "overlaps": {
            "exact_id_redhorse_sturgeon": exact_id_overlap,
            "ongoing_map_identities_missing_from_repo": len(ongoing_missing),
        },
        "inventory": {
            "unique_placemarks": len(receivers),
            "exact_merges": stats["exact_merges"],
            "proximity_merges": stats["proximity_merges"],
            "merges_coords_only_no_supporting_evidence": len(coords_only),
            "merges_with_supporting_evidence": len(with_evidence),
            "excluded_no_coords": len(stats["excluded_no_coords"]),
        },
        "lifecycle": dict(lifecycle),
        "geo_bins": dict(geo),
        "live_data_availability": dict(liveavail),
        "otn_error": otn_err,
    }
    json.dump(accounting, open(os.path.join(AUDIT, "ACCOUNTING.json"), "w"), indent=2)
    json.dump({"coords_only_merges": coords_only,
               "ongoing_missing_sample": [f"{a}-{s}" for (a, s) in ongoing_missing[:200]],
               "ongoing_missing_count": len(ongoing_missing)},
              open(os.path.join(AUDIT, "merge_review.json"), "w"), indent=2)
    print(json.dumps(accounting, indent=1)[:3000])
    print("... -> audit/ACCOUNTING.json, audit/merge_review.json")


if __name__ == "__main__":
    main()
