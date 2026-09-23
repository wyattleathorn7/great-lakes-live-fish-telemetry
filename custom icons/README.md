# Custom icons

Expected: `fish copy3.png` (the fish artwork for every receiver placemark).

Status 2026-09-23: the file was **not found anywhere in the workspace**
(searched `Default Project`, `~/Documents`, `custom images/`), and the LIVE
NOAA buoy KMZs contain **no custom buoy icon asset** (default pushpins only,
so there were no buoy dimensions to match). `icons/fish_receiver.png`
(64×64 RGBA, transparent, antialiased, programmatically verified) was generated
as a stand-in.

To adopt the real artwork: place `fish copy3.png` in this folder, remove its
background to true transparency (RGBA, antialiased edges, no white/colored
box), verify corners are transparent programmatically, resize canvas to the
final standard (currently 64×64 — update only with a measured buoy asset, never
a guess), confirm one test placemark in Google Earth, then re-run the generator.
