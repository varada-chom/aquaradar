#!/usr/bin/env python3
"""AquaRadar collector (Python 3.11+, stdlib only).

fetch -> normalize -> unified signals -> province summary -> <out>/latest.json
Severity scale used by every source: 0 normal, 1 watch, 2 prepare, 3 high, 4 critical/over-bank.
Exit code 1 when NO source returned data, so the workflow never deploys an empty map.
"""
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

TW = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public"
EWS = "https://ews.dwr.go.th/ews/web-service/stn"
UA = "Mozilla/5.0 (AquaRadar prototype)"
ICT = timezone(timedelta(hours=7))
MAX_RAIN_ROWS = 600


def fetch(url, form=None, tries=3, timeout=30):
    last = None
    for i in range(tries):
        try:
            if form is None:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            else:
                b = "----aqr%d" % int(time.time())
                body = "".join(
                    '--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)
                    for k, v in form.items()
                ) + "--%s--\r\n" % b
                req = urllib.request.Request(
                    url, data=body.encode("utf-8"),
                    headers={"User-Agent": UA, "Content-Type": "multipart/form-data; boundary=" + b})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # network, HTTP, JSON: retry then report
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError("%s: %s" % (url, last))


def records(p, depth=0):
    """Longest list of dicts found in a (possibly nested) payload."""
    if isinstance(p, list):
        return [x for x in p if isinstance(x, dict)]
    best = []
    if isinstance(p, dict) and depth < 4:
        for v in p.values():
            r = records(v, depth + 1)
            if len(r) > len(best):
                best = r
    return best


def th(x):
    if isinstance(x, dict):
        return str(x.get("th") or x.get("en") or "")
    return "" if x is None else str(x)


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) < 1e9 else None


def ptime(s):
    """'YYYY-MM-DD HH:MM[:SS]' in Thai time (UTC+7) -> epoch seconds, or None."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", str(s or ""))
    if not m:
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi, tzinfo=ICT).timestamp()
    except ValueError:
        return None


def coords(lat, lon):
    a, b = num(lat), num(lon)
    if a is None or b is None or not (4 <= a <= 22 and 96 <= b <= 107):
        return None
    return round(a, 5), round(b, 5)


WL_SEV = {2: 1, 3: 2, 4: 3, 5: 4}


def norm_wl(p):
    out = []
    for r in records(p):
        st = r.get("station") or {}
        c = coords(st.get("tele_station_lat"), st.get("tele_station_long"))
        t = ptime(r.get("waterlevel_datetime"))
        if not c or t is None:
            continue
        g = r.get("geocode") or {}
        txt = str(r.get("diff_wl_bank_text") or "")
        d = num(r.get("diff_wl_bank"))
        over = txt.startswith("ล้นตลิ่ง")
        under = txt.startswith("ต่ำกว่าตลิ่ง")
        bank = None if d is None or not (over or under) else (abs(d) if over else -abs(d))
        sev = 4 if over else WL_SEV.get(int(num(r.get("situation_level")) or 0), 0)
        out.append(dict(
            id="wl:%s" % st.get("id", r.get("id")), name=th(st.get("tele_station_name")) or "สถานีน้ำ",
            prov=th(g.get("province_name")), amp=th(g.get("amphoe_name")), lat=c[0], lon=c[1],
            kind="wl", val=num(r.get("waterlevel_msl")), bank=bank, sev=sev, t=t))
    return out


def rain_sev(mm):
    return 3 if mm >= 90 else 2 if mm >= 35 else 1 if mm >= 10 else 0


def norm_rain(p):
    out = []
    for r in records(p):
        st = r.get("station") or {}
        c = coords(st.get("tele_station_lat"), st.get("tele_station_long"))
        t = ptime(r.get("rainfall_datetime"))
        mm = num(r.get("rain_24h"))
        if not c or t is None or mm is None or not (1 <= mm <= 800):
            continue
        g = r.get("geocode") or {}
        out.append(dict(
            id="rain:%s" % st.get("id", r.get("id")), name=th(st.get("tele_station_name")) or "สถานีฝน",
            prov=th(g.get("province_name")), amp=th(g.get("amphoe_name")), lat=c[0], lon=c[1],
            kind="rain", val=mm, bank=None, sev=rain_sev(mm), t=t))
    out.sort(key=lambda x: -x["val"])
    return out[:MAX_RAIN_ROWS]


EWS_SEV = {1: 1, 2: 2, 3: 4}


def norm_ews(p):
    out = []
    for r in records(p):
        c = coords(r.get("latitude"), r.get("longitude"))
        if not c:
            continue
        val = num(r.get("wl")) if r.get("stn_type") == "WL" else num(r.get("rain12h"))
        out.append(dict(
            id="ews:%s" % r.get("stn"), name=str(r.get("name") or r.get("stn") or "สถานี EWS"),
            prov=str(r.get("province") or ""), amp=str(r.get("amphoe") or ""), lat=c[0], lon=c[1],
            kind="ews", val=val, bank=None, sev=EWS_SEV.get(int(num(r.get("status")) or 0), 0),
            t=ptime(r.get("date"))))
    return out


def summarize(sigs):
    by = {}
    for s in sigs:
        name = s["prov"] or "ไม่ระบุ"
        a = by.setdefault(name, dict(prov=name, n=0, max=0, over=0, rain_max=0.0, lat=0.0, lon=0.0, counts=[0] * 5))
        a["n"] += 1
        a["max"] = max(a["max"], s["sev"])
        a["counts"][s["sev"]] += 1
        a["lat"] += s["lat"]
        a["lon"] += s["lon"]
        if s["kind"] == "wl" and (s.get("bank") or 0) > 0:
            a["over"] += 1
        if s["kind"] == "rain":
            a["rain_max"] = max(a["rain_max"], s["val"] or 0)
    out = []
    for a in by.values():
        a["lat"] = round(a["lat"] / a["n"], 4)
        a["lon"] = round(a["lon"] / a["n"], 4)
        a["rain_max"] = round(a["rain_max"], 1)
        out.append(a)
    return sorted(out, key=lambda a: (-a["max"], -a["over"], a["prov"]))


def mock_payloads(now):
    """Synthetic payloads in the documented response shapes. For demos and tests only."""
    rnd = random.Random(7)
    centers = [("จังหวัดจำลอง ก", 15.7, 100.1), ("จังหวัดจำลอง ข", 14.4, 100.6),
               ("จังหวัดจำลอง ค", 14.0, 100.5), ("จังหวัดจำลอง ง", 13.7, 100.5)]
    stamp = lambda: datetime.fromtimestamp(now - rnd.randint(5, 120) * 60, ICT).strftime("%Y-%m-%d %H:%M")
    wl, rain, ews = [], [], []
    n = 0
    for pi, (pn, la, lo) in enumerate(centers):
        geo = {"province_name": {"th": pn}, "amphoe_name": {"th": "อำเภอจำลอง"}}
        for i in range(12):
            n += 1
            st = {"id": n, "tele_station_name": {"th": "สถานีจำลอง %d" % n},
                  "tele_station_lat": la + rnd.uniform(-.3, .3), "tele_station_long": lo + rnd.uniform(-.3, .3)}
            d = rnd.uniform(-2, 0.6 + pi * 0.3)
            wl.append({"station": st, "geocode": geo, "waterlevel_datetime": stamp(), "waterlevel_msl": round(rnd.uniform(1, 12), 2),
                       "diff_wl_bank": round(abs(d), 2), "diff_wl_bank_text": ("ล้นตลิ่ง (ม.)" if d > 0 else "ต่ำกว่าตลิ่ง (ม.)"),
                       "situation_level": 5 if d > 0 else rnd.choice([1, 1, 2, 3])})
            rain.append({"station": dict(st, id=1000 + n), "geocode": geo, "rainfall_datetime": stamp(),
                         "rain_24h": round(rnd.uniform(0, 40 + pi * 25), 1)})
            ews.append({"stn": "STN%04d" % n, "name": "EWS จำลอง %d" % n, "stn_type": rnd.choice(["RF", "WL"]),
                        "province": pn, "amphoe": "อำเภอจำลอง", "latitude": la + rnd.uniform(-.3, .3),
                        "longitude": lo + rnd.uniform(-.3, .3), "status": rnd.choice([0, 0, 0, 1, 2, 3][:3 + pi + 1]),
                        "wl": "N/A", "rain12h": round(rnd.uniform(0, 60), 1), "date": stamp()})
    return {"wl": {"result": "OK", "waterlevel_data": {"data": wl}}, "rain": {"result": "OK", "data": rain}, "ews": ews}


# (key, url, form, normalizer, fetch options). EWS timed out from GitHub's US runners on the
# first live run, so it gets one short attempt instead of 3 x 30 s that delay every deploy.
SOURCES = [("wl", TW + "/waterlevel_load", None, norm_wl, {}),
           ("rain", TW + "/rain_24h", None, norm_rain, {}),
           ("ews", EWS, {"action": "LoadStation"}, norm_ews, {"tries": 1, "timeout": 15})]


def collect(mock=False, now=None):
    now = now or time.time()
    payloads = mock_payloads(now) if mock else None
    sigs, meta = [], {}
    for key, url, form, fn, kw in SOURCES:
        try:
            rows = fn(payloads[key] if mock else fetch(url, form, **kw))
            ts = [r["t"] for r in rows if r["t"] is not None]
            meta[key] = {"ok": True, "n": len(rows), "newest_age_min": int((now - max(ts)) / 60) if ts else None}
            sigs += rows
        except Exception as e:
            meta[key] = {"ok": False, "n": 0, "error": str(e)[:200]}
    for s in sigs:
        s["age"] = None if s["t"] is None else max(0, int((now - s["t"]) / 60))
    signals = [{k: v for k, v in s.items() if k != "t"} for s in sigs]
    return {"schema": 1, "mock": bool(mock), "generated_ts": int(now),
            "generated_at": datetime.fromtimestamp(now, ICT).isoformat(timespec="minutes"),
            "sources": meta, "provinces": summarize(signals), "signals": signals}


def write_archive(dirp, res):
    os.makedirs(dirp, exist_ok=True)
    day = datetime.fromtimestamp(res["generated_ts"], ICT).strftime("%Y-%m-%d")
    path = os.path.join(dirp, day + ".json")
    snaps = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            snaps = json.load(f)
    over = [{k: s[k] for k in ("id", "name", "prov", "bank")} for s in res["signals"]
            if s["kind"] == "wl" and (s.get("bank") or 0) > 0][:200]
    snaps.append({"at": res["generated_at"], "provinces": res["provinces"], "overbank": over})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snaps, f, ensure_ascii=False, separators=(",", ":"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--archive", default=None)
    ap.add_argument("--mock", action="store_true", help="synthetic data for demo/testing")
    a = ap.parse_args(argv)
    res = collect(mock=a.mock)
    for k, m in res["sources"].items():
        print("%-5s ok=%s n=%s %s" % (k, m["ok"], m["n"], m.get("error", "")))
    if not res["signals"]:
        print("no data from any source; not writing output", file=sys.stderr)
        return 1
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, separators=(",", ":"))
    if a.archive:
        write_archive(a.archive, res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
