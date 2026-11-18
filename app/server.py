"""Local web server for the 안심하차 Agent. Standard library only.

Run:  python app/server.py      then open http://127.0.0.1:8765
Binds to 127.0.0.1 only, so nothing is reachable from outside the PC.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from agent import Session, describe_file, llm_available, run_turn  # noqa: E402
import intent  # noqa: E402

STATIC = os.path.join(HERE, "static")
SOURCES = {
    "input": {"label": "실제 자료 (공개 자료 + 올린 CSV)",
              "dirs": [os.path.join(ROOT, "data", "input"), os.path.join(ROOT, "data", "public")]},
    "sample": {"label": "가상 데이터로 시연", "dirs": [os.path.join(ROOT, "data", "sample")]},
}
MAX_UPLOAD = 2 * 1024 ** 3



def _default_source():
    """실제 자료가 하나라도 있으면 실제 자료로, 없으면 가상 시연으로 시작해요."""
    has_real = any(f for d in SOURCES["input"]["dirs"] if os.path.isdir(d)
                   for f in os.listdir(d) if f.lower().endswith(".csv"))
    return "input" if has_real else "sample"


SESSION = Session(SOURCES[_default_source()]["dirs"])
SESSION.source = _default_source()


def _file_list(dirs):
    out = []
    for d in dirs:
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(".csv"):
                    info = describe_file(os.path.join(d, f))
                    out.append({"name": f, "folder": os.path.basename(d), "label": info["label"],
                                "mb": round(os.path.getsize(os.path.join(d, f)) / 1e6, 2)})
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "NightGap/0.1"

    def log_message(self, fmt, *args):  # quieter console; args[0] may be an HTTPStatus, not a str
        if "/api/events" not in (str(args[0]) if args else ""):
            sys.stderr.write("%s\n" % (fmt % args))

    def do_HEAD(self):
        """Answer health checks (and browsers' HEAD probes) without a body."""
        self.send_response(200 if urlparse(self.path).path in ("/", "/index.html") or self.path.startswith("/static/") or self.path.startswith("/api/status") else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    # ---- helpers -----------------------------------------------------------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    # ---- routes ------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._static("index.html")
        if u.path.startswith("/static/"):
            return self._static(u.path[len("/static/"):])
        if u.path == "/api/status":
            ok, why = llm_available()
            src = SOURCES[SESSION.source]
            return self._json({"source": SESSION.source, "sources": {k: v["label"] for k, v in SOURCES.items()},
                               "files": _file_list(src["dirs"]), "llm": ok, "llm_note": why,
                               "busy": SESSION.busy, "events": len(SESSION.events)})
        if u.path == "/api/events":
            since = int((parse_qs(u.query).get("since") or ["0"])[0])
            return self._json({"events": SESSION.events[since:], "busy": SESSION.busy})
        if u.path == "/api/report.csv":
            return self._report_csv()
        self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/ask":
            body = self._body_json()
            q = (body.get("question") or "").strip()
            preview = bool(body.get("preview"))
            if not q:
                return self._json({"error": "질문을 입력해 주세요"}, 400)
            if SESSION.busy:
                return self._json({"error": "앞의 질문을 처리하고 있어요. 잠시만 기다려 주세요."}, 409)
            keep = not preview and intent.classify(q, "") == "data"   # 자료 질문은 지금 결과를 그대로 두고 답해요
            if not keep:
                SESSION.reset()
            SESSION.emit("user", q, {"preview": preview, "keep": keep})
            SESSION.busy = True
            threading.Thread(target=run_turn, args=(SESSION, q, preview), daemon=True).start()
            return self._json({"ok": True, "keep": keep})
        if u.path == "/api/source":
            key = self._body_json().get("source")
            if key not in SOURCES:
                return self._json({"error": "알 수 없는 자료 묶음"}, 400)
            if SESSION.busy:
                return self._json({"error": "분석 중에는 바꿀 수 없어요"}, 409)
            SESSION.source = key
            SESSION.reset(SOURCES[key]["dirs"])
            return self._json({"ok": True})
        if u.path == "/api/upload":
            return self._upload(parse_qs(u.query).get("name", [""])[0])
        self.send_error(404)

    def _upload(self, name):
        name = os.path.basename(name)
        if not re.fullmatch(r"[\w가-힣 .()\-]+\.csv", name, flags=re.I):
            return self._json({"error": "CSV 파일만 올릴 수 있어요"}, 400)
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_UPLOAD:
            return self._json({"error": "파일 크기를 확인해 주세요"}, 400)
        dest_dir = SOURCES["input"]["dirs"][0]
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, name)
        with open(dest, "wb") as f:
            left = n
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        info = describe_file(dest)
        if info["kind"] == "unknown":   # 무엇인지 모르는 파일은 분석 폴더에 두지 않아요
            os.remove(dest)
            return self._json({"ok": False, "saved": name, **info})
        SESSION.source = "input"
        SESSION.reset(SOURCES["input"]["dirs"])
        return self._json({"ok": True, "saved": name, **info})

    def _report_csv(self):
        rep = SESSION.report
        if not rep:
            return self._json({"error": "아직 결과가 없어요"}, 404)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["순위", "정류장", "지역", "공백 유형", "근거", "건의 문장"])
        for c in rep["cards"]:
            w.writerow([c["rank"], c["name"], c.get("area") or "", " / ".join(c["gaps"]),
                        " / ".join(c["evidence"]), c["recommendation"]])
        body = buf.getvalue().encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''nightgap_top10.csv")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel):
        path = os.path.normpath(os.path.join(STATIC, rel))
        if not path.startswith(STATIC) or not os.path.isfile(path):
            return self.send_error(404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _load_env(path):
    """Read KEY=VALUE lines from config.env without overriding real env vars."""
    if not os.path.isfile(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    _load_env(os.path.join(ROOT, "config.env"))
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}"
    print(f"안심하차 Agent 실행 중: {url}  (끄려면 Ctrl+C)")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
