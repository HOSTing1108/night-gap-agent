// 웹 데모(GitHub Pages)에서 서버 대신 답해요. 화면 코드(app.js)는 센터 PC 버전과 똑같이 써요.
(function () {
  const PREVIEW_Q = "제주 전체에서 밤길 공백이 큰 정류장 10곳 알려줘";
  const worker = new Worker("pages/worker.js");
  let events = [], busy = false, ready = false, failed = "", nid = 0;
  const uploads = [], waiting = {};
  const manifestP = fetch("manifest.json").then((r) => r.json());
  const previewP = fetch("preview.json").then((r) => r.json()).catch(() => null);

  worker.onmessage = (m) => {
    const d = m.data;
    if (d.type === "event") { events.push({ ...d.e, i: events.length }); return; }
    if (d.type === "ready") { ready = true; worker.postMessage({ type: "warm", q: PREVIEW_Q }); if (typeof refreshStatus === "function") refreshStatus(); return; }
    if (d.type === "accepted" && !d.keep) events = [];
    if (d.type === "done") busy = false;
    if (d.type === "fatal") { failed = d.error; busy = false; console.error(d.error); }
    if (d.id && waiting[d.id]) { waiting[d.id](d); delete waiting[d.id]; }
  };
  const call = (msg, transfer) => new Promise((res) => { msg.id = ++nid; waiting[msg.id] = res; worker.postMessage(msg, transfer || []); });

  window.PAGES = true;
  window.PAGES_API = async (path, opts) => {
    const u = new URL(path, location.href), body = opts && opts.body;
    switch (u.pathname.replace(/^.*\/api\//, "")) {
      case "status": {
        const m = await manifestP;
        return {
          source: "input", sources: { input: "실제 자료" }, files: [...m.files, ...uploads], llm: false, busy, events: events.length,
          mode_label: ready ? "브라우저 분석" : "준비 중",
          llm_note: ready ? "센터 PC 버전과 같은 Agent 코드가 이 브라우저 안에서 돌아요. 올린 파일은 밖으로 나가지 않아요."
            : "분석 엔진을 불러오고 있어요. 첫 질문은 조금 걸릴 수 있어요.",
        };
      }
      case "events": {
        const since = Number(u.searchParams.get("since") || 0);
        return { events: events.slice(since), busy };
      }
      case "ask": {
        const { question, preview } = JSON.parse(body || "{}");
        if (busy) throw new Error("앞의 질문을 처리하고 있어요. 잠시만 기다려 주세요.");
        if (failed) throw new Error("이 브라우저에서 분석 엔진을 불러오지 못했어요. 새로고침해 주세요.");
        const cached = preview && (await previewP);
        if (cached) { events = cached.map((e) => ({ ...e })); return { ok: true, keep: false }; }
        busy = true;
        const r = await call({ type: "ask", q: question, preview: !!preview });
        return { ok: true, keep: r.keep };
      }
      case "source": return { ok: true };
      case "upload": {
        const name = decodeURIComponent(u.searchParams.get("name") || "file.csv");
        if (!body || body.size > 50 * 1024 * 1024) throw new Error("웹 데모에서는 50MB까지 올릴 수 있어요");
        const buf = await body.arrayBuffer();
        const r = (await call({ type: "upload", name, buf }, [buf])).info;
        if (r.ok) uploads.push({ name, folder: "uploads", label: r.label, mb: Math.round(body.size / 1e4) / 100 });
        return { ...r, saved: name };
      }
    }
    throw new Error("알 수 없는 요청이에요");
  };

  // 순위 CSV 저장: 서버 대신 브라우저에서 만들어요
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("#csv");
    if (!a || typeof report === "undefined" || !report) return;
    e.preventDefault();
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const rows = [["순위", "정류장", "지역", "공백 유형", "근거", "건의 문장"]].concat(report.cards.map((c) =>
      [c.rank, c.name, c.area || "", (c.gaps || []).join(" / "), (c.evidence || []).join(" / "), c.recommendation]));
    const blob = new Blob(["﻿" + rows.map((r) => r.map(q).join(",")).join("\r\n")], { type: "text/csv" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob); link.download = "nightgap_top10.csv"; link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }, true);
})();
