#!/usr/bin/env python3
"""TagID -> species resolution layer.

Priority: 1) USGS/RAFT transmitter lookup 2) OTN animal releases
3) USGS tag-metadata files 4) GLATOS project tag metadata.
Cache: data/tag_species_cache.json {tag: {common, scientific, source, resolved_utc}}.
Unresolvable tags -> UNRESOLVED_TAG, retried every cycle. Never invent species.
"""
import csv
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(BASE, "data", "tag_species_cache.json")
CACHE_DIR = os.path.join(BASE, "source", "cache")

RAFT_LOOKUP = "https://umesc-gisdb03.er.usgs.gov/raft/TransmitterLookup/SearchTags"
OTN_ANIMAL = ("https://erddap.oceantrack.org/erddap/tabledap/"
              "view_otn_aat_animal_tag_releases.csv"
              "?transmittername,vernacularname,scientificname,time,project_reference")

UNRESOLVED = "UNRESOLVED_TAG"


def _now():
    return datetime.now(timezone.utc).isoformat()


def load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            return json.load(open(CACHE_PATH))
        except ValueError:
            pass
    return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    json.dump(cache, open(CACHE_PATH, "w"), indent=1, sort_keys=True)


def _fetch(url, data=None, timeout=40):
    req = urllib.request.Request(
        url, data=data,
        headers={"User-Agent": "great-lakes-live-fish-telemetry/1.0",
                 "Content-Type": "application/x-www-form-urlencoded"} if data else
        {"User-Agent": "great-lakes-live-fish-telemetry/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def raft_lookup(tags):
    """Batch RAFT transmitter lookup. Returns {tag: (common, scientific, project)}."""
    import http.cookiejar
    out = {}
    if not tags:
        return out
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    page = op.open(urllib.request.Request(
        RAFT_LOOKUP, headers={"User-Agent": "great-lakes-live-fish-telemetry/1.0"}),
        timeout=30).read().decode("utf-8", errors="ignore")
    m = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', page)
    if not m:
        raise RuntimeError("RAFT antiforgery token not found")
    # RAFT handles ~50 tags per POST comfortably; chunk defensively.
    taglist = sorted(tags)
    for i in range(0, len(taglist), 40):
        batch = taglist[i:i + 40]
        data = urllib.parse.urlencode(
            {"Tags": ", ".join(batch), "IncludeMismatches": "false",
             "__RequestVerificationToken": m.group(1)}).encode()
        req = urllib.request.Request(
            RAFT_LOOKUP, data=data,
            headers={"User-Agent": "great-lakes-live-fish-telemetry/1.0",
                     "Content-Type": "application/x-www-form-urlencoded"})
        html = op.open(req, timeout=60).read().decode("utf-8", errors="ignore")
        for row in re.findall(r'<tr class="tag-row">(.*?)</tr>', html, re.S):
            cells = [re.sub(r"<[^>]+>", " ", c).strip()
                     for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
            cells = [__import__("html").unescape(c) for c in cells]
            if len(cells) >= 3 and cells[1]:
                out[cells[0]] = (cells[2] or cells[1], cells[1],
                                 cells[7] if len(cells) > 7 else "")
    return out


def otn_lookup(tags):
    """OTN animal-release lookup one tag at a time (ERDDAP equality constraint)."""
    out = {}
    for tag in sorted(tags):
        try:
            url = (OTN_ANIMAL +
                   "&transmittername%3D%22" + urllib.parse.quote(tag) + "%22")
            text = _fetch(url, timeout=30).decode("utf-8", errors="ignore")
        except Exception:
            continue
        if text.startswith("Error"):
            continue
        lines = text.splitlines()
        for line in lines[2:]:  # skip header + units
            parts = line.split(",")
            if len(parts) >= 3 and parts[1]:
                out[tag] = (parts[1], parts[2] if len(parts) > 2 else parts[1], "")
                break
    return out


def local_files_lookup(tags):
    """USGS tag-metadata files in source/cache. Returns {tag: (common, scientific, file)}."""
    out = {}
    # sturgeon tag_meta: mfr_tag_id like A69-9001-31362, all lake sturgeon
    p = os.path.join(CACHE_DIR, "tag_meta.csv")
    if not os.path.exists(p):
        try:
            data = _fetch("https://www.sciencebase.gov/catalog/file/get/"
                          "65a831f9d34ebad3f34c9aab?name=tag_meta_20241101.csv")
            open(p, "wb").write(data)
        except Exception:
            p = None
    if p and os.path.exists(p):
        with open(p, newline="") as f:
            for r in csv.DictReader(f):
                tid = (r.get("mfr_tag_id") or "").strip()
                if tid in tags and tid not in out:
                    out[tid] = ("Lake Sturgeon", "Acipenser fulvescens",
                                "USGS sturgeon tag_meta (HECST)")
    # Sandusky walleye/grass carp tag_data: transmitter_id + codespace
    p2 = "/tmp/GC_walleye_tag_data.csv"
    if os.path.exists(p2):
        with open(p2, newline="") as f:
            for r in csv.DictReader(f):
                full = f"{r['transmitter_codespace']}-{r['transmitter_id']}"
                if full in tags and full not in out:
                    sp = r["species"].replace("_", " ").title()
                    out[full] = (sp, sp, "USGS Sandusky walleye/grass-carp tag_data")
    return out


def title_species(common):
    return "/".join(" ".join(w.capitalize() for w in part.split())
                    for part in common.strip().split("/"))


def resolve(tags, failures_log=None):
    """Resolve a set of TagIDs. Updates + saves cache. Returns {tag: cache-entry}."""
    cache = load_cache()
    todo = [t for t in tags if t not in cache or cache[t].get("common") == UNRESOLVED]
    if todo:
        found = {}
        for name, fn in (("RAFT", raft_lookup), ("OTN", otn_lookup), ("USGS-files", local_files_lookup)):
            try:
                missing = [t for t in todo if t not in found]
                if not missing:
                    break
                for tag, (common, sci, proj) in fn(set(missing)).items():
                    found[tag] = {"common": title_species(common), "scientific": sci,
                                  "project": proj, "source": name,
                                  "resolved_utc": _now()}
            except Exception as e:
                if failures_log is not None:
                    failures_log.append(f"{_now()} species source {name} failed: {e}")
        for t in todo:
            if t in found:
                cache[t] = found[t]
            else:
                cache[t] = {"common": UNRESOLVED, "scientific": "", "project": "",
                            "source": "none-yet", "resolved_utc": _now()}
        save_cache(cache)
    return {t: cache[t] for t in tags}
