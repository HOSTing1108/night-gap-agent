"""Geometry helpers. Standard library only.

- haversine distance
- a simple grid index for radius queries
- EPSG:5174 / 5181 / 5186 (Korean TM) -> WGS84 conversion
"""
from __future__ import annotations

import math
from collections import defaultdict

R_EARTH = 6371008.8

# Jeju bounding box (WGS84), used for sanity checks and filtering.
JEJU_BBOX = (33.10, 126.10, 33.60, 127.00)  # min_lat, min_lon, max_lat, max_lon
CHUJA_BBOX = (33.90, 126.20, 34.02, 126.40)  # 추자도


def in_jeju(lat: float, lon: float) -> bool:
    for a, b, c, d in (JEJU_BBOX, CHUJA_BBOX):
        if a <= lat <= c and b <= lon <= d:
            return True
    return False


def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))


class PointIndex:
    """Bucket points into ~cell_m cells so radius queries stay fast."""

    def __init__(self, points, cell_m: float = 250.0):
        # points: iterable of (lat, lon, payload)
        self.cell_lat = cell_m / 111_320.0
        self.cell_lon = cell_m / (111_320.0 * math.cos(math.radians(33.4)))
        self.buckets = defaultdict(list)
        self.n = 0
        for lat, lon, payload in points:
            self.buckets[self._key(lat, lon)].append((lat, lon, payload))
            self.n += 1

    def _key(self, lat, lon):
        return (int(math.floor(lat / self.cell_lat)), int(math.floor(lon / self.cell_lon)))

    def within(self, lat, lon, radius_m):
        """Return [(distance_m, payload)] sorted by distance."""
        ki, kj = self._key(lat, lon)
        span = int(math.ceil(radius_m / (self.cell_lat * 111_320.0))) + 1
        # 평면 근사로 먼저 거르고(여유 2%), 남은 점만 정확한 거리로 재요. 결과는 같아요.
        ky, kx = 111_320.0, 111_320.0 * math.cos(math.radians(lat))
        lim = (radius_m * 1.02) ** 2
        out = []
        get = self.buckets.get
        for i in range(ki - span, ki + span + 1):
            for j in range(kj - span, kj + span + 1):
                for plat, plon, payload in get((i, j), ()):
                    dy, dx = (plat - lat) * ky, (plon - lon) * kx
                    if dx * dx + dy * dy > lim:
                        continue
                    d = haversine(lat, lon, plat, plon)
                    if d <= radius_m:
                        out.append((d, payload))
        out.sort(key=lambda t: t[0])
        return out

    def nearest(self, lat, lon, max_m: float = 5000.0):
        """가장 가까운 점 (거리, payload). max_m 안에 없으면 None.

        가운데 칸부터 한 겹씩 넓혀 가며 찾고, 남은 칸이 지금까지 찾은 것보다 멀면 멈춰요.
        """
        ki, kj = self._key(lat, lon)
        cell = min(self.cell_lat * 111_320.0, self.cell_lon * 111_320.0 * math.cos(math.radians(lat))) * 0.98
        max_span = int(math.ceil(max_m / cell)) + 1
        best, bd = None, float("inf")
        get = self.buckets.get
        for s in range(max_span + 1):
            if best is not None and (s - 1) * cell > bd:
                break
            if s == 0:
                ring = ((ki, kj),)
            else:
                ring = [(ki - s, j) for j in range(kj - s, kj + s + 1)] + [(ki + s, j) for j in range(kj - s, kj + s + 1)] \
                    + [(i, kj - s) for i in range(ki - s + 1, ki + s)] + [(i, kj + s) for i in range(ki - s + 1, ki + s)]
            for key in ring:
                for plat, plon, payload in get(key, ()):
                    d = haversine(lat, lon, plat, plon)
                    if d < bd:
                        bd, best = d, payload
        if best is None or bd > max_m:
            return None
        return (bd, best)


# --------------------------------------------------------------------------
# Korean TM -> WGS84
# --------------------------------------------------------------------------
_BESSEL = (6377397.155, 1 / 299.1528128)
_GRS80 = (6378137.0, 1 / 298.257222101)
_WGS84 = (6378137.0, 1 / 298.257223563)
# Korean 1985 (Bessel) -> WGS84, 7-parameter (as used by EPSG/proj for 5174).
_TOWGS84_BESSEL = (-115.80, 474.99, 674.11, 1.16, -2.31, -1.63, 6.43)

CRS_PARAMS = {
    # name: (ellipsoid, lat0, lon0, k0, false_easting, false_northing, towgs84 or None)
    "EPSG:5174": (_BESSEL, 38.0, 127.0028902777778, 1.0, 200000.0, 500000.0, _TOWGS84_BESSEL),
    "EPSG:5175": (_BESSEL, 38.0, 127.0028902777778, 1.0, 200000.0, 550000.0, _TOWGS84_BESSEL),
    "EPSG:5181": (_GRS80, 38.0, 127.0, 1.0, 200000.0, 500000.0, None),
    "EPSG:5186": (_GRS80, 38.0, 127.0, 1.0, 200000.0, 600000.0, None),
}


def _meridian_arc(phi, a, e2):
    e4, e6 = e2 * e2, e2 * e2 * e2
    return a * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
        - (35 * e6 / 3072) * math.sin(6 * phi)
    )


def tm_forward(lat, lon, crs="EPSG:5174"):
    """Geographic (on the CRS's own ellipsoid) -> TM x(easting), y(northing)."""
    (a, f), lat0, lon0, k0, fe, fn, _ = CRS_PARAMS[crs]
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    phi, lam = math.radians(lat), math.radians(lon)
    phi0, lam0 = math.radians(lat0), math.radians(lon0)
    N = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    T = math.tan(phi) ** 2
    C = ep2 * math.cos(phi) ** 2
    A = (lam - lam0) * math.cos(phi)
    M = _meridian_arc(phi, a, e2)
    M0 = _meridian_arc(phi0, a, e2)
    x = fe + k0 * N * (A + (1 - T + C) * A**3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * A**5 / 120)
    y = fn + k0 * (
        M - M0 + N * math.tan(phi) * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * A**4 / 24
                                       + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * A**6 / 720)
    )
    return x, y


def tm_inverse(x, y, crs="EPSG:5174"):
    """TM x, y -> geographic lat, lon on the CRS's own ellipsoid."""
    (a, f), lat0, lon0, k0, fe, fn, _ = CRS_PARAMS[crs]
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    M0 = _meridian_arc(math.radians(lat0), a, e2)
    M = M0 + (y - fn) / k0
    mu = M / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
            + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
            + (151 * e1**3 / 96) * math.sin(6 * mu)
            + (1097 * e1**4 / 512) * math.sin(8 * mu))
    N1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    T1 = math.tan(phi1) ** 2
    C1 = ep2 * math.cos(phi1) ** 2
    R1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    D = (x - fe) / (N1 * k0)
    phi = phi1 - (N1 * math.tan(phi1) / R1) * (
        D * D / 2 - (5 + 3 * T1 + 10 * C1 - 4 * C1 * C1 - 9 * ep2) * D**4 / 24
        + (61 + 90 * T1 + 298 * C1 + 45 * T1 * T1 - 252 * ep2 - 3 * C1 * C1) * D**6 / 720
    )
    lam = math.radians(lon0) + (
        D - (1 + 2 * T1 + C1) * D**3 / 6
        + (5 - 2 * C1 + 28 * T1 - 3 * C1 * C1 + 8 * ep2 + 24 * T1 * T1) * D**5 / 120
    ) / math.cos(phi1)
    return math.degrees(phi), math.degrees(lam)


def _geo_to_ecef(lat, lon, h, a, f):
    e2 = f * (2 - f)
    phi, lam = math.radians(lat), math.radians(lon)
    N = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    return ((N + h) * math.cos(phi) * math.cos(lam),
            (N + h) * math.cos(phi) * math.sin(lam),
            (N * (1 - e2) + h) * math.sin(phi))


def _ecef_to_geo(X, Y, Z, a, f):
    e2 = f * (2 - f)
    lon = math.atan2(Y, X)
    p = math.hypot(X, Y)
    lat = math.atan2(Z, p * (1 - e2))
    for _ in range(8):
        N = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - N
        lat = math.atan2(Z, p * (1 - e2 * N / (N + h)))
    return math.degrees(lat), math.degrees(lon)


def _helmert(X, Y, Z, params, inverse=False):
    dx, dy, dz, rx, ry, rz, ds = params
    s = 1 + ds * 1e-6
    sec = math.pi / (180 * 3600)
    rx, ry, rz = rx * sec, ry * sec, rz * sec
    if inverse:
        dx, dy, dz, rx, ry, rz = -dx, -dy, -dz, -rx, -ry, -rz
        s = 1 / s
    # position-vector convention (as in PROJ towgs84)
    return (dx + s * (X - rz * Y + ry * Z),
            dy + s * (rz * X + Y - rx * Z),
            dz + s * (-ry * X + rx * Y + Z))


def tm_to_wgs84(x, y, crs="EPSG:5174"):
    (a, f), *_rest, towgs = CRS_PARAMS[crs]
    lat, lon = tm_inverse(x, y, crs)
    if towgs is None:  # GRS80 ~ WGS84 at this precision
        return lat, lon
    X, Y, Z = _geo_to_ecef(lat, lon, 0.0, a, f)
    X, Y, Z = _helmert(X, Y, Z, towgs)
    return _ecef_to_geo(X, Y, Z, *_WGS84)


def wgs84_to_tm(lat, lon, crs="EPSG:5174"):
    (a, f), *_rest, towgs = CRS_PARAMS[crs]
    if towgs is not None:
        X, Y, Z = _geo_to_ecef(lat, lon, 0.0, *_WGS84)
        X, Y, Z = _helmert(X, Y, Z, towgs, inverse=True)
        lat, lon = _ecef_to_geo(X, Y, Z, a, f)
    return tm_forward(lat, lon, crs)


def guess_tm_crs(xs, ys):
    """Pick the TM CRS whose inverse lands the points inside Jeju."""
    best, best_hits = None, -1
    sample = list(zip(xs, ys))[:200]
    for crs in CRS_PARAMS:
        hits = 0
        for x, y in sample:
            try:
                lat, lon = tm_to_wgs84(x, y, crs)
            except (ValueError, ZeroDivisionError):
                continue
            hits += in_jeju(lat, lon)
        if hits > best_hits:
            best, best_hits = crs, hits
    return best, best_hits / max(1, len(sample))
