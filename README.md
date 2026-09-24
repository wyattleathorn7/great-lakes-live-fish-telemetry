# 🐟 LIVE GREAT LAKES FISH ACOUSTIC TELEMETRY RECEIVERS

Known public acoustic-telemetry receiver network across the Great Lakes, with
**persistent placemarks** whose **status + live information change automatically**
as the authoritative sources change. Receiver existence ≠ receiver status.

Open `kml/LIVE_GREAT_LAKES_FISH_ACOUSTIC_TELEMETRY_RECEIVERS.kmz` in Google
Earth (KMZ packages the icon, so it renders offline). Start with the
single-receiver proof: `kml/TEST_SINGLE_RECEIVER.kmz`. `kml/ICON_TEST.kmz`
verifies the icon alone.

Current inventory: **9,215 placemarks** — 11 ONLINE with live species counts,
3,157 ONGOING — NO LIVE FEED (deployed per GLATOS map, no public feed),
3,120 SEASONAL — CURRENTLY OUT OF SEASON, 2,446 HISTORICAL — NOT CURRENTLY
DEPLOYED, 481 UNKNOWN. Every placemark carries an evidence-based reason
(`data/receiver_audit.json` holds the full per-receiver audit table).
See `audit/COVERAGE_GAP_REPORT.md` for the source-backed accounting.

## Receiver lifecycle

`ONLINE ⇄ OFFLINE → DISMANTLED / REMOVED`. Same placemark, stable receiver ID,
status history in `data/receiver_status_history.json`. OFFLINE placemarks keep
coordinates/ID/history and show `Current live detections: UNAVAILABLE` (last-known
data, if any, is labeled **LAST KNOWN LIVE DATA**, never current). ONLINE with
zero detections (`Tag: 0`) is NOT offline.

## Live source (current status + current detections)

**USGS Central Midwest Water Science Center — Real-Time Fish Telemetry**
(hourly, near-real-time acoustic receivers; GLRI-supported invasive-carp network):
<https://cm.water.usgs.gov/data/Fish_Tracks_Real_Time/>

Station pages publish 5-minute records (`VRTagCount` = detection events incl.
repeats, `VRUniqTagCount` = unique tags, `TagID`). Counts are reported exactly
that way — detection events are **never** labeled as fish. Explicit source states
(`Discontinued`, `Removed for season`) are honored; otherwise a documented
30-hour freshness threshold for this hourly network applies (see
`source/provenance.json`). Zero detections ≠ offline. Source outages are logged
to `source/fetch_failures.log` and the previous valid KML is retained.

## Union inventory (not one network)

| # | Source | Receivers | Role |
|---|--------|-----------|------|
| A | USGS real-time telemetry (live) | 13 placeable | live status + live TagID detections |
| B | GLATOS deployments via USGS redhorse release (DOI 10.5066/P13H22V6) | 565 unique stations | Erie/Sandusky/Cuyahoga existence + history |
| C | GLATOS deployments via USGS sturgeon release (DOI 10.5066/P142JQOJ) | 3,990 unique stations | Huron–Erie corridor + connected waters |
| D | OTN ERDDAP `view_otn_aat_receivers` (Great Lakes bbox) | 386 unique receivers | basin-wide deployments + projects |
| E | GLATOS public map (`glatos.org/map`, Ongoing/Proposed/Finished) | 9,928 station identities | current deployment status + missing stations |

Dedupe: 456 exact-ID merges + 513 equivalent-code merges (same
`(ALPHA, NUMBER)` station code across naming conventions, co-located;
identical codes at distance kept as one redeployed station). Distinct station
numbers are never merged. Every merge logs evidence (`audit/merge_review.json`).

Consulted but excluded with reasons (`source/provenance.json`): GLATOS member
portal (login-only detections), RAFT ReceiverMap (no public bulk API),
MI DNR Macatawa muskie receivers (no public coords), USGS salmon/whitefish
releases (no receiver tables), 5 live rows without authoritative coords
(incl. malformed ID `412652509111316` and 4 `Discontinued` rows) — never
geocoded or invented.

## Species (TagID → species layer)

Live feeds publish TagIDs. `scripts/species.py` resolves each tag —
RAFT transmitter lookup → OTN animal releases → USGS tag-metadata files —
into `data/tag_species_cache.json`, retried hourly. The canonical
28-species registry (`source/species_registry.json`, built by
`scripts/species_registry.py` from tag evidence + cited GLATOS project
metadata) appears one-per-line in every ONLINE description with zeros.
Unresolvable tags are counted only in a data-quality note, never invented.
24 h reporting window, 7-day detection history (`data/detection_history.json`)
with aging to 0. Counts are detection events, never fish. ONLINE + zero stays
ONLINE. No raw TagIDs in descriptions.

## Coordinates

USGS NWIS site coordinates where available; USGS coordinate-derived station IDs
(validated ≤ ~170 m against NWIS siblings) otherwise; release-file deployment
coordinates for GLATOS/OTN records. Method per placemark in `coord_method`.

## Icon

`icons/fish_receiver.png` — 64×64 RGBA, 8-bit truecolor+alpha, genuinely
transparent, minimal chunks (IHDR/IDAT/IEND only, no ICC), verified
programmatically. KML references `icons/fish_receiver.png`, which resolves
both next to a standalone KML and packaged inside the KMZs (`doc.kml` +
`icons/fish_receiver.png`), fixing the unsupported-format/relative-path
failure. No buoy icon asset exists anywhere (buoy KMZs use default pushpins),
so there were no buoy dimensions to match — documented in
`custom icons/README.md`; drop the real `fish copy3.png` there to adopt it.

## Automation

`.github/workflows/update.yml` runs hourly: fetch → source-version check
(content-aware publish: identical bytes are never republished) → classify →
resolve new TagIDs → update 24 h counts / 7-day history / timestamps →
publish KML+KMZ. `OFFLINE → ONLINE` recovery is automatic by stable ID.

## Layout

```
├── icons/fish_receiver.png   kml/*.kml + *.kmz (KMZ = doc.kml + packaged icon)
│   data/{live_receivers,live_detections,detection_history,last_known_species,
│   tag_species_cache,receiver_status_history,source_state}.json
│   source/{provenance.json,fetch_failures.log,cache/}
│   scripts/{generate_fish_receivers.py,species.py,audit.py}   audit/
```
