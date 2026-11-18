"""기획서(docs/plan/plan_template.html)의 숫자를 실제 자료로 채워 plan.html과 PDF를 만들어요.

실행: python tools/build_plan.py            (숫자 채우기 + PDF)
      python tools/build_plan.py --no-pdf   (HTML만)
- 템플릿의 {{이름}} 자리를 이번 실행 결과로 바꿔요. 빠진 이름이 있으면 멈춰요.
- 그림 c11(예산별 효과), c12(처방 유형)도 함께 만들어요. 나머지 차트는 tools/make_real_stats.py가 만들어요.
- 화면 캡처(docs/img/real_*.png)는 이 스크립트가 만들지 않아요.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
os.environ["NIGHTGAP_FORCE_RULES"] = "1"
import agent as A  # noqa: E402
import tools as T  # noqa: E402

PLAN = os.path.join(ROOT, "docs", "plan")
CHARTS = os.path.join(ROOT, "docs", "img", "charts")
ORG, ORGL, BLUE, BLUEL, GREEN, GREY, INK, SUB = "#e8892b", "#f6c48f", "#2f6fd6", "#9dbcf0", "#1e9e74", "#b8bfcc", "#1d2433", "#5d6678"


def n(v, d=0):
    return f"{v:,.{d}f}"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------- budget game, same rules as app.js ----------------
def g_eval(g, costs, picks):
    ce, cb, cost = set(), set(), 0
    for c in g["candidates"]:
        p = picks.get(c["id"])
        if not p:
            continue
        if p.get("e"):
            cost += costs[p["e"]]
            ce.update(c["ce"])
        if p.get("b"):
            cost += costs["bell"]
            cb.update(c["cb"])
    eu = sum(g["demand"].get(i, 0) for i in ce)
    bu = sum(g["demand"].get(i, 0) for i in cb)
    return {"cost": cost, "eu": eu, "bu": bu, "score": eu + bu, "stopsE": len(ce), "stopsB": len(cb)}


def g_auto(g, costs, budget, mode):
    picks, left = {}, budget
    if mode == "busy":
        for c in sorted(g["candidates"], key=lambda c: -c["demand"]):
            p = {"e": None, "b": False}
            if "new_cctv" in c["opts"] and left >= costs["new_cctv"]:
                p["e"], left = "new_cctv", left - costs["new_cctv"]
            if "bell" in c["opts"] and left >= costs["bell"]:
                p["b"], left = True, left - costs["bell"]
            if p["e"] or p["b"]:
                picks[c["id"]] = p
        return picks
    while True:
        base, best = g_eval(g, costs, picks)["score"], None
        for c in g["candidates"]:
            cur = picks.get(c["id"], {"e": None, "b": False})
            for a in c["opts"]:
                if (cur["b"] if a == "bell" else (cur["e"] and not (cur["e"] == "adjust_cctv" and a == "new_cctv"))):
                    continue
                add = costs["bell"] if a == "bell" else costs[a] - (costs[cur["e"]] if cur["e"] else 0)
                if add > left or add <= 0:
                    continue
                np_ = dict(cur)
                if a == "bell":
                    np_["b"] = True
                else:
                    np_["e"] = a
                gain = g_eval(g, costs, {**picks, c["id"]: np_})["score"] - base
                if gain > 0 and (best is None or gain / add > best[0]):
                    best = (gain / add, c["id"], np_, add)
        if not best:
            return picks
        picks[best[1]] = best[2]
        left -= best[3]


# ---------------- small SVG charts ----------------
def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'font-family="Apple SD Gothic Neo, Malgun Gothic, sans-serif"><title>{esc(title)}</title>'
            f'<rect width="{w}" height="{h}" fill="#fff"/>{body}</svg>')


def tx(x, y, t, fs=12, c=SUB, a="start", w=400):
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{fs}" fill="{c}" text-anchor="{a}" font-weight="{w}">{esc(t)}</text>'


def budget_chart(pts, mark):
    W, H, L, R, Tp, B = 760, 280, 70, 20, 30, 36
    import math
    mx = max(p["ag"] for p in pts) * 1.1
    sx = lambda b: L + (W - L - R) * math.log(b / pts[0]["b"]) / math.log(pts[-1]["b"] / pts[0]["b"])  # noqa: E731
    sy = lambda v: Tp + (H - Tp - B) * (1 - v / mx)  # noqa: E731
    out = []
    for f in (0, .5, 1):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(mx * f):.1f}" y2="{sy(mx * f):.1f}" stroke="#e5e8ef"/>')
        out.append(tx(L - 8, sy(mx * f) + 4, n(mx * f), 11, a="end"))
    for b in (1000, 4000, 10000, 30000):
        out.append(tx(sx(b), H - 12, f"{b // 10000}억원" if b >= 10000 else f"{b:,}만원", 11, a="middle"))
    out.append(f'<line x1="{sx(mark):.1f}" x2="{sx(mark):.1f}" y1="{Tp}" y2="{H - B}" stroke="{INK}" stroke-dasharray="4 4"/>')
    for k, c in (("bz", GREY), ("ag", ORG)):
        d = " ".join(f'{"L" if i else "M"}{sx(p["b"]):.1f},{sy(p[k]):.1f}' for i, p in enumerate(pts))
        out.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="3"/>')
        out += [f'<circle cx="{sx(p["b"]):.1f}" cy="{sy(p[k]):.1f}" r="4" fill="{c}"/>' for p in pts]
    out.append(tx(L, 18, "━ Agent 추천 (같은 돈으로 더 많이 지키는 곳부터)", 12, ORG, w=700))
    out.append(tx(L + 330, 18, "━ 이용 많은 곳부터 새로 달기", 12, "#8e94a3", w=700))
    return svg(W, H, "".join(out), "예산에 따라 지킬 수 있는 사람 수")


def rx_chart(rx):
    lab = [("new_cctv", "CCTV 새로 달기", ORG), ("adjust_cctv", "근처 CCTV 방향 돌리기", BLUE), ("bell", "비상벨 달기", GREEN)]
    W, L, R, rh = 760, 230, 90, 34
    tot = sum(rx.values()) or 1
    out = []
    for i, (k, l, c) in enumerate(lab):
        v = rx.get(k, 0)
        y = 8 + i * rh
        w = (W - L - R) * v / max(rx.values())
        out.append(tx(L - 10, y + 19, l, 13, INK, "end"))
        out.append(f'<rect x="{L}" y="{y + 6}" width="{W - L - R}" height="18" rx="4" fill="#f2f4f8"/>')
        out.append(f'<rect x="{L}" y="{y + 6}" width="{max(2, w):.1f}" height="18" rx="4" fill="{c}"/>')
        out.append(tx(L + max(2, w) + 8, y + 20, f"{v:,}곳 ({100 * v / tot:.0f}%)", 12, INK, w=700))
    return svg(W, 8 + len(lab) * rh + 6, "".join(out), "고치는 방법")


# ---------------- numbers ----------------
def collect():
    secs = None
    for _ in range(2):      # take the better of two runs so a busy machine does not skew the number
        t0 = time.perf_counter()
        s = A.Session([os.path.join(ROOT, "data", "input"), os.path.join(ROOT, "data", "public")])
        A.run_turn(s, "제주 전체에서 밤길 공백이 큰 정류장 10곳 알려줘", force_rules=True)
        el = time.perf_counter() - t0
        secs = el if secs is None else min(secs, el)
    rep, sm = s.report, s.report["summary"]
    st = json.load(open(os.path.join(ROOT, "docs", "results", "real", "stats.json"), encoding="utf-8"))
    cards, ov, sc = rep["cards"], sm["overview"], sm["school"]
    cats = ov["cats"]
    V = {}
    V["days"] = n(st["usage_days"])
    V["matched"] = st["usage_matched_pct"]
    V["stops"] = n(sm["stops_scored"])
    V["blind"] = n(cats["both"]["stops"])
    V["blind_pct"] = cats["both"]["stop_pct"]
    V["blind_pct0"] = n(cats["both"]["stop_pct"])
    V["blind_use_pct"] = cats["both"]["use_pct"]
    V["blind_use_pct0"] = n(cats["both"]["use_pct"])
    V["only_cctv"], V["only_bell"], V["covered"] = (n(cats[k]["stops"]) for k in ("no_cctv", "no_bell", "covered"))
    V["covered_pct"] = n(cats["covered"]["stop_pct"])
    V["blind_dark"] = n(st["blind_dark"])
    V["blind_dark_use"] = n(round(st["blind_dark_use"], -1))
    V["top10pct"] = n(st["top10pct_usage_share"])
    V["daily_total"] = n(st["daily_use_total"])
    d = st["distance"]
    V["cctv_far_pct"] = n(d["cctv_all"][-1])
    V["top100_near_pct"] = n(d["cctv_top100"][0])
    V["cctv_jeju"] = n(st["cctv_purpose_by_city"]["제주시"].get("교통", 0))
    hb = {b["band"]: b["per_day"] for b in st["hourly"]["bands"]}
    tot = sum(hb.values())
    V["late_per_day"] = n(hb.get("22시-24시", 0))
    V["night_per_day"] = n(hb.get("19시-21시", 0) + hb.get("22시-24시", 0))
    V["night_share"] = f'{100 * (hb.get("19시-21시", 0) + hb.get("22시-24시", 0)) / tot:.1f}'
    # areas
    areas = st["areas"]
    rows = sorted([a for a in areas if not a["area"].endswith("동지역")], key=lambda a: -a["blind_pct"])[:4] + \
        [a for a in areas if a["area"].endswith("동지역")]
    V["area_rows"] = "\n".join(
        f'    <tr><td>{esc(a["area"])}</td><td class="num">{a["stops"]:,}</td><td class="num">{a["blind"]:,} ({a["blind_pct"]:.0f}%)</td>'
        f'<td class="num">{a["blind_dark"]:,}</td><td class="num">{round(a["use"]):,}명</td><td class="num">{a["blind_use_pct"]:.0f}%</td></tr>' for a in rows)
    top_area = rows[0]
    V["area1"], V["area1_stops"], V["area1_blind"], V["area1_pct"] = top_area["area"], n(top_area["stops"]), n(top_area["blind"]), n(top_area["blind_pct"])
    V["area_list_all"] = " · ".join(f'{a["area"]} {a["blind_pct"]:.0f}%' for a in sorted(areas, key=lambda a: -a["blind_pct"]))
    V["area_list"] = " · ".join(f'{a["area"]} {a["blind_pct"]:.0f}%' for a in sorted(areas, key=lambda a: -a["blind_pct"])[:4])
    # top 10
    V["top10_rows"] = "\n".join(
        f'    <tr><td class="num">{c["rank"]}</td><td>{esc(c["name"])}</td><td>{esc(c.get("area") or "")}</td><td>{ {"new_cctv": "새로 달기", "adjust_cctv": "방향 돌리기", "bell": "비상벨 달기"}.get(c["rx"]["code"], c["rx"]["label"]) }</td>'
        f'<td class="num">{c["demand"]:,.0f}명</td><td class="num">{c["cctv_m"]:,}m</td><td class="num">{c["bell_m"]:,}m</td>'
        f'<td class="num">{c["stay"]}/{c["stay_of"]}</td></tr>' for c in cards)
    RXS = {"new_cctv": "새로 달기", "adjust_cctv": "방향 돌리기", "bell": "비상벨 달기"}
    V["top10_md"] = "\n".join(
        f'| {c["rank"]} | {c["name"]} | {c.get("area") or ""} | {RXS.get(c["rx"]["code"], c["rx"]["label"])} | {c["demand"]:,.0f}명 | {c["cctv_m"]:,}m | {c["bell_m"]:,}m | {c["stay"]}/{c["stay_of"]} |' for c in cards)
    c1, c2, c3 = cards[0], cards[1], cards[2]
    V["top1"], V["top1_area"], V["top1_use"] = c1["name"], c1.get("area") or "", n(c1["demand"])
    V["top1_rx"] = c1["rx"]["label"]
    V["top1_cctv"], V["top1_bell"] = n(c1["cctv_m"]), n(c1["bell_m"])
    V["top1_walk"] = max(1, round(min(c1["cctv_m"], c1["bell_m"]) / 80))
    V["top2"], V["top3"] = c2["name"], c3["name"]
    adj = [c for c in cards if c["rx"]["code"] == "adjust_cctv"]
    V["adj_n"] = len(adj)
    V["adj_examples"] = ", ".join(f'{c["name"]}({c["cctv_m"]}m)' for c in adj[:3])
    sch = [c for c in cards if c.get("school") and c["school"]["n500"]]
    V["school_cards"] = len(sch)
    V["school_examples"] = ", ".join(f'{c["name"]}({c["school"]["name"]} {c["school"]["m"]}m)' for c in sch[:3])
    # robustness
    rb = rep["robustness"]
    V["rob_n"], V["rob_stable"], V["rob_avg"] = rb["n"], rb["stable"], rb["avg_kept"]
    day1 = json.load(open(os.path.join(ROOT, "docs", "results", "real", "top10_day1.json"), encoding="utf-8"))
    V["overlap_day1"] = len(set(day1["top10"]) & {c["name"] for c in cards})
    d13 = json.load(open(os.path.join(ROOT, "docs", "results", "real", "top10_day13.json"), encoding="utf-8"))
    V["overlap_day13"] = len(set(d13["top10"]) & {c["name"] for c in cards})
    # placement + game
    pl = sm["placement"]["cctv"]
    p10 = pl["picks"][9]
    V["k10_users"], V["k10_pct"] = n(p10["cum"]), f'{p10["cum_pct"]:.0f}'
    b = pl["bench"]
    V["k10_greedy"], V["k10_busy"], V["k10_far"], V["k10_rand"] = n(b["greedy"]), n(b["demand_first"]), n(b["farthest_first"]), n(b["random_avg"])
    V["k10_x_far"] = n(b["greedy"] / max(1, b["farthest_first"]))
    V["k10_more"] = n(b["greedy"] - b["demand_first"])
    newstop = next((x for x in pl["picks"][:10] if x["id"] not in {c["id"] for c in cards}), None)
    V["new_pick"] = f'{newstop["name"]}(설치 순서 {newstop["n"]}번, 정류장 {newstop["covered_stops"]}곳에 함께 닿음)' if newstop else "-"
    g = sm["game"]
    costs = g["costs"]
    V["cost_cctv"], V["cost_adj"], V["cost_bell"] = costs["new_cctv"], costs["adjust_cctv"], costs["bell"]
    V["game_cands"] = len(g["candidates"])
    ag = g_eval(g, costs, g_auto(g, costs, 10000, "agent"))
    bz = g_eval(g, costs, g_auto(g, costs, 10000, "busy"))
    agp = g_auto(g, costs, 10000, "agent")
    V["g_score"], V["g_busy"] = n(ag["score"]), n(bz["score"])
    V["g_more_pct"] = n(100 * (ag["score"] / bz["score"] - 1))
    V["g_eu"], V["g_bu"], V["g_sites"] = n(ag["eu"]), n(ag["bu"]), len(agp)
    V["g_adj"] = sum(1 for p in agp.values() if p.get("e") == "adjust_cctv")
    V["g_busy_pct"] = n(100 * bz["score"] / ag["score"])
    pts = [{"b": bb, "ag": g_eval(g, costs, g_auto(g, costs, bb, "agent"))["score"],
            "bz": g_eval(g, costs, g_auto(g, costs, bb, "busy"))["score"]} for bb in (1000, 2000, 4000, 6000, 10000, 15000, 20000, 30000)]
    open(os.path.join(CHARTS, "c11_budget.svg"), "w", encoding="utf-8").write(budget_chart(pts, 10000))
    rx = sm["rx_counts"]
    open(os.path.join(CHARTS, "c12_rx.svg"), "w", encoding="utf-8").write(rx_chart(rx))
    V["rx_new"], V["rx_adj"], V["rx_bell"] = n(rx.get("new_cctv", 0)), n(rx.get("adjust_cctv", 0)), n(rx.get("bell", 0))
    V["school_n"], V["school_near"], V["school_blind"], V["school_blind_use"] = n(sc["schools"]), n(sc["stops_near"]), n(sc["blind_near"]), n(sc["blind_near_use"])
    V["school_blind_pct"] = n(100 * sc["blind_near"] / sc["stops_near"])
    # performance
    V["secs"] = f"{secs:.1f}"
    V["tools"] = len(A.TOOLS)
    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"))
    V["tests"] = suite.countTestCases()
    cases = json.load(open(os.path.join(ROOT, "tests", "intent_cases.json"), encoding="utf-8"))
    import intent as Iq
    towns = s.towns()
    ok = 0
    for c in cases:
        place, hours = A.parse_question(c["q"], s.stops, towns)
        cnt, kind = A.parse_budget(c["q"])
        qtype = Iq.classify(c["q"], place)
        ok += (place == c.get("place", place) and (hours or None) == c.get("hours", hours or None)
               and cnt == c.get("count", cnt) and kind == c.get("kind", kind) and qtype == c.get("type", qtype))
    V["intent_ok"], V["intent_n"] = ok, len(cases)
    return V


def fill(src, dst, V):
    tpl = open(src, encoding="utf-8").read()
    missing = sorted(set(re.findall(r"\{\{(\w+)\}\}", tpl)) - set(V))
    if missing:
        sys.exit(f"{os.path.basename(src)}에 값이 없는 자리: " + ", ".join(missing))
    open(dst, "w", encoding="utf-8").write(re.sub(r"\{\{(\w+)\}\}", lambda m: str(V[m.group(1)]), tpl))


def render(V):
    fill(os.path.join(PLAN, "plan_template.html"), os.path.join(PLAN, "plan.html"), V)
    fill(os.path.join(ROOT, "docs", "result_explanation_template.md"), os.path.join(ROOT, "docs", "result_explanation.md"), V)
    json.dump(V, open(os.path.join(ROOT, "docs", "results", "real", "plan_numbers.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def pdf():
    chrome = next((c for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                               r"C:\Program Files\Google\Chrome\Application\chrome.exe", shutil.which("google-chrome") or "") if c and os.path.exists(c)), None)
    if not chrome:
        print("Chrome을 찾지 못해 PDF는 만들지 않았어요. plan.html을 브라우저에서 인쇄하세요.")
        return
    out = os.path.join(PLAN, "nightgap_plan.pdf")
    subprocess.run([chrome, "--headless=new", "--no-pdf-header-footer", "--virtual-time-budget=6000",
                    f"--print-to-pdf={out}", "file://" + os.path.join(PLAN, "plan.html")], capture_output=True)
    dist = os.path.join(os.path.dirname(ROOT), "dist")
    if os.path.isdir(dist):
        shutil.copy(out, os.path.join(dist, "안심하차Agent_기획서.pdf"))
    print("PDF →", os.path.relpath(out, ROOT))


if __name__ == "__main__":
    V = collect()
    render(V)
    print(json.dumps({k: V[k] for k in ("days", "stops", "blind_pct", "top1", "rob_stable", "k10_users", "g_score", "tests", "intent_ok", "secs")}, ensure_ascii=False))
    if "--no-pdf" not in sys.argv:
        pdf()
