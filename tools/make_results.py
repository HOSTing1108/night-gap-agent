"""Reproduce the numbers quoted in docs/result_explanation.md.

Writes docs/results/*. Run:  python tools/make_results.py
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
os.environ["NIGHTGAP_FORCE_RULES"] = "1"
import agent as A  # noqa: E402
import tools as T  # noqa: E402

OUT = os.path.join(ROOT, "docs", "results")
os.makedirs(OUT, exist_ok=True)

scenarios = [
    ("S1_jeju_all", "제주 전체에서 밤길 공백이 큰 정류장 10곳 알려줘"),
    ("S2_nohyeong_22h", "우리 애가 밤 10시에 노형에서 버스 내리는데, 그 근처 괜찮아?"),
    ("S3_aewol", "애월 쪽에 CCTV나 비상벨을 먼저 놓아야 할 곳 건의서 만들어줘"),
]
summary = {}
for key, q in scenarios:
    s = A.Session([os.path.join(ROOT, "data", "sample")])
    A.run_turn(s, q)
    with open(os.path.join(OUT, f"{key}.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["순위", "정류장", "지역", "공백 유형", "근거", "건의 문장"])
        for c in s.report["cards"]:
            w.writerow([c["rank"], c["name"], c["area"], " / ".join(c["gaps"]), " / ".join(c["evidence"]), c["recommendation"]])
    summary[key] = {
        "question": q,
        "steps": [e["text"] for e in s.events if e["type"] in ("tool", "decision", "tool_result", "answer")],
        "settings": s.report["settings"],
        "top3": [{k: c[k] for k in ("rank", "name", "gaps", "evidence")} for c in s.report["cards"][:3]],
    }

cov = {}
for f, k in (("cctv_jeju.csv", "cctv"), ("safety_bell_jeju.csv", "bell")):
    p = os.path.join(ROOT, "data", "public", f)
    prof = T.profile_csv(p)
    pts, rep = T.load_points(p, T.identify_dataset(prof)["coords"], k)
    cov[k] = {"rows": prof["rows"], "load": rep, "coverage": T.check_coverage(pts, k)}
summary["public_coverage"] = cov
json.dump(summary, open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps({k: (v["top3"][0]["name"] if "top3" in v else "") for k, v in summary.items()}, ensure_ascii=False))
