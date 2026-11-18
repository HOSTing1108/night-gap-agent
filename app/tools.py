"""Deterministic analysis tools the agent calls. Standard library only.

Every function returns plain dicts/lists so results can go to the LLM as JSON.
Raw rows never leave this module: only profiles, aggregates and scores do.
"""
from __future__ import annotations

import csv
import io
import os
import re
from collections import Counter, defaultdict

from geo import PointIndex, guess_tm_crs, haversine, in_jeju, tm_to_wgs84

# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
ENCODINGS = ("utf-8-sig", "cp949", "euc-kr")


def _open_text(path):
    for enc in ENCODINGS:
        try:
            with open(path, encoding=enc) as f:
                f.read(65536)
            return open(path, encoding=enc, newline=""), enc
        except UnicodeDecodeError:
            continue
    raise ValueError(f"인코딩을 알 수 없어요: {os.path.basename(path)}")


def iter_rows(path):
    f, _ = _open_text(path)
    with f:
        yield from csv.DictReader(f)


def profile_csv(path, sample_rows: int = 2000):
    """Column names, row count, and per-column hints. No raw rows are returned."""
    f, enc = _open_text(path)
    with f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        samples = defaultdict(list)
        n = 0
        for row in reader:
            n += 1
            if n <= sample_rows:
                for c in cols:
                    samples[c].append((row.get(c) or "").strip())
    hints = {}
    for c in cols:
        vals = [v for v in samples[c] if v and v.upper() != "NA"]
        nums = [_to_float(v) for v in vals]
        nums = [x for x in nums if x is not None]
        h = {"non_empty": len(vals), "distinct": len(set(vals))}
        if nums and len(nums) >= 0.9 * max(1, len(vals)):
            h["numeric"] = True
            h["min"], h["max"] = round(min(nums), 4), round(max(nums), 4)
        else:
            h["top"] = [v for v, _ in Counter(vals).most_common(5)]
        hints[c] = h
    return {"file": os.path.basename(path), "encoding": enc, "rows": n, "columns": cols, "hints": hints}


def _to_float(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Identifying what a file is
# --------------------------------------------------------------------------
DATASET_RULES = {
    "bus_stops": {"need": ["bus_station_id", "latitude", "longitude"], "forbid": ["get_on_off_type"]},
    "bus_usage": {"need": ["bus_station_id", "base_hour", "get_on_off_type", "user_count"]},
    "grid_pop": {"need": ["grid", "time"], "any": ["resd_pop_cnt", "visit_pop_cnt", "work_pop_cnt"]},
    "dong_pop": {"need": ["time"], "any": ["adong_nm", "adm_nm", "emd_nm", "dong"]},
}
POINT_KEYWORDS = {
    "cctv": ["설치목적", "카메라대수", "카메라화소", "촬영방면", "cctv"],
    "bell": ["비상벨", "경찰연계", "연계방식", "설치위치"],
    "streetlight": ["보안등", "가로등", "등주", "램프", "lamp", "light"],
    "school": ["학교명", "학교급구분", "교육청", "학교id", "school"],
}
LAT_PAT = re.compile(r"(위도|latitude|^lat$|wgs84위도|y좌표\(위도\))", re.I)
LON_PAT = re.compile(r"(경도|longitude|^lon$|^lng$|wgs84경도)", re.I)
X_PAT = re.compile(r"(좌표.*x|^x$|x좌표|tm_?x|경도좌표x)", re.I)
Y_PAT = re.compile(r"(좌표.*y|^y$|y좌표|tm_?y)", re.I)


def identify_dataset(profile):
    """Guess the dataset kind from a profile. Returns kind, confidence, reasons."""
    cols = profile["columns"]
    low = [c.lower().strip() for c in cols]
    reasons = []
    for kind, rule in DATASET_RULES.items():
        if all(n in low for n in rule["need"]) and not any(x in low for x in rule.get("forbid", [])):
            if "any" in rule and not any(a in low for a in rule["any"]):
                continue
            reasons.append(f"필수 컬럼 {rule['need']}가 모두 있어요")
            return {"kind": kind, "confidence": 0.95, "reasons": reasons, "coords": None}
    if {"stationid", "board", "transfer", "days"} <= set(low):
        reasons.append("정류장 아이디별 승차·환승 합계와 일수가 있어요: 정류장별 하루 이용 규모예요")
        return {"kind": "bus_daily_usage", "confidence": 0.9, "reasons": reasons, "coords": None}
    if {"base_date", "base_hour", "user_count"} <= set(low) and not any("station" in c or "정류" in c for c in low):
        reasons.append("날짜·시간대·이용자 수만 있고 정류장 컬럼이 없어요: 제주 전체 시간대별 승차 합계예요")
        return {"kind": "bus_hourly_total", "confidence": 0.9, "reasons": reasons, "coords": None}
    stop_cols = stop_columns(cols)
    if stop_cols and detect_coords(profile) and not any(k in " ".join(low) for k in ("설치목적", "카메라", "비상벨", "보안등")):
        reasons.append(f"정류소 컬럼({stop_cols['id']}, {stop_cols['name']})과 위경도가 있어요")
        return {"kind": "bus_stops", "confidence": 0.9, "reasons": reasons, "coords": detect_coords(profile), "cols": stop_cols}
    joined = " ".join(low) + " " + profile["file"].lower()
    scores = {k: sum(kw in joined for kw in kws) for k, kws in POINT_KEYWORDS.items()}
    kind, score = max(scores.items(), key=lambda kv: kv[1])
    coords = detect_coords(profile)
    if score == 0 or coords is None:
        return {"kind": "unknown", "confidence": 0.0, "coords": coords,
                "reasons": ["알려진 형식과 맞는 컬럼을 찾지 못했어요"]}
    reasons.append(f"'{kind}' 관련 단어 {score}개가 컬럼이나 파일명에 있어요")
    conf = 0.6 + 0.1 * min(score, 3)
    return {"kind": kind, "confidence": round(conf, 2), "reasons": reasons, "coords": coords}


def stop_columns(cols):
    """Find id/name columns of a bus stop table in either the center's English or the portal's Korean headers."""
    low = {c: c.lower().replace(" ", "") for c in cols}
    cid = next((c for c in cols if low[c] in ("bus_station_id", "정류소아이디", "정류소id", "정류장아이디", "정류장id", "station_id")), None)
    cname = next((c for c in cols if low[c] in ("bus_station_name", "정류소명", "정류장명", "station_name")), None)
    if not cid:
        cid = next((c for c in cols if ("정류소" in c or "정류장" in c) and ("아이디" in c or "id" in low[c] or "번호" in c)), None)
    if not cname:
        cname = next((c for c in cols if ("정류소" in c or "정류장" in c) and "명" in c), None)
    return {"id": cid, "name": cname} if cid and cname else None


def detect_coords(profile):
    cols, hints = profile["columns"], profile["hints"]
    lat = next((c for c in cols if LAT_PAT.search(c) and hints[c].get("numeric")), None)
    lon = next((c for c in cols if LON_PAT.search(c) and hints[c].get("numeric")), None)
    if lat and lon and 30 < hints[lat]["min"] and hints[lat]["max"] < 40:
        return {"type": "wgs84", "lat": lat, "lon": lon}
    x = next((c for c in cols if X_PAT.search(c) and hints[c].get("numeric")), None)
    y = next((c for c in cols if Y_PAT.search(c) and hints[c].get("numeric") and c != x), None)
    if x and y:
        return {"type": "tm", "x": x, "y": y, "crs": None}
    return None


# --------------------------------------------------------------------------
# Loading point layers (CCTV, bells, streetlights)
# --------------------------------------------------------------------------
def load_points(path, coords, kind, declared_crs: str | None = None):
    """Load a point layer into WGS84, keeping only Jeju. Returns (points, report)."""
    pts, skipped, outside = [], 0, 0
    f, _ = _open_text(path)
    with f:
        fields = csv.DictReader(f).fieldnames or []
    if kind == "cctv":
        purpose_col = next((c for c in fields if "목적" in c), None)
    elif kind == "bell":
        purpose_col = next((c for c in fields if "장소유형" in c), None) or next((c for c in fields if c == "설치위치"), None)
    elif kind == "school":
        purpose_col = next((c for c in fields if "학교급" in c), None)
    else:
        purpose_col = None
    name_col = next((c for c in fields if c in ("학교명", "시설명", "명칭")), None)
    addr_cols = [c for c in fields if "주소" in c or "관리기관" in c]
    raw = []
    for row in iter_rows(path):
        if coords["type"] == "wgs84":
            a, b = _to_float(row.get(coords["lat"])), _to_float(row.get(coords["lon"]))
        else:
            a, b = _to_float(row.get(coords["x"])), _to_float(row.get(coords["y"]))
        if a is None or b is None:
            skipped += 1
            continue
        addr = " ".join((row.get(c) or "") for c in addr_cols)
        city = "서귀포시" if "서귀포" in addr else ("제주시" if "제주시" in addr else "")
        raw.append((a, b, ((row.get(purpose_col) or "").strip() if purpose_col else "", city, town_of_addr(addr),
                           (row.get(name_col) or "").strip() if name_col else "")))
    report = {"file": os.path.basename(path), "kind": kind, "coord_type": coords["type"]}
    if coords["type"] == "tm":
        crs = declared_crs
        if crs is None:
            crs, ratio = guess_tm_crs([r[0] for r in raw], [r[1] for r in raw])
            report["crs_guess_ratio_in_jeju"] = round(ratio, 3)
        report["crs"] = crs
        conv = []
        for x, y, p in raw:
            lat, lon = tm_to_wgs84(x, y, crs)
            conv.append((lat, lon, p))
        raw = conv
    seen, dupes = set(), 0
    for lat, lon, (p, city, town, nm) in raw:
        if not in_jeju(lat, lon):
            outside += 1
            continue
        key = (round(lat, 5), round(lon, 5), p)          # same spot (~1 m) and purpose = one record
        if key in seen:
            dupes += 1
            if kind == "cctv":      # CCTV rows are sites: the same site twice is a double registration
                continue            # bells/lights: several devices at one spot are real, keep them
        seen.add(key)
        pts.append({"lat": lat, "lon": lon, "purpose": p, "city": city or city_of(lat, lon), "town": town, "name": nm})
    report.update({"loaded": len(pts), "skipped_no_coord": skipped, "outside_jeju": outside, "duplicates_merged": dupes})
    if kind == "cctv":
        report["purpose_counts"] = dict(Counter(p["purpose"] or "미상" for p in pts).most_common())
    return pts, report


# --------------------------------------------------------------------------
# Bus stops and night demand
# --------------------------------------------------------------------------
def load_stops(path, ident=None):
    """Load stops from the center's 3-03 table or the portal's Korean stop file. Keeps the latest row per id."""
    prof = profile_csv(path, sample_rows=200)
    cols = (ident or {}).get("cols") or stop_columns(prof["columns"]) or {"id": "bus_station_id", "name": "bus_station_name"}
    coords = (ident or {}).get("coords") or detect_coords(prof) or {"type": "wgs84", "lat": "latitude", "lon": "longitude"}
    latest = {}
    for row in iter_rows(path):
        sid = (row.get(cols["id"]) or "").strip()
        lat, lon = _to_float(row.get(coords.get("lat", "latitude"))), _to_float(row.get(coords.get("lon", "longitude")))
        if not sid or lat is None or lon is None or not in_jeju(lat, lon):
            continue
        prev = latest.get(sid)
        if prev is None or row.get("base_date", "") >= prev["base_date"]:
            latest[sid] = {"id": sid, "name": (row.get(cols["name"]) or sid).strip(), "lat": lat, "lon": lon,
                           "base_date": row.get("base_date", "")}
    return list(latest.values())


def night_demand(path, hours=(21, 22, 23), use_boarding_fallback=False):
    """Average daily alightings per stop in the given hours, plus last active hour.

    Jeju does not require tapping off, so alightings are under-recorded. With
    use_boarding_fallback, night boardings are added as a lower-bound proxy.
    """
    hours = {int(h) for h in hours}
    off = defaultdict(float)
    on = defaultdict(float)
    last_hour = defaultdict(lambda: -1)
    days = set()
    type_totals = Counter()
    for row in iter_rows(path):
        sid = row["bus_station_id"].strip()
        try:
            h = int(row["base_hour"])
            n = float(row["user_count"])
        except (TypeError, ValueError):
            continue
        days.add(row.get("base_date"))
        typ = row.get("get_on_off_type", "").strip()
        type_totals[typ] += n
        if n > 0 and h > last_hour[sid]:
            last_hour[sid] = h
        if h in hours:
            if typ == "하차":
                off[sid] += n
            elif typ in ("승차", "환승"):
                on[sid] += n
    nd = max(1, len(days))
    out = {}
    for sid in set(off) | set(on) | set(last_hour):
        demand = off[sid] / nd
        if use_boarding_fallback:
            demand += on[sid] / nd
        out[sid] = {"night_demand": demand, "last_hour": last_hour[sid]}
    total = sum(type_totals.values()) or 1
    meta = {"days": nd, "hours": sorted(hours),
            "alight_share": round(type_totals.get("하차", 0) / total, 3),
            "boarding_fallback": use_boarding_fallback}
    return out, meta


# --------------------------------------------------------------------------
# Gap scoring
# --------------------------------------------------------------------------
DEFAULT_OPTS = {
    "cctv_radius_m": 100, "bell_radius_m": 200, "light_radius_m": 50,
    "late_hour_threshold": 22,
    "weights": {"eyes": 1.0, "bell": 1.0, "last_bus": 0.5, "light": 1.0},
    "cctv_purposes": ["생활방범", "방범", "어린이보호", "다목적"],
}

GAP_LABEL = {"eyes": "눈 공백(CCTV 없음)", "bell": "벨 공백(비상벨 없음)",
             "last_bus": "막차 공백(일찍 끊김)", "light": "빛 공백(보안등 없음)"}


def daily_usage(path):
    """Aggregated hub API file: stationId, stationName, board, transfer, days -> {id: avg per day}."""
    rows = list(iter_rows(path))
    span = max((int(_to_float(r.get("days")) or 0) for r in rows), default=0) or 1
    out, by_name = {}, defaultdict(float)
    for r in rows:
        sid = (r.get("stationId") or "").strip()
        if not sid:
            continue
        tot = (_to_float(r.get("board")) or 0) + (_to_float(r.get("transfer")) or 0)
        out[sid] = {"night_demand": tot / span, "last_hour": -1}
        by_name[stop_name_key(r.get("stationName"))] += tot / span
    return out, {"days": span, "stations": len(out), "by_name": dict(by_name)}


def stop_name_key(name):
    return re.sub(r"[\s()\[\]·.]", "", name or "")


def _short_key(key):
    """'노형오거리한라병원방면' -> '노형오거리', 'A/B' -> 'A' (looser match for direction-split names)."""
    k = key.split("/")[0]
    return re.sub(r"(가상정류소|[가-힣0-9]*(방면|방향))$", "", k) or k


def match_usage_by_name(stops, by_name):
    """When usage ids differ from stop ids, join on stop names. A name's usage is split evenly across stops sharing it."""
    exact = defaultdict(list)
    for s in stops:
        exact[stop_name_key(s["name"])].append(s["id"])
    short = defaultdict(list)
    for k, ids in exact.items():
        short[_short_key(k)].extend(ids)
    demand, matched, total = {}, 0.0, sum(by_name.values()) or 1
    for k, v in by_name.items():
        ids = exact.get(k) or short.get(_short_key(k))
        if not ids:
            continue
        matched += v
        for sid in ids:
            d = demand.setdefault(sid, {"night_demand": 0.0, "last_hour": -1})
            d["night_demand"] += v / len(ids)
    return demand, round(100 * matched / total, 1)


def hourly_share(path, recent_days=90):
    """Island-wide boardings by time band (hub file 512). Uses the latest `recent_days` dates."""
    by = Counter()
    rows = [(r.get("base_date", ""), r.get("base_hour", ""), _to_float(r.get("user_count")) or 0) for r in iter_rows(path)]
    dates = sorted({d for d, _, _ in rows if d})
    keep = set(dates[-recent_days:])
    for d, h, n in rows:
        if d in keep:
            by[h.strip()] += n
    total = sum(by.values()) or 1
    days = max(1, len(keep))
    bands = [{"band": h, "per_day": round(v / days), "share": round(100 * v / total, 1)} for h, v in sorted(by.items())]
    night = [b for b in bands if b["band"].startswith(("19", "22"))]
    return {"from": min(keep) if keep else "", "to": max(keep) if keep else "", "days": days, "bands": bands,
            "night_share": round(sum(b["share"] for b in night), 1),
            "late_share": next((b["share"] for b in bands if b["band"].startswith("22")), None),
            "late_per_day": next((b["per_day"] for b in bands if b["band"].startswith("22")), None)}


_IDX_CACHE = []   # [(points list, filter key, index, nearest cache)] 같은 자료로 기준만 바꿔 다시 계산할 때 재사용해요


def _cached_index(points, fkey, filt):
    for pts, k, idx, near in _IDX_CACHE:
        if pts is points and k == fkey and idx.n == sum(1 for p in points if filt(p)):
            return idx, near
    idx = PointIndex(((p["lat"], p["lon"], p) for p in points if filt(p)))
    _IDX_CACHE.append((points, fkey, idx, {}))
    del _IDX_CACHE[:-6]
    return idx, _IDX_CACHE[-1][3]


def _nearest(idx, cache, lat, lon):
    k = (lat, lon)
    if k not in cache:
        cache[k] = idx.nearest(lat, lon)
    return cache[k]


def compute_gaps(stops, demand, cctv=None, bells=None, lights=None, opts=None):
    o = {**DEFAULT_OPTS, **(opts or {})}
    w = {**DEFAULT_OPTS["weights"], **(o.get("weights") or {})}

    def keep(p):
        if not o["cctv_purposes"]:
            return True
        return any(k in (p["purpose"] or "") for k in o["cctv_purposes"]) or not p["purpose"]

    idx_c, near_c = _cached_index(cctv, ("cctv", tuple(o["cctv_purposes"] or ())), keep) if cctv is not None else (None, None)
    idx_b, near_b = _cached_index(bells, ("bell",), lambda p: True) if bells is not None else (None, None)
    idx_l = PointIndex(((p["lat"], p["lon"], p) for p in lights), cell_m=100) if lights is not None else None

    rows = []
    no_demand = demand is None
    idx_s = PointIndex(((s["lat"], s["lon"], s) for s in stops), cell_m=500) if no_demand else None
    for s in stops:
        d = {"night_demand": None, "last_hour": -1} if no_demand else demand.get(s["id"])
        if not d:
            continue
        r = {"id": s["id"], "name": s["name"], "lat": s["lat"], "lon": s["lon"],
             "night_demand": None if no_demand else round(d["night_demand"], 2), "last_hour": d["last_hour"], "gaps": []}
        if idx_c is not None:
            hits = idx_c.within(s["lat"], s["lon"], o["cctv_radius_m"])
            r["cctv_n"] = len(hits)
            near = _nearest(idx_c, near_c, s["lat"], s["lon"])
            r["cctv_nearest_m"] = round(near[0]) if near else None
            if not hits:
                r["gaps"].append("eyes")
        if idx_b is not None:
            hits = idx_b.within(s["lat"], s["lon"], o["bell_radius_m"])
            r["bell_n"] = len(hits)
            near = _nearest(idx_b, near_b, s["lat"], s["lon"])
            r["bell_nearest_m"] = round(near[0]) if near else None
            if not hits:
                r["gaps"].append("bell")
        if idx_l is not None:
            r["light_n"] = len(idx_l.within(s["lat"], s["lon"], o["light_radius_m"]))
            if r["light_n"] == 0:
                r["gaps"].append("light")
        if idx_s is not None:
            r["stops_500m"] = len(idx_s.within(s["lat"], s["lon"], 500))
        if 0 <= d["last_hour"] < o["late_hour_threshold"]:
            r["gaps"].append("last_bus")
        rows.append(r)

    # demand percentile (ties share rank). Without usage data: facility gaps only.
    vals = sorted(r["night_demand"] for r in rows) if not no_demand else []
    n = len(vals)
    for r in rows:
        gap_w = sum(w[g] for g in r["gaps"])
        if no_demand:
            r["demand_pct"] = None
            r["score"] = round(gap_w, 3)
        else:
            below = _bisect_left(vals, r["night_demand"])
            r["demand_pct"] = round(100 * below / max(1, n - 1), 1) if r["night_demand"] > 0 else 0.0
            r["score"] = round(r["demand_pct"] / 100 * gap_w, 3)
        r["gap_labels"] = [GAP_LABEL[g] for g in r["gaps"]]
    far = lambda r: (r.get("cctv_nearest_m") or 99999) + (r.get("bell_nearest_m") or 99999)  # noqa: E731
    rows.sort(key=lambda r: (-r["score"], -(r["night_demand"] or 0), -r.get("stops_500m", 0), -far(r)))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    layers = {"cctv": idx_c is not None, "bell": idx_b is not None, "light": idx_l is not None}
    return {"rows": rows, "options": o, "layers": layers, "facility_only": no_demand}


def _bisect_left(a, x):
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


# --------------------------------------------------------------------------
# Self-checks the agent uses to decide what to do next
# --------------------------------------------------------------------------
def diagnose(result, top_n=10, town_of=None):
    rows = result["rows"]
    top = rows[:top_n]
    issues = []
    if result.get("facility_only"):
        issues.append({"code": "NO_DEMAND", "detail": "이용 현황(3-04)이 없어 밤 하차 수요 없이 시설 공백만으로 순위를 매겼어요"})
        if not result["layers"]["light"]:
            issues.append({"code": "NO_LIGHT_LAYER", "detail": "가로등 위치 자료를 올리면 가로등도 같이 봐요"})
        return issues
    nonzero = [r for r in rows if r["night_demand"] > 0]
    top_d = sorted(r["night_demand"] for r in top)
    median_top = top_d[len(top_d) // 2] if top_d else 0
    if len(nonzero) < top_n * 2:
        issues.append({"code": "THIN_DEMAND", "detail": f"밤 수요가 있는 정류장이 {len(nonzero)}곳뿐이에요"})
    elif median_top < 10:
        issues.append({"code": "THIN_DEMAND", "detail": f"상위 {len(top)}곳의 밤 하차가 대부분 하루 10명 미만이라 순위가 흔들릴 수 있어요"})
    if top and top[-1]["score"] == 0:
        issues.append({"code": "FEW_GAPS", "detail": "상위 10곳 안에 공백이 없는 정류장이 들어 있어요"})
    if town_of:
        c = Counter(town_of(r) for r in top)
        town, k = c.most_common(1)[0] if c else (None, 0)
        if k / max(1, len(top)) > 0.5:
            issues.append({"code": "CONCENTRATED", "detail": f"상위 {len(top)}곳 중 {k}곳이 '{town}'에 몰려 있어요",
                           "town": town})
    if not result["layers"]["light"]:
        issues.append({"code": "NO_LIGHT_LAYER", "detail": "가로등 위치 자료를 올리면 가로등도 같이 봐요"})
    return issues


# --------------------------------------------------------------------------
# Export check (center rule: no value below 10; no back-calculation)
# --------------------------------------------------------------------------
def export_check(rows, min_count=10):
    """Mask small counts in the output table. Returns masked rows and a log."""
    log = []
    out = []
    for r in rows:
        m = dict(r)
        for key in ("night_demand",):
            v = m.get(key)
            if v is not None and 0 < v < min_count:
                m[key] = f"{min_count} 미만"
                log.append(f"{r['name']}: {key} 값을 '{min_count} 미만'으로 바꿨어요")
        # demand_pct and score are applied statistics (rank/index), allowed as numbers.
        out.append(m)
    return out, log


def town_from_name(r):
    m = re.match(r"가상(.+?)정류소", r["name"])
    return m.group(1) if m else r["name"][:2]


# --------------------------------------------------------------------------
# Areas, coverage and place lookup
# --------------------------------------------------------------------------
CRIME_PREVENTION = ("생활방범", "방범", "어린이보호", "다목적")


def city_of(lat, lon):
    """Rough Jeju-si / Seogwipo-si split for summaries only (addresses win when present)."""
    if lat >= 33.90:  # 추자도
        return "제주시"
    if lon < 126.25 and lat >= 33.29:  # 한경면
        return "제주시"
    if lon >= 126.86 and lat < 33.47:  # 성산읍
        return "서귀포시"
    return "제주시" if lat >= 33.36 else "서귀포시"


def overview(rows, min_stops=15):
    """Gap mix by stop count and by usage, and the share of blind stops per 읍/면 (동 folded per city). Aggregates only."""
    cats = {"both": [0, 0.0], "no_cctv": [0, 0.0], "no_bell": [0, 0.0], "covered": [0, 0.0]}
    groups = {}
    for r in rows:
        g = r.get("gaps", [])
        if "eyes" not in g and "bell" not in g and ("cctv_n" not in r or "bell_n" not in r):
            continue
        k = "both" if "eyes" in g and "bell" in g else "no_cctv" if "eyes" in g else "no_bell" if "bell" in g else "covered"
        use = r.get("night_demand") or 0
        cats[k][0] += 1
        cats[k][1] += use
        town = r.get("area") or "?"
        grp = town if town.endswith(("읍", "면")) else f"{city_of(r['lat'], r['lon'])} 동지역"
        a = groups.setdefault(grp, [0, 0])
        a[0] += 1
        a[1] += k == "both"
    n = sum(v[0] for v in cats.values()) or 1
    u = sum(v[1] for v in cats.values())
    return {"cats": {k: {"stops": v[0], "stop_pct": round(100 * v[0] / n, 1),
                         "use_pct": round(100 * v[1] / u, 1) if u else None} for k, v in cats.items()},
            "by_group": sorted(({"group": k, "stops": v[0], "blind": v[1], "pct": round(100 * v[1] / v[0], 1)}
                                for k, v in groups.items() if v[0] >= min_stops), key=lambda x: -x["pct"])}


def area_key(lat, lon, cell_deg=0.02):
    return (round(lat / cell_deg), round(lon / cell_deg))


TOWN_PAT = re.compile(r"(?:제주시|서귀포시)\s+([가-힣0-9]+(?:동|읍|면))(?=\s|$)")


def town_of_addr(addr):
    """'제주특별자치도 제주시 노형동 925' -> '노형동'. Empty when the address has no 동/읍/면."""
    m = TOWN_PAT.search(addr or "")
    if not m:
        return ""
    t = m.group(1)
    return re.sub(r"(?<=[가-힣])([일이삼])동$", lambda k: "123"["일이삼".index(k.group(1))] + "동", t) if len(t) >= 4 else t


def stop_towns(stops, refs, radius_m=700):
    """Per-stop 동/읍/면: the most common town among address points within 700m, else the nearest within 6km."""
    towns = [(p["lat"], p["lon"], p["town"]) for p in refs or [] if p.get("town")]
    if len(towns) < 30:
        return {}
    idx = PointIndex(((a, b, t) for a, b, t in towns), cell_m=1000)
    out = {}
    for s in stops:
        hits = idx.within(s["lat"], s["lon"], radius_m)
        if hits:
            out[s["id"]] = Counter(t for _, t in hits).most_common(1)[0][0]
        else:
            near = idx.nearest(s["lat"], s["lon"], 6000)
            if near:
                out[s["id"]] = near[1]
    return out


def area_labels(stops, refs=None):
    """Label ~2km cells. With address points (CCTV, bells) use their 동/읍/면, else the stop names' first word."""
    if refs:
        towns = [(p["lat"], p["lon"], p["town"]) for p in refs if p.get("town")]
        if len(towns) >= 30:
            idx = PointIndex(((a, b, t) for a, b, t in towns), cell_m=1000)
            out = {}
            for s in stops:
                k = area_key(s["lat"], s["lon"])
                if k in out:
                    continue
                lat, lon = k[0] * 0.02, k[1] * 0.02
                hits = idx.within(lat, lon, 1500)
                if hits:
                    out[k] = Counter(t for _, t in hits).most_common(1)[0][0]
                else:
                    near = idx.nearest(lat, lon, 6000)
                    out[k] = near[1] if near else None
            if sum(v is not None for v in out.values()) >= 0.8 * len(out):
                fallback = area_labels(stops)
                return {k: v or fallback.get(k, "?") for k, v in out.items()}
    words = defaultdict(Counter)
    for s in stops:
        w = re.sub(r"^가상", "", s["name"])
        w = re.split(r"[\s(·.,/-]|정류소|입구|사거리|오거리|삼거리|마을|초등학교|중학교", w)[0][:4] or s["name"][:3]
        words[area_key(s["lat"], s["lon"])][w] += 1
    return {k: c.most_common(1)[0][0] for k, c in words.items()}


def check_coverage(points, kind, stops=None):
    """How well does a point layer cover the island? Flags layers that look incomplete."""
    by_city = Counter(p["city"] or "미상" for p in points)
    rep = {"kind": kind, "total": len(points), "by_city": dict(by_city), "warnings": []}
    stop_city = Counter(city_of(s["lat"], s["lon"]) for s in stops) if stops else Counter()
    if kind == "cctv":
        purposes = Counter(p["purpose"] or "미상" for p in points)
        rep["by_purpose"] = dict(purposes.most_common())
        cp = Counter(p["city"] for p in points if any(k in (p["purpose"] or "") for k in CRIME_PREVENTION))
        rep["crime_prevention_by_city"] = dict(cp)
        for city in ("제주시", "서귀포시"):
            n_city, n_cp = by_city.get(city, 0), cp.get(city, 0)
            if n_city and n_cp < max(1, 0.05 * n_city):   # 방범용이 없거나 5%도 안 되면
                why = "모두 교통용으로 올라와 있어요" if n_cp == 0 else f"{n_city:,}곳 중 방범용이 {n_cp}곳뿐이에요"
                rep["warnings"].append({"code": "NO_CRIME_CCTV", "city": city, "crime_cctv": n_cp,
                                        "detail": f"{city} CCTV는 {why}. 용도와 상관없이 모든 CCTV를 세요."})
    if kind == "bell":
        places = Counter(p["purpose"] or "미상" for p in points)
        rep["by_place_type"] = dict(places.most_common(8))
        top, k = places.most_common(1)[0] if places else ("", 0)
        if points and k / len(points) >= 0.9:
            rep["warnings"].append({"code": "ONE_PLACE_TYPE", "place": top,
                                    "detail": f"비상벨 {len(points)}개 위치를 확인했어요. 정류장에서 얼마나 떨어져 있는지로 판단해요."})
    for city, n in stop_city.items():
        if n and by_city.get(city, 0) / n < 0.2:
            ko = {"cctv": "CCTV", "bell": "비상벨", "light": "보안등"}.get(kind, kind)
            rep["warnings"].append({"code": "SPARSE", "city": city,
                                    "detail": f"{city} 정류장 {n:,}곳과 {ko} {by_city.get(city, 0):,}곳 사이 거리를 재요"})
    return rep


def find_stops(stops, query, radius_m=800):
    """Stops whose name contains the query, plus stops within radius of those."""
    q = query.replace(" ", "")
    q2 = re.sub(r"(동|읍|면|정류장|정류소)$", "", q)
    q = q2 if len(q2) >= 2 else q
    seeds = [s for s in stops if q and q in s["name"].replace(" ", "")]
    if not seeds:
        return [], []
    idx = PointIndex(((s["lat"], s["lon"], s) for s in stops))
    ids, out = set(), []
    for seed in seeds:
        for _, s in idx.within(seed["lat"], seed["lon"], radius_m):
            if s["id"] not in ids:
                ids.add(s["id"])
                out.append(s)
    return seeds, out


# --------------------------------------------------------------------------
# Prescription and installation planning
# --------------------------------------------------------------------------
RX_LABEL = {"new_cctv": "CCTV 새로 달기", "adjust_cctv": "근처 CCTV 방향 돌리기",
            "bell": "비상벨 달기", "light": "가로등 밝히기", "last_bus": "막차 늦추기", "none": "그대로 두기"}


def prescribe(r, cctv_radius_m=100, bell_radius_m=200):
    """One primary action per stop, plus the full action list. Pure rule on distances."""
    g = r.get("gaps", [])
    acts = []
    if "eyes" in g:
        near = r.get("cctv_nearest_m")
        if near is not None and near <= 2 * cctv_radius_m:
            acts.append(("adjust_cctv", f"{near:,}m 거리에 CCTV가 이미 있어요. "
                                        "새로 달지 않고 정류장 쪽으로 방향만 돌려도 돼요."))
        else:
            far = "5km 안에 CCTV가 없어요" if near is None else f"가장 가까운 CCTV가 {near:,}m 떨어져 있어요"
            acts.append(("new_cctv", f"{far}. 정류장을 비추는 CCTV를 새로 달아야 해요."))
    if "bell" in g:
        near = r.get("bell_nearest_m")
        acts.append(("bell", f"반경 {bell_radius_m}m 안에 비상벨이 없어요"
                             + (f"(가장 가까운 곳 {near:,}m)." if near is not None else ".") + " 정류장 기둥에 비상벨을 달면 좋아요."))
    if "light" in g:
        acts.append(("light", "50m 안에 가로등이 없어요. 정류장을 밝혀 주세요."))
    if "last_bus" in g:
        acts.append(("last_bus", f"마지막 버스가 {r.get('last_hour')}시대로 일찍 끊겨요. 막차를 늦추거나 부르면 오는 버스를 생각해 봐요."))
    if not acts:
        acts.append(("none", "이번 기준에서 공백이 없어요."))
    return {"code": acts[0][0], "label": RX_LABEL[acts[0][0]], "detail": acts[0][1],
            "actions": [{"code": c, "label": RX_LABEL[c], "detail": d} for c, d in acts]}


_COVER_CACHE = {}


def _cover_map(rows, radius_m, gap):
    """Gap stops with users, and for each the gap stops one device there would cover. Cached per rows list."""
    key = (id(rows), len(rows), radius_m, gap)
    hit = _COVER_CACHE.get(key)
    if hit and hit[0] is rows:
        return hit[1]
    cand = [r for r in rows if gap in r.get("gaps", []) and (r.get("night_demand") or 0) > 0]
    idx = PointIndex(((r["lat"], r["lon"], r) for r in cand), cell_m=200)
    cover = {r["id"]: [x for _, x in idx.within(r["lat"], r["lon"], radius_m)] for r in cand}
    out = (cand, cover, sum(r["night_demand"] for r in cand))
    if len(_COVER_CACHE) > 16:
        _COVER_CACHE.clear()
    _COVER_CACHE[key] = (rows, out)
    return out


def plan_placement(rows, k=30, radius_m=100, gap="eyes", key=None):
    """Greedy max-coverage: place k new devices at gap stops so that the most daily users are covered.

    A device placed at stop s covers every `gap` stop within radius_m of s (both directions of a road,
    stops sharing a corner). Returns the picks in order with cumulative covered users.
    key: optional order to compare against (a function giving a sort key); then devices are placed in that
    order instead of greedily, still counting the neighbours each one covers.
    """
    cand, cover, total_gap = _cover_map(rows, radius_m, gap)
    covered, picks, cum = set(), [], 0.0
    order = sorted(cand, key=key) if key else None
    for step in range(min(k, len(cand))):
        if order is not None:
            nxt = next((r for r in order if r["id"] not in covered), None)
            if nxt is None:
                break
            best, gain = nxt, sum(x["night_demand"] for x in cover[nxt["id"]] if x["id"] not in covered)
        else:
            best, gain = None, 0.0
            for r in cand:
                if r["id"] in covered:
                    continue
                gn = sum(x["night_demand"] for x in cover[r["id"]] if x["id"] not in covered)
                if gn > gain:
                    best, gain = r, gn
            if best is None:
                break
        new = [x for x in cover[best["id"]] if x["id"] not in covered]
        covered.update(x["id"] for x in new)
        cum += gain
        picks.append({"n": step + 1, "id": best["id"], "name": best["name"], "lat": best["lat"], "lon": best["lon"],
                      "area": best.get("area"), "gain": round(gain, 1), "covered_stops": len(new),
                      "cum": round(cum, 1), "cum_pct": round(100 * cum / total_gap, 2) if total_gap else 0.0})
    return {"gap": gap, "radius_m": radius_m, "candidates": len(cand), "gap_users": round(total_gap, 1), "picks": picks}


def placement_benchmark(rows, k=10, radius_m=100, gap="eyes", seed_runs=20):
    """Compare greedy placement with two plain rules at the same budget k."""
    import random
    g = plan_placement(rows, k, radius_m, gap)
    far = plan_placement(rows, k, radius_m, gap, key=lambda r: -(r.get("cctv_nearest_m" if gap == "eyes" else "bell_nearest_m") or 99999))
    dem = plan_placement(rows, k, radius_m, gap, key=lambda r: -(r.get("night_demand") or 0))
    cand = [r for r in rows if gap in r.get("gaps", []) and (r.get("night_demand") or 0) > 0]
    rnd = []
    for s in range(seed_runs):
        rng = random.Random(s)
        pos = {r["id"]: rng.random() for r in cand}
        p = plan_placement(rows, k, radius_m, gap, key=lambda r: pos.get(r["id"], 1))
        rnd.append(p["picks"][-1]["cum"] if p["picks"] else 0)
    last = lambda p: p["picks"][-1]["cum"] if p["picks"] else 0.0  # noqa: E731
    return {"k": k, "greedy": round(last(g), 1), "demand_first": round(last(dem), 1), "farthest_first": round(last(far), 1),
            "random_avg": round(sum(rnd) / max(1, len(rnd)), 1), "gap_users": g["gap_users"]}
