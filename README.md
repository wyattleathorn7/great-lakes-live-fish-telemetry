# 🐟 LIVE GREAT LAKES FISH ACOUSTIC TELEMETRY RECEIVERS

Known public acoustic-telemetry receiver network across the Great Lakes, with
**persistent placemarks** whose **status + live information change automatically**
as the authoritative sources change. Receiver existence ≠ receiver status: an
OFFLINE or DISMANTLED receiver stays on the map.

Open `kml/LIVE_GREAT_LAKES_FISH_ACOUSTIC_TELEMETRY_RECEIVERS.kml` in Google Earth.
Start with the single-receiver proof: `kml/TEST_SINGLE_RECEIVER.kml`.

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
| A | USGS real-time telemetry (live) | 14 linked + seasonal rows | live status + live TagID detections |
| B | GLATOS deployments via USGS redhorse release (DOI 10.5066/P13H22V6) | 565 unique stations | Erie/Sandusky/Cuyahoga existence + history |
| C | GLATOS deployments via USGS sturgeon release (DOI 10.5066/P142JQOJ) | 3,990 unique stations | Huron–Erie corridor + connected waters |
| D | OTN ERDDAP `view_otn_aat_receivers` (Great Lakes bbox) | 386 unique receivers | basin-wide deployments + projects |

Dedupe: exact station-ID merge across B/C (+456), 150 m proximity merge across
all sources (+707, e.g. live Brady's Island = GLATOS `LSR-021`), all provenance
URLs retained per placemark. Current total: **3,791 placemarks**
(10 ONLINE, 3 OFFLINE live-linked, remainder OFFLINE historical).

Consulted but excluded with reasons (`source/provenance.json`): GLATOS member
portal (login-only), RAFT ReceiverMap (no public bulk API), MI DNR Macatawa
muskie receivers (no public coords), USGS salmon/whitefish releases (no receiver
tables), 5 live rows without authoritative coords (incl. malformed ID
`412652509111316` and 4 `Discontinued` rows) — never geocoded or invented.

## Species honesty

The live feed publishes **TagIDs, not species** (species summaries go to
subscribers / RAFT lookup). Placemarks therefore report TagID-level live counts
and never invent a species list. Historical receivers cite their project context
(redhorse 2022–2025, sturgeon 2011–2024, OTN project codes) as last-known only.

## Coordinates

USGS NWIS site coordinates where available; USGS coordinate-derived station IDs
(validated ≤ ~170 m against NWIS siblings) otherwise; release-file deployment
coordinates for GLATOS/OTN records. Method per placemark in `coord_method`.

## Icon

`icons/fish_receiver.png` — 64×64 RGBA, genuinely transparent, antialiased.
The specified `custom icons/fish copy3.png` was **not present** in the workspace
and the buoy KMZs use default pushpins (no custom buoy icon asset exists), so a
replacement fish icon was generated; drop the real `fish copy3.png` into
`custom icons/` and re-run the icon step to adopt it (see
`custom icons/README.md`).

## Automation

`.github/workflows/update.yml` runs hourly: fetch → source-version check (skip if
unchanged) → classify → update counts/timestamps/history → publish KML.
`OFFLINE → ONLINE` recovery is automatic by stable ID.

## Layout

```
├── icons/fish_receiver.png   kml/*.kml   data/{live_receivers,live_detections,
│   receiver_status_history,source_state}.json   source/{provenance.json,
│   fetch_failures.log,cache/}   scripts/generate_fish_receivers.py
```
