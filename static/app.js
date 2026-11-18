"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const GAP_SHORT = { eyes: "CCTV 없음", bell: "비상벨 없음", last_bus: "막차 일찍 끊김", light: "보안등 없음" };
const SOURCE_SHORT = { input: "실제 자료", sample: "가상 시연" };
let lastQ = "", keepTurn = false;
const PREVIEW_Q = "제주 전체에서 밤길 공백이 큰 정류장 10곳 알려줘";
const PARAMS = new URLSearchParams(location.search);
// ?still=1 turns motion off (used for screenshots and by people who prefer it).
if (PARAMS.get("view") === "ws") document.documentElement.classList.add("only-ws");   // 캡처용: 소개를 숨기고 작업 공간만
const STILL = PARAMS.has("still") || matchMedia("(prefers-reduced-motion: reduce)").matches;
if (STILL) document.documentElement.classList.add("still");

let isPreview = false, curSource = "sample";
let since = 0, polling = null, mapData = null, report = null, steps = 0, stepNow = 0;
let sel = { zone: null, stop: null };

const map = new NightMap3D($("#map3d"), {
  outline: typeof JEJU_OUTLINE !== "undefined" ? JEJU_OUTLINE : [],
  still: STILL,
  font: getComputedStyle(document.body).fontFamily,
  onPick: (h) => (h.type === "zone" ? selectZone(h.key) : selectStop(h.id)),
  nightLights: "static/nightlights_jeju_2016.png",
  onSatellite: () => { drawDarkStat(); drawInspect(); },
});

// 첫 화면 뒤에 깔리는 3D 지도 (보기 전용, 누르면 큰 지도로)
const mapHero = $("#map-hero") ? new NightMap3D($("#map-hero"), {
  outline: typeof JEJU_OUTLINE !== "undefined" ? JEJU_OUTLINE : [],
  still: STILL,
  font: getComputedStyle(document.body).fontFamily,
  nightLights: "static/nightlights_jeju_2016.png",
}) : null;
function placeHero() { if (!mapHero) return; const narrow = matchMedia("(max-width: 900px)").matches, phone = matchMedia("(max-width: 600px)").matches;
  let y = narrow ? 0.62 : 0.52;
  if (phone) {   // 휴대폰: 버튼 아래 빈 자리 한가운데에 섬을 놓아요
    const hero = $(".lp-hero"), cta = $(".lp-cta");
    if (hero && cta) {
      const h = hero.getBoundingClientRect(), b = cta.getBoundingClientRect().bottom - h.top;
      if (h.height > 0) y = Math.min(0.92, (b + (h.height - b) / 2) / h.height);
    }
  }
  mapHero.setCenter(narrow ? 0.5 : 0.74, y); }
placeHero(); window.addEventListener("resize", placeHero); window.addEventListener("load", placeHero);

async function api(path, opts) {
  if (window.PAGES_API) return window.PAGES_API(path, opts);   // 웹 데모: 서버 없이 브라우저 안에서 분석해요
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// ---------------- top bar ----------------
async function refreshStatus() {
  const s = await api("/api/status");
  curSource = s.source;
  const m = $("#mode");
  m.textContent = s.llm ? "Claude 연결됨" : (s.mode_label || "규칙 모드");
  m.title = s.llm ? "Claude가 분석 순서를 정해요" : (s.mode_label ? s.llm_note : "인터넷 연결 없이도 같은 순서로 분석해요 (" + s.llm_note + ")");
  m.className = "status" + (s.llm ? " llm" : "");
  $("#sources").hidden = Object.keys(s.sources).length < 2;
  $("#sources").innerHTML = Object.keys(s.sources).map((k) =>
    `<button role="radio" aria-checked="${k === s.source}" data-src="${k}" title="${esc(s.sources[k])}">${esc(SOURCE_SHORT[k] || s.sources[k])}</button>`).join("");
  $("#sources").querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
    try { await api("/api/source", { method: "POST", body: JSON.stringify({ source: b.dataset.src }) }); }
    catch (e) { alert(e.message); }
    await refreshStatus();
    ask(true);
  }));
  $("#files-count").textContent = `자료 ${s.files.length}개`;
  $("#files").innerHTML = s.files.length
    ? s.files.map((f) => `<li><span>${esc(f.name)}<small>${esc(f.label || "")}</small></span><span>${f.mb} MB</span></li>`).join("")
    : "<li><span>아직 파일이 없어요</span><span></span></li>";
}
$("#files-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  const pop = $("#files-pop"); pop.hidden = !pop.hidden;
  $("#files-btn").setAttribute("aria-expanded", String(!pop.hidden));
});
document.addEventListener("click", (e) => { if (!$("#files-pop").contains(e.target)) $("#files-pop").hidden = true; });
// 올린 파일을 Agent가 알아보고, 무엇이 달라지는지 말한 뒤 바로 다시 분석해요
async function uploadFiles(list) {
  const files = [...list].filter((f) => /\.csv$/i.test(f.name));
  if (!files.length) { addChat("agent", "CSV 파일만 읽을 수 있어요. 엑셀 파일이면 'CSV UTF-8'로 저장해서 다시 올려 주세요."); return; }
  $("#files-pop").hidden = true;
  toWorkspace();
  addChat("agent", `파일 ${files.length}개를 받았어요. 어떤 자료인지 살펴볼게요.`);
  const lines = [];
  let used = 0;
  for (const f of files) {
    try {
      const r = await api("/api/upload?name=" + encodeURIComponent(f.name), { method: "POST", body: f });
      if (r.ok) { used++; lines.push(`· ${f.name} → ${r.label}. ${r.effect}.`); }
      else lines.push(`· ${f.name} → ${r.effect}.`);
    } catch (e) { lines.push(`· ${f.name}: 올리지 못했어요 (${e.message}).`); }
  }
  await refreshStatus();
  if (!used) { addChat("agent", lines.join("\n")); return; }
  addChat("agent", lines.join("\n") + "\n\n기존 공개 자료와 합쳐서 다시 분석할게요.");
  $("#q").value = lastQ || PREVIEW_Q;
  ask(false);
}
$("#upload").addEventListener("change", async (ev) => { await uploadFiles(ev.target.files); ev.target.value = ""; });
// 화면 어디에든 CSV를 끌어다 놓아도 돼요
let dragDepth = 0;
const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
window.addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth++; document.body.classList.add("dropping"); });
window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) document.body.classList.remove("dropping"); });
window.addEventListener("drop", (e) => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth = 0; document.body.classList.remove("dropping"); uploadFiles(e.dataTransfer.files); });

// ---------------- tabs ----------------
function showTab(name, keepScroll) {
  if (typeof heroMapOpen !== "undefined" && heroMapOpen && name !== "map") closeHeroMap();
  $$("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$(".panel").forEach((p) => (p.hidden = p.dataset.panel !== name));
  if (!keepScroll) window.scrollTo({ top: 0 });
  if (name === "map") { placeMap(); map.redraw(); }
  if (name === "doc") buildDoc();
}
$$("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ---------------- ask ----------------
$$("#chips button").forEach((b) => b.addEventListener("click", () => { $("#q").value = b.dataset.q; ask(false); }));
$("#ask").addEventListener("submit", (e) => { e.preventDefault(); ask(false); });

let pipe = [], pipePre = [], pipeDone = false;
function resetResults() {
  pipe = []; pipePre = []; pipeDone = false; if ($("#pipe")) $("#pipe").hidden = true;
  since = 0; report = null; mapData = null; steps = 0; stepNow = 0; sel = { zone: null, stop: null };
  $("#steps").innerHTML = ""; $("#steps-count").textContent = "";
  $("#sum-eyebrow").textContent = "분석 중";
  $("#sum-title").textContent = "Agent가 자료를 살펴보고 있어요";
  setStep(1, false);
  $("#summary").classList.remove("finished");
  $("#sum-text").textContent = "";
  $("#kpis").innerHTML = ""; $("#tab-n").textContent = ""; $("#dark-stat").hidden = true;
  $("#cards").innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  $("#list-title").textContent = "우선 개선 장소";
  $("#csv").hidden = true; $("#actions").hidden = true;
  $("#paper").innerHTML = '<p class="doc-empty">분석이 끝나면 건의서가 여기에 만들어져요.</p>';
  map.setData(null, []); map.home(0); if (mapHero) { mapHero.setData(null, []); mapHero.home(0); }
  drawInspect();
  showTab(PARAMS.get("tab") || "map", true);
}

async function ask(preview) {
  preview = preview === true;
  const q = preview ? PREVIEW_Q : $("#q").value.trim();
  if (!q) { $("#q").focus(); return; }
  let res;
  try { res = await api("/api/ask", { method: "POST", body: JSON.stringify({ question: q, preview }) }); }
  catch (e) { if (!preview) alert(e.message); return; }
  if (!res.keep) resetResults();
  if (!preview) { $("#q").value = ""; toWorkspace(); }
  $("#go").disabled = true;
  clearInterval(polling);
  polling = setInterval(poll, 450);
}

async function poll() {
  const r = await api("/api/events?since=" + since).catch(() => null);
  if (!r) return;
  for (const e of r.events) { since = e.i + 1; handle(e); }
  $$(".timeline li.running").forEach((li) => li.classList.remove("running"));
  const last = $("#steps").lastElementChild;
  if (r.busy && last && last.classList.contains("tool")) last.classList.add("running");
  if (!r.busy) { clearInterval(polling); $("#go").disabled = false; }
}

// ---------------- events ----------------
const STEP_OF = { inspect_data: 1, check_coverage: 2, run_gap_analysis: 3, look_up_place: 3, finalize_report: 4 };
function setStep(n, finished) {
  if (!n) return;
  if (!finished && n < stepNow && n !== 1) n = stepNow;  // never move backwards mid-run
  stepNow = n;
  $$("#stepper li").forEach((li) => {
    const k = Number(li.dataset.step);
    li.className = finished || k < n ? "done" : k === n ? "active" : "";
  });
}

function handle(e) {
  switch (e.type) {
    case "map": mapData = e.data; map.setData(mapData, report ? report.cards : []); if (mapHero) { mapHero.setData(mapData, report ? report.cards : []); mapHero.home(0); } drawInspect(); drawDarkStat(); return;
    case "report": report = e.data; drawReport(); return;
    case "user": isPreview = !!(e.data && e.data.preview); keepTurn = !!(e.data && e.data.keep); if (isPreview) $("#sum-eyebrow").textContent = "예시 결과를 준비하고 있어요"; else { addChat("user", e.text); if (!keepTurn) lastQ = e.text; } return;
    case "answer": if (keepTurn) { addChat("agent", e.text); return; } pipeDone = true; drawPipeline(); addChat("agent", e.text, isPreview); setStep(4, true); $("#summary").classList.add("finished"); drawAnswer(e.text); addStep("done", "✓", "완료", firstSentence(e.text)); return;
    case "tool": pipe.push({ name: (e.data || {}).name, cap: e.text, res: "", ms: null, judge: [] }); steps += 1; addStep("tool", steps, "계산", e.text); setStep(STEP_OF[(e.data || {}).name] || 0, false); return;
    case "tool_result": if (pipe.length) { pipe[pipe.length - 1].res = e.text; pipe[pipe.length - 1].ms = (e.data || {})._ms; } addStep("result", "·", "", e.text); return;
    case "decision": case "thought": if (e.type === "decision") (pipe.length ? pipe[pipe.length - 1].judge : pipePre).push(e.text); addStep("judge", "!", "Agent 판단", e.text); return;
    case "warning": case "info": addStep("warn", "i", e.type === "info" ? "안내" : "자료 점검", e.text); return;
    case "error": addStep("err", "×", "오류", e.text); drawAnswer(e.text, true); return;
  }
}
function firstSentence(t) { const m = t.match(/^.*?[.!?](\s|$)/); return (m ? m[0] : t).trim(); }
function addStep(cls, icon, label, text) {
  const li = document.createElement("li");
  li.className = cls;
  li.innerHTML = `<span class="ic">${esc(icon)}</span>${label ? `<span class="lab">${esc(label)}</span>` : ""}<span class="txt">${esc(text)}</span>`;
  $("#steps").appendChild(li);
  $("#steps-count").textContent = `계산 ${steps}번`;
}
function drawAnswer(text, isErr) {
  $("#sum-eyebrow").textContent = isErr ? "문제가 생겼어요" : isPreview ? `예시 결과 · ${(report && report.summary && report.summary.area) ? "'" + report.summary.area + "' 주변" : "제주 전체"} · ${curSource === "sample" ? "가상 데이터" : "실제 공개 자료"}` : "Agent의 답변";
  if (!report) $("#sum-title").textContent = isErr ? "분석을 끝내지 못했어요" : "분석을 마쳤어요";
  $("#sum-text").textContent = text.replace(/\*\*/g, "").replace(/^#+\s*/gm, "");
}
function countUp(el) {
  const m = el.textContent.match(/^(\d+(?:\.\d+)?)(.*)$/);
  if (!m || STILL) return;
  const target = parseFloat(m[1]), dec = (m[1].split(".")[1] || "").length, rest = m[2], t0 = performance.now();
  const tick = (t) => {
    const k = Math.min(1, (t - t0) / 700), e = 1 - Math.pow(1 - k, 3);
    el.textContent = (target * e).toFixed(dec) + rest;
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

// ---------------- report ----------------
const fmtM = (m) => (m == null ? "5km+" : m >= 1000 ? (m / 1000).toFixed(1) + "km" : m + "m");
const fmtDemand = (v) => (v === null || v === undefined ? "자료 없음" : typeof v === "string" ? "10명 미만" : (v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(1)) + "명");
function hourlyHTML(h) {
  if (!h || !h.bands) return "";
  const max = Math.max(...h.bands.map((b) => b.share));
  return `<h4>제주 전체 시간대별 버스 승차 (${esc(h.from)}~${esc(h.to)})</h4>
    <ul class="v-bars v-hours">${h.bands.map((b) => `<li class="${/^(19|22)/.test(b.band) ? "night" : ""}"><span>${esc(b.band)}</span><i><b style="width:${(100 * b.share / max).toFixed(0)}%"></b></i><em>${b.share}%</em></li>`).join("")}</ul>
    <p class="v-note">22~24시 승차는 하루 약 ${(h.late_per_day || 0).toLocaleString()}명이에요. 밤에도 꾸준히 버스를 타고 내려요.</p>`;
}
const KIND_KO = { bus_daily_usage: "정류장별 이용(허브 API)", bus_hourly_total: "시간대별 승차(제주 전체)", bus_stops: "정류장", bus_usage: "버스 이용 현황", cctv: "CCTV", bell: "비상벨", streetlight: "보안등", school: "학교" };
function verifyHTML(report) {
  const rob = report.robustness || {}, q = report.quality || [];
  const tone = rob.verdict === "흔들려요" ? "mid" : "good";
  const bars = (rob.variants || []).map((v) => `<li><span>${esc(v.setting)}</span><i><b style="width:${(100 * v.kept / Math.max(1, v.of)).toFixed(0)}%"></b></i><em>${v.kept}/${v.of}</em></li>`).join("");
  const rows = q.map((f) => {
    const notes = [];
    if (f.kind === "bus_usage" || f.kind === "bus_hourly_total") notes.push(`${(f.rows || 0).toLocaleString()}행`);
    else if (f.kind === "bus_daily_usage") { notes.push(`정류장 ${(f.stations || 0).toLocaleString()}곳`); if (f.matched_pct != null) notes.push(`정류장 이름으로 자동 연결 (이용 인원의 ${f.matched_pct}%)`); }
    else {
      notes.push(`${(f.loaded ?? 0).toLocaleString()}개 사용`);
      if (f.outside) notes.push(`제주 밖 좌표 ${f.outside}개 제외`);
      if (f.no_coord) notes.push(`좌표 없음 ${f.no_coord}개 제외`);
      if (f.dupes) notes.push(f.merged ? `같은 위치 중복 ${f.dupes}건 합침` : `같은 위치에 여러 개 ${f.dupes}건`);
      if (f.dropped) notes.push(`위치 오류 ${f.dropped}개 제외`);
      if (f.cross) notes.push(`다른 파일과 겹치는 ${f.cross}곳 합침`);
    }
    return `<tr><th>${esc(KIND_KO[f.kind] || f.kind)}</th><td>${esc(notes.join(" · "))}</td></tr>`;
  }).join("");
  return `<h3>결과 확인</h3>
    ${rob.verdict ? `<div class="v-head v-${tone}"><b>기준을 ${rob.n}가지로 바꿔 다시 계산했어요</b><span>${rob.stable}곳은 어떻게 바꿔도 그대로 10위 안에 있어요. 10곳 중 평균 ${rob.avg_kept}곳이 그대로예요.</span></div>
    <ul class="v-bars">${bars}</ul>` : ""}
    ${hourlyHTML(report.summary && report.summary.hourly)}
    ${rows ? `<h4>사용한 자료</h4><table class="v-q">${rows}</table>` : ""}`;
}
const gapChips = (codes) => (codes || []).map((g) => `<span class="gap ${g}">${esc(GAP_SHORT[g] || g)}</span>`).join("");
function distRow(label, m, limit, color) {
  if (m === undefined) return "";
  const max = Math.max(limit * 4, 400), v = m == null ? max : Math.min(m, max);
  return `<div class="dist"><span>${label}</span>
    <span class="track"><i style="width:${(v / max) * 100}%;background:${color}"></i><span class="lim" style="left:${(limit / max) * 100}%" title="기준 ${limit}m"></span></span>
    <span class="v">${fmtM(m)}</span></div>`;
}
const radii = () => (report && report.summary && report.summary.radii) || (mapData && mapData.radii) || { cctv: 100, bell: 200 };

function drawReport() {
  const sm = report.summary || {}, h = sm.hours || [], cards = report.cards;
  $("#sum-title").textContent = report.headline;
  const gc = sm.gap_counts || {}, top = Object.entries(gc).sort((a, b) => b[1] - a[1])[0], first = cards[0];
  $("#kpis").innerHTML = [
    [sm.stops_scored ?? "-", sm.area ? `'${sm.area}' 주변 정류장` : "분석한 정류장"],
    sm.demand_kind === "daily"
      ? [sm.overview && sm.overview.cats ? `${sm.overview.cats.both.stops.toLocaleString()}곳` : "-", "CCTV·비상벨 모두 닿지 않는 정류장"]
      : [h.length ? `${h[0]}~${h[h.length - 1]}시` : "-", sm.boarding_fallback ? "밤 시간대 (승차 보정)" : "밤 시간대"],
    [top ? `${top[1]}/${cards.length}곳` : "-", top ? `상위 ${cards.length}곳 중 ${GAP_SHORT[top[0]]}` : "공백 유형"],
    sm.facility_only && !sm.demand_kind && sm.hourly && sm.hourly.late_share != null
      ? [`${sm.hourly.late_share}%`, "제주 버스 승차 중 22~24시 비중"]
      : [first ? fmtDemand(first.demand) : "-", sm.demand_kind === "daily" ? "1위 정류장의 하루 이용(승차+환승)" : "1위 정류장의 하루 밤 하차"],
  ].map(([b, s]) => `<div class="kpi"><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join("");
  $("#kpis").querySelectorAll("b").forEach(countUp);
  $("#tab-n").textContent = cards.length;

  $("#list-title").textContent = `우선 개선 ${cards.length}곳`;
  const rr = radii();
  $("#cards").innerHTML = cards.map((c, i) => `
    <div class="row${i === 0 ? " open" : ""}" data-id="${esc(c.id)}">
      <button aria-expanded="${i === 0}">
        <span class="rk">${c.rank}</span>
        <span>
          <div class="rname">${esc(c.name)}</div>
          <div class="rmeta">${esc(c.area || "")}${c.last_hour >= 0 ? ` · 마지막 이용 ${c.last_hour}시대` : ""}</div>
          <div class="gaps">${gapChips(c.gap_codes)}${c.rx ? `<span class="gap rxchip ${RX_TONE[c.rx.code] || ""}">${esc(c.rx.label)}</span>` : ""}${firm(c) ? '<span class="gap firmchip">순위 확실</span>' : ""}</div>
        </span>
        <span class="rdemand"><b>${esc(fmtDemand(c.demand))}</b><span>${c.stay != null ? `기준 바꿔도 ${c.stay}/${(report.robustness || {}).n || "-"}` : sm.demand_kind === "daily" ? "하루 이용" : "밤 하차/일"}</span></span>
      </button>
      <div class="detail"><div class="detail-in">
        <div class="row-fig">${spotlightSVG(c, rr, 200)}<div>${rxHTML(c)}${walkHTML(c)}</div></div>
        ${infoGrid(c)}
        <div class="rec"><div class="rec-head"><span>건의 문장</span><button class="copy" data-copy="${esc(c.recommendation)}">복사</button></div>${esc(c.recommendation)}</div>
        <div class="row-links"><button class="text-btn see-map" data-id="${esc(c.id)}">지도에서 보기</button>${linksHTML(c)}</div>
      </div></div>
    </div>`).join("");
  $("#cards").querySelectorAll(".row > button").forEach((b) => b.addEventListener("click", () => {
    const row = b.parentElement, open = !row.classList.contains("open");
    row.classList.toggle("open", open); b.setAttribute("aria-expanded", String(open));
  }));
  bindCopy($("#cards"));
  $("#cards").querySelectorAll(".see-map").forEach((b) => b.addEventListener("click", () => { showTab("map"); selectStop(b.dataset.id); }));
  drawAbout(); drawHeroCards(); drawPick(); drawPlan(); G.picks = {}; G.costs = null;
  if (PARAMS.get("game") === "agent" && gameData()) { G.costs = { ...gameData().costs }; G.picks = gAuto(G.budget, "agent"); }
  drawGame();
  $("#verify").hidden = !(report.robustness && report.robustness.n);
  $("#verify").innerHTML = verifyHTML(report);
  $("#csv").hidden = false; $("#actions").hidden = false;

  map.setCards(cards); if (mapHero) mapHero.setCards(cards);
  if (sm.area) map.fit(cards, STILL ? 0 : 1400);
  drawCharts(); drawInspect(); drawDarkStat();
  if (PARAMS.get("tab") === "doc") buildDoc();

  const lk = (PARAMS.get("look") || "").split(",").map(Number);   // ?look=lat,lon,distance for screenshots
  if (lk.length >= 2 && lk.every(Number.isFinite)) map.lookAt(lk[0], lk[1], lk[2] || 7000, 0);
  const f = PARAMS.get("focus");
  if (f === "zone" && mapData && mapData.zones.length) selectZone(mapData.zones[0].key);
  else if (f && cards.length) selectStop(cards[0].id);
}
function bindCopy(root) {
  root.querySelectorAll(".copy").forEach((b) => b.addEventListener("click", (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(b.dataset.copy).then(() => { b.textContent = "복사됨"; setTimeout(() => (b.textContent = "복사"), 1500); });
  }));
}

// ---------------- map inspector (zone and stop details) ----------------
const zoneOf = (key) => (mapData ? mapData.zones.find((z) => z.key === key) : null);
const stopOf = (id) => (mapData ? mapData.stops.find((s) => s.id === id) : null);
const cardOf = (id) => (report ? report.cards.find((c) => c.id === id) : null);

function selectZone(key) {
  const z = zoneOf(key);
  if (!z) return;
  sel = { zone: key, stop: null };
  map.focusZone(z);
  drawInspect();
}
function selectStop(id) {
  const s = stopOf(id);
  if (!s) return;
  sel = { zone: s.z, stop: id };
  map.focusStop(s);
  drawInspect();
}
// Satellite brightness bands (NASA Black Marble 2016, rescaled 0..1 within the Jeju crop).
const SAT_DARK = 0.15, SAT_BRIGHT = 0.5;
const satLabel = (v) => (v == null ? "-" : v < SAT_DARK ? "어두움" : v < SAT_BRIGHT ? "보통" : "밝음");

// "N of M stops where people get off at night have neither CCTV nor a bell in range."
function drawDarkStat() {
  const el = $("#dark-stat");
  if (!mapData) { el.hidden = true; return; }
  const people = mapData.stops.filter((s) => s.demand !== 0);
  const blind = people.filter((s) => s.gaps.includes("eyes") && s.gaps.includes("bell"));
  $("#ds-n").textContent = `${blind.length.toLocaleString()}곳`;
  $("#ds-total").textContent = people.length.toLocaleString();
  const smm = (report && report.summary) || {};
  $("#ds-scope").textContent = smm.facility_only || smm.demand_kind === "daily" ? "사람이 타는 제주의 실제 버스 정류장" : "밤에 사람이 내리는 정류장";
  const sat = blind.map((s) => map.nightAt(s.lat, s.lon));
  const known = sat.filter((v) => v != null);
  const darkSat = known.filter((v) => v < SAT_DARK).length;
  $("#ds-sat").hidden = !known.length;
  $("#ds-sat-n").textContent = `${darkSat}곳`;
  el.hidden = !people.length;
}

// Keep the island clear of whatever panel is covering the map.
function placeMap() {
  const narrow = matchMedia("(max-width: 900px)").matches;
  if (narrow) map.setCenter(0.5, 0.52);
  else if (heroMapOpen && document.body.classList.contains("inspecting")) map.setCenter(0.36, 0.52);
  else if (document.body.classList.contains("inspecting")) map.setCenter($("#workspace") && $("#workspace").classList.contains("ws-big") ? 0.4 : 0.34, 0.52);
  else map.setCenter(0.5, 0.5);
}
window.addEventListener("resize", placeMap);
function goHome() { sel = { zone: null, stop: null }; map.home(); drawInspect(); }

function drawInspect() {
  const el = $("#inspect"), z = sel.zone ? zoneOf(sel.zone) : null, s = sel.stop ? stopOf(sel.stop) : null;
  const crumbs = [`<button data-go="home">제주 전체</button>`];
  if (z) crumbs.push(`<button data-go="zone">${esc(z.label)} 구역</button>`);
  if (s) crumbs.push(`<span>${esc(s.name)}</span>`);
  $("#crumbs").innerHTML = crumbs.join('<i aria-hidden="true">›</i>');
  $("#crumbs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => (b.dataset.go === "home" ? goHome() : selectZone(sel.zone))));
  const open = !!(mapData && (z || s));
  document.body.classList.toggle("inspecting", open);
  $("#crumbs").hidden = !open; el.hidden = !open; $("#map-hint").hidden = open || !mapData;
  placeMap();
  if (!open) { el.innerHTML = ""; return; }
  const rr = radii();
  if (s) {
    const c = cardOf(s.id);
    const purposes = nearbyPurposes(s);
    el.innerHTML = `
      <p class="eyebrow">${c ? `우선 개선 ${c.rank}위` : "정류장"}</p>
      <h3>${esc(s.name)}</h3>
      <p class="i-sub">${z ? esc(z.label) + " 구역 · " : ""}전체 ${mapData.stops.length}곳 중 ${s.rank}위${s.last >= 0 ? ` · 마지막 이용 ${s.last}시대` : ""}</p>
      <div class="i-big"><b>${esc(fmtDemand(s.demand))}</b><span>${(report && report.summary && report.summary.demand_kind) === "daily" ? "하루 이용" : "하루 평균 밤 하차"}</span></div>
      <div class="gaps">${gapChips(s.gaps) || '<span class="gap ok">확인된 공백 없음</span>'}</div>
      <div class="i-block">
        ${distRow("CCTV", s.cm, rr.cctv, "var(--cctv)")}
        ${distRow("비상벨", s.bm, rr.bell, "var(--bell)")}
        <p class="i-note">세로선은 기준 거리예요 (CCTV ${rr.cctv}m, 비상벨 ${rr.bell}m). 지도의 점선 원과 같아요.</p>
      </div>
      <div class="i-grid">
        <div><b>${s.cn ?? "-"}</b><span>${rr.cctv}m 안 CCTV</span></div>
        <div><b>${s.bn ?? "-"}</b><span>${rr.bell}m 안 비상벨</span></div>
        <div><b class="sat-${satLabel(map.nightAt(s.lat, s.lon))}">${satLabel(map.nightAt(s.lat, s.lon))}</b><span>위성으로 본 밤 밝기</span></div>
      </div>
      <p class="i-note">밤 밝기는 NASA 위성 사진으로 봤어요.</p>
      ${purposes ? `<p class="i-note">주변 500m CCTV 목적: ${esc(purposes)}</p>` : ""}
      ${c ? `<div class="rec"><div class="rec-head"><span>건의 문장</span><button class="copy" data-copy="${esc(c.recommendation)}">복사</button></div>${esc(c.recommendation)}</div>` : ""}`;
    bindCopy(el);
    return;
  }
  if (z) {
    const stops = mapData.stops.filter((x) => x.z === z.key).sort((a, b) => a.rank - b.rank);
    el.innerHTML = `
      <p class="eyebrow">구역</p>
      <h3>${esc(z.label)}</h3>
      <p class="i-sub">약 2km 구역 · 정류장 ${z.stops}곳</p>
      <div class="i-big"><b>${z.gap_stops}<small>/${z.stops}곳</small></b><span>공백이 확인된 정류장</span></div>
      <div class="i-grid">
        <div><b>${esc(fmtDemand(z.demand))}</b><span>${(report && report.summary && report.summary.demand_kind) === "daily" ? "하루 이용 합계" : "밤 하차 합계 (하루)"}</span></div>
        <div><b>${z.cctv}</b><span>구역 안 CCTV</span></div>
        <div><b>${z.bell}</b><span>구역 안 비상벨</span></div>
        <div><b>${z.eyes_gap}</b><span>CCTV 없는 정류장</span></div>
        <div><b>${z.bell_gap}</b><span>비상벨 없는 정류장</span></div>
        ${(report && report.summary && report.summary.demand_kind) === "daily" ? `<div><b>${Math.round(100 * z.gap_stops / Math.max(1, z.stops))}%</b><span>공백 정류장 비율</span></div>` : `<div><b>${z.late_gap}</b><span>막차 이른 정류장</span></div>`}
      </div>
      <p class="i-note">위성으로 본 이 구역의 밤 밝기: <b class="sat-${satLabel(map.nightAt(z.lat, z.lon))}">${satLabel(map.nightAt(z.lat, z.lon))}</b> (NASA Black Marble)</p>
      <h4>이 구역의 정류장</h4>
      <div class="i-list">${stops.slice(0, 12).map((x) => stopLine(x)).join("")}</div>
      ${stops.length > 12 ? `<p class="i-note">외 ${stops.length - 12}곳은 지도에서 눌러 볼 수 있어요.</p>` : ""}`;
    bindLines(el);
    return;
  }
}
function stopLine(x) {
  const c = cardOf(x.id);
  return `<button class="i-line" data-stop="${esc(x.id)}"><span class="i-name">${c ? `<em>${c.rank}</em>` : ""}${esc(x.name)}</span>
    <span class="i-meta">${x.gaps.length ? esc(x.gaps.map((g) => GAP_SHORT[g]).join(" · ")) : "공백 없음"}</span><span class="i-val">${esc(fmtDemand(x.demand))}</span></button>`;
}
function bindLines(el) {
  el.querySelectorAll("[data-zone]").forEach((b) => b.addEventListener("click", () => selectZone(b.dataset.zone)));
  el.querySelectorAll("[data-stop]").forEach((b) => b.addEventListener("click", () => selectStop(b.dataset.stop)));
}
function nearbyPurposes(s) {
  const k = Math.cos((33.4 * Math.PI) / 180), cnt = {};
  for (const c of mapData.cctv) {
    const d = Math.hypot((c[0] - s.lat) * 110540, (c[1] - s.lon) * 111320 * k);
    if (d <= 500) cnt[c[2] || "미상"] = (cnt[c[2] || "미상"] || 0) + 1;
  }
  return Object.entries(cnt).sort((a, b) => b[1] - a[1]).map(([p, n]) => `${p} ${n}`).join(", ");
}

$("#m-in").addEventListener("click", () => map.zoom(0.6));
$("#m-out").addEventListener("click", () => map.zoom(1.6));
$("#m-left").addEventListener("click", () => map.rotate(-0.5));
$("#m-right").addEventListener("click", () => map.rotate(0.5));
let tiltUp = false;
$("#m-tilt").addEventListener("click", () => { tiltUp = !tiltUp; map.tilt(tiltUp ? 0.35 : -0.35); });
$("#m-home").addEventListener("click", goHome);
$("#png").addEventListener("click", () => {
  const a = document.createElement("a"); a.download = "nightgap_map.png"; a.href = map.snapshot(); a.click();
});

// ---------------- charts ----------------
function median(a) { const b = a.filter((x) => x != null).sort((x, y) => x - y); return b.length ? b[Math.floor(b.length / 2)] : null; }
function drawCharts() {
  const sm = report.summary || {}, ov = sm.overview, daily = sm.demand_kind === "daily";
  $("#list-sub").textContent = `${daily ? "이용이 많고" : sm.facility_only ? "주변 정류장이 많고" : "밤에 내리는 사람이 많고"} CCTV·비상벨이 없는 순서예요. 정류장을 누르면 자세한 내용이 나와요.`;
  const both = ov && ov.cats.both, pl = sm.placement && sm.placement.cctv, sc = sm.school, hr = sm.hourly;
  const k = [
    [(sm.stops_scored || 0).toLocaleString() + "곳", "분석한 정류장"],
    both ? [both.stop_pct + "%", "CCTV·비상벨 모두 닿지 않는 정류장"] : null,
    both && both.use_pct != null ? [both.use_pct + "%", "하루 버스 이용 중 이 정류장들에서 타는 비율"] : null,
    sc ? [sc.blind_near.toLocaleString() + "곳", "학교 500m 안 공백 정류장"] : null,
  ].filter(Boolean);
  $("#ins-kpis").innerHTML = k.map(([b, t]) => `<div class="ins-kpi"><b>${esc(b)}</b><span>${esc(t)}</span></div>`).join("");
  const out = [];
  if (ov) {
    const frac = both.stop_pct >= 70 ? "4곳 중 3곳" : both.stop_pct >= 60 ? "5곳 중 3곳" : both.stop_pct >= 45 ? "2곳 중 1곳" : `${Math.round(both.stop_pct)}%`;
    out.push(`<article class="ins-card" data-go="map"><p class="ins-n">1</p><h3>제주 정류장 ${frac}은 CCTV도 비상벨도 닿지 않아요</h3><div id="chart-mix"></div></article>`);
    const g = ov.by_group, top = g.slice(0, 4), low = g.length > 6 ? g.slice(-2) : [];
    out.push(`<article class="ins-card" data-go="map"><p class="ins-n">2</p><h3>시내보다 읍·면이 더 비어 있어요</h3>
      <div class="bars">${top.concat(low).map((x, i) => `${i === top.length ? '<div class="ins-gap">⋯</div>' : ""}<div class="bar-row g"><span class="b-name">${esc(x.group)}</span><span class="b-track"><i class="eyes" style="width:${x.pct}%"></i></span><span class="b-val">${Math.round(x.pct)}%</span></div>`).join("")}</div>
      <p class="ins-foot">${esc(top[0].group)}은 정류장 ${top[0].stops}곳 중 ${top[0].blind}곳에 CCTV도 비상벨도 없어요.</p></article>`);
  }
  if (hr && hr.bands) {
    const max = Math.max(...hr.bands.map((b) => b.share));
    out.push(`<article class="ins-card"><p class="ins-n">3</p><h3>밤 10시 이후에도 하루 약 ${(hr.late_per_day || 0).toLocaleString()}명이 버스를 타요</h3>
      <div class="ins-cols">${hr.bands.map((b) => `<div class="${/^(19|22)/.test(b.band) ? "night" : ""}"><i style="height:${(100 * b.share / max).toFixed(0)}%"></i><em>${b.share}%</em><span>${esc(b.band)}</span></div>`).join("")}</div>
      <p class="ins-foot">주황 막대가 밤 시간이에요. 내린 뒤에는 대부분 걸어서 집에 가요.</p></article>`);
  }
  if (pl && pl.picks.length >= 10) {
    const p10 = pl.picks[9];
    out.push(`<article class="ins-card" data-go="plan"><p class="ins-n">4</p><h3>CCTV 10대를 잘 고른 곳에 달면 하루 ${Math.round(p10.cum).toLocaleString()}명에게 새로 CCTV가 닿아요</h3>
      <div class="ins-curve">${curveSVG(pl.picks, 10)}</div>
      <p class="ins-foot">외진 곳부터 다는 것보다 ${Math.round(pl.bench.greedy / Math.max(1, pl.bench.farthest_first))}배 많은 사람이에요. 눌러서 직접 골라 보세요.</p></article>`);
  }
  $("#ins-grid").innerHTML = out.join("");
  $$("#ins-grid [data-go]").forEach((el) => el.addEventListener("click", () => showTab(el.dataset.go)));
  drawOverview(ov, !sm.facility_only || daily);
}

// Gap mix (stops vs usage) and blind share per 읍/면. Aggregates only, computed by the agent.
const MIX = [["both", "둘 다 없음"], ["no_cctv", "CCTV만 없음"], ["no_bell", "비상벨만 없음"], ["covered", "둘 다 있음"]];
function drawOverview(ov, withUse) {
  if (!ov || !$("#chart-mix")) return;
  const bar = (key, label) => `<div class="mix-row"><span class="mix-l">${label}</span><span class="mix-bar">${MIX.map(([k]) => {
    const v = ov.cats[k][key] || 0;
    return v > 0 ? `<i class="mix-${k}" style="width:${v}%" title="${esc(MIX.find((m) => m[0] === k)[1])} ${v}%">${v >= 9 ? Math.round(v) + "%" : ""}</i>` : "";
  }).join("")}</span></div>`;
  $("#chart-mix").innerHTML = bar("stop_pct", "정류장 수") + (withUse && ov.cats.both.use_pct != null ? bar("use_pct", "이용 인원") : "") +
    `<div class="mix-legend">${MIX.map(([k, l]) => `<span><i class="mix-${k}"></i>${l}</span>`).join("")}</div>` +
    (withUse && ov.cats.both.use_pct != null ? `<p class="i-note">정류장 ${ov.cats.both.stop_pct}%에 CCTV도 비상벨도 없어요. 하루 버스 이용의 ${ov.cats.both.use_pct}%가 이런 정류장에서 일어나요.</p>` : "");
  if ($("#chart-groups")) $("#chart-groups").innerHTML = ov.by_group.map((g) => `<div class="bar-row g"><span class="b-name">${esc(g.group)}</span>
      <span class="b-track"><i class="eyes" style="width:${g.pct}%"></i></span><span class="b-val">${Math.round(g.pct)}% · ${g.blind}/${g.stops}</span></div>`).join("");
}

// ---------------- petition document ----------------
function buildDoc() {
  if (!report) return;
  const sm = report.summary || {}, h = sm.hours || [], cards = report.cards, rr = radii();
  const d = new Date(), today = `${d.getFullYear()}. ${d.getMonth() + 1}. ${d.getDate()}.`;
  const scope = sm.area ? `'${sm.area}' 주변 정류장 ${sm.stops_scored}곳` : `제주 전체 정류장 ${sm.stops_scored}곳`;
  const demo = curSource === "sample";
  // Take the map picture from an off-screen-sized view fitted to the priority stops.
  const keep = Object.assign({}, sel);
  const wasHidden = $('[data-panel="map"]').hidden;
  $('[data-panel="map"]').hidden = false;
  map.fit(cards, 0);
  const img = map.snapshot();
  if (keep.stop) map.focusStop(stopOf(keep.stop), 0); else if (keep.zone) map.focusZone(zoneOf(keep.zone), 0); else if (!sm.area) map.home(0);
  $('[data-panel="map"]').hidden = wasHidden;
  $("#paper").innerHTML = docHTML({ sm, h, cards, rr, today, scope, demo, img });
}

// ---------------- print-friendly charts for the proposal (white paper) ----------------
const DC = { org: "#e8892b", orgL: "#f6c48f", blue: "#2f6fd6", blueL: "#9dbcf0", green: "#2fa36b", grey: "#b8bfcc", ink: "#1d1d1f", sub: "#6e6e73", line: "#e5e5ea" };
const dTxt = (x, y, t, o = {}) => `<text x="${x}" y="${y}" font-size="${o.fs || 11}" fill="${o.c || DC.sub}" text-anchor="${o.a || "start"}" font-weight="${o.w || 400}">${esc(t)}</text>`;
function docFig(title, svg, note) {
  return `<figure class="doc-fig"><figcaption>${esc(title)}</figcaption>${svg}${note ? `<p class="doc-cap">${note}</p>` : ""}</figure>`;
}
// horizontal bars: rows [{label, v, color?, tag?}]
function dBarsH(rows, o = {}) {
  const W = 680, L = o.lw || 180, R = rows.some((r) => r.tag) ? (o.tw || 150) : 70, rowH = o.rh || 22, T = 6, H = T + rows.length * rowH + 8;
  const max = o.max || Math.max(1, ...rows.map((r) => r.v));
  const sx = (v) => ((W - L - R) * v) / max;
  const body = rows.map((r, i) => {
    const y = T + i * rowH;
    return `${dTxt(L - 8, y + rowH * 0.66, r.label, { a: "end", c: DC.ink })}
      <rect x="${L}" y="${y + 4}" width="${W - L - R}" height="${rowH - 9}" rx="3" fill="#f2f2f5"/>
      <rect x="${L}" y="${y + 4}" width="${Math.max(2, sx(r.v)).toFixed(1)}" height="${rowH - 9}" rx="3" fill="${r.color || DC.org}"/>
      ${dTxt(L + Math.max(2, sx(r.v)) + 6, y + rowH * 0.66, o.fmt ? o.fmt(r.v, r) : r.v, { c: DC.ink, w: 600 })}
      ${r.tag ? dTxt(W - 2, y + rowH * 0.66, r.tag, { a: "end" }) : ""}`;
  }).join("");
  const ref = o.ref != null ? `<line x1="${L + sx(o.ref)}" x2="${L + sx(o.ref)}" y1="${T}" y2="${H - 6}" stroke="${DC.ink}" stroke-dasharray="3 3"/>${dTxt(L + sx(o.ref) + 4, H - 1, o.refLabel || "", { fs: 10 })}` : "";
  return `<svg class="doc-svg" viewBox="0 0 ${W} ${H + (o.ref != null ? 6 : 0)}">${body}${ref}</svg>`;
}
// 100% stacked rows: rows [{label, parts:[{v,color,name}]}]
function dStack(rows, legend) {
  const W = 680, L = 90, rowH = 34, H = rows.length * rowH + 30;
  const body = rows.map((r, i) => {
    const y = i * rowH + 4; let x = L;
    return dTxt(L - 10, y + 19, r.label, { a: "end", c: DC.ink, w: 600 }) + r.parts.map((p) => {
      const w = ((W - L - 4) * p.v) / 100, seg = `<rect x="${x.toFixed(1)}" y="${y}" width="${Math.max(0, w - 2).toFixed(1)}" height="24" rx="3" fill="${p.color}"/>${w > 38 ? dTxt(x + w / 2, y + 16.5, Math.round(p.v) + "%", { a: "middle", c: p.dark ? "#fff" : DC.ink, w: 700 }) : ""}`;
      x += w; return seg;
    }).join("");
  }).join("");
  const lg = legend.map(([n, c], i) => `<rect x="${L + i * 130}" y="${H - 18}" width="10" height="10" rx="2" fill="${c}"/>${dTxt(L + i * 130 + 15, H - 9, n, { c: DC.ink })}`).join("");
  return `<svg class="doc-svg" viewBox="0 0 ${W} ${H}">${body}${lg}</svg>`;
}
// distance dot plot: nearest CCTV and bell per stop against the threshold
function dDots(cards, rr) {
  const W = 680, L = 190, R = 20, rowH = 22, T = 18, H = T + cards.length * rowH + 22, MAX = 2000;
  const sx = (m) => L + ((W - L - R) * Math.min(m == null ? MAX : m, MAX)) / MAX;
  const ticks = [0, 500, 1000, 1500, 2000].map((m) => `<line x1="${sx(m)}" x2="${sx(m)}" y1="${T - 4}" y2="${H - 18}" stroke="${DC.line}"/>${dTxt(sx(m), H - 5, m === 2000 ? "2km+" : m ? fmtM(m) : "0", { a: "middle", fs: 10 })}`).join("");
  const zone = `<rect x="${sx(0)}" y="${T - 4}" width="${sx(rr.cctv) - sx(0)}" height="${H - T - 14}" fill="${DC.blue}" opacity=".10"/><rect x="${sx(rr.cctv)}" y="${T - 4}" width="${sx(rr.bell) - sx(rr.cctv)}" height="${H - T - 14}" fill="${DC.green}" opacity=".07"/>`;
  const rows = cards.map((c, i) => {
    const y = T + i * rowH + 8;
    const a = sx(c.cctv_m), b = sx(c.bell_m);
    return `${dTxt(L - 8, y + 4, `${c.rank}. ${c.name.length > 13 ? c.name.slice(0, 12) + "…" : c.name}`, { a: "end", c: DC.ink })}
      <line x1="${sx(0)}" x2="${Math.max(a, b)}" y1="${y}" y2="${y}" stroke="#d2d2d7"/>
      <circle cx="${b}" cy="${y}" r="5" fill="${DC.green}"/><circle cx="${a}" cy="${y}" r="5" fill="${DC.blue}"/>`;
  }).join("");
  const lg = `${dTxt(L, 10, "● 가장 가까운 CCTV", { c: DC.blue, w: 600 })}${dTxt(L + 140, 10, "● 가장 가까운 비상벨", { c: DC.green, w: 600 })}${dTxt(W - R, 10, `옅은 칸: CCTV ${rr.cctv}m · 비상벨 ${rr.bell}m 기준`, { a: "end", fs: 10 })}`;
  return `<svg class="doc-svg" viewBox="0 0 ${W} ${H}">${zone}${ticks}${rows}${lg}</svg>`;
}
// budget curve: Agent plan vs busiest-first across budgets
function dBudgetCurve() {
  const g = gameData(); if (!g || !g.candidates.length) return "";
  const keep = G.picks, B = [1000, 2000, 4000, 6000, 8000, 10000, 15000, 20000, 30000];
  const pts = B.map((b) => ({ b, ag: gEval(gAuto(b, "agent")).score, bz: gEval(gAuto(b, "busy")).score }));
  G.picks = keep;
  const W = 680, H = 230, L = 56, R = 16, T = 14, Bm = 30, max = Math.max(...pts.map((p) => p.ag)) * 1.08;
  const sx = (b) => L + ((W - L - R) * Math.log(b / 1000)) / Math.log(30), sy = (v) => T + (H - T - Bm) * (1 - v / max);
  const path = (k) => pts.map((p, i) => `${i ? "L" : "M"}${sx(p.b).toFixed(1)},${sy(p[k]).toFixed(1)}`).join("");
  const grid = [0, 0.5, 1].map((f) => `<line x1="${L}" x2="${W - R}" y1="${sy(max * f)}" y2="${sy(max * f)}" stroke="${DC.line}"/>${dTxt(L - 6, sy(max * f) + 4, Math.round(max * f).toLocaleString(), { a: "end", fs: 10 })}`).join("");
  const xt = B.filter((b) => [1000, 4000, 10000, 30000].includes(b)).map((b) => dTxt(sx(b), H - 10, won(b), { a: "middle", fs: 10 })).join("");
  const cur = pts.find((p) => p.b === G.budget);
  const mark = cur ? `<line x1="${sx(G.budget)}" x2="${sx(G.budget)}" y1="${T}" y2="${H - Bm}" stroke="${DC.ink}" stroke-dasharray="3 3"/>${dTxt(sx(G.budget) + 5, T + 10, `이번 예산 ${won(G.budget)}`, { fs: 10, c: DC.ink })}` : "";
  const dots = (k, c) => pts.map((p) => `<circle cx="${sx(p.b)}" cy="${sy(p[k])}" r="3.5" fill="${c}"/>`).join("");
  return `<svg class="doc-svg" viewBox="0 0 ${W} ${H}">${grid}${xt}${mark}
    <path d="${path("bz")}" fill="none" stroke="${DC.grey}" stroke-width="2.5"/>${dots("bz", DC.grey)}
    <path d="${path("ag")}" fill="none" stroke="${DC.org}" stroke-width="2.5"/>${dots("ag", DC.org)}
    ${dTxt(L + 8, T + 12, "━ Agent 추천", { c: DC.org, w: 700 })}${dTxt(L + 100, T + 12, "━ 이용 많은 곳부터 새로 달기", { c: "#8e8e93", w: 700 })}</svg>`;
}
// small column chart for hourly share
function dCols(bands) {
  const W = 680, H = 170, L = 10, Bm = 34, T = 18, n = bands.length, gw = (W - L * 2) / n, max = Math.max(...bands.map((b) => b.share));
  return `<svg class="doc-svg" viewBox="0 0 ${W} ${H}">${bands.map((b, i) => {
    const h = ((H - T - Bm) * b.share) / max, x = L + i * gw + 8, night = /^(19|22)/.test(b.band);
    return `<rect x="${x}" y="${H - Bm - h}" width="${gw - 16}" height="${h}" rx="3" fill="${night ? DC.org : DC.grey}"/>${dTxt(x + (gw - 16) / 2, H - Bm - h - 5, b.share + "%", { a: "middle", c: DC.ink, w: 600 })}${dTxt(x + (gw - 16) / 2, H - 14, b.band, { a: "middle", fs: 10 })}`;
  }).join("")}</svg>`;
}

function docHTML({ sm, h, cards, rr, today, scope, demo, img }) {
  const daily = sm.demand_kind === "daily", ov = sm.overview, sc = sm.school, g = gameData();
  const kinds = new Set((report.quality || []).map((f) => f.kind));
  const SEC = []; let n = 0; const H = (t) => `<h2>${++n}. ${t}</h2>`;
  // plan: the user's game plan if any, else the Agent plan at the current budget
  let plan = null;
  if (g && g.candidates.length) {
    if (!G.costs) G.costs = { ...g.costs };
    const mine = Object.keys(G.picks).length > 0, picks = mine ? G.picks : gAuto(G.budget, "agent");
    const ev = gEval(picks), busy = gEval(gAuto(G.budget, "busy"));
    const rows = g.candidates.filter((c) => picks[c.id]).map((c) => {
      const p = picks[c.id], acts = [], cost = (p.e ? G.costs[p.e] : 0) + (p.b ? G.costs.bell : 0);
      if (p.e) acts.push(ACT[p.e].long); if (p.b) acts.push(ACT.bell.long);
      const ids = new Set([...(p.e ? c.ce : []), ...(p.b ? c.cb : [])]);
      return { c, acts, cost, users: Math.round([...ids].reduce((a, i) => a + (g.demand[i] || 0), 0)) };
    }).sort((x, y) => y.users / y.cost - x.users / x.cost);
    plan = { mine, ev, busy, rows };
  }
  const first = cards[0], both = ov && ov.cats.both;
  // 1. summary
  const kp = [[(sm.stops_scored || 0).toLocaleString() + "곳", "분석한 정류장"], both ? [both.stop_pct + "%", "CCTV·비상벨 모두 없음"] : null,
    sc ? [sc.blind_near.toLocaleString() + "곳", "학교 500m 안 공백"] : null, plan && plan.rows.length ? [Math.round(plan.ev.eu + plan.ev.bu).toLocaleString() + "명", `예산 ${won(G.budget)}의 하루 효과`] : null].filter(Boolean);
  SEC.push(`${H("건의 요지")}
    <div class="doc-kpis">${kp.map(([b, t]) => `<div><b>${esc(b)}</b><span>${esc(t)}</span></div>`).join("")}</div>
    <p class="doc-lead">${esc(report.headline)}</p>
    <p>${daily ? `제주 버스 정류장 ${(sm.stops_scored || 0).toLocaleString()}곳의 하루 이용 인원(승차+환승)과 주변 CCTV·안전비상벨 위치를 함께 분석했습니다.`
      : sm.facility_only ? `제주 버스 정류장 ${(sm.stops_scored || 0).toLocaleString()}곳 주변의 CCTV·안전비상벨 위치를 분석했습니다.`
      : `밤 ${h[0]}~${h[h.length - 1]}시 버스 하차 인원과 주변 CCTV·안전비상벨 위치를 함께 분석했습니다.`}
      이용이 많은데 반경 ${rr.cctv}m 안에 CCTV가, ${rr.bell}m 안에 안전비상벨이 없는 정류장 ${cards.length}곳을 우선 개선 대상으로 건의합니다.</p>
    <ul class="doc-keys">
      ${both ? `<li>분석한 정류장의 <b>${both.stop_pct}%</b>(${both.stops.toLocaleString()}곳)는 CCTV와 비상벨이 모두 닿지 않으며${both.use_pct != null ? `, 이 정류장들이 하루 버스 이용의 <b>${both.use_pct}%</b>를 차지합니다` : ""}.</li>` : ""}
      ${first ? `<li>1순위 <b>${esc(first.name)}</b>(${esc(first.area || "")})은 ${first.rx ? esc(first.rx.label) : "시설 보강"}가 필요하며, 가장 가까운 CCTV까지 걸어서 약 ${first.cctv_m != null ? walkMin(first.cctv_m) : "-"}분 거리입니다.</li>` : ""}
      ${sc ? `<li>초·중·고 반경 500m 안 정류장 중 <b>${sc.blind_near.toLocaleString()}곳</b>이 공백이며, 하루 ${sc.blind_near_use.toLocaleString()}명이 이용합니다.</li>` : ""}
      ${plan && plan.rows.length ? `<li>예산 <b>${won(G.budget)}</b>으로 ${plan.rows.length}곳을 보강하면 하루 <b>${Math.round(plan.ev.eu).toLocaleString()}명</b>이 CCTV 범위에, <b>${Math.round(plan.ev.bu).toLocaleString()}명</b>이 비상벨 범위에 새로 들어옵니다.</li>` : ""}
    </ul>`);
  // 2. status
  if (ov) SEC.push(`${H("현황")}
    <table class="doc-table doc-small"><thead><tr><th>구분</th><th>정류장</th><th>비율</th>${both.use_pct != null ? "<th>하루 이용 비중</th>" : ""}</tr></thead><tbody>
    ${[["both", "CCTV·비상벨 모두 없음"], ["no_cctv", "CCTV만 없음"], ["no_bell", "비상벨만 없음"], ["covered", "둘 다 있음"]].map(([k, l]) => `<tr><td class="l">${l}</td><td>${ov.cats[k].stops.toLocaleString()}곳</td><td>${ov.cats[k].stop_pct}%</td>${both.use_pct != null ? `<td>${ov.cats[k].use_pct}%</td>` : ""}</tr>`).join("")}</tbody></table>
    ${docFig("그림 1. 공백 구성: 정류장 수와 하루 이용 인원", dStack([
      { label: "정류장 수", parts: [["both", DC.org, 1], ["no_cctv", DC.orgL], ["no_bell", DC.blueL], ["covered", DC.blue, 1]].map(([k, c, d]) => ({ v: ov.cats[k].stop_pct, color: c, dark: d })) },
      ...(both.use_pct != null ? [{ label: "이용 인원", parts: [["both", DC.org, 1], ["no_cctv", DC.orgL], ["no_bell", DC.blueL], ["covered", DC.blue, 1]].map(([k, c, d]) => ({ v: ov.cats[k].use_pct, color: c, dark: d })) }] : [])],
      [["둘 다 없음", DC.org], ["CCTV만 없음", DC.orgL], ["비상벨만 없음", DC.blueL], ["둘 다 있음", DC.blue]]),
      both.use_pct != null ? `정류장 수로는 ${both.stop_pct}%, 하루 이용 인원으로는 ${both.use_pct}%가 CCTV·비상벨이 모두 닿지 않는 정류장에서 일어납니다.` : "")}
    ${docFig("그림 2. 지역별 CCTV·비상벨이 모두 없는 정류장 비율", dBarsH(ov.by_group.map((x) => ({ label: x.group, v: x.pct, color: /동지역/.test(x.group) ? DC.blue : DC.org, tag: `${x.blind}/${x.stops}곳` })), { max: 100, fmt: (v) => Math.round(v) + "%", lw: 130 }),
      "주황은 읍·면, 파랑은 시 동지역입니다. 읍·면일수록 정류장 대부분이 공백입니다.")}
    ${sm.hourly && sm.hourly.bands ? docFig(`그림 3. 제주 전체 시간대별 버스 승차 비중 (${esc(sm.hourly.from)}~${esc(sm.hourly.to)})`, dCols(sm.hourly.bands),
      `주황은 밤 시간대입니다. 22~24시에도 하루 약 ${(sm.hourly.late_per_day || 0).toLocaleString()}명이 버스를 탑니다.`) : ""}`);
  // 3. table
  SEC.push(`${H("우선 개선 대상")}
    <table class="doc-table"><thead><tr><th>순위</th><th>정류장</th><th>지역</th><th>고치는 방법</th><th>${daily ? "하루 이용" : "밤 하차<br>(하루 평균)"}</th><th>가장 가까운<br>CCTV</th><th>가장 가까운<br>비상벨</th><th>기준 바꿔도</th></tr></thead>
    <tbody>${cards.map((c) => `<tr><td>${c.rank}</td><td>${esc(c.name)}</td><td class="l">${esc(c.area || "")}</td><td class="l">${esc(c.rx ? c.rx.label : "")}</td>
      <td>${esc(fmtDemand(c.demand))}</td><td>${fmtM(c.cctv_m)}</td><td>${fmtM(c.bell_m)}</td><td>${c.stay != null ? `${c.stay}/${c.stay_of}` : "-"}</td></tr>`).join("")}</tbody></table>
    ${cards.some((c) => typeof c.demand === "number") ? docFig(`그림 4. 우선 개선 ${cards.length}곳의 ${daily ? "하루 이용" : "밤 하차"}과 고치는 방법`, dBarsH(cards.map((c) => ({ label: `${c.rank}. ${c.name.length > 14 ? c.name.slice(0, 13) + "…" : c.name}`, v: typeof c.demand === "number" ? c.demand : 5,
      color: c.rx && c.rx.code === "adjust_cctv" ? DC.blue : c.rx && c.rx.code === "bell" ? DC.green : DC.org, tag: c.rx ? c.rx.label : "" })), { fmt: (v, r) => typeof r === "object" && v === 5 ? "10명 미만" : Math.round(v).toLocaleString() + "명", lw: 200 }),
      "주황은 CCTV를 새로 달아야 하는 곳, 파랑은 근처 CCTV 방향만 돌리면 되는 곳입니다.") : ""}
    ${docFig("그림 5. 정류장에서 가장 가까운 CCTV·비상벨까지 거리", dDots(cards, rr), "점이 옅은 칸 밖에 있으면 기준 거리 안에 CCTV·비상벨이 없다는 뜻입니다. 2km가 넘으면 오른쪽 끝에 표시했습니다.")}`);
  // 4. map
  SEC.push(`${H("위치")}<div class="doc-mapbox"><img class="doc-map" src="${img}" alt="우선 개선 정류장 위치"></div>
    <p class="doc-cap">번호는 위 표의 순위입니다. 붉은 테두리는 CCTV·비상벨이 모두 닿지 않는 정류장, 파란 점은 CCTV, 초록 점은 비상벨입니다.</p>`);
  // 5. per-stop
  SEC.push(`${H("정류장별 건의 내용")}
    ${cards.map((c) => `<div class="doc-stop"><div class="doc-stop-fig">${spotlightSVG(c, rr, 150)}</div><div>
      <p class="doc-stop-h"><b>${c.rank}. ${esc(c.name)}</b> <span>${esc(c.area || "")}${c.rx ? " · " + esc(c.rx.label) : ""}</span></p>
      <ul>${c.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}
        ${c.cctv_m != null || c.bell_m != null ? `<li>정류장에서 가장 가까운 CCTV·비상벨까지 걸어서 약 ${walkMin(Math.min(...[c.cctv_m, c.bell_m].filter((v) => v != null)))}분 (분당 80m)</li>` : ""}
        ${c.together && c.together.stops > 1 ? `<li>이곳에 CCTV 1대를 두면 반경 ${rr.cctv}m 안 정류장 ${c.together.stops}곳(하루 ${c.together.users.toLocaleString()}명)에도 함께 CCTV가 닿습니다</li>` : ""}</ul>
      <p class="doc-rec">${esc(c.recommendation)}${c.rx && c.rx.code === "adjust_cctv" ? " 근처에 있는 CCTV의 방향을 정류장 쪽으로 돌리면 새로 다는 것보다 비용을 줄일 수 있습니다." : ""}</p>
    </div></div>`).join("")}`);
  // 6. plan
  if (plan && plan.rows.length) SEC.push(`${H(`설치 계획안 (예산 ${won(G.budget)})`)}
    <p>${plan.mine ? "설치 계획 화면에서 직접 고른 계획입니다." : "Agent가 같은 돈으로 가장 많은 사람을 지킬 수 있는 곳부터 고른 계획입니다."} 비용은 CCTV 새로 달기 ${G.costs.new_cctv}만원, 방향 돌리기 ${G.costs.adjust_cctv}만원, 비상벨 달기 ${G.costs.bell}만원으로 계산했습니다.</p>
    <table class="doc-table"><thead><tr><th>순서</th><th>정류장</th><th>조치</th><th>비용</th><th>새로 닿는<br>하루 이용자</th></tr></thead>
    <tbody>${plan.rows.slice(0, 15).map((r, i) => `<tr><td>${i + 1}</td><td>${esc(r.c.name)}</td><td class="l">${esc(r.acts.join(" + "))}</td><td>${r.cost.toLocaleString()}만원</td><td>${r.users.toLocaleString()}명</td></tr>`).join("")}
    ${plan.rows.length > 15 ? `<tr><td></td><td class="l" colspan="2">외 ${plan.rows.length - 15}곳 (${plan.rows.slice(15).map((r) => esc(r.c.name)).slice(0, 6).join(", ")} 등)</td><td>${plan.rows.slice(15).reduce((a, r) => a + r.cost, 0).toLocaleString()}만원</td><td>${plan.rows.slice(15).reduce((a, r) => a + r.users, 0).toLocaleString()}명</td></tr>` : ""}
    <tr class="sum"><td></td><td>합계 ${plan.rows.length}곳</td><td></td><td>${plan.ev.cost.toLocaleString()}만원</td><td>CCTV ${Math.round(plan.ev.eu).toLocaleString()}명 · 벨 ${Math.round(plan.ev.bu).toLocaleString()}명</td></tr></tbody></table>
    ${docFig("그림 6. 계획안의 정류장별 효과 (같은 돈으로 더 많이 지키는 순)", dBarsH(plan.rows.slice(0, 15).map((r) => ({ label: r.c.name.length > 14 ? r.c.name.slice(0, 13) + "…" : r.c.name, v: r.users,
      color: r.acts.some((a) => /방향/.test(a)) ? DC.blue : r.acts.some((a) => /CCTV/.test(a)) ? DC.org : DC.green, tag: `${r.cost.toLocaleString()}만원` })), { fmt: (v) => Math.round(v).toLocaleString() + "명", lw: 170 }),
      `${plan.rows.length > 15 ? `상위 15곳만 표시했습니다. ` : ""}주황은 CCTV 새로 달기, 파랑은 방향 돌리기, 초록은 비상벨만 다는 곳입니다.`)}
    ${docFig("그림 7. 예산에 따라 지킬 수 있는 사람 수: Agent 추천과 이용 많은 순 비교", dBudgetCurve(), "CCTV와 비상벨이 새로 닿는 하루 이용자를 더한 숫자입니다. 가로축은 예산입니다.")}
    <p class="doc-cap">같은 예산을 이용 많은 정류장부터 새로 다는 데 쓰는 방식(${Math.round(plan.busy.score).toLocaleString()}명)보다 이 계획(${Math.round(plan.ev.score).toLocaleString()}명)이 ${plan.busy.score ? Math.round((plan.ev.score / plan.busy.score - 1) * 100) : 0}% 더 많은 이용자를 지킵니다.</p>`);
  // 7. effects
  if (plan && plan.rows.length) SEC.push(`${H("기대 효과")}<ul>
    <li>하루 ${Math.round(plan.ev.eu).toLocaleString()}명, 1년 약 ${Math.round(plan.ev.eu * 365 / 10000).toLocaleString()}만 명의 버스 이용이 CCTV가 비추는 정류장에서 이루어집니다.</li>
    <li>CCTV가 닿지 않던 정류장 ${plan.ev.stopsE.toLocaleString()}곳, 비상벨이 닿지 않던 정류장 ${plan.ev.stopsB.toLocaleString()}곳이 범위 안에 들어옵니다.</li>
    ${plan.ev.school ? `<li>학교 반경 500m 안 정류장 ${plan.ev.school}곳도 함께 보강됩니다.</li>` : ""}
    ${plan.ev.adj ? `<li>${plan.ev.adj}곳은 새로 달지 않고 근처 CCTV 방향만 돌려 비용을 아낍니다.</li>` : ""}</ul>`);
  // 8. verification
  if (report.robustness && report.robustness.n) SEC.push(`${H("결과 검증")}
    <p>CCTV 기준 거리, 비상벨 기준 거리${(report.robustness.variants || []).some((v) => /시간대/.test(v.setting)) ? ", 시간대" : ""}를 ${report.robustness.n}가지로 바꿔 다시 계산했습니다. ${report.robustness.stable}곳은 모든 조건에서 그대로 남았고, 상위 ${report.robustness.base}곳 중 평균 ${report.robustness.avg_kept}곳이 유지되었습니다. 표의 "기준 바꿔도" 열이 정류장별 결과입니다.</p>
    ${docFig("그림 8. 기준을 바꿔도 상위 10곳에 남은 정류장 수", dBarsH((report.robustness.variants || []).map((v) => ({ label: v.setting, v: v.kept, color: DC.blue })), { max: report.robustness.base, fmt: (v) => `${v}/${report.robustness.base}곳`, lw: 140 }))}`);
  SEC.push(`${H("활용 안내")}<ul><li>이 건의서는 정류장 주변에 CCTV·비상벨이 있는지를 기준으로 먼저 손볼 순서를 정리한 것입니다.</li>
    <li>현장을 확인하신 뒤 CCTV·안전비상벨 설치, 기존 CCTV 방향 조정, 조명 보강, 막차 시간 조정을 검토해 주시기 바랍니다.</li>
    <li>정류장마다 붙인 그림은 300m 안의 CCTV·비상벨 위치와 비추는 범위입니다.</li></ul>`);
  const src = demo ? ["버스 정류소 정보, 버스 정류소별 이용 현황: 제주빅데이터센터 형식의 가상 데이터", "CCTV 정보, 안전비상벨 위치: 공공데이터포털 표준데이터 형식의 가상 데이터"] : [
    kinds.has("bus_usage") ? "버스 정류소 정보, 버스 정류소별 이용 현황: 제주빅데이터센터" : "버스 정류장 위치: 공공데이터포털 제주특별자치도_버스정류소현황",
    kinds.has("bus_daily_usage") ? "정류장별 버스 이용(승차+환승): 제주데이터허브 일일 정류소별 버스 이용자 정보" : "",
    sm.hourly ? `시간대별 버스 승차 인원: 제주데이터허브 (${esc(sm.hourly.from)}~${esc(sm.hourly.to)})` : "",
    "CCTV 위치: 공공데이터포털 전국CCTV표준데이터, 제주데이터허브 CCTV 정보",
    "안전비상벨 위치: 공공데이터포털 전국안전비상벨위치표준데이터",
    kinds.has("school") ? "학교 위치: 공공데이터포털 전국초중등학교위치표준데이터" : "",
    "밤 불빛: NASA Black Marble, NASA GIBS",
    plan ? "CCTV 단가: 2024년 지자체 CCTV 설치 비용·대수 집계(행정안전부 자료 보도)로 계산한 대당 금액" : "",
  ].filter(Boolean);
  SEC.push(`${H("자료 출처")}<ul>${src.map((x) => `<li>${x}</li>`).join("")}<li>하루 평균 10명 미만인 값은 "10명 미만"으로 표시했습니다.</li></ul>`);
  return `${demo ? '<p class="doc-demo">시연용 가상 데이터로 만든 예시 문서예요. 실제 장소에 대한 내용이 아니에요.</p>' : ""}
    <p class="doc-kicker">안심하차 Agent · 건의서</p>
    <h1>버스 정류장 주변 밤길 안심 시설 개선 건의</h1>
    <table class="doc-meta">
      <tr><th>작성일</th><td>${today}</td><th>분석 범위</th><td>${esc(scope)}</td></tr>
      <tr><th>${daily ? "수요 기준" : "분석 시간대"}</th><td>${daily ? "정류장별 하루 이용(승차+환승)" : (h.length ? `${h[0]}~${h[h.length - 1]}시` : "-") + (sm.boarding_fallback ? " (승차 기록으로 보정)" : "")}</td><th>판정 기준</th><td>CCTV ${rr.cctv}m, 비상벨 ${rr.bell}m 이내</td></tr>
    </table>
    ${SEC.join("\n")}`;
}
$("#doc-btn").addEventListener("click", () => showTab("doc"));
$$("[data-goto]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.goto)));
$("#doc-print").addEventListener("click", () => { buildDoc(); window.print(); });

// ---------------- start ----------------
async function restore() {
  const r = await api("/api/events?since=0").catch(() => null);
  if (!r || !r.events.length) { ask(true); return; }
  resetResults();
  const u = r.events.find((e) => e.type === "user");
  if (u && !(u.data && u.data.preview)) $("#q").value = u.text;
  for (const e of r.events) { since = e.i + 1; handle(e); }
  if (r.busy) { $("#go").disabled = true; polling = setInterval(poll, 450); }
}
refreshStatus().then(() => {
  const q = PARAMS.get("q");
  if (q) { $("#q").value = q; ask(false); return; }
  restore();
});

// ---------------- pick hero, radius diagram, budget simulator ----------------
const RX_TONE = { new_cctv: "rx-new", adjust_cctv: "rx-adj", bell: "rx-bell", light: "rx-light", last_bus: "rx-bus", none: "rx-none" };
const num = (v) => (typeof v === "number" ? v : null);
const firm = (c) => c.stay != null && c.stay_of && c.stay === c.stay_of;
function radialSVG(c, rr, size = 220) {
  const R = 300, h = size / 2, sc = h / R * 0.92;
  const pts = (c.near || []).map(([k, dx, dy]) => `<circle cx="${(h + dx * sc).toFixed(1)}" cy="${(h - dy * sc).toFixed(1)}" r="${k === "light" ? 2.2 : k === "school" ? 6 : 4}" class="rd-${k}"/>`).join("");
  const ring = (m, cls, label) => `<circle cx="${h}" cy="${h}" r="${(m * sc).toFixed(1)}" class="${cls}"/><text x="${h + 4}" y="${(h - m * sc - 4).toFixed(1)}" class="rd-t">${label}</text>`;
  const out = [];
  if (c.cctv_m != null && c.cctv_m > R) out.push(`CCTV ${fmtM(c.cctv_m)}`);
  if (c.bell_m != null && c.bell_m > R) out.push(`비상벨 ${fmtM(c.bell_m)}`);
  return `<svg class="radial" viewBox="0 0 ${size} ${size}" role="img" aria-label="정류장 반경 300m 안의 CCTV와 비상벨">
    <rect x="0" y="0" width="${size}" height="${size}" rx="16" class="rd-bg"/>
    ${ring(R, "rd-ring", "300m")}${ring(rr.bell, "rd-bell-r", `비상벨 기준 ${rr.bell}m`)}${ring(rr.cctv, "rd-cctv-r", `CCTV 기준 ${rr.cctv}m`)}
    ${pts}
    <circle cx="${h}" cy="${h}" r="7" class="rd-stop"/>
    ${out.length ? `<text x="${h}" y="${size - 24}" text-anchor="middle" class="rd-out">300m 안에 없음</text><text x="${h}" y="${size - 10}" text-anchor="middle" class="rd-out">${esc(out.join(" · "))}</text>` : ""}
  </svg>`;
}
function feelHTML(c) {
  const d = num(c.demand);
  const items = [];
  if (d != null) items.push([`${Math.round(d).toLocaleString()}명`, "하루 이용"], [`${(Math.round(d * 365 / 1000) * 1000).toLocaleString()}명`, "1년으로 치면"]);
  if (c.demand_pct != null) items.push([`상위 ${Math.max(1, Math.round(100 - c.demand_pct))}%`, "제주 정류장 중 이용"]);
  if (c.school) items.push([c.school.m <= 999 ? `${c.school.m}m` : fmtM(c.school.m), `가장 가까운 학교 · ${c.school.name}`]);
  if (c.area_rank) items.push([`${c.area_rank}위 / ${c.area_n}곳`, `${c.area || "지역"} 안 순위`]);
  return `<div class="feel">${items.map(([b, s]) => `<div><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join("")}</div>`;
}
function rxHTML(c) {
  if (!c.rx) return "";
  return `<div class="rx ${RX_TONE[c.rx.code] || ""}"><span class="rx-tag">이렇게 고쳐요</span><b>${esc(c.rx.label)}</b><p>${esc(c.rx.detail)}</p>
    ${c.rx.actions.length > 1 ? `<p class="rx-more">같이 하면 좋아요: ${c.rx.actions.slice(1).map((a) => esc(a.label)).join(", ")}</p>` : ""}</div>`;
}
function drawPick() {
  const el = $("#pick-hero"), c = report && report.cards && report.cards[0];
  if (!c) { el.hidden = true; return; }
  const rr = radii();
  el.hidden = false;
  el.innerHTML = `
    <div class="pick-main">
      <p class="eyebrow">Agent가 고른 1순위 정류장${firm(c) ? ' <span class="firm">기준을 바꿔도 그대로 1위권</span>' : ""}</p>
      <h3 class="pick-name">${esc(c.name)}</h3>
      <p class="pick-sub">${esc(c.area || "")} · ${gapChips(c.gap_codes)}</p>
      ${rxHTML(c)}
      ${walkHTML(c)}
      ${infoGrid(c)}
      <div class="pick-why"><h4>왜 여기를 골랐나요</h4><ul>${c.evidence.slice(0, 4).map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>
      <div class="actions"><button class="btn sm" id="pick-map">지도에서 보기</button><button class="btn sm ghost" data-goto="plan">설치 계획 보기</button></div>
    </div>
    <div class="pick-fig">${spotlightSVG(c, rr, 300)}
      <p class="fig-cap"><i class="dot cctv"></i>CCTV가 비추는 곳(${rr.cctv}m) <i class="dot bell"></i>비상벨(${rr.bell}m) <i class="dot school"></i>학교</p>${linksHTML(c)}</div>`;
  $("#pick-map").addEventListener("click", () => { showTab("map"); selectStop(c.id); });
  el.querySelector("[data-goto]").addEventListener("click", () => showTab("plan"));
}

let simKind = "cctv";
function drawPlan() {
  const pl = report && report.summary && report.summary.placement;
  const tile = $(".sim-tile");
  if (!pl || !Object.keys(pl).length) { $("#sim-big").innerHTML = '<p class="muted">이용 자료가 있어야 설치 계획을 계산해요.</p>'; return; }
  if (!pl[simKind]) simKind = Object.keys(pl)[0];
  $("#sim-kind").innerHTML = Object.keys(pl).map((k) => `<button role="radio" aria-checked="${k === simKind}" data-k="${k}">${k === "cctv" ? "CCTV" : "비상벨"}</button>`).join("");
  $("#sim-kind").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { simKind = b.dataset.k; drawPlan(); }));
  const P = pl[simKind], max = P.picks.length, slider = $("#sim-k");
  slider.max = String(max);
  if (+slider.value > max) slider.value = String(max);
  const upd = () => {
    const k = +slider.value, p = P.picks[k - 1], what = simKind === "cctv" ? "CCTV" : "비상벨";
    $("#sim-k-out").textContent = `${k}대`;
    $("#sim-big").innerHTML = `<b>하루 ${Math.round(p.cum).toLocaleString()}명</b><span>${what} ${k}대가 새로 닿는 사람</span>`;
    $("#sim-curve").innerHTML = curveSVG(P.picks, k);
    $("#sim-picks").innerHTML = P.picks.slice(0, k).map((x) => `<li><span class="sp-n">${x.n}</span><span class="sp-name">${esc(x.name)}<small>${esc(x.area || "")}${x.covered_stops > 1 ? ` · 정류장 ${x.covered_stops}곳에 함께 닿음` : ""}</small></span><span class="sp-gain">+${esc(typeof x.gain === "number" ? Math.round(x.gain).toLocaleString() + "명" : x.gain)}</span><button class="text-btn" data-id="${esc(x.id)}">지도</button></li>`).join("");
    $("#sim-picks").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { showTab("map"); selectStop(b.dataset.id); }));
  };
  slider.oninput = upd; upd();
  const B = P.bench, rows = [["Agent 추천 위치 (옆 정류장까지 함께)", B.greedy, "acc"], ["이용 많은 곳부터", B.demand_first, ""], ["아무 데나 (평균)", B.random_avg, ""], ["외진 곳부터", B.farthest_first, ""]];
  const top = Math.max(...rows.map((r) => r[1]), 1);
  $("#sim-bench").innerHTML = `<h3>CCTV ${B.k}대, 어디에 다느냐에 따라</h3>
    <p class="muted small-p">CCTV가 새로 닿는 하루 이용자 수예요.</p>
    <ul class="bench">${rows.map(([l, v, c]) => `<li class="${c}"><span>${l}</span><i><b style="width:${(100 * v / top).toFixed(1)}%"></b></i><em>${Math.round(v).toLocaleString()}명</em></li>`).join("")}</ul>
    <p class="bench-note">Agent가 고른 위치에 달면 외진 곳부터 다는 것보다 <b>${Math.round(B.greedy / Math.max(1, B.farthest_first))}배</b>, 이용 많은 곳부터 다는 것보다 <b>${Math.round(B.greedy - B.demand_first).toLocaleString()}명</b> 더 지켜요.</p>`;
  const rx = report.summary.rx_counts || {}, tot = Object.values(rx).reduce((a, b) => a + b, 0) || 1;
  const sc = report.summary.school;
  $("#school-tile").hidden = !sc;
  if (sc) $("#school-tile").innerHTML = `<h3>학교 근처 정류장</h3><p class="muted small-p">제주 초·중·고 ${sc.schools}곳에서 500m 안에 있는 정류장이에요.</p>
    <div class="feel"><div><b>${sc.blind_near.toLocaleString()}곳</b><span>CCTV도 비상벨도 없는 곳</span></div><div><b>${Math.round(100 * sc.blind_near / Math.max(1, sc.stops_near))}%</b><span>학교 근처 정류장 ${sc.stops_near.toLocaleString()}곳 중</span></div><div><b>${sc.blind_near_use.toLocaleString()}명</b><span>이 정류장들의 하루 이용</span></div></div>
    <p class="bench-note">"학원", "우리 애"처럼 물어보시면 Agent가 학교 근처 정류장만 따로 골라 드려요.</p>`;
  const RXL = { new_cctv: "CCTV 새로 달기", adjust_cctv: "근처 CCTV 방향 돌리기", bell: "비상벨 달기", light: "가로등 밝히기", last_bus: "막차 늦추기" };
  $("#rx-tile").innerHTML = `<h3>어떻게 고치면 될까</h3><p class="muted small-p">CCTV나 비상벨이 없는 정류장 ${tot.toLocaleString()}곳을 고치는 방법이에요.</p>
    <ul class="bench rxb">${Object.entries(rx).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<li class="${RX_TONE[k]}"><span>${RXL[k] || k}</span><i><b style="width:${(100 * v / tot).toFixed(1)}%"></b></i><em>${v.toLocaleString()}곳</em></li>`).join("")}</ul>
    <p class="bench-note">200m 안에 CCTV가 이미 있으면 새로 달지 않고 방향만 돌려도 돼요. 그만큼 돈이 덜 들어요.</p>`;
}
function curveSVG(picks, k) {
  const W = 560, H = 190, L = 40, B = 26, T = 12, R = 12, n = picks.length;
  const ymax = Math.max(5, Math.ceil(picks[n - 1].cum_pct / 5) * 5);
  const x = (i) => L + (W - L - R) * (i - 1) / Math.max(1, n - 1), y = (v) => T + (H - T - B) * (1 - v / ymax);
  const path = picks.map((p, i) => `${i ? "L" : "M"}${x(p.n).toFixed(1)},${y(p.cum_pct).toFixed(1)}`).join("");
  const area = path + `L${x(n)},${y(0)}L${x(1)},${y(0)}Z`;
  const grid = [0, ymax / 2, ymax].map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="cv-grid"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" class="cv-t">${v}%</text>`).join("");
  const xt = [1, 10, 20, 30].filter((v) => v <= n).map((v) => `<text x="${x(v)}" y="${H - 6}" text-anchor="middle" class="cv-t">${v}대</text>`).join("");
  const p = picks[k - 1];
  return `<svg viewBox="0 0 ${W} ${H}" class="curve" role="img" aria-label="설치 대수별 덮는 이용자 비율">${grid}${xt}
    <path d="${area}" class="cv-area"/><path d="${path}" class="cv-line"/>
    <line x1="${x(k)}" x2="${x(k)}" y1="${T}" y2="${y(0)}" class="cv-k"/><circle cx="${x(k)}" cy="${y(p.cum_pct)}" r="5" class="cv-dot"/></svg>`;
}

// ---------------- stop visuals v2: spotlight map + walking line ----------------
const WALK = 80;   // m per minute (about 4.8 km/h)
const walkMin = (m) => Math.max(1, Math.round(m / WALK));
let svgUid = 0;
function spotlightSVG(c, rr, size = 260) {
  const R = 300, h = size / 2, sc = (h / R) * 0.94, u = ++svgUid;
  const near = c.near || [];
  const P = (dx, dy) => [h + dx * sc, h - dy * sc];
  const cctv = near.filter((p) => p[0] === "cctv"), bell = near.filter((p) => p[0] === "bell"), sch = near.filter((p) => p[0] === "school");
  const lit = cctv.some(([, dx, dy]) => Math.hypot(dx, dy) <= rr.cctv) || bell.some(([, dx, dy]) => Math.hypot(dx, dy) <= rr.bell);
  const glow = (arr, r, id) => arr.map(([, dx, dy]) => { const [x, y] = P(dx, dy); return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(r * sc).toFixed(1)}" fill="url(#${id}${u})"/>`; }).join("");
  const dots = (arr, cls, r) => arr.map(([, dx, dy]) => { const [x, y] = P(dx, dy); return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r}" class="${cls}"/>`; }).join("");
  const grid = [-200, -100, 0, 100, 200].map((v) => `<line x1="0" x2="${size}" y1="${h - v * sc}" y2="${h - v * sc}" class="sp-grid"/><line y1="0" y2="${size}" x1="${h + v * sc}" x2="${h + v * sc}" class="sp-grid"/>`).join("");
  const bar = 100 * sc;
  return `<svg class="spot" viewBox="0 0 ${size} ${size}" role="img" aria-label="정류장 주변 300m의 CCTV·비상벨 빛">
    <defs>
      <radialGradient id="gc${u}"><stop offset="0" stop-color="#64a8ff" stop-opacity=".55"/><stop offset=".7" stop-color="#64a8ff" stop-opacity=".18"/><stop offset="1" stop-color="#64a8ff" stop-opacity="0"/></radialGradient>
      <radialGradient id="gb${u}"><stop offset="0" stop-color="#5fd7a9" stop-opacity=".35"/><stop offset=".75" stop-color="#5fd7a9" stop-opacity=".1"/><stop offset="1" stop-color="#5fd7a9" stop-opacity="0"/></radialGradient>
      <radialGradient id="gs${u}"><stop offset="0" stop-color="#ffb340" stop-opacity=".9"/><stop offset="1" stop-color="#ffb340" stop-opacity="0"/></radialGradient>
      <clipPath id="cl${u}"><rect width="${size}" height="${size}" rx="18"/></clipPath>
    </defs>
    <g clip-path="url(#cl${u})">
      <rect width="${size}" height="${size}" class="sp-bg"/>${grid}
      ${glow(bell, rr.bell, "gb")}${glow(cctv, rr.cctv, "gc")}
      ${dots(sch, "sp-school", 6)}${dots(bell, "sp-bell", 3.6)}${dots(cctv, "sp-cctv", 3.6)}
      <circle cx="${h}" cy="${h}" r="${(rr.cctv * sc).toFixed(1)}" class="${lit ? "sp-ring-ok" : "sp-ring-dark"}"/>
      <circle cx="${h}" cy="${h}" r="22" fill="url(#gs${u})"/>
      <circle cx="${h}" cy="${h}" r="7" class="sp-stop"/>
      <text x="${h}" y="${h - 30}" text-anchor="middle" class="sp-lbl">${lit ? "CCTV·벨 범위 안" : "CCTV·벨 범위 밖"}</text>
      <line x1="14" x2="${14 + bar}" y1="${size - 16}" y2="${size - 16}" class="sp-bar"/><text x="${14 + bar / 2}" y="${size - 22}" text-anchor="middle" class="sp-t">100m</text>
    </g>
  </svg>`;
}
function walkHTML(c) {
  const marks = [];
  if (c.cctv_m != null) marks.push(["cctv", "CCTV", c.cctv_m]);
  else marks.push(["cctv", "CCTV", null]);
  if (c.bell_m != null) marks.push(["bell", "비상벨", c.bell_m]);
  if (c.school) marks.push(["school", c.school.name, c.school.m]);
  const known = marks.filter((m) => m[2] != null).map((m) => m[2]);
  const max = Math.min(2000, Math.max(400, ...known) * 1.08);
  const first = Math.min(...[c.cctv_m, c.bell_m].filter((v) => v != null));
  const head = Number.isFinite(first)
    ? `여기서 내리면 가장 가까운 CCTV나 비상벨까지 걸어서 <b>약 ${walkMin(first)}분</b> 걸려요.`
    : "이 정류장 주변 5km 안에는 CCTV·비상벨이 없어요.";
  const pin = ([k, label, m], i) => {
    const off = m == null || m > max;
    const x = off ? 100 : (100 * m) / max;
    return `<div class="wk-pin wk-${k}${i % 2 ? " low" : ""}" style="left:${x.toFixed(1)}%"><i></i><span>${esc(label)}<b>${m == null ? "5km+" : fmtM(m)} · ${m == null ? "-" : walkMin(m) + "분"}</b></span></div>`;
  };
  return `<div class="walk"><p class="wk-head">${head}</p>
    <div class="wk-track"><div class="wk-safe" style="width:${(100 * Math.max(...[radii().cctv])) / max}%"></div>
      <div class="wk-stop"><i></i><span>정류장</span></div>${marks.map(pin).join("")}</div>
    <p class="wk-note">1분에 80m 걷는다고 보고 계산했어요. 밝은 칸은 CCTV가 비추는 ${radii().cctv}m 안이에요.</p></div>`;
}
function infoGrid(c) {
  const d = num(c.demand), items = [];
  if (d != null) items.push([`${Math.round(d).toLocaleString()}명`, `하루 이용 (1년이면 약 ${Math.round(d * 365 / 10000)}만 명)`]);
  if (c.x_median) items.push([`${c.x_median >= 10 ? Math.round(c.x_median) : c.x_median}배`, "보통 정류장보다 많이 타요"]);
  if (c.area_blind) items.push([`${c.area_blind.blind}/${c.area_blind.stops}곳`, `${c.area || "같은 동네"} 정류장 중 CCTV·벨 없는 곳`]);
  if (c.plan_n) items.push([`${c.plan_n}번째`, "Agent 추천 CCTV 설치 순서"]);
  if (c.together && c.together.stops > 1) items.push([`${c.together.stops}곳`, `CCTV 한 대가 함께 닿는 정류장 (하루 ${c.together.users.toLocaleString()}명)`]);
  if (c.lat != null && typeof map !== "undefined" && map.nightAt) items.push([satLabel(map.nightAt(c.lat, c.lon)), "위성 사진으로 본 밤 밝기"]);
  return `<div class="feel f3">${items.map(([b, s]) => `<div><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join("")}</div>`;
}
const linksHTML = (c) => `<div class="ext-links"><a class="text-btn" target="_blank" rel="noopener" href="https://map.kakao.com/link/roadview/${c.lat},${c.lon}">로드뷰로 현장 보기 ↗</a><a class="text-btn" target="_blank" rel="noopener" href="https://map.kakao.com/link/map/${encodeURIComponent(c.name)},${c.lat},${c.lon}">카카오맵 ↗</a></div>`;

// ---------------- budget game ----------------
const ACT = { adjust_cctv: { short: "방향 돌리기", long: "근처 CCTV 방향 돌리기", kind: "e" }, new_cctv: { short: "CCTV 새로 달기", long: "CCTV 새로 달기", kind: "e" }, bell: { short: "비상벨 달기", long: "비상벨 달기", kind: "b" } };
const G = { budget: 10000, costs: null, picks: {}, filter: "all" };   // budget and costs in 만원; picks: id -> {e: action|null, b: bool}
function gameData() { return report && report.summary && report.summary.game; }
function gEval(picks) {
  const g = gameData(), ce = new Set(), cb = new Set();
  let cost = 0, adj = 0;
  for (const c of g.candidates) {
    const p = picks[c.id]; if (!p) continue;
    if (p.e) { cost += G.costs[p.e]; c.ce.forEach((i) => ce.add(i)); if (p.e === "adjust_cctv") adj++; }
    if (p.b) { cost += G.costs.bell; c.cb.forEach((i) => cb.add(i)); }
  }
  const sum = (set) => [...set].reduce((a, i) => a + (g.demand[i] || 0), 0);
  const eu = sum(ce), bu = sum(cb);
  const school = g.candidates.filter((c) => c.school && picks[c.id] && (picks[c.id].e || picks[c.id].b)).length;
  return { cost, eu, bu, score: eu + bu, stopsE: ce.size, stopsB: cb.size, school, adj };
}
function gGain(c, act) {           // marginal score of adding act at c on top of the current plan
  const cur = gEval(G.picks), p = { ...(G.picks[c.id] || { e: null, b: false }) };
  if (act === "bell") p.b = true; else p.e = act;
  const nx = gEval({ ...G.picks, [c.id]: p });
  return Math.round(nx.score - cur.score);
}
function gLoss(c) {                // what stays uncovered if nothing is done here
  const g = gameData(), p = G.picks[c.id];
  if (p && (p.e || p.b)) return 0;
  const rest = { ...G.picks }; delete rest[c.id];
  const ids = new Set([...c.ce, ...c.cb]);
  return Math.round([...ids].reduce((a, i) => a + (g.demand[i] || 0), 0));
}
function gAuto(budget, mode) {     // mode "agent": best users per 만원 first; "busy": busiest stop first, full kit
  const g = gameData(), picks = {};
  let left = budget;
  if (mode === "busy") {
    for (const c of [...g.candidates].sort((a, b) => b.demand - a.demand)) {
      const p = { e: null, b: false };
      if (c.opts.includes("new_cctv") && left >= G.costs.new_cctv) { p.e = "new_cctv"; left -= G.costs.new_cctv; }
      if (c.opts.includes("bell") && left >= G.costs.bell) { p.b = true; left -= G.costs.bell; }
      if (p.e || p.b) picks[c.id] = p;
    }
    return picks;
  }
  for (;;) {
    let best = null;
    const base = gEval(picks).score;
    for (const c of g.candidates) {
      const cur = picks[c.id] || { e: null, b: false };
      for (const a of c.opts) {
        if (a === "bell" ? cur.b : (cur.e && !(cur.e === "adjust_cctv" && a === "new_cctv"))) continue;
        const add = a === "bell" ? G.costs.bell : G.costs[a] - (cur.e ? G.costs[cur.e] : 0);
        if (add > left || add <= 0) continue;
        const np = { ...cur }; if (a === "bell") np.b = true; else np.e = a;
        const gain = gEval({ ...picks, [c.id]: np }).score - base;
        if (gain > 0 && (!best || gain / add > best.r)) best = { r: gain / add, id: c.id, np, add };
      }
    }
    if (!best) break;
    picks[best.id] = best.np; left -= best.add;
  }
  return picks;
}
const won = (v) => (v >= 10000 ? `${(v / 10000).toFixed(v % 10000 ? 1 : 0)}억원` : `${v.toLocaleString()}만원`);
function grade(r) { return r >= 0.97 ? ["S", "Agent만큼 잘했어요"] : r >= 0.85 ? ["A", "아주 좋아요"] : r >= 0.7 ? ["B", "괜찮아요"] : r >= 0.5 ? ["C", "조금 아쉬워요"] : ["D", "다시 골라 볼까요"]; }
function drawGame() {
  const g = gameData();
  if (!g || !g.candidates.length) { $("#game-board").innerHTML = '<p class="muted">이용 자료가 있어야 설치 계획을 시뮬레이션할 수 있어요.</p>'; return; }
  if (!G.costs) G.costs = { ...g.costs };
  const me = gEval(G.picks), agentPicks = gAuto(G.budget, "agent"), ag = gEval(agentPicks), busy = gEval(gAuto(G.budget, "busy"));
  const ratio = ag.score ? me.score / ag.score : 0, [gr, grTxt] = grade(ratio), over = me.cost > G.budget;
  $("#game-board").innerHTML = `
    <div class="gb-top">
      <div class="gb-budget"><label>예산 <b>${won(G.budget)}</b></label>
        <input type="range" id="g-budget" min="500" max="50000" step="500" value="${G.budget}" aria-label="예산">
        <div class="gb-meter${over ? " over" : ""}"><i style="width:${Math.min(100, (100 * me.cost) / G.budget).toFixed(1)}%"></i></div>
        <p class="gb-used">${over ? "예산을 넘었어요 · " : ""}사용 ${won(me.cost)} / 남음 ${won(Math.max(0, G.budget - me.cost))}</p>
        <div class="gb-btns"><button class="btn sm" id="g-agent">Agent 추천대로 채우기</button><button class="btn sm ghost" id="g-busy">이용 많은 곳부터 채우기</button><button class="btn sm ghost" id="g-clear">처음부터</button></div>
      </div>
      <div class="gb-score">
        <div class="gb-grade g-${gr}"><b>${me.score ? gr : "-"}</b><span>${me.score ? grTxt : "정류장을 골라 보세요"}</span></div>
        <div class="gb-nums">
          <div><b>${Math.round(me.eu).toLocaleString()}명</b><span>새로 CCTV가 닿는 사람 (하루)</span></div>
          <div><b>${Math.round(me.bu).toLocaleString()}명</b><span>가까이에 비상벨이 생기는 사람 (하루)</span></div>
          <div><b>${me.stopsE + me.stopsB ? (me.stopsE).toLocaleString() + " / " + me.stopsB.toLocaleString() : "0"}</b><span>새로 닿는 정류장 (CCTV / 비상벨)</span></div>
          <div><b>${me.cost ? Math.round((me.score / me.cost) * 10000).toLocaleString() + "명" : "-"}</b><span>1억원으로 지키는 사람</span></div>
        </div>
        <p class="gb-vs">같은 예산의 Agent 추천과 비교하면 <b>${ag.score ? Math.round(ratio * 100) : 0}%</b>예요 (Agent 추천 ${Math.round(ag.score).toLocaleString()}명)</p>
      </div>
    </div>
    <details class="gb-cost"><summary>설치 비용 바꾸기</summary>
      <div class="gb-cost-in">${Object.keys(ACT).map((k) => `<label>${ACT[k].long}<input type="number" min="1" step="10" data-cost="${k}" value="${G.costs[k]}"> 만원</label>`).join("")}</div>
      <p class="muted small-p">${esc(g.cost_note)}</p></details>`;
  $("#g-budget").addEventListener("input", (e) => { G.budget = +e.target.value; drawGame(); });
  $("#g-agent").addEventListener("click", () => { G.picks = gAuto(G.budget, "agent"); drawGame(); });
  $("#g-busy").addEventListener("click", () => { G.picks = gAuto(G.budget, "busy"); drawGame(); });
  $("#g-clear").addEventListener("click", () => { G.picks = {}; drawGame(); });
  $$("#game-board [data-cost]").forEach((el) => el.addEventListener("change", () => { G.costs[el.dataset.cost] = Math.max(1, +el.value || 1); drawGame(); }));
  // candidate list
  const F = { all: "전체", school: "학교 주변", adjust: "방향만 돌리면 되는 곳", chosen: "고른 곳" };
  $("#game-filter").innerHTML = Object.entries(F).map(([k, v]) => `<button class="${G.filter === k ? "on" : ""}" data-f="${k}">${v}</button>`).join("");
  $$("#game-filter button").forEach((b) => b.addEventListener("click", () => { G.filter = b.dataset.f; drawGame(); }));
  let list = g.candidates;
  if (G.filter === "school") list = list.filter((c) => c.school);
  if (G.filter === "adjust") list = list.filter((c) => c.opts.includes("adjust_cctv"));
  if (G.filter === "chosen") list = list.filter((c) => G.picks[c.id] && (G.picks[c.id].e || G.picks[c.id].b));
  $("#game-count").textContent = `${list.length}곳`;
  const left = G.budget - me.cost;
  $("#game-list").innerHTML = list.map((c) => {
    const p = G.picks[c.id] || { e: null, b: false };
    const eo = c.opts.filter((a) => a !== "bell"), hasB = c.opts.includes("bell");
    const btn = (a) => {
      const on = a === "bell" ? p.b : p.e === a;
      const add = a === "bell" ? G.costs.bell : G.costs[a] - (p.e ? G.costs[p.e] : 0);
      const gain = on ? null : gGain(c, a);
      return `<button class="g-opt${on ? " on" : ""}" data-id="${esc(c.id)}" data-a="${a}" ${!on && add > left ? "disabled" : ""}>
        <span>${ACT[a].short}</span><small>${won(G.costs[a])}${on ? " · 선택됨" : ` · +${gain.toLocaleString()}명`}</small></button>`;
    };
    const loss = gLoss(c);
    return `<li class="${p.e || p.b ? "chosen" : ""}">
      <div class="g-head"><b>${esc(c.name)}</b><span>${esc(c.area || "")} · 하루 ${c.demand.toLocaleString()}명${c.school ? ' · <em class="g-sch">학교 주변</em>' : ""}</span>
        <span class="g-dist">CCTV ${fmtM(c.cctv_m)} · 비상벨 ${fmtM(c.bell_m)}</span></div>
      <div class="g-opts">${eo.length ? `<button class="g-opt g-none${!p.e ? " on" : ""}" data-id="${esc(c.id)}" data-a="none-e"><span>CCTV 그대로</span><small>${p.e ? "취소하기" : "지금 상태"}</small></button>${eo.map(btn).join("")}` : ""}${hasB ? btn("bell") : ""}</div>
      ${loss ? `<p class="g-loss">그대로 두면 하루 <b>${loss.toLocaleString()}명</b>이 CCTV·비상벨 없는 정류장을 계속 이용해요${c.ce.length + c.cb.length > 2 ? ` (주변 정류장 ${new Set([...c.ce, ...c.cb]).size}곳 포함)` : ""}.</p>` : ""}
    </li>`;
  }).join("") || '<li class="muted">해당하는 정류장이 없어요.</li>';
  $$("#game-list .g-opt").forEach((b) => b.addEventListener("click", () => {
    const id = b.dataset.id, a = b.dataset.a, p = { ...(G.picks[id] || { e: null, b: false }) };
    if (a === "none-e") p.e = null; else if (a === "bell") p.b = !p.b; else p.e = p.e === a ? null : a;
    if (!p.e && !p.b) delete G.picks[id]; else G.picks[id] = p;
    drawGame();
  }));
  // comparison + badges
  const rows = [["내 계획", me.score, "mine"], ["Agent 추천", ag.score, "acc"], ["이용 많은 곳부터 새로 달기", busy.score, ""]];
  const top = Math.max(1, ...rows.map((r) => r[1]));
  $("#game-compare").innerHTML = `<h3>예산 ${won(G.budget)}으로 몇 명을 지킬까</h3><p class="muted small-p">CCTV와 비상벨이 새로 닿는 하루 이용자를 더한 숫자예요.</p>
    <ul class="bench">${rows.map(([l, v, c]) => `<li class="${c}"><span>${l}</span><i><b style="width:${(100 * v / top).toFixed(1)}%"></b></i><em>${Math.round(v).toLocaleString()}</em></li>`).join("")}</ul>
    <p class="bench-note">Agent는 같은 돈으로 더 많은 사람을 지키는 곳부터 고르고, 근처에 CCTV가 있으면 새로 달지 않고 방향만 돌려요. 그래서 이용 많은 곳부터 새로 다는 것보다 <b>${busy.score ? Math.round((ag.score / busy.score - 1) * 100) : 0}%</b> 더 많은 사람을 지켜요.</p>`;
  const B = [
    ["학교 앞 지킴이", "학교 주변 정류장 3곳 이상 손보기", me.school >= 3],
    ["알뜰살뜰", "CCTV 방향 돌리기로 3곳 이상 손보기", me.adj >= 3],
    ["1억원으로 1만 명", "1억원으로 1만 명 넘게 지키기", me.cost && (me.score / me.cost) * 10000 >= 10000],
    ["Agent 따라잡기", "Agent 추천만큼 또는 그보다 많이 지키기", me.score && me.score >= ag.score],
  ];
  $("#game-badges").innerHTML = `<h3>도전 과제</h3><ul class="badges">${B.map(([t, d, ok]) => `<li class="${ok ? "got" : ""}"><b>${ok ? "★" : "☆"} ${t}</b><span>${d}</span></li>`).join("")}</ul>`;
}


// ---------------- pipeline diagram (Agent가 한 일) ----------------
const TOOL_KO = { inspect_data: ["자료 확인", "어떤 파일인지 알아보고 위치를 맞춰요"], check_coverage: ["자료 점검", "한쪽에 몰린 자료가 없는지 봐요"],
  run_gap_analysis: ["정류장 계산", "정류장마다 주변 CCTV·비상벨을 세요"], check_robustness: ["다시 확인", "기준을 바꿔 한 번 더 계산해요"],
  plan_installations: ["설치 위치 고르기", "한 대로 가장 많은 사람을 지키는 곳"], look_up_place: ["장소 찾기", "질문에 나온 동네 주변"], finalize_report: ["결과 정리", "작은 숫자 가리기 · 건의 문장 쓰기"] };
function drawPipeline() {
  const el = $("#pipe");
  if (!el || !report || !pipe.length) return;
  const sm = report.summary || {}, q = report.quality || [];
  const src = [
    ["제주데이터허브", q.filter((f) => ["bus_daily_usage", "bus_hourly_total"].includes(f.kind) || (f.kind === "cctv" && /datahub/.test(f.file || ""))).length],
    ["공공데이터포털", q.filter((f) => ["bus_stops", "bell", "school", "streetlight"].includes(f.kind) || (f.kind === "cctv" && !/datahub/.test(f.file || ""))).length],
    ["제주빅데이터센터", q.filter((f) => f.kind === "bus_usage").length],
  ];
  const llm = report.mode === "llm";
  const total = pipe.reduce((a, p) => a + (p.ms || 0), 0);
  const short = (t, n = 46) => (t.length > n ? t.slice(0, n - 1) + "…" : t);
  const node = (p, i) => {
    const [ko, how] = TOOL_KO[p.name] || [p.name, ""];
    return `<li class="pp-node${p.judge.length ? " judged" : ""}">
      <div class="pp-card"><span class="pp-i">${i + 1}</span><b>${esc(ko)}</b><code>${esc(p.name || "")}</code>
        <small>${esc(how)}</small>${p.ms != null ? `<em>${p.ms < 1000 ? "1초 안" : (p.ms / 1000).toFixed(1) + "초"}</em>` : ""}
        <p>${esc(short(p.res.replace(/^판별 결과: /, ""), 64))}</p></div>
      ${p.judge.map((j) => `<div class="pp-judge"><i>◆</i>${esc(short(j, 70))}</div>`).join("")}
    </li>`;
  };
  el.hidden = false;
  el.innerHTML = `
    <div class="tile-head"><h3>Agent 분석 흐름</h3><span class="muted">계산 ${pipe.length}번 · 판단 ${pipe.reduce((a, p) => a + p.judge.length, 0) + pipePre.length}번 · ${(total / 1000).toFixed(1)}초</span></div>
    <div class="pp">
      <div class="pp-in"><h4>쓴 자료</h4>${src.map(([n, k]) => `<div class="pp-src${k ? "" : " off"}"><b>${n}</b><span>${k ? `파일 ${k}개` : "센터 PC에서 연결"}</span></div>`).join("")}
        <div class="pp-src q"><b>질문</b><span>말한 그대로 동네·시간·대상·대수를 알아들어요</span></div></div>
      <div class="pp-core">
        <div class="pp-brain"><b>${llm ? "Claude Agent가 순서를 정해 분석" : "Agent가 정해진 순서대로 분석"}</b><span>${llm ? "Agent가 결과를 보고 다음에 무엇을 계산할지 스스로 정해요" : "인터넷 연결 없이도 Claude 모드와 같은 순서로 끝까지 분석해요"}</span></div>
        <ol class="pp-flow">${pipe.map(node).join("")}</ol>
      </div>
      <div class="pp-out"><h4>결과 화면</h4>
        ${[["지도", "map"], [`우선 개선 ${report.cards.length}곳`, "list"], ["설치 계획", "plan"], ["한눈에 보기", "charts"], ["건의서", "doc"]].map(([t, k]) => `<button class="pp-o" data-goto-pp="${k}">${t}</button>`).join("")}</div>
    </div>
    <div class="pp-guard"><span>◎ 숫자는 모두 정해진 계산식으로 구해요. AI가 숫자를 지어내지 않아요</span><span>◎ 원래 자료는 AI에 보내지 않아요</span><span>◎ 10명 미만 숫자는 가려요</span><span>◎ 인터넷이 끊겨도 끝까지 분석해요</span></div>`;
  el.querySelectorAll("[data-goto-pp]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.gotoPp)));
}


// ---------------- home: about section ----------------
function drawAbout() {
  const sm = (report && report.summary) || {}, ov = sm.overview, both = ov && ov.cats && ov.cats.both;
  if (curSource === "sample" || sm.area || (report && report.settings && report.settings.near_school)) return;  // 제주 전체 실제 자료일 때만 바꿔요
  const put = (id, n, unit) => { const el = $(id); if (el) el.innerHTML = `${n}<small>${unit}</small>`; };
  if (sm.hourly && sm.hourly.late_per_day) put("#ab-late", sm.hourly.late_per_day.toLocaleString(), "명");
  if (both) { put("#ab-blind", Math.round(both.stop_pct), "%"); put("#ab-blind-n", both.stops.toLocaleString(), "곳"); }
  if (sm.school) put("#ab-school", sm.school.blind_near.toLocaleString(), "곳");
}

// ---------------- home: scrolling, chat bubbles ----------------
function toWorkspace() { const w = $("#workspace"); if (w && !$('[data-panel="map"]').hidden) w.scrollIntoView({ behavior: STILL ? "auto" : "smooth", block: "start" }); }
$$("[data-scroll]").forEach((b) => b.addEventListener("click", () => { showTab("map", true); const t = document.getElementById(b.dataset.scroll); if (t) t.scrollIntoView({ behavior: "smooth", block: "start" }); if (b.dataset.scroll === "workspace") setTimeout(() => $("#q").focus({ preventScroll: true }), 500); }));
$$("[data-ask]").forEach((b) => b.addEventListener("click", () => { $("#q").value = b.dataset.ask; ask(false); }));
function addChat(who, text, preview) {
  const log = $("#chat-log");
  if (!log) return;
  const el = document.createElement("div");
  el.className = `msg ${who}`;
  const clean = String(text || "").replace(/\*\*/g, "").replace(/^#+\s*/gm, "");
  el.innerHTML = who === "user" ? `<p>${esc(clean)}</p>`
    : `<b>${preview ? "예시 결과 · 안심하차 Agent" : "안심하차 Agent"}</b><p>${esc(clean)}</p>`;
  log.appendChild(el);
  while (log.children.length > 16) log.removeChild(log.children[1]);
  log.scrollTop = log.scrollHeight;
}


// ---------------- big map mode: the full map like the old home ----------------
function setBigMap(on) {
  const ws = $("#workspace");
  if (!ws) return;
  ws.classList.toggle("ws-big", on);
  $("#m-big").textContent = on ? "대화 보기" : "크게";
  $("#m-big").title = on ? "대화창 다시 보기" : "지도 크게 보기";
  requestAnimationFrame(() => { placeMap(); map.redraw(); });
}
function openBigMap() {
  showTab("map", true);
  setBigMap(true);
  const w = $("#workspace");
  if (w) setTimeout(() => w.scrollIntoView({ behavior: STILL ? "auto" : "smooth", block: "start" }), 30);
}
$("#m-big").addEventListener("click", () => setBigMap(!$("#workspace").classList.contains("ws-big")));
// 첫 화면 지도를 누르면 그 자리에서 지도가 커져 지도만 보여요 (예전 홈처럼)
let heroMapOpen = false;
function openHeroMap() {
  const hero = $(".lp-hero"), stage = $(".stage");
  if (!hero || !stage || heroMapOpen) return;
  heroMapOpen = true;
  window.scrollTo({ top: 0 });
  document.body.classList.add("hero-map-open");
  hero.classList.add("opening");
  setTimeout(() => {
    hero.appendChild(stage);
    hero.classList.add("open");
    hero.style.height = Math.max(320, window.innerHeight - hero.getBoundingClientRect().top) + "px";   // 보이는 화면에 딱 맞게
    requestAnimationFrame(() => { placeMap(); map.redraw(); map.home(0); if (!matchMedia("(max-width: 600px)").matches) setTimeout(() => $("#m-in").click(), 80); });
  }, STILL ? 0 : 420);
}
function closeHeroMap() {
  const hero = $(".lp-hero"), stage = hero && hero.querySelector(".stage"), wsMap = $(".ws-map");
  if (!heroMapOpen || !stage || !wsMap) return;
  heroMapOpen = false;
  sel = { zone: null, stop: null }; drawInspect();
  wsMap.insertBefore(stage, wsMap.querySelector(".strip"));
  hero.classList.remove("open", "opening");
  hero.style.height = "";
  document.body.classList.remove("hero-map-open");
  requestAnimationFrame(() => { placeMap(); map.redraw(); map.home(0); if (mapHero) mapHero.redraw(); });
}
$("#hero-open").addEventListener("click", openHeroMap);
$("#hero-map-btn").addEventListener("click", openHeroMap);
$("#hero-close").addEventListener("click", closeHeroMap);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && heroMapOpen) closeHeroMap(); });
function drawHeroCards() {
  if (!report || curSource === "sample") return;
  const sm = report.summary || {}, both = sm.overview && sm.overview.cats && sm.overview.cats.both, c = report.cards[0];
  if (sm.area || (report.settings || {}).near_school) return;
  if (both) { $("#hc-blind").innerHTML = `${both.stops.toLocaleString()}<small>곳</small>`; $("#hc-blind-s").textContent = `이용 기록이 있는 정류장의 ${Math.round(both.stop_pct)}%`; }
  if (sm.hourly && sm.hourly.late_per_day) $("#hc-late").innerHTML = `${sm.hourly.late_per_day.toLocaleString()}<small>명</small>`;
  if (c) { $("#hc-top").textContent = c.name; $("#hc-top-s").textContent = `${typeof c.demand === "number" ? "하루 " + Math.round(c.demand).toLocaleString() + "명 · " : ""}${c.rx ? c.rx.label : ""}`; }
}
