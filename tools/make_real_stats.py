"""실제 공개 자료로 기획서·결과 설명서에 넣을 통계와 차트를 만들어요.

실행: python tools/make_real_stats.py
- data/input (허브 정류장별 이용) + data/public (정류장, CCTV, 비상벨, 시간대별 승차)을 규칙 모드로 분석해요.
- docs/results/real/ 에 stats.json, top10.csv, by_area.csv 를 쓰고
- docs/img/charts/ 에 SVG 차트를 써요 (인쇄용 밝은 배경).
반출 기준: 정류장 단위 값 중 하루 10 미만은 "10명 미만"으로 가려요. 지역 합계는 응용 통계예요.
"""
import csv
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
os.environ["NIGHTGAP_FORCE_RULES"] = "1"
import agent as A  # noqa: E402
import tools as T  # noqa: E402
from geo import PointIndex  # noqa: E402

OUT = os.path.join(ROOT, "docs", "results", "real")
IMG = os.path.join(ROOT, "docs", "img", "charts")
os.makedirs(OUT, exist_ok=True)
os.makedirs(IMG, exist_ok=True)

ACC, BLUE, GRAY, INK, MUTED, LINE = "#e8892b", "#2f6fd6", "#b8bfcc", "#1d2433", "#5d6678", "#e3e7ee"
FONT = 'font-family="Apple SD Gothic Neo, Malgun Gothic, sans-serif"'


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" {FONT}>'
            f'<title>{esc(title)}</title><rect width="{w}" height="{h}" fill="#fff"/>{body}</svg>')


def save(name, content):
    open(os.path.join(IMG, name), "w", encoding="utf-8").write(content)


def hbars(rows, w=760, label_w=150, value_fmt=str, color=ACC, title="", unit="", row_h=26, maxv=None, ctx=None):
    """Horizontal bars. rows = [(label, value, highlight_bool)]; ctx = optional grey background value per row."""
    maxv = maxv or max([v for _, v, _ in rows] + [c for c in (ctx or []) if c] + [1e-9])
    plot_w = w - label_w - 70
    h = row_h * len(rows) + 16
    b = []
    for i, (lab, v, hi) in enumerate(rows):
        y = 8 + i * row_h
        if ctx and ctx[i]:
            b.append(f'<rect x="{label_w}" y="{y + 4}" width="{plot_w * ctx[i] / maxv:.1f}" height="{row_h - 10}" rx="3" fill="{LINE}"/>')
        bw = max(2, plot_w * v / maxv)
        b.append(f'<text x="{label_w - 8}" y="{y + row_h / 2 + 4}" text-anchor="end" font-size="13" fill="{INK}">{esc(lab)}</text>')
        b.append(f'<rect x="{label_w}" y="{y + 4}" width="{bw:.1f}" height="{row_h - 10}" rx="3" fill="{color if hi else GRAY}"/>')
        b.append(f'<text x="{label_w + bw + 6:.1f}" y="{y + row_h / 2 + 4}" font-size="12.5" fill="{INK}">{esc(value_fmt(v))}{unit}</text>')
    return svg(w, h, "".join(b), title)


# ---------------------------------------------------------------- analysis
s = A.Session([os.path.join(ROOT, "data", "input"), os.path.join(ROOT, "data", "public")])
A.run_turn(s, "제주 전체에서 밤길 공백이 큰 정류장 10곳 알려줘")
rows = s.analysis["rows"]
rep = s.report
cctv, bell = s.layers["cctv"], s.layers["bell"]
radii = rep["summary"]["radii"]

# town of each stop = the 동/읍/면 of the nearest address point (CCTV or bell); 동 are folded into "제주시 동지역" etc.
refs = [p for p in cctv + bell if p.get("town")]
idx = PointIndex(((p["lat"], p["lon"], p) for p in refs), cell_m=1000)


def group_of(r):
    """Same grouping as tools.overview (shown in the app): 읍/면 as is, 동 folded per city."""
    town = r.get("area") or "?"
    return town if town.endswith(("읍", "면")) else f"{T.city_of(r['lat'], r['lon'])} 동지역"


# satellite night brightness (NASA Black Marble 2016), same sampling and thresholds as the map (app/static/map3d.js, app.js)
SAT = None
try:
    from PIL import Image
    im = Image.open(os.path.join(ROOT, "app", "static", "nightlights_jeju_2016.png")).convert("RGB")
    SAT = (im.width, im.height, im.load())
except Exception:  # Pillow is only needed for this document tool, not for the app
    pass


def sat_at(lat, lon):
    if not SAT:
        return None
    w, h, px = SAT
    fx, fy = (lon - 126.1) / 0.9, (33.6 - lat) / 0.45
    if not (0 <= fx < 1 and 0 <= fy < 1):
        return 0.0
    r_, g_, b_ = px[int(fx * w), int(fy * h)]
    y = (0.3 * r_ + 0.6 * g_ + 0.1 * b_) / 255
    return max(0.0, min(1.0, (y - 0.07) / 0.85)) ** 1.5


for r in rows:
    r["group"] = group_of(r)
    r["blind"] = "eyes" in r["gaps"] and "bell" in r["gaps"]
    v = sat_at(r["lat"], r["lon"])
    r["dark"] = v is not None and v < 0.15

total_use = sum(r["night_demand"] or 0 for r in rows)
cat = Counter()
use_cat = Counter()
for r in rows:
    k = ("both" if r["blind"] else "no_cctv" if "eyes" in r["gaps"] else "no_bell" if "bell" in r["gaps"] else "covered")
    cat[k] += 1
    use_cat[k] += r["night_demand"] or 0

# concentration: share of usage at the top 10% stops
vals = sorted((r["night_demand"] or 0 for r in rows), reverse=True)
top10p = sum(vals[: len(vals) // 10]) / total_use

# per area
area = defaultdict(lambda: {"stops": 0, "blind": 0, "blind_dark": 0, "use": 0.0, "blind_use": 0.0, "cctv": 0, "bell": 0})
for r in rows:
    a = area[r["group"]]
    a["stops"] += 1
    a["blind"] += r["blind"]
    a["blind_dark"] += r["blind"] and r["dark"]
    a["use"] += r["night_demand"] or 0
    a["blind_use"] += (r["night_demand"] or 0) if r["blind"] else 0
for p in cctv:
    if p.get("town"):
        g = f"{T.city_of(p['lat'], p['lon'])} 동지역" if p["town"].endswith("동") else p["town"]
        if g in area:
            area[g]["cctv"] += 1
for p in bell:
    if p.get("town"):
        g = f"{T.city_of(p['lat'], p['lon'])} 동지역" if p["town"].endswith("동") else p["town"]
        if g in area:
            area[g]["bell"] += 1
areas = sorted(((k, v) for k, v in area.items() if v["stops"] >= 20), key=lambda kv: -kv[1]["blind"] / kv[1]["stops"])

# nearest-CCTV distance bins: all stops vs top 100 by use
bins = [(0, 100, "100m 이내"), (100, 300, "100~300m"), (300, 500, "300~500m"), (500, 1000, "0.5~1km"), (1000, 10 ** 9, "1km 넘음")]
top100 = sorted(rows, key=lambda r: -(r["night_demand"] or 0))[:100]


def dist_bins(rs, key):
    c = Counter()
    for r in rs:
        d = r.get(key)
        d = 10 ** 8 if d is None else d
        for lo, hi, lab in bins:
            if lo <= d < hi:
                c[lab] += 1
    return [round(100 * c[lab] / len(rs), 1) for _, _, lab in bins]


dist = {"labels": [b[2] for b in bins],
        "cctv_all": dist_bins(rows, "cctv_nearest_m"), "cctv_top100": dist_bins(top100, "cctv_nearest_m"),
        "bell_all": dist_bins(rows, "bell_nearest_m"), "bell_top100": dist_bins(top100, "bell_nearest_m")}

# CCTV purpose by city (merged public + hub)
purpose = defaultdict(Counter)
for p in cctv:
    pr = p["purpose"] or "미상"
    k = "방범·어린이보호" if any(x in pr for x in T.CRIME_PREVENTION) else ("교통" if ("교통" in pr or "단속" in pr or "주정차" in pr) else "기타")
    purpose[p["city"] or "미상"][k] += 1
bell_place = Counter(p["purpose"] or "미상" for p in bell)

# hourly (island-wide, hub 512): bands, user type mix at night vs all day, weekday, monthly late share
hrows = list(csv.DictReader(open(os.path.join(ROOT, "data", "public", "bus_hourly_boarding_jejudatahub.csv"), encoding="utf-8-sig")))
dates = sorted({r["base_date"] for r in hrows})
recent = set(dates[-90:])
band = Counter()
type_all, type_late = Counter(), Counter()
dow_late, dow_all = Counter(), Counter()
month_late, month_all = Counter(), Counter()
for r in hrows:
    n = float(r["user_count"] or 0)
    late = r["base_hour"].startswith("22")
    m = r["base_date"][:6]
    month_all[m] += n
    month_late[m] += n if late else 0
    if r["base_date"] in recent:
        band[r["base_hour"]] += n
        type_all[r["user_type"]] += n
        type_late[r["user_type"]] += n if late else 0
        dow_all[r["day_of_week"]] += n
        dow_late[r["day_of_week"]] += n if late else 0
days = len(recent)
bands = [(b, band[b] / days) for b in sorted(band)]
ttot, tlate = sum(type_all.values()), sum(type_late.values())
types = sorted(type_all, key=lambda t: -type_late[t])
type_mix = [{"type": t, "all_share": round(100 * type_all[t] / ttot, 1), "late_share": round(100 * type_late[t] / tlate, 1),
             "late_ratio": round(100 * type_late[t] / type_all[t], 2) if type_all[t] else 0} for t in types]
DOW = "월화수목금토일"
dow = [{"dow": d, "late_share": round(100 * dow_late[d] / dow_all[d], 2)} for d in DOW if dow_all[d]]
months = [m for m in sorted(month_all) if month_all[m] > 0]
monthly = [{"month": m, "late_share": round(100 * month_late[m] / month_all[m], 2)} for m in months]

top = rep["cards"]
stats = {
    "stops_scored": len(rows), "usage_days": s.daily_meta["days"], "usage_matched_pct": s.daily_meta.get("matched_pct"),
    "radii": radii, "cctv_points": len(cctv), "bell_points": len(bell),
    "categories": dict(cat), "usage_share_by_category": {k: round(100 * v / total_use, 1) for k, v in use_cat.items()},
    "daily_use_total": round(total_use),
    "blind_dark": sum(r["blind"] and r["dark"] for r in rows),
    "blind_dark_use": round(sum((r["night_demand"] or 0) for r in rows if r["blind"] and r["dark"])), "top10pct_usage_share": round(100 * top10p, 1),
    "areas": [{"area": k, **{kk: (round(vv) if isinstance(vv, float) else vv) for kk, vv in v.items()},
               "blind_pct": round(100 * v["blind"] / v["stops"], 1),
               "blind_use_pct": round(100 * v["blind_use"] / v["use"], 1) if v["use"] else None} for k, v in areas],
    "distance": dist, "cctv_purpose_by_city": {k: dict(v) for k, v in purpose.items()}, "bell_place": dict(bell_place.most_common()),
    "hourly": {"from": min(recent), "to": max(recent), "days": days, "bands": [{"band": b, "per_day": round(v)} for b, v in bands],
               "type_mix": type_mix, "dow": dow, "monthly": monthly},
    "robustness": rep.get("robustness"),
    "top10": [{k: c[k] for k in ("rank", "name", "area", "gaps", "demand", "cctv_m", "bell_m", "stay")} for c in top],
}
json.dump(stats, open(os.path.join(OUT, "stats.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

with open(os.path.join(OUT, "top10.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["순위", "정류장", "지역", "공백 유형", "하루 이용(승차+환승)", "가장 가까운 CCTV(m)", "가장 가까운 비상벨(m)", "기준 바꿔도 남은 횟수", "건의 문장"])
    for c in top:
        dem = c["demand"] if c["demand"] is None or c["demand"] >= 10 else "10명 미만"
        w.writerow([c["rank"], c["name"], c["area"], " / ".join(c["gaps"]), dem, c["cctv_m"], c["bell_m"],
                    f"{c.get('stay')}/{(rep.get('robustness') or {}).get('n')}", c["recommendation"]])
with open(os.path.join(OUT, "by_area.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["지역", "이용 기록 있는 정류장", "CCTV·비상벨 둘 다 없는 정류장", "비율(%)", "그중 위성으로도 어두운 곳", "하루 이용 합계", "그중 공백 정류장 이용 비율(%)"])
    for a in stats["areas"]:
        w.writerow([a["area"], a["stops"], a["blind"], a["blind_pct"], a["blind_dark"], a["use"], a["blind_use_pct"]])

# ---------------------------------------------------------------- charts
# 1. stop categories: count share vs usage share (two stacked 100% bars)
labs = [("both", "둘 다 없음", ACC), ("no_cctv", "CCTV만 없음", "#f3c08f"), ("no_bell", "비상벨만 없음", "#9fbbe9"), ("covered", "둘 다 있음", BLUE)]
W, X0, PW = 760, 130, 600
b = []
for i, (title, data) in enumerate((("정류장 수", {k: 100 * cat[k] / len(rows) for k, _, _ in labs}),
                                   ("하루 이용 인원", {k: 100 * use_cat[k] / total_use for k, _, _ in labs}))):
    y = 34 + i * 58
    b.append(f'<text x="{X0 - 10}" y="{y + 22}" text-anchor="end" font-size="13.5" fill="{INK}" font-weight="700">{title}</text>')
    x = X0
    for k, lab, col in labs:
        ww = PW * data[k] / 100
        b.append(f'<rect x="{x:.1f}" y="{y}" width="{max(0, ww - 2):.1f}" height="34" rx="3" fill="{col}"/>')
        if ww > 44:
            b.append(f'<text x="{x + ww / 2:.1f}" y="{y + 22}" text-anchor="middle" font-size="12.5" fill="{"#fff" if col in (ACC, BLUE) else INK}" font-weight="700">{data[k]:.0f}%</text>')
        x += ww
lx = X0
for k, lab, col in labs:
    b.append(f'<rect x="{lx}" y="8" width="11" height="11" rx="2" fill="{col}"/><text x="{lx + 16}" y="18" font-size="12" fill="{MUTED}">{lab}</text>')
    lx += 130
save("c1_categories.svg", svg(W, 160, "".join(b), "정류장 수와 이용 인원 기준 공백 비율"))

# 2. area blind rate (bar) with usage share at blind stops as text
rws = [(f"{k}", 100 * v["blind"] / v["stops"], True) for k, v in areas]
save("c2_area.svg", hbars(rws, value_fmt=lambda v: f"{v:.0f}", unit="%", title="지역별 공백 정류장 비율", label_w=130, maxv=100))
# 2b. funnel: scored -> blind -> blind & dark
fun = [("이용 기록이 있는 정류장", len(rows), False), ("CCTV·비상벨 둘 다 없음", cat["both"], True),
       ("그중 위성으로도 어두움", stats["blind_dark"], True)]
save("c10_funnel.svg", hbars(fun, value_fmt=lambda v: f"{v:,}", unit="곳", title="공백 정류장 좁혀 보기", label_w=200, color=ACC))

# 3. distance distribution, all vs top 100 (grouped hbars)
b = []
W, X0, PW, RH = 760, 110, 560, 46
mx = max(dist["cctv_all"] + dist["cctv_top100"])
for i, lab in enumerate(dist["labels"]):
    y = 28 + i * RH
    b.append(f'<text x="{X0 - 8}" y="{y + 22}" text-anchor="end" font-size="13" fill="{INK}">{lab}</text>')
    for j, (key, col) in enumerate((("cctv_all", GRAY), ("cctv_top100", ACC))):
        v = dist[key][i]
        ww = max(2, PW * v / mx)
        yy = y + 3 + j * 19
        b.append(f'<rect x="{X0}" y="{yy}" width="{ww:.1f}" height="16" rx="3" fill="{col}"/>')
        b.append(f'<text x="{X0 + ww + 6:.1f}" y="{yy + 13}" font-size="12" fill="{INK}">{v:.0f}%</text>')
b.append(f'<rect x="{X0}" y="6" width="11" height="11" rx="2" fill="{GRAY}"/><text x="{X0 + 16}" y="16" font-size="12" fill="{MUTED}">전체 정류장 {len(rows):,}곳</text>')
b.append(f'<rect x="{X0 + 190}" y="6" width="11" height="11" rx="2" fill="{ACC}"/><text x="{X0 + 206}" y="16" font-size="12" fill="{MUTED}">이용 상위 100곳</text>')
save("c3_distance.svg", svg(W, 28 + RH * len(dist["labels"]) + 4, "".join(b), "가장 가까운 CCTV까지 거리"))

# 4. hourly bands (per day)
rws = [(bnd, v, bnd.startswith(("19", "22"))) for bnd, v in bands]
save("c4_hourly.svg", hbars(rws, value_fmt=lambda v: f"{v:,.0f}", unit="명", title="시간대별 하루 평균 승차 (제주 전체)", label_w=100))

# 5. user type: share of all-day vs late (22~24) — dumbbell-ish grouped bars, top 6 types
b = []
W, X0, PW, RH = 760, 110, 560, 46
tm = [t for t in type_mix if t["all_share"] >= 0.5][:6]
mx = max(max(t["all_share"], t["late_share"]) for t in tm)
for i, t in enumerate(tm):
    y = 28 + i * RH
    b.append(f'<text x="{X0 - 8}" y="{y + 22}" text-anchor="end" font-size="13" fill="{INK}">{esc(t["type"])}</text>')
    for j, (key, col) in enumerate((("all_share", GRAY), ("late_share", BLUE))):
        v = t[key]
        ww = max(2, PW * v / mx)
        yy = y + 3 + j * 19
        b.append(f'<rect x="{X0}" y="{yy}" width="{ww:.1f}" height="16" rx="3" fill="{col}"/>')
        b.append(f'<text x="{X0 + ww + 6:.1f}" y="{yy + 13}" font-size="12" fill="{INK}">{v:.1f}%</text>')
b.append(f'<rect x="{X0}" y="6" width="11" height="11" rx="2" fill="{GRAY}"/><text x="{X0 + 16}" y="16" font-size="12" fill="{MUTED}">하루 전체 승차 중 비중</text>')
b.append(f'<rect x="{X0 + 190}" y="6" width="11" height="11" rx="2" fill="{BLUE}"/><text x="{X0 + 206}" y="16" font-size="12" fill="{MUTED}">22~24시 승차 중 비중</text>')
save("c5_usertype.svg", svg(W, 28 + RH * len(tm) + 4, "".join(b), "이용자 유형 구성: 하루 전체와 밤 22~24시"))

# 6. monthly late share line
W, H, X0, Y0, PW, PH = 760, 220, 50, 20, 680, 160
vs = [m["late_share"] for m in monthly]
lo, hi = min(vs) * 0.9, max(vs) * 1.08
pts = [(X0 + PW * i / (len(vs) - 1), Y0 + PH - PH * (v - lo) / (hi - lo)) for i, v in enumerate(vs)]
b = [f'<line x1="{X0}" y1="{Y0 + PH}" x2="{X0 + PW}" y2="{Y0 + PH}" stroke="{LINE}"/>']
for t in (lo + (hi - lo) * k / 3 for k in range(4)):
    y = Y0 + PH - PH * (t - lo) / (hi - lo)
    b.append(f'<line x1="{X0}" y1="{y:.1f}" x2="{X0 + PW}" y2="{y:.1f}" stroke="{LINE}" stroke-dasharray="2 3"/><text x="{X0 - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="{MUTED}">{t:.1f}%</text>')
b.append(f'<polyline fill="none" stroke="{BLUE}" stroke-width="2" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}"/>')
for i, (x, y) in enumerate(pts):
    m = monthly[i]["month"]
    if m.endswith(("01", "07")) or i in (0, len(pts) - 1):
        b.append(f'<text x="{x:.1f}" y="{Y0 + PH + 18}" text-anchor="middle" font-size="11" fill="{MUTED}">{m[2:4]}.{m[4:]}</text>')
    if i in (vs.index(max(vs)), len(pts) - 1):
        b.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{BLUE}" stroke="#fff" stroke-width="2"/><text x="{x:.1f}" y="{y - 9:.1f}" text-anchor="middle" font-size="11.5" fill="{INK}">{vs[i]:.1f}%</text>')
save("c6_monthly.svg", svg(W, H, "".join(b), "월별 22~24시 승차 비중"))

# 7. CCTV purpose by city (stacked counts)
W, X0, PW = 760, 90, 600
cities = ["제주시", "서귀포시"]
keys = [("방범·어린이보호", ACC), ("교통", BLUE), ("기타", GRAY)]
mx = max(sum(purpose[c].values()) for c in cities)
b = []
for i, c in enumerate(cities):
    y = 34 + i * 50
    b.append(f'<text x="{X0 - 10}" y="{y + 21}" text-anchor="end" font-size="13.5" fill="{INK}" font-weight="700">{c}</text>')
    x = X0
    for k, col in keys:
        v = purpose[c][k]
        ww = PW * v / mx
        if v:
            b.append(f'<rect x="{x:.1f}" y="{y}" width="{max(2, ww - 2):.1f}" height="32" rx="3" fill="{col}"/>')
            if ww > 30:
                b.append(f'<text x="{x + ww / 2:.1f}" y="{y + 21}" text-anchor="middle" font-size="12" fill="{"#fff" if col != GRAY else INK}" font-weight="700">{v}</text>')
        x += ww
    if purpose[c]["방범·어린이보호"] == 0:
        b.append(f'<text x="{x + 8:.1f}" y="{y + 21}" font-size="12" fill="{ACC}" font-weight="700">방범 0곳</text>')
lx = X0
for k, col in keys:
    b.append(f'<rect x="{lx}" y="8" width="11" height="11" rx="2" fill="{col}"/><text x="{lx + 16}" y="18" font-size="12" fill="{MUTED}">{k}</text>')
    lx += 150
save("c7_cctv_purpose.svg", svg(W, 140, "".join(b), "공개 CCTV 설치 목적 (시별, 설치 장소 수)"))

# 8. top 10 usage bars
rws = [(f"{c['rank']}. {c['name'][:16]}", c["demand"] or 0, True) for c in top]
save("c8_top10.svg", hbars(rws, value_fmt=lambda v: f"{v:,.0f}", unit="명", title="우선 개선 10곳의 하루 이용(승차+환승)", label_w=230, color=ACC))

# 9. robustness variants
rb = rep.get("robustness") or {}
rws = [(v["setting"], v["kept"], True) for v in rb.get("variants", [])]
if rws:
    save("c9_robust.svg", hbars(rws, value_fmt=lambda v: f"{v}", unit="/10곳 유지", title="기준을 바꿔도 남는 곳", label_w=150, color=BLUE, maxv=10))

print(json.dumps({k: stats[k] for k in ("stops_scored", "categories", "usage_share_by_category", "top10pct_usage_share")}, ensure_ascii=False))
print(json.dumps(stats["areas"], ensure_ascii=False))
print("blind_dark", stats["blind_dark"], stats["blind_dark_use"])
print(json.dumps(stats["distance"], ensure_ascii=False))
print(json.dumps(stats["cctv_purpose_by_city"], ensure_ascii=False), json.dumps(stats["bell_place"], ensure_ascii=False))
print(json.dumps(stats["hourly"]["bands"], ensure_ascii=False))
print(json.dumps(stats["hourly"]["type_mix"], ensure_ascii=False))
print(json.dumps(stats["hourly"]["dow"], ensure_ascii=False))
print(json.dumps(stats["hourly"]["monthly"][::3], ensure_ascii=False))
