"""웹 데모(GitHub Pages)용 정적 사이트를 site/ 에 만들어요.

센터 PC 버전과 같은 화면(app/static)과 같은 파이썬 Agent 코드(app/*.py)를 그대로 쓰고,
서버 대신 브라우저 안의 파이썬(Pyodide)이 분석해요. 첫 화면 결과는 여기서 미리 계산해 둬요.

실행: python tools/build_pages.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
OUT = os.path.join(ROOT, "site")
sys.path.insert(0, APP)
os.environ["NIGHTGAP_FORCE_RULES"] = "1"

import agent as A  # noqa: E402

PREVIEW_Q = "제주 전체에서 밤길 공백이 큰 정류장 10곳 알려줘"
PY_FILES = ["agent.py", "tools.py", "geo.py", "intent.py"]
DATA = {"input": ["hub_stop_daily_usage.csv"], "public": None}   # None = 폴더의 CSV 전부


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    # 1. 화면: 절대 경로(/static/)를 GitHub Pages 하위 경로에서도 열리게 바꿔요
    shutil.copytree(os.path.join(APP, "static"), os.path.join(OUT, "static"))
    for name, new in (("style.css", ""), ("app.js", "static/")):
        p = os.path.join(OUT, "static", name)
        s = open(p, encoding="utf-8").read().replace("/static/", new)
        open(p, "w", encoding="utf-8").write(s)
    html = open(os.path.join(APP, "static", "index.html"), encoding="utf-8").read()
    html = html.replace('"/static/', '"static/').replace('href="/"', 'href="./"')
    html = html.replace('<script src="static/jeju_outline.js"></script>',
                        '<script src="pages/bridge.js"></script>\n<script src="static/jeju_outline.js"></script>', 1)
    assert "pages/bridge.js" in html
    open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(html)
    shutil.copytree(os.path.join(ROOT, "pages"), os.path.join(OUT, "pages"))

    # 2. Agent 코드와 자료
    os.makedirs(os.path.join(OUT, "py"))
    for f in PY_FILES:
        shutil.copy(os.path.join(APP, f), os.path.join(OUT, "py", f))
    data, files = [], []
    for folder, names in DATA.items():
        src = os.path.join(ROOT, "data", folder)
        names = names or sorted(n for n in os.listdir(src) if n.lower().endswith(".csv"))
        os.makedirs(os.path.join(OUT, "data", folder), exist_ok=True)
        for n in names:
            shutil.copy(os.path.join(src, n), os.path.join(OUT, "data", folder, n))
            data.append(f"{folder}/{n}")
            info = A.describe_file(os.path.join(src, n))
            files.append({"name": n, "folder": folder, "label": info["label"],
                          "mb": round(os.path.getsize(os.path.join(src, n)) / 1e6, 2)})
    json.dump({"py": PY_FILES, "data": data, "files": files},
              open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False)

    # 3. 첫 화면 결과를 미리 계산해요 (브라우저와 같은 코드, 같은 자료)
    s = A.Session([os.path.join(ROOT, "data", "input"), os.path.join(ROOT, "data", "public")])
    s.source = "input"
    s.emit("user", PREVIEW_Q, {"preview": True, "keep": False})
    A.run_turn(s, PREVIEW_Q, True)
    # 실제로 쓴 입력 폴더의 사용자 업로드 파일은 넣지 않아요
    used = {os.path.basename(p) for p in s.files}
    extra = used - {n.split("/", 1)[1] for n in data}
    if extra:
        raise SystemExit(f"data/input 에 웹 데모에 넣지 않을 파일이 있어요: {sorted(extra)}")
    json.dump(s.events, open(os.path.join(OUT, "preview.json"), "w", encoding="utf-8"), ensure_ascii=False, default=str)
    open(os.path.join(OUT, ".nojekyll"), "w").close()

    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(OUT) for f in fs)
    print(f"site/ 완료: 파일 {sum(len(fs) for _, _, fs in os.walk(OUT))}개, {size / 1e6:.1f} MB, "
          f"첫 화면 1위 {s.report['cards'][0]['name'] if s.report else '-'}")


if __name__ == "__main__":
    main()
