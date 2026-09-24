# Custom icons

Supplied source artwork: `fish_receiver_SUPPLIED_SOURCE.png` — byte-identical
copy of the user's `Documents/custom images/fish_receiver.png`
(512×512 RGBA, transparent background, dark-blue fish).

It is processed (direct resize, aspect preserved, metadata stripped) through
the validated strict-PNG pipeline into `icons/fish_receiver.png` (64×64,
8-bit RGBA truecolor+alpha, IHDR/IDAT/IEND chunks only, no ICC), which the
production KML references at its absolute published repository URL.
KML-only architecture: zero `.kmz` files.

No LIVE NOAA buoy icon asset exists anywhere. Verified by direct inspection:
`great-lakes-live-environment` contains no `IconStyle` and no icon image
asset (only data-viz PNGs under `site/*/`), and the buoy implementation
(`Projects/Open code/locks/create_live_buoys_kmz.py:522-523`) writes
`doc.kml` via zipfile with **no custom icon at all** (default pushpins);
`generate_live_kml.py` likewise contains no icon code. There are therefore
no buoy dimensions, color mode, or encoder settings to reproduce — any such
"match" would be invented.
