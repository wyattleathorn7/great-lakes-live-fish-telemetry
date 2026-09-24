# Custom icons

Expected: `fish copy3.png` (the fish artwork for every receiver placemark).

Status 2026-09-23: the file was **not found anywhere in the workspace**
(searched `Default Project`, `~/Documents`, `custom images/`), and no LIVE
NOAA buoy icon asset exists anywhere. Verified by direct inspection:
`great-lakes-live-environment` contains no `IconStyle` and no icon image
asset (only data-viz PNGs under `site/*/`), and the buoy implementation
(`Projects/Open code/locks/create_live_buoys_kmz.py:522-523`) writes
`doc.kml` via zipfile with **no custom icon at all** (default pushpins);
`generate_live_kml.py` likewise contains no icon code. There are therefore
no buoy dimensions, color mode, or encoder settings to reproduce — any such
"match" would be invented. `icons/fish_receiver.png`
(64×64, 8-bit RGBA truecolor+alpha, IHDR/IDAT/IEND chunks only, no ICC —
programmatically verified, Google-Earth-compatible) is packaged inside the
KMZs at `icons/fish_receiver.png`, mirroring the buoy KMZ pattern
(`doc.kml` at archive root, same zipfile/ZIP_DEFLATED method).

To adopt the real artwork: place `fish copy3.png` in this folder, remove its
background to true transparency (RGBA, antialiased edges, no white/colored
box), verify corners are transparent programmatically, resize canvas to the
final standard (currently 64×64 — update only with a measured buoy asset, never
a guess), confirm one test placemark in Google Earth, then re-run the generator.
