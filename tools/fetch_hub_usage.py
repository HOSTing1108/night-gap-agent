"""제주데이터허브 '일일 정류소별 버스 이용자 정보' API를 날짜별로 받아 정류장별 합계로 만들어요.

준비: config.env 에 JEJU_HUB_KEY=<허브 마이페이지 > 프로젝트 관리 > 프로젝트키> 한 줄을 넣어요.
실행: python tools/fetch_hub_usage.py 20251013 20251025
- 하루치마다 data/input/hub_daily/YYYYMMDD.csv 로 저장해요. 이미 받은 날은 건너뛰어서, 중간에 멈춰도 다시 실행하면 이어서 받아요.
- 허브 API는 한 번에 100행, 하루 1,000회까지예요. 하루치가 약 65회라 하루에 13일 정도 받을 수 있어요.
- 마지막에 data/input/hub_stop_daily_usage.csv (stationId, stationName, board, transfer, days)를 만들어요.
- 키는 화면, 로그, 결과 파일 어디에도 쓰지 않아요.
"""
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://open.jejudatahub.net/api/proxy/1b1ta1a1ba6t6a1ttt3bD6b1tb1t1D3t/{key}?startDate={d}&endDate={d}&limit=100&number={p}"
OUT = os.path.join(ROOT, "data", "input", "hub_daily")


def load_key():
    key = os.environ.get("JEJU_HUB_KEY")
    path = os.path.join(ROOT, "config.env")
    if not key and os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            if line.strip().startswith("JEJU_HUB_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        sys.exit("config.env 에 JEJU_HUB_KEY 가 없어요.")
    return key


class QuotaOver(Exception):
    pass


def get(key, day, page, stats):
    url = API.format(key=key, d=day, p=page)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                stats["calls"] += 1
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            stats["calls"] += 1
            body = e.read().decode("utf-8", "ignore")
            if e.code in (401, 403, 429) or "quota" in body.lower() or "쿼터" in body:
                raise QuotaOver(f"HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def fetch_day(key, day, stats):
    first = get(key, day, 1, stats)
    if first is None:
        return None
    total = first.get("totCnt", 0)
    pages = (total + 99) // 100
    rows, lost = list(first.get("data", [])), []
    for p in range(2, pages + 1):
        j = get(key, day, p, stats)
        if j is None:
            lost.append(p)
        else:
            rows.extend(j.get("data", []))
    return rows, total, lost


def main():
    if len(sys.argv) != 3:
        sys.exit("사용법: python tools/fetch_hub_usage.py 시작일 종료일  (예: 20251013 20251025)")
    key = load_key()
    d0, d1 = (dt.datetime.strptime(a, "%Y%m%d").date() for a in sys.argv[1:])
    os.makedirs(OUT, exist_ok=True)
    stats = {"calls": 0}
    day = d0
    try:
        while day <= d1:
            ds = day.strftime("%Y%m%d")
            path = os.path.join(OUT, ds + ".csv")
            if os.path.isfile(path):
                print(ds, "이미 있음, 건너뜀")
            else:
                got = fetch_day(key, ds, stats)
                if got is None:
                    print(ds, "실패 (첫 페이지를 받지 못함). 한도를 다 썼을 수 있어요. 여기서 멈춰요.")
                    break
                rows, total, lost = got
                if len(lost) > max(2, total // 1000):     # too many holes: don't save, so the next run fetches the day again
                    print(f"{ds} 못 받은 페이지가 {len(lost)}개라 저장하지 않았어요. 한도를 다 썼을 수 있어요. 여기서 멈춰요.")
                    break
                with open(path, "w", encoding="utf-8", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["baseDate", "stationId", "stationName", "moveType", "userCount"])
                    for x in rows:
                        w.writerow([x.get("baseDate"), x.get("stationId"), x.get("stationName"), x.get("moveType"), x.get("userCount")])
                print(f"{ds} {len(rows)}/{total}행" + (f", 못 받은 페이지 {lost}" if lost else "") + f" (누적 호출 {stats['calls']})")
            day += dt.timedelta(days=1)
    except QuotaOver as e:
        print(f"허브가 요청을 거절했어요({e}). 하루 한도를 다 쓴 것 같아요. 내일 같은 명령으로 이어서 받으면 돼요.")
    aggregate()


def aggregate():
    agg, days = {}, set()
    for f in sorted(os.listdir(OUT)):
        if not f.endswith(".csv"):
            continue
        for r in csv.DictReader(open(os.path.join(OUT, f), encoding="utf-8")):
            a = agg.setdefault(r["stationId"], {"name": "", "board": 0, "transfer": 0})
            if not a["name"] and r["stationName"]:      # some days come back without names; keep the first real one
                a["name"] = r["stationName"]
            a["transfer" if r["moveType"] == "환승" else "board"] += int(float(r["userCount"] or 0))
            days.add(r["baseDate"])
    if not agg:
        return
    out = os.path.join(ROOT, "data", "input", "hub_stop_daily_usage.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stationId", "stationName", "board", "transfer", "days"])
        for sid, a in sorted(agg.items()):
            w.writerow([sid, a["name"], a["board"], a["transfer"], len(days)])
    print(f"정류장 {len(agg)}곳, {len(days)}일 합계 → {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
