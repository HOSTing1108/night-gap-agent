// 웹 데모용 분석 엔진: 센터 PC 버전과 같은 파이썬 Agent 코드를 브라우저 안(Pyodide)에서 돌려요.
// 자료와 올린 파일은 이 브라우저 밖으로 나가지 않아요.
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
importScripts(PYODIDE + "pyodide.js");

let py = null;
const NC = { cache: "no-cache" };   // 바뀐 Agent 코드·자료가 바로 반영되게 매번 확인해요
let queue = Promise.resolve();
const run = (fn) => (queue = queue.then(fn).catch((e) => postMessage({ type: "fatal", error: String(e) })));

async function init() {
  py = await loadPyodide({ indexURL: PYODIDE });
  const m = await (await fetch("../manifest.json", NC)).json();
  for (const d of ["/app/app", "/app/data/input", "/app/data/public", "/app/data/uploads"]) py.FS.mkdirTree(d);
  await Promise.all(m.py.map(async (f) => py.FS.writeFile("/app/app/" + f, await (await fetch("../py/" + f, NC)).text())));
  await Promise.all(m.data.map(async (f) => py.FS.writeFile("/app/data/" + f,
    new Uint8Array(await (await fetch("../data/" + f, NC)).arrayBuffer()))));
  py.globals.set("post_event", (s) => postMessage({ type: "event", e: JSON.parse(s) }));
  await py.runPythonAsync(`
import sys, os, json
os.environ["NIGHTGAP_FORCE_RULES"] = "1"
sys.path.insert(0, "/app/app")
import agent as A
import intent as I
DIRS = ["/app/data/uploads", "/app/data/input", "/app/data/public"]
S = A.Session(DIRS)
S.source = "input"
QUIET = [False]
_emit = A.Session.emit
def _emit_and_post(self, *a, **k):
    _emit(self, *a, **k)
    if not QUIET[0]:
        post_event(json.dumps(self.events[-1], ensure_ascii=False, default=str))
A.Session.emit = _emit_and_post

def ask(q, preview, quiet=False):
    keep = (not preview) and I.classify(q, "") == "data"
    if not keep:
        S.reset(DIRS)
    return keep

def turn(q, preview, keep, quiet=False):
    QUIET[0] = quiet
    try:
        S.emit("user", q, {"preview": preview, "keep": keep})
        A.run_turn(S, q, True)
    finally:
        QUIET[0] = False

def upload(name):
    path = os.path.join("/app/data/uploads", os.path.basename(name))
    info = dict(A.describe_file(path))
    if info["kind"] == "unknown":
        os.remove(path)
        info["ok"] = False
    else:
        S.reset(DIRS)
        info["ok"] = True
    return json.dumps(info, ensure_ascii=False)
`);
  postMessage({ type: "ready" });
}

run(init);

self.onmessage = (ev) => {
  const d = ev.data;
  if (d.type === "warm") {
    // 첫 화면은 미리 계산한 결과를 보여주고, 그 사이 같은 계산을 조용히 해 두어요 (이어지는 질문에 씀)
    run(async () => { py.globals.get("ask")(d.q, true); py.globals.get("turn")(d.q, true, false, true); postMessage({ type: "warmed" }); });
  } else if (d.type === "ask") {
    run(async () => {
      const keep = py.globals.get("ask")(d.q, d.preview);
      postMessage({ type: "accepted", id: d.id, keep });
      py.globals.get("turn")(d.q, d.preview, keep);
      postMessage({ type: "done" });
    });
  } else if (d.type === "upload") {
    run(async () => {
      py.FS.writeFile("/app/data/uploads/" + d.name.replace(/[\\/]/g, "_"), new Uint8Array(d.buf));
      postMessage({ type: "uploaded", id: d.id, info: JSON.parse(py.globals.get("upload")(d.name.replace(/[\\/]/g, "_"))) });
    });
  }
};
