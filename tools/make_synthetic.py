"""Generate SYNTHETIC demo data in the same format as the real sources.

Every value here is invented. Stop names start with "가상" so nobody mistakes
them for real places. Run:  python tools/make_synthetic.py
Outputs go to data/sample/.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from geo import wgs84_to_tm  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "sample")
rng = random.Random(20261109)

# (town, lat, lon, kind, n_stops, spread_deg, city)
# kind: urban / suburb / rural. Rough real town centers; everything else is synthetic.
TOWNS = [
    ("노형", 33.4840, 126.4820, "urban", 45, 0.010, "제주시"),
    ("연동", 33.4880, 126.4960, "urban", 40, 0.008, "제주시"),
    ("이도", 33.5000, 126.5320, "urban", 40, 0.009, "제주시"),
    ("아라", 33.4700, 126.5450, "suburb", 30, 0.012, "제주시"),
    ("삼양", 33.5180, 126.5850, "suburb", 22, 0.010, "제주시"),
    ("외도", 33.4900, 126.4300, "suburb", 22, 0.010, "제주시"),
    ("애월", 33.4630, 126.3300, "rural", 20, 0.020, "제주시"),
    ("한림", 33.4110, 126.2690, "rural", 20, 0.018, "제주시"),
    ("조천", 33.5380, 126.6350, "rural", 16, 0.018, "제주시"),
    ("구좌", 33.5200, 126.8500, "rural", 16, 0.025, "제주시"),
    ("서귀동", 33.2500, 126.5630, "urban", 35, 0.008, "서귀포시"),
    ("동홍", 33.2620, 126.5750, "suburb", 25, 0.008, "서귀포시"),
    ("대정", 33.2250, 126.2500, "rural", 16, 0.020, "서귀포시"),
    ("성산", 33.4380, 126.9100, "rural", 14, 0.020, "서귀포시"),
    ("표선", 33.3260, 126.8350, "rural", 14, 0.018, "서귀포시"),
    ("남원", 33.2800, 126.7100, "rural", 14, 0.020, "서귀포시"),
]
# A deliberately planted "new apartment district": high night demand, few cameras.
HOTSPOT = ("신도시", 33.4560, 126.5650, "newtown", 18, 0.006, "제주시")

NIGHT_BASE = {"urban": 9.0, "suburb": 6.0, "rural": 1.5, "newtown": 12.0}
CCTV_PER_STOP = {"urban": 3.0, "suburb": 1.6, "rural": 0.6, "newtown": 0.25}
BELL_PER_STOP = {"urban": 1.2, "suburb": 0.7, "rural": 0.25, "newtown": 0.15}
LAST_HOUR = {"urban": 23, "suburb": 23, "rural": 21, "newtown": 22}


def jitter(lat, lon, spread):
    return lat + rng.gauss(0, spread), lon + rng.gauss(0, spread * 1.2)


def main():
    os.makedirs(OUT, exist_ok=True)
    stops = []
    sid = 4000001
    for town in TOWNS + [HOTSPOT]:
        name, lat, lon, kind, n, spread, city = town
        for i in range(n):
            plat, plon = jitter(lat, lon, spread)
            stops.append({
                "id": str(sid), "name": f"가상{name}정류소{i + 1:02d}", "lat": plat, "lon": plon,
                "kind": kind, "town": name, "city": city,
                "night": max(0.0, rng.lognormvariate(0, 0.6) * NIGHT_BASE[kind]),
            })
            sid += 1

    # ---- 3-03 버스 정류소 정보 --------------------------------------------
    base = "20251001"
    with open(os.path.join(OUT, "3-03_bus_station_info.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["serial_number", "base_date", "pt_year", "pt_month", "bus_station_id",
                    "bus_station_name", "bus_city_type", "longitude", "latitude"])
        for i, s in enumerate(stops, 1):
            w.writerow([f"{i:07d}", base, "2025", "10", s["id"], s["name"], "시내",
                        f"{s['lon']:.6f}", f"{s['lat']:.6f}"])

    # ---- 3-04 버스 정류소별 이용 현황 (2주) ------------------------------
    days = [dt.date(2025, 10, 6) + dt.timedelta(d) for d in range(14)]
    dow = "월화수목금토일"
    hour_shape = {h: v for h, v in zip(range(5, 24), [
        0.2, 0.8, 1.6, 1.4, 0.9, 0.8, 0.9, 1.0, 0.9, 0.9, 1.0, 1.2, 1.5, 1.4, 1.0, 0.8, 0.7, 0.55, 0.35])}
    rows = 0
    with open(os.path.join(OUT, "3-04_bus_station_usage.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["base_date", "pt_year", "pt_month", "day_of_week", "base_hour", "bus_station_id",
                    "bus_station_name", "get_on_off_type", "user_category", "pay_type", "user_count"])
        for d in days:
            weekend = d.weekday() >= 5
            for s in stops:
                last = LAST_HOUR[s["kind"]] - (1 if weekend and s["kind"] == "rural" else 0)
                for h, shape in hour_shape.items():
                    if h > last:
                        continue
                    night = h >= 21
                    lam_off = s["night"] * (1.0 if night else shape * 1.8)
                    lam_on = s["night"] * (0.35 if night else shape * 1.9)
                    for typ, lam in (("하차", lam_off), ("승차", lam_on)):
                        # Jeju does not force tap-off, so alighting is under-recorded (~60%).
                        if typ == "하차":
                            lam *= 0.6
                        n = _poisson(lam)
                        if n <= 0:
                            continue
                        for cat, share in (("일반", 0.8), ("청소년", 0.12), ("경로", 0.08)):
                            k = int(round(n * share))
                            if k > 0:
                                w.writerow([d.strftime("%Y%m%d"), d.year, d.month, dow[d.weekday()],
                                            f"{h:02d}", s["id"], s["name"], typ, cat, "카드", k])
                                rows += 1

    # ---- CCTV (전국CCTV표준데이터 형식, 제주 일부, WGS84) ------------------
    cctv_rows = []
    for s in stops:
        n = _poisson(CCTV_PER_STOP[s["kind"]])
        for _ in range(n):
            lat, lon = jitter(s["lat"], s["lon"], 0.0012)
            purpose = rng.choices(["생활방범", "어린이보호", "교통단속", "쓰레기단속", "시설물관리"],
                                  [0.55, 0.15, 0.12, 0.1, 0.08])[0]
            cctv_rows.append([f"{s['city']}청", f"제주특별자치도 {s['city']} 가상로 {rng.randint(1, 300)}",
                              "", purpose, rng.randint(1, 4), 200, "360도전방면", 30,
                              f"20{rng.randint(12, 25)}-0{rng.randint(1, 9)}", "064-000-0000",
                              f"{lat:.7f}", f"{lon:.7f}", "2026-04-22"])
    with open(os.path.join(OUT, "cctv_standard_jeju.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["관리기관명", "소재지도로명주소", "소재지지번주소", "설치목적구분", "카메라대수",
                    "카메라화소수", "촬영방면정보", "보관일수", "설치연월", "관리기관전화번호",
                    "WGS84위도", "WGS84경도", "데이터기준일자"])
        w.writerows(cctv_rows)

    # ---- 안전비상벨 (전국안전비상벨위치표준데이터 형식, EPSG:5174 TM) ------
    bell_rows = []
    for s in stops:
        n = _poisson(BELL_PER_STOP[s["kind"]])
        for _ in range(n):
            lat, lon = jitter(s["lat"], s["lon"], 0.0015)
            x, y = wgs84_to_tm(lat, lon, "EPSG:5174")
            bell_rows.append([rng.choice(["공원", "버스정류장", "공중화장실", "골목길", "주차장"]),
                              rng.choice(["방범용", "여성안심"]),
                              f"제주특별자치도 {s['city']} 가상로 {rng.randint(1, 300)}", "",
                              f"{x:.3f}", f"{y:.3f}", "경찰연계", "Y", "2026-01-15", f"{s['city']}청", "2025-11-27"])
    with open(os.path.join(OUT, "safety_bell_standard_jeju.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["설치위치", "설치목적", "소재지도로명주소", "소재지지번주소", "좌표정보(X)", "좌표정보(Y)",
                    "연계방식", "경찰연계유무", "최종점검일자", "관리기관명", "데이터기준일자"])
        w.writerows(bell_rows)

    print(f"stops={len(stops)} usage_rows={rows} cctv={len(cctv_rows)} bells={len(bell_rows)} -> {OUT}")


def _poisson(lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, lam ** 0.5))))
    L, k, p = pow(2.718281828459045, -lam), 0, 1.0
    while True:
        p *= rng.random()
        if p < L:
            return k
        k += 1


if __name__ == "__main__":
    main()
