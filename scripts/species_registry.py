#!/usr/bin/env python3
"""Canonical tracked-species registry, generated from authoritative metadata.

Inputs: tag-evidence species (RAFT cache + USGS file columns) and GLATOS
project ShortTitles matched against data/fish_lexicon.json (word-boundary,
case-insensitive; every lexicon entry carries its citation).
Output: source/species_registry.json {order, entries:{species:{scientific,
evidence:[...]}}}. Nothing is hard-coded here beyond the cited lexicon.
"""
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE, "source", "cache")


def build():
    lex = json.load(open(os.path.join(BASE, "data", "fish_lexicon.json")))
    registry = {}

    def add(name, sci, ev):
        e = registry.setdefault(name, {"scientific": sci, "evidence": []})
        if ev not in e["evidence"]:
            e["evidence"].append(ev)

    # 1) tag-evidence species
    cache = json.load(open(os.path.join(BASE, "data", "tag_species_cache.json")))
    for tag, m in cache.items():
        common = m.get("common", "")
        if common in ("UNRESOLVED_TAG", "") or "/" in common:
            continue
        for le in lex:
            if common.lower() == le["name"].lower():
                add(le["name"], le["scientific"], f"live tag {tag} ({m.get('source')})")
    usgs_file_species = {
        "tag_meta.csv": [("Lake Sturgeon", "Acipenser fulvescens")],
        "GC_walleye_tag_data.csv": [("Walleye", "Sander vitreus"),
                                    ("Grass Carp", "Ctenopharyngodon idella")],
        "Mox_detections.csv": [("Shorthead Redhorse", "Moxostoma macrolepidotum"),
                               ("Silver Redhorse", "Moxostoma anisurum")],
        "five_native_species_release": [("White Bass", "Morone chrysops"),
                                        ("Channel Catfish", "Ictalurus punctatus"),
                                        ("Freshwater Drum", "Aplodinotus grunniens"),
                                        ("Smallmouth Buffalo", "Ictiobus bubalus")],
    }
    for fname, spp in usgs_file_species.items():
        for name, sci in spp:
            add(name, sci, f"USGS file {fname}")

    # 2) GLATOS project-title evidence (map pins carry authoritative ShortTitles)
    pins = None
    for f in sorted(os.listdir(CACHE)):
        if f.startswith("glatos_map_pins_"):
            pins = json.load(open(os.path.join(CACHE, f)))["Pins"]
    if pins:
        for p in pins:
            title = f"{p.get('ShortTitle') or ''} [{p.get('Project') or ''}]"
            for le in lex:
                for term in le["terms"]:
                    pat = term if term.startswith(" ") else r"\b" + re.escape(term) + r"\b"
                    if re.search(pat, " " + title.lower() + " " if term.startswith(" ")
                                 else title, re.IGNORECASE):
                        add(le["name"], le["scientific"],
                            f"GLATOS project {p.get('Project')}: {p.get('ShortTitle')}")
                        break
    order = sorted(registry)
    out = {"order": order, "entries": registry,
           "n_projects_scanned": len({(p.get('Project')) for p in pins}) if pins else 0}
    json.dump(out, open(os.path.join(BASE, "source", "species_registry.json"), "w"), indent=1)
    return out


if __name__ == "__main__":
    reg = build()
    print(len(reg["order"]), "species")
    for s in reg["order"]:
        print(f" - {s} ({len(reg['entries'][s]['evidence'])} evidence)")
