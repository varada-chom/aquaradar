# AquaRadar TH (Phase 1)

Collector + unified signals + map. Python 3.11 stdlib only, no API keys.

```
collector/collect.py       fetch -> normalize -> province summary -> site/latest.json
collector/test_collect.py  unit tests (run: python -m unittest discover -s collector)
web/index.html             Leaflet map (province view zoomed out, stations zoomed in)
.github/workflows/collect.yml   every 30 min: test, collect, deploy to Pages; every 6 h also commit archive/
```

## Setup
1. Push to a **public** repo (private repos have limited free Actions minutes at this cadence; check current GitHub terms).
2. Settings > Pages > Source: **GitHub Actions**.
3. Actions > collect > Run workflow. The map appears at the Pages URL.

## Try locally (synthetic data, clearly flagged as mock)
```
python collector/collect.py --mock --out site && cp web/index.html site/ && python -m http.server -d site 8000
```

## Unified signal
`{id, name, prov, amp, lat, lon, kind: wl|rain|ews, val, bank, sev 0-4, age (min)}`
Severity: 0 normal, 1 watch, 2 prepare, 3 high, 4 critical or over-bank.

## Not yet verified (check before relying on it)
- The collector has been tested against synthetic payloads built from the documented field names, **not against the live APIs** (the build sandbox cannot reach those hosts). Run the workflow once and read the log: each source prints `ok`/`n`.
- Rain severity cut-offs (10/35/90 mm per 24 h) follow the usual TMD classes; confirm before use.
- Action versions (checkout/setup-python v7, Pages trio v5/v6) were the latest tags found; if a step fails on version, adjust the major.
- OSM public tiles are for light use only (see OSM tile usage policy); use another provider or self-hosted tiles for real traffic.
- Station names from the APIs are escaped before display. Keep it that way.
- Official orders (ปภ., province) always override anything shown here.

## Next
HII flashflood (needs your own token), flood_road, SAR flood extent, tambon boundaries, personal plan layer.
