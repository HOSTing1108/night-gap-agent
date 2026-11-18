"""안심하차 Agent: session state, tools, Claude tool loop, rule-based fallback.

The LLM decides which tool to call next and writes the final wording.
All numbers come from deterministic functions in tools.py.
Only profiles, aggregates and scores are sent to the LLM, never raw rows.
"""
from __future__ import annotations

import glob
import json
import os
import re
import threading
import time
import traceback

import geo
import intent as I
import tools as T

MODEL = os.environ.get("NIGHTGAP_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("NIGHTGAP_EFFORT", "low")
MAX_STEPS = 14

SUGGEST = {"eyes": "방범 CCTV", "bell": "안전비상벨", "light": "보안등",
           "last_bus": "막차 시간 연장 또는 수요응답형 교통"}

SYSTEM_PROMPT = """너는 제주 '안심하차' Agent야. 밤에 버스에서 내려 걸어가는 사람이 많은데
CCTV, 안전비상벨, 막차가 부족한 정류장 주변을 찾아
우선 개선할 곳을 근거와 함께 알려줘. 사용자는 IT 비전문가 도민이야.

일하는 순서
1. inspect_data로 어떤 자료가 있는지 먼저 확인해.
2. 불러온 위치 자료(cctv, bell, light)마다 check_coverage로 빠진 곳이 없는지 확인해.
   - 어떤 시에 방범 CCTV가 없거나 5%도 안 되면, 목적과 상관없이 모든 CCTV를 세도록
     run_gap_analysis의 count_all_cctv_purposes를 true로 해. 왜 그랬는지 꼭 알려줘.
   - 비상벨은 장소 유형과 상관없이 정류장과의 거리로 공백을 판단해. 장소 유형은 설명에 쓰지 않아.
3. 사용자 질문에서 지역과 시간대를 읽어. 지역이 있으면 look_up_place로 찾아봐.
   시간대가 없으면 21~23시를 써. 학생·청소년·자녀의 귀가를 묻고 inspect_data에 school 자료가 있으면 near_school을 true로 해.
4. run_gap_analysis를 실행하고 issues를 읽어.
   - THIN_DEMAND면 시간대를 20~23시로 넓히고 use_boarding_fallback을 true로 다시 실행해.
   - CONCENTRATED면 몰린 지역을 알리고, 사용자가 지역을 정하지 않았다면 그대로 두되 설명에 적어.
   - NO_DEMAND면 이용 현황이 없는 거야. 시설 공백만 본 순위(동점이면 주변 정류장이 많은 곳 우선)라고 설명에 꼭 적어.
   - inspect_data에 night_context가 있으면 제주 전체 22~24시 승차(하루 약 몇 명)를 설명에 한 번 인용해.
5. check_robustness로 순위가 기준을 바꿔도 유지되는지 검증하고, 모든 조건에서 남은 곳을 근거에 적어.
6. plan_installations로 설치 대수(질문에 '10대'처럼 있으면 그 수, 없으면 10)에 맞는 설치 위치를 계산하고, 덮는 이용자 수와 비교 결과를 설명에 한 문장으로 넣어.
7. 마지막에 finalize_report를 꼭 호출해. 정류장마다 건의 문장 한두 개를 써서 넣어.
   건의 문장에는 그 정류장의 수요(demand_kind가 night면 '밤 하차', daily면 '하루 이용(승차+환승)', 작으면 '10명 미만')와 가장 가까운 CCTV·비상벨 거리를 넣어.

질문 유형에 맞춰 답해 (질문 형식이 달라도 무엇을 묻는지 먼저 파악해)
- 인사나 사용법 질문("안녕", "뭐 할 수 있어?"): 도구를 쓰지 말고, 할 수 있는 일과 질문 예시 3~4개를 짧게 알려줘.
- 이 서비스와 관계없는 질문(날씨, 맛집 등): 도구를 쓰지 말고, 답하기 어렵다고 말한 뒤 할 수 있는 일을 안내해.
- 특정 장소가 괜찮은지("연동 정류장 안전해?", "시청에서 내리는데"): 그 장소를 area에 넣어 분석하고 look_up_place로 찾아, 그 정류장의 CCTV·비상벨 거리와 걸어서 몇 분인지, 고치는 방법을 먼저 말해. 시설이 가까우면 가깝다고 말해.
- 예산이나 대수("1억이면", "CCTV 5대"): plan_installations에 budget_manwon이나 count를 넣고, 몇 대를 어디에 달면 하루 몇 명을 지키는지 말해.
- 동네 비교("어느 동네가 제일 비어 있어?"): 결과의 지역별 비율을 높은 순으로 3곳 말하고, 시내와 비교해.
- 전체 현황("몇 곳이야?", "많아?"): CCTV·비상벨이 없는 정류장 수와 비율, 하루 이용 비율을 말해.
- 그 밖에는 먼저 손볼 곳 순위로 답해.
- 동네 이름(연동, 아라동, 한경면, 서귀포)은 정류장 이름에 없어도 area에 그대로 넣으면 그 동네 정류장만 봐.
- 시간 표현은 숫자로 바꿔: "열시"·"10시 반"·"밤 10시" → 22시, "밤늦게"·"막차" → 22~23시, "퇴근길" → 18~20시, "자정" → 23시.

지켜야 할 것
- 숫자는 도구 결과에 있는 것만 써. 새로 계산하거나 추측하지 마.
- '위험한 곳', '범죄 다발' 같은 말을 쓰지 마. 이건 기반시설이 부족한 곳을 찾는 일이야.
- 자료 점검에서 계산 방식을 바꿨다면 그 판단을 근거에 한 문장으로 밝혀.
- 질문이 너무 모호해서 진행할 수 없을 때만 되물어. 그 외에는 합리적인 기본값으로 진행해.
- 도구를 부르기 전에 쓰는 짧은 설명도 사용자 화면에 보여. 한국어 한 문장으로, 무엇을 왜 하는지 써.
- 사람 수는 천 단위 쉼표를 넣은 정수(예: 3,217명)로 쓰고, 10명 미만이면 '10명 미만'이라고 써.
- 설명은 결론 → 근거 → 제안만 써. 자료에 없는 항목(보안등 등), 자료의 부족한 점, '다만'으로 시작하는 단서는 쓰지 마.
- 순위 검증은 '보통', '흔들린다' 같은 평가 없이 모든 조건에서 남은 곳 수와 평균 유지 수만 써.
- run_gap_analysis의 demand_kind가 daily면 수요를 '하루 이용'이라고 불러. 밤 하차 자료가 없다는 말, '추정', '대신', '정류장별 값이 아니다' 같은 표현은 쓰지 말고, 이용이 많은 정류장부터 봤다고만 써.
- 마지막 답변은 한국어로 5~8문장. 별표나 목록 같은 마크다운 기호 없이 평범한 문장으로만 써. 결론(상위 몇 곳, 주된 공백 유형) → 근거 → 바로 할 수 있는 개선 제안 순서로 써."""

TOOLS = [
    {
        "name": "inspect_data",
        "description": "자료 폴더의 CSV를 모두 열어 어떤 자료인지 판별하고 불러와. 행 수, 판별 근거, 좌표 변환 결과만 돌려줘. 원자료 행은 돌려주지 않아.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "check_coverage",
        "description": "위치 자료(cctv, bell, light)가 섬 전체를 얼마나 덮는지, 목적이나 장소 유형이 한쪽에 몰렸는지 점검해.",
        "input_schema": {"type": "object", "properties": {
            "layer": {"type": "string", "enum": ["cctv", "bell", "light"]}},
            "required": ["layer"], "additionalProperties": False},
    },
    {
        "name": "look_up_place",
        "description": "정류장 이름에 지명이 들어간 정류장과 그 주변 정류장을 찾아. 분석을 이미 실행했다면 각 정류장의 공백과 순위도 함께 돌려줘.",
        "input_schema": {"type": "object", "properties": {
            "place": {"type": "string", "description": "예: 노형, 애월, 제주대"}},
            "required": ["place"], "additionalProperties": False},
    },
    {
        "name": "run_gap_analysis",
        "description": "정류장마다 이용 수요(밤 하차 또는 하루 이용)와 CCTV, 비상벨, 보안등, 막차 공백을 계산해 우선순위를 매겨. 상위 10곳 요약과 스스로 점검한 issues를 돌려줘.",
        "input_schema": {"type": "object", "properties": {
            "hours": {"type": "array", "items": {"type": "integer"}, "description": "밤 시간대(0~23). 기본 [21,22,23]"},
            "cctv_radius_m": {"type": "integer", "description": "기본 100"},
            "bell_radius_m": {"type": "integer", "description": "기본 200"},
            "count_all_cctv_purposes": {"type": "boolean", "description": "true면 교통단속 등 모든 목적의 CCTV를 셈"},
            "use_boarding_fallback": {"type": "boolean", "description": "하차 기록이 적을 때 승차 기록을 더해 수요를 보정"},
            "area": {"type": "string", "description": "특정 장소만 볼 때 그 이름. 정류장 이름(제주대, 시청), 동네 이름(연동, 아라동, 한경면), 시(서귀포) 모두 돼요. 전체면 빈 문자열"},
            "near_school": {"type": "boolean", "description": "true면 학교 반경 500m 안 정류장만 봐. 학생·청소년·자녀 귀가를 묻는 질문이고 학교 자료가 있을 때"}},
            "required": ["hours", "cctv_radius_m", "bell_radius_m", "count_all_cctv_purposes",
                         "use_boarding_fallback", "area", "near_school"], "additionalProperties": False},
    },
    {
        "name": "check_robustness",
        "description": "기준 거리(CCTV 50·150m, 비상벨 100·300m)와 시간대를 바꿔 다시 계산해, 상위 10곳이 몇 곳이나 그대로 남는지 확인해. run_gap_analysis 뒤에 호출해.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "plan_installations",
        "description": "예산(설치 대수)이 정해졌을 때 어디에 놓아야 가장 많은 이용자를 덮는지 계산해. 한 대가 반경 안의 이웃 정류장까지 함께 덮는 효과를 반영해 효과가 큰 곳부터 고르고, 이용순·먼 곳 우선과 비교한 결과를 돌려줘. run_gap_analysis 뒤에 호출해.",
        "input_schema": {"type": "object", "properties": {
            "count": {"type": "integer", "description": "설치할 대수. 기본 10"},
            "kind": {"type": "string", "enum": ["cctv", "bell"], "description": "cctv 또는 bell"},
            "budget_manwon": {"type": "integer", "description": "질문에 예산 금액이 있으면 만원 단위(1억 = 10000). 있으면 대수는 CCTV 430만원·비상벨 200만원으로 나눠 정해. 없으면 0"}},
            "required": ["count", "kind", "budget_manwon"], "additionalProperties": False},
    },
    {
        "name": "finalize_report",
        "description": "최종 결과를 확정해. 반출 기준(10 미만 값 가리기)을 적용하고 정류장별 근거 카드를 만들어. 마지막에 꼭 호출해.",
        "input_schema": {"type": "object", "properties": {
            "top_n": {"type": "integer", "description": "기본 10"},
            "headline": {"type": "string", "description": "화면 제목으로 쓸 결론 한 문장. 40자 이내로 짧게, 괄호 없이"},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "recommendations": {"type": "array", "items": {"type": "object", "properties": {
                "stop_id": {"type": "string"}, "text": {"type": "string"}},
                "required": ["stop_id", "text"], "additionalProperties": False}}},
            "required": ["top_n", "headline", "limitations", "recommendations"], "additionalProperties": False},
    },
]


class Session:
    def __init__(self, data_dir: str):
        self.lock = threading.Lock()
        self.reset(data_dir)

    def reset(self, data_dir=None):
        self.data_dir = data_dir or self.data_dir
        self.events = []
        self.busy = False
        self.files = {}
        self.stops = None
        self.demand = None
        self.demand_meta = None
        self.layers = {}       # kind -> points
        self.layer_reports = {}
        self.coverage = {}
        self.analysis = None
        self.analysis_args = None
        self.issues = []
        self.report = None
        self.messages = []     # LLM conversation (append-only)
        self.mode = None
        self._usage_path = None
        self.quality = []
        self.hourly = None
        self.daily = None
        self.daily_meta = None
        self._daily_joined = None
        self.robust = None
        self.placement = None
        self._town_of = None

    # ---- places: 동·읍·면 and the two cities ----------------------------------
    def towns(self):
        out = set()
        for kind in ("cctv", "bell", "school"):
            for p in self.layers.get(kind, []):
                if p.get("town"):
                    out.add(p["town"])
        return out

    def stops_in_town(self, name):
        """Stops whose 동·읍·면 (or city) matches a place name such as '연동', '아라동', '한경', '서귀포'."""
        if not self.stops:
            return []
        q = re.sub(r"\s|쪽|시내|일대", "", name)
        if q in ("제주시", "서귀포", "서귀포시"):
            city = "서귀포시" if q.startswith("서귀포") else "제주시"
            return [x for x in self.stops if T.city_of(x["lat"], x["lon"]) == city]
        if self._town_of is None:
            refs = self.layers.get("cctv", []) + self.layers.get("bell", [])
            self._town_of = T.stop_towns(self.stops, refs) if len(refs) >= 30 else {}
        base = re.sub(r"(동|읍|면)$", "", q) if len(q) >= 3 else q
        def hit(t):
            if not t:
                return False
            if t == q:
                return True
            tb = re.sub(r"[0-9]?(동|읍|면)$", "", t)
            return tb == base
        return [x for x in self.stops if hit(self._town_of.get(x["id"]))]

    # ---- events for the UI -------------------------------------------------
    def emit(self, kind, text, data=None):
        with self.lock:
            self.events.append({"i": len(self.events), "t": round(time.time(), 2),
                                "type": kind, "text": text, "data": data})

    # ---- tools -------------------------------------------------------------
    def tool_inspect_data(self):
        dirs = self.data_dir if isinstance(self.data_dir, (list, tuple)) else [self.data_dir]
        paths = sorted(p for d in dirs for p in glob.glob(os.path.join(d, "*.csv")))
        if not paths:
            return {"error": "자료 폴더에 CSV가 없어요: " + ", ".join(os.path.basename(d) for d in dirs)}
        out = []
        usage_path = None
        for p in paths:
            prof = T.profile_csv(p)
            ident = T.identify_dataset(prof)
            self.files[p] = {"profile": prof, "ident": ident}
            item = {"file": prof["file"], "rows": prof["rows"], "encoding": prof["encoding"],
                    "kind": ident["kind"], "confidence": ident["confidence"], "reasons": ident["reasons"]}
            kind = ident["kind"]
            q = {"file": prof["file"], "kind": kind, "rows": prof["rows"]}
            if kind == "bus_stops":
                stops = T.load_stops(p, ident)
                self.stops = _merge_stops(self.stops, stops)
                item["loaded_stops"] = len(stops)
                q.update(loaded=len(stops), dropped=max(0, prof["rows"] - len(stops)))
            elif kind == "bus_usage":
                usage_path = p
            elif kind == "bus_daily_usage":
                self.daily, self.daily_meta = T.daily_usage(p)
                item["daily_usage"] = {k: v for k, v in self.daily_meta.items() if k != "by_name"}
                q.update(stations=self.daily_meta["stations"], days=self.daily_meta["days"])
            elif kind == "bus_hourly_total":
                self.hourly = T.hourly_share(p)
                item["night_context"] = {k: self.hourly[k] for k in ("from", "to", "night_share", "late_share", "late_per_day")}
            elif kind in ("cctv", "bell", "streetlight", "school"):
                key = "light" if kind == "streetlight" else kind
                pts, rep = T.load_points(p, ident["coords"], kind)
                before = len(self.layers.get(key, []))
                self.layers[key] = _merge_points(self.layers.get(key, []), pts, kind == "cctv")
                rep["cross_file_dupes"] = before + len(pts) - len(self.layers[key])
                self.layer_reports[key] = rep
                item["load_report"] = {k: v for k, v in rep.items() if k != "purpose_counts"}
                q.update(loaded=rep.get("loaded"), no_coord=rep.get("skipped_no_coord"),
                         outside=rep.get("outside_jeju"), dupes=rep.get("duplicates_merged"), merged=kind == "cctv",
                         cross=rep.get("cross_file_dupes"))
            elif kind in ("grid_pop", "dong_pop"):
                item["note"] = "유동인구 자료예요. 이번 버전은 정류장 분석에 쓰지 않고 참고로만 표시해요."
            out.append(item)
            if kind in ("bus_stops", "cctv", "bell", "streetlight", "school", "bus_usage", "bus_hourly_total", "bus_daily_usage"):
                self.quality.append(q)
        if usage_path:
            self._usage_path = usage_path
            self.demand, self.demand_meta = T.night_demand(usage_path)
        missing = [] if self.stops else ["버스 정류소 정보(3-03 또는 공공데이터포털 정류소 현황)"]
        return {"files": out, "layers_loaded": {k: len(v) for k, v in self.layers.items()},
                "missing_required": missing,
                "demand": "있음(밤 하차)" if usage_path else ("정류장별 하루 이용(승차+환승)만 있음" if self.daily else "없음(이용 현황이 없어 시설 공백만으로 순위를 매겨요)"),
                "light_layer": "있음" if "light" in self.layers else "없음(빛 공백은 계산하지 않음)",
                "alight_share": (self.demand_meta or {}).get("alight_share")}

    def tool_check_coverage(self, layer):
        pts = self.layers.get(layer)
        if pts is None:
            return {"layer": layer, "error": "이 자료는 불러오지 않았어요"}
        rep = T.check_coverage(pts, layer, self.stops)
        self.coverage[layer] = rep
        return rep

    def tool_run_gap_analysis(self, hours=(21, 22, 23), cctv_radius_m=100, bell_radius_m=200,
                              count_all_cctv_purposes=False, use_boarding_fallback=False, area="", near_school=False):
        if not self.stops:
            return {"error": "버스 정류소 자료가 있어야 해요. inspect_data를 먼저 실행해요."}
        hours = sorted({int(h) for h in hours if 0 <= int(h) <= 23}) or [21, 22, 23]
        if self._usage_path and (tuple(hours) != tuple(self.demand_meta["hours"])
                                 or use_boarding_fallback != self.demand_meta["boarding_fallback"]):
            self.demand, self.demand_meta = T.night_demand(self._usage_path, hours, use_boarding_fallback)
        stops = self.stops
        area_note = None
        if area:
            town = self.stops_in_town(area)
            seeds, near = ([], []) if town else T.find_stops(self.stops, area, radius_m=1500)
            if town:
                stops = town
                area_note = f"'{area}' 동네 정류장 {len(town)}곳만 봤어요"
            elif near:
                stops = near
                area_note = f"'{area}'가 이름에 들어간 정류장 {len(seeds)}곳과 주변 1.5km 정류장 {len(near)}곳만 봤어요"
            else:
                area_note = f"'{area}'라는 곳을 찾지 못해 제주 전체를 봤어요"
        if near_school and self.layers.get("school"):
            sidx = geo.PointIndex(((p["lat"], p["lon"], p) for p in self.layers["school"]), cell_m=500)
            before = len(stops)
            stops = [x for x in stops if sidx.within(x["lat"], x["lon"], 500)]
            note = f"학교 반경 500m 안 정류장 {len(stops):,}곳(전체 {before:,}곳 중)만 봤어요"
            area_note = (area_note + " / " + note) if area_note else note
        opts = {"cctv_radius_m": cctv_radius_m, "bell_radius_m": bell_radius_m}
        if count_all_cctv_purposes:
            opts["cctv_purposes"] = []
        demand = self.demand if self._usage_path else self.daily
        if demand is self.daily and self.daily:
            ids = {s["id"] for s in self.stops}
            if sum(1 for k in self.daily if k in ids) < 0.3 * len(self.daily):   # different id systems: join on names
                demand, self.daily_meta["matched_pct"] = T.match_usage_by_name(self.stops, self.daily_meta["by_name"])
                self.daily_meta["joined_by"] = "name"
                for q in self.quality:
                    if q["kind"] == "bus_daily_usage":
                        q["matched_pct"] = self.daily_meta["matched_pct"]
                self._daily_joined = demand
        res = T.compute_gaps(stops, demand, self.layers.get("cctv"), self.layers.get("bell"),
                             self.layers.get("light"), opts)
        res["demand_kind"] = "night" if self._usage_path else ("daily" if self.daily else None)
        refs = self.layers.get("cctv", []) + self.layers.get("bell", [])
        labels = T.area_labels(self.stops, refs)
        per_stop = T.stop_towns(res["rows"], refs)
        for r in res["rows"]:
            r["area"] = per_stop.get(r["id"]) or labels.get(T.area_key(r["lat"], r["lon"]), "?")
        town_of = lambda r: r.get("area") or "?"  # noqa: E731
        self.analysis = res
        self.robust = None
        self._analysis_stops = stops
        self.analysis_args = {"hours": hours, "cctv_radius_m": cctv_radius_m, "bell_radius_m": bell_radius_m,
                              "count_all_cctv_purposes": count_all_cctv_purposes,
                              "use_boarding_fallback": use_boarding_fallback, "area": area,
                              "near_school": bool(near_school and self.layers.get("school"))}
        self.issues = T.diagnose(res, town_of=None if area_note and "찾지 못해" not in area_note else town_of)
        top = self._safe_rows(res["rows"][:10])
        gap_counts = {}
        for r in res["rows"][:10]:
            for g in r["gaps"]:
                gap_counts[T.GAP_LABEL[g]] = gap_counts.get(T.GAP_LABEL[g], 0) + 1
        self.emit("map", "지도를 갱신했어요", self.map_payload())
        return {"settings": self.analysis_args, "area_note": area_note, "stops_scored": len(res["rows"]), "demand_kind": res.get("demand_kind"),
                "demand_days": (self.demand_meta or {}).get("days"), "facility_only": res["facility_only"], "top10": top, "top10_gap_counts": gap_counts,
                "issues": self.issues}

    def tool_check_robustness(self):
        """Re-rank under nearby settings and count how many of the top 10 stay. Numbers only, no raw rows."""
        if not self.analysis:
            return {"error": "run_gap_analysis를 먼저 실행해야 해요"}
        a = self.analysis_args
        base = [r["id"] for r in _top_places(self.analysis["rows"], 10) if r["score"] > 0]
        opts0 = {"cctv_radius_m": a["cctv_radius_m"], "bell_radius_m": a["bell_radius_m"]}
        if a["count_all_cctv_purposes"]:
            opts0["cctv_purposes"] = []
        variants = [("CCTV 기준 50m", {"cctv_radius_m": 50}, None), ("CCTV 기준 150m", {"cctv_radius_m": 150}, None),
                    ("비상벨 기준 100m", {"bell_radius_m": 100}, None), ("비상벨 기준 300m", {"bell_radius_m": 300}, None)]
        if self._usage_path:
            h = a["hours"]
            wide = sorted(set([max(0, h[0] - 1)] + list(h)))
            narrow = h[1:] if len(h) > 1 else h
            variants += [(f"시간대 {wide[0]}~{wide[-1]}시", {}, wide), (f"시간대 {narrow[0]}~{narrow[-1]}시", {}, narrow)]
        stay = {sid: 0 for sid in base}
        by_id = {r["id"]: r for r in self.analysis["rows"]}
        out = []
        for label, ov, hrs in variants:
            dem = T.night_demand(self._usage_path, hrs, a["use_boarding_fallback"])[0] if hrs else self.demand
            if not self._usage_path:
                dem = getattr(self, "_daily_joined", None) or self.daily
            res = T.compute_gaps(self._analysis_stops, dem, self.layers.get("cctv"), self.layers.get("bell"),
                                 self.layers.get("light"), {**opts0, **ov})
            top = {r["id"] for r in _top_places(res["rows"], 10) if r["score"] > 0}
            names = {(r["name"], round(r["lat"], 2), round(r["lon"], 2)) for r in _top_places(res["rows"], 10) if r["score"] > 0}
            kept = [sid for sid in base if sid in top or _place_key(by_id[sid]) in names]
            for sid in kept:
                stay[sid] += 1
            out.append({"setting": label, "kept": len(kept), "of": len(base)})
        n = len(variants)
        stable = [sid for sid in base if stay[sid] == n]
        mostly = [sid for sid in base if stay[sid] >= n - 1]
        avg = sum(v["kept"] for v in out) / max(1, n)
        verdict = ("튼튼해요" if avg >= 0.8 * len(base) else "보통이에요" if avg >= 0.6 * len(base) else "흔들려요")
        self.robust = {"variants": out, "n": n, "base": len(base), "stable": len(stable), "mostly": len(mostly),
                       "avg_kept": round(avg, 1), "verdict": verdict, "per_stop": stay}
        return {k: v for k, v in self.robust.items() if k != "per_stop"}

    def tool_look_up_place(self, place):
        if not self.stops:
            return {"error": "정류장 자료를 먼저 불러와야 해요"}
        seeds, near = T.find_stops(self.stops, place)
        if not seeds:
            town = self.stops_in_town(place)
            seeds, near = town, town
        if not seeds:
            return {"place": place, "found": 0, "hint": "정류장 이름이나 동네 이름에 이 지명이 없어요. 다른 지명이나 큰 지역명으로 다시 찾아봐요."}
        out = {"place": place, "matched_stops": [s["name"] for s in seeds[:8]], "nearby_count": len(near)}
        if self.analysis:
            by_id = {r["id"]: r for r in self.analysis["rows"]}
            rows = sorted((by_id[s["id"]] for s in near if s["id"] in by_id), key=lambda r: r["rank"])
            out["nearby_ranked"] = self._safe_rows(rows[:8])
            out["total_ranked"] = len(self.analysis["rows"])
        return out

    def tool_plan_installations(self, count=10, kind="cctv", budget_manwon=0):
        if not self.analysis:
            return {"error": "run_gap_analysis를 먼저 실행해야 해요"}
        unit = 200 if kind == "bell" else 430
        if budget_manwon and int(budget_manwon) > 0:
            count = int(budget_manwon) // unit
        count = max(1, min(int(count or 10), 30))
        gap, rad = ("bell", self.analysis_args["bell_radius_m"]) if kind == "bell" else ("eyes", self.analysis_args["cctv_radius_m"])
        rows = self.analysis["rows"]
        plan = T.plan_placement(rows, count, rad, gap)
        bench = T.placement_benchmark(rows, count, rad, gap)
        self.placement = self.placement or {}
        self.placement[kind] = {"count": count, "plan": plan, "bench": bench}
        mask = lambda v: "10 미만" if 0 < v < 10 else v  # noqa: E731
        last = plan["picks"][-1] if plan["picks"] else {"cum": 0, "cum_pct": 0}
        return {"kind": kind, "count": len(plan["picks"]), "covered_users": last["cum"], "covered_pct": last["cum_pct"],
                "gap_users": plan["gap_users"],
                "picks": [{"n": p["n"], "name": p["name"], "covers_users": mask(p["gain"]), "covers_stops": p["covered_stops"]}
                          for p in plan["picks"]],
                "compare": {"this_plan": bench["greedy"], "busiest_first": bench["demand_first"],
                            "farthest_from_facility_first": bench["farthest_first"], "random_average": bench["random_avg"]}}

    def tool_finalize_report(self, top_n=10, headline="", limitations=(), recommendations=()):
        if not self.analysis:
            return {"error": "run_gap_analysis를 먼저 실행해야 해요"}
        top_n = max(1, min(int(top_n or 10), 30))
        rows = _top_places(self.analysis["rows"], top_n)
        masked, log = T.export_check(rows)
        rec = {r["stop_id"]: r["text"] for r in (recommendations or [])}
        hours = self.analysis_args["hours"]
        cards = []
        fac_only = self.analysis.get("facility_only")
        daily = self.analysis.get("demand_kind") == "daily"
        if not self.robust:
            self.tool_check_robustness()
        stay = (self.robust or {}).get("per_stop", {})
        rad = (self.analysis_args["cctv_radius_m"], self.analysis_args["bell_radius_m"])
        by_area = {}
        for r in self.analysis["rows"]:
            by_area.setdefault(r.get("area") or "?", []).append(r["id"])
        for i, (r, m) in enumerate(zip(rows, masked), 1):
            what = "하차+승차(보정)" if self.analysis_args["use_boarding_fallback"] else "하차"
            if fac_only:
                ev = ["밤 하차 수요: 자료 없음 (시설 공백만으로 순위를 매겼어요)"]
                if "stops_500m" in r:
                    ev.append(f"반경 500m 안 정류장: {r['stops_500m']}곳 (정류장이 몰린 곳을 앞에 뒀어요)")
            elif daily:
                nd = self.daily_meta['days']
                ev = [f"하루 {'평균 ' if nd >= 7 else ''}이용(승차+환승{f', {nd}일 평균' if nd >= 7 else ''}): {_people(m['night_demand'])} "
                      f"({'이번에 본' if self.analysis_args.get('area') or self.analysis_args.get('near_school') else '제주'} 정류장 중 상위 {max(1, round(100 - r['demand_pct']))}%)"]
            else:
                ev = [f"밤 {hours[0]}~{hours[-1]}시 {what} 하루 평균: {_people(m['night_demand'])} "
                      f"(제주 정류장 중 상위 {max(1, round(100 - r['demand_pct']))}%)"]
            if "cctv_nearest_m" in r:
                ev.append(f"가장 가까운 CCTV: {_dist(r['cctv_nearest_m'])} ({self.analysis_args['cctv_radius_m']}m 안에는 {r['cctv_n']}곳)")
            if "bell_nearest_m" in r:
                ev.append(f"가장 가까운 비상벨: {_dist(r['bell_nearest_m'])} ({self.analysis_args['bell_radius_m']}m 안에는 {r['bell_n']}곳)")
            if r["last_hour"] >= 0:
                ev.append(f"마지막 이용 기록 시각: {r['last_hour']}시대")
            sch = self._school_near(r["lat"], r["lon"])
            if sch and sch["n500"]:
                ev.append(f"500m 안에 학교가 {sch['n500']}곳 있어요 (가장 가까운 곳: {sch['name']} {sch['m']:,}m). 학생들이 다니는 길이에요")
            if r["id"] in stay:
                ev.append(f"기준을 바꿔 {self.robust['n']}번 다시 계산해도 {stay[r['id']]}번 10위 안에 들어요")
            text = rec.get(r["id"]) or _template_text(r, m, hours, self.analysis_args, daily)
            cards.append({"rank": i, "id": r["id"], "name": r["name"], "area": r.get("area"),
                          "lat": r["lat"], "lon": r["lon"], "gaps": r["gap_labels"], "gap_codes": r["gaps"],
                          "score": r["score"], "demand": m["night_demand"], "demand_pct": r["demand_pct"],
                          "cctv_m": r.get("cctv_nearest_m"), "bell_m": r.get("bell_nearest_m"),
                          "last_hour": r["last_hour"], "evidence": ev, "recommendation": text,
                          "stay": stay.get(r["id"]), "stay_of": (self.robust or {}).get("n"),
                          "rx": T.prescribe(r, *rad), "near": self._near_points(r["lat"], r["lon"], 300),
                          "area_rank": (by_area.get(r.get("area") or "?", []).index(r["id"]) + 1) if r["id"] in by_area.get(r.get("area") or "?", []) else None,
                          "area_n": len(by_area.get(r.get("area") or "?", [])),
                          "light": r.get("light_n"), "school": sch})
        self._enrich_cards(cards)
        lim = list(limitations or [])

        def add(text, key):
            if not any(key in x for x in lim):
                lim.append(text)
        for layer, rep in self.coverage.items():
            for w in rep.get("warnings", []):
                key = {"NO_CRIME_CCTV": "방범", "ONE_PLACE_TYPE": w.get("place") or "장소"}.get(w["code"], w["detail"][:12])
                add(w["detail"], key)
        if "light" not in self.layers:
            add("보안등 위치 자료가 없어 빛 공백은 계산하지 않았어요.", "보안등")
        if self.hourly and self.hourly.get("late_share") is not None:
            add(f"제주 전체 버스 승차 중 22~24시는 {self.hourly['late_share']}%(하루 약 {self.hourly['late_per_day']:,}명), 19~24시는 {self.hourly['night_share']}%예요"
                f"({self.hourly['from']}~{self.hourly['to']}, 제주데이터허브). 정류장별 밤 하차가 아니라 섬 전체 승차 비중이에요.", "22~24시")
        if daily and self.daily_meta.get("joined_by") == "name":
            add(f"이용 자료와 정류장 위치 자료의 아이디 체계가 달라 정류장 이름으로 연결했어요. 이용 인원의 {self.daily_meta['matched_pct']}%가 연결됐고, "
                "같은 이름의 정류장(양방향 등)에는 똑같이 나눠 넣었어요.", "이름으로 연결")
        if daily:
            add(f"정류장별 이용 인원은 제주데이터허브 API의 하루 승차+환승 합계({self.daily_meta['days']}일 평균)예요. "
                "시간대와 하차가 없어 밤 하차 수요가 아니라 정류장의 하루 이용 규모로 순위를 매겼어요. 센터 3-04를 연결하면 밤 하차로 바뀌어요.", "승차+환승")
        if fac_only:
            add("버스 이용 현황(3-04)이 없어 밤에 내리는 사람 수를 반영하지 못했어요. 시설 공백만 본 순위예요.", "이용 현황")
        if self.robust and self.robust["verdict"] == "흔들려요":
            add(f"기준 거리나 시간대를 조금 바꾸면 상위 10곳 중 평균 {self.robust['avg_kept']}곳만 남아요. 순위보다 공백 유형을 먼저 보세요.", "기준 거리")
        if self.demand_meta and self.demand_meta["alight_share"] < 0.45:
            lim.append(f"제주 버스는 하차 태그가 의무가 아니라 하차 기록이 전체의 {self.demand_meta['alight_share']*100:.0f}%뿐이에요. 실제 밤 하차는 더 많을 수 있어요.")
        gap_counts = {}
        for c in cards:
            for g in c["gap_codes"]:
                gap_counts[g] = gap_counts.get(g, 0) + 1
        summary = {"stops_scored": len(self.analysis["rows"]), "hours": hours,
                   "area": self.analysis_args.get("area") or "", "gap_counts": gap_counts,
                   "boarding_fallback": self.analysis_args["use_boarding_fallback"],
                   "facility_only": bool(fac_only),
                   "demand_kind": self.analysis.get("demand_kind"),
                   "daily_days": (self.daily_meta or {}).get("days") if daily else None,
                   "hourly": self.hourly,
                   "overview": T.overview(self.analysis["rows"]),
                   "placement": self._placement_summary(),
                   "rx_counts": self._rx_counts(),
                   "school": self._school_stats(),
                   "game": self._game_data(),
                   "median_demand": self._median_demand(),
                   "radii": {"cctv": self.analysis_args["cctv_radius_m"], "bell": self.analysis_args["bell_radius_m"]}}
        area_h = self.analysis_args.get("area") or ""
        school_h = "학교 근처 " if self.analysis_args.get("near_school") else ""
        self.report = {"headline": headline or (f"'{area_h}' " if area_h else "") + f"{school_h}먼저 손볼 정류장 {len(cards)}곳", "cards": cards,
                       "summary": summary,
                       "limitations": lim, "export_log": log, "settings": self.analysis_args,
                       "robustness": {k: v for k, v in (self.robust or {}).items() if k != "per_stop"},
                       "quality": self.quality,
                       "mode": self.mode}
        self.emit("report", "결과 카드를 만들었어요", self.report)
        return {"ok": True, "cards": len(cards), "masked_values": len(log), "limitations": lim}

    def _near_points(self, lat, lon, radius_m=300):
        """CCTV and bell points around a stop as metre offsets (east, north) for the card diagram."""
        out = []
        k = 111_320.0
        for kind in ("cctv", "bell", "light", "school"):
            for p in self.layers.get(kind, []):
                dy = (p["lat"] - lat) * k
                dx = (p["lon"] - lon) * k * 0.8348   # cos(33.4°)
                if abs(dx) <= radius_m and abs(dy) <= radius_m and dx * dx + dy * dy <= radius_m * radius_m:
                    out.append([kind, round(dx), round(dy)])
        return out[:120]

    def _school_near(self, lat, lon, radius_m=500):
        """Schools around a stop: count within radius and the nearest one (name, level, metres)."""
        sch = self.layers.get("school")
        if not sch:
            return None
        best, n = None, 0
        for p in sch:
            d = geo.haversine(lat, lon, p["lat"], p["lon"])
            if d <= radius_m:
                n += 1
            if best is None or d < best[0]:
                best = (d, p)
        return {"n500": n, "name": best[1].get("name") or "", "level": best[1].get("purpose") or "", "m": round(best[0])}

    def _school_stats(self):
        if not self.layers.get("school"):
            return None
        idx = geo.PointIndex(((p["lat"], p["lon"], p) for p in self.layers["school"]), cell_m=500)
        rows = [r for r in self.analysis["rows"] if (r["night_demand"] is None or r["night_demand"] > 0)]
        near = [r for r in rows if idx.within(r["lat"], r["lon"], 500)]
        blind = [r for r in near if "eyes" in r["gaps"] and "bell" in r["gaps"]]
        use = lambda xs: round(sum(r["night_demand"] or 0 for r in xs))  # noqa: E731
        return {"schools": len(self.layers["school"]), "stops_near": len(near), "blind_near": len(blind),
                "blind_near_use": use(blind), "near_use": use(near)}

    def _median_demand(self):
        v = sorted(r["night_demand"] for r in self.analysis["rows"] if r["night_demand"])
        return round(v[len(v) // 2], 1) if v else None

    def _enrich_cards(self, cards):
        """Per-card context: area gap share, median comparison, place in the install plan, neighbours solved together."""
        rows = self.analysis["rows"]
        med = self._median_demand()
        area = {}
        for r in rows:
            if r["night_demand"] is None or r["night_demand"] > 0:
                a = area.setdefault(r.get("area") or "?", [0, 0])
                a[0] += 1
                a[1] += "eyes" in r["gaps"] and "bell" in r["gaps"]
        rad = self.analysis_args["cctv_radius_m"]
        plan = T.plan_placement(rows, 30, rad, "eyes") if not self.analysis.get("facility_only") else {"picks": []}
        order = {p["id"]: p for p in plan["picks"]}
        cand, cover, _ = T._cover_map(rows, rad, "eyes")
        for c in cards:
            a = area.get(c.get("area") or "?")
            c["area_blind"] = {"stops": a[0], "blind": a[1]} if a else None
            c["x_median"] = round(c["demand"] / med, 1) if isinstance(c["demand"], (int, float)) and med else None
            p = order.get(c["id"])
            c["plan_n"] = p["n"] if p else None
            nb = cover.get(c["id"], [])
            c["together"] = {"stops": len(nb), "users": round(sum(x["night_demand"] for x in nb))} if nb else None

    def _game_data(self):
        """Candidates for the budget game: the stops worth acting on, each with the gap stops one action there would cover."""
        if self.analysis.get("facility_only"):
            return None
        rows = self.analysis["rows"]
        rc, rb = self.analysis_args["cctv_radius_m"], self.analysis_args["bell_radius_m"]
        _, cov_e, tot_e = T._cover_map(rows, rc, "eyes")
        _, cov_b, tot_b = T._cover_map(rows, rb, "bell")
        ids = []
        for gap, rad in (("eyes", rc), ("bell", rb)):
            for p in T.plan_placement(rows, 30, rad, gap)["picks"]:
                if p["id"] not in ids:
                    ids.append(p["id"])
        for r in _top_places(rows, 10):
            if r["id"] not in ids:
                ids.append(r["id"])
        byid = {r["id"]: r for r in rows}
        sidx = geo.PointIndex(((p["lat"], p["lon"], p) for p in self.layers.get("school", [])), cell_m=500) if self.layers.get("school") else None
        demand, cands = {}, []
        for i in ids:
            r = byid[i]
            ce = [x["id"] for x in cov_e.get(i, [])]
            cb = [x["id"] for x in cov_b.get(i, [])]
            for x in cov_e.get(i, []) + cov_b.get(i, []):
                demand[x["id"]] = round(x["night_demand"], 1)
            opts = []
            if ce:
                near = r.get("cctv_nearest_m")
                if near is not None and near <= 2 * rc:
                    opts.append("adjust_cctv")
                opts.append("new_cctv")
            if cb:
                opts.append("bell")
            if not opts:
                continue
            cands.append({"id": i, "name": r["name"], "area": r.get("area"), "lat": round(r["lat"], 5), "lon": round(r["lon"], 5),
                          "demand": round(r["night_demand"] or 0), "gaps": r["gaps"], "cctv_m": r.get("cctv_nearest_m"),
                          "bell_m": r.get("bell_nearest_m"), "school": bool(sidx and sidx.within(r["lat"], r["lon"], 500)),
                          "opts": opts, "ce": ce, "cb": cb})
        return {"candidates": cands, "demand": demand, "eyes_total": round(tot_e), "bell_total": round(tot_b),
                "costs": {"new_cctv": 430, "adjust_cctv": 50, "bell": 200},
                "cost_note": "CCTV 신규 430만원은 2024년 지자체 CCTV 설치 집계(2,824억원·65,296대)로 나눈 대당 금액이에요. 방향 조정·비상벨은 예시 단가라 직접 바꿀 수 있어요."}

    def _rx_counts(self):
        rad = (self.analysis_args["cctv_radius_m"], self.analysis_args["bell_radius_m"])
        out = {}
        for r in self.analysis["rows"]:
            if r["gaps"] and (r["night_demand"] is None or r["night_demand"] > 0):
                c = T.prescribe(r, *rad)["code"]
                out[c] = out.get(c, 0) + 1
        return out

    def _placement_summary(self):
        """Coverage curve for 1..30 devices (CCTV and bell) plus a same-budget comparison, for the simulator."""
        if self.analysis.get("facility_only"):
            return None
        rows = self.analysis["rows"]
        out = {}
        mask = lambda v: "10 미만" if 0 < v < 10 else v  # noqa: E731
        for kind, gap, rad in (("cctv", "eyes", self.analysis_args["cctv_radius_m"]), ("bell", "bell", self.analysis_args["bell_radius_m"])):
            if not any(gap in r["gaps"] for r in rows):
                continue
            plan = T.plan_placement(rows, 30, rad, gap)
            req = (self.placement or {}).get(kind, {}).get("count", 10)
            out[kind] = {"radius_m": rad, "gap_users": plan["gap_users"], "candidates": plan["candidates"], "requested": req,
                         "picks": [{**{k: p[k] for k in ("n", "id", "name", "lat", "lon", "area", "covered_stops", "cum", "cum_pct")},
                                    "gain": mask(p["gain"])} for p in plan["picks"]],
                         "bench": T.placement_benchmark(rows, req, rad, gap)}
        return out

    def _safe_rows(self, rows):
        masked, _ = T.export_check(rows)
        keep = ("rank", "id", "name", "area", "night_demand", "demand_pct", "last_hour", "cctv_n",
                "cctv_nearest_m", "bell_n", "bell_nearest_m", "light_n", "gap_labels", "score")
        return [{k: m[k] for k in keep if k in m} for m in masked]

    def map_payload(self):
        """Everything the 3D map needs: stops with their gaps, facility points, and ~2km zones.

        Demand below 10 is masked here too, so the screen never holds small counts.
        """
        rows = (self.analysis or {}).get("rows", [])

        def mask(v):
            if v is None:
                return None
            return "10 미만" if 0 < v < 10 else round(v, 1)

        zkey = lambda lat, lon: "%d_%d" % T.area_key(lat, lon)  # noqa: E731
        labels = T.area_labels(self.stops or [], self.layers.get("cctv", []) + self.layers.get("bell", []))
        zones = {}
        stops = []
        for r in rows:
            k = zkey(r["lat"], r["lon"])
            z = zones.setdefault(k, {"key": k, "label": labels.get(T.area_key(r["lat"], r["lon"]), "?"),
                                     "lat": 0.0, "lon": 0.0, "stops": 0, "demand": 0.0, "cctv": 0, "bell": 0,
                                     "eyes_gap": 0, "bell_gap": 0, "late_gap": 0, "gap_stops": 0, "score": 0.0})
            z["lat"] += r["lat"]; z["lon"] += r["lon"]; z["stops"] += 1  # noqa: E702
            z["demand"] += r["night_demand"] or 0; z["score"] += r["score"]  # noqa: E702
            z["eyes_gap"] += "eyes" in r["gaps"]; z["bell_gap"] += "bell" in r["gaps"]  # noqa: E702
            z["late_gap"] += "last_bus" in r["gaps"]
            z["gap_stops"] += bool(r["gaps"]) and (r["night_demand"] is None or r["night_demand"] > 0)
            stops.append({"id": r["id"], "name": r["name"], "lat": round(r["lat"], 5), "lon": round(r["lon"], 5),
                          "score": r["score"], "rank": r["rank"], "demand": mask(r["night_demand"]),
                          "cn": r.get("cctv_n"), "cm": r.get("cctv_nearest_m"), "bn": r.get("bell_n"),
                          "bm": r.get("bell_nearest_m"), "gaps": r["gaps"], "last": r["last_hour"], "z": k})
        for kind in ("cctv", "bell"):
            for p in self.layers.get(kind, []):
                z = zones.get(zkey(p["lat"], p["lon"]))
                if z:
                    z[kind] += 1
        zlist = []
        for z in zones.values():
            z["lat"] = round(z["lat"] / z["stops"], 5); z["lon"] = round(z["lon"] / z["stops"], 5)  # noqa: E702
            z["demand"] = None if (self.analysis or {}).get("facility_only") else mask(z["demand"])
            z["score"] = round(z["score"], 2)
            zlist.append(z)
        zlist.sort(key=lambda z: -z["score"])
        return {
            "stops": stops, "zones": zlist,
            "cctv": [[round(p["lat"], 5), round(p["lon"], 5), p.get("purpose") or ""] for p in self.layers.get("cctv", [])],
            "bell": [[round(p["lat"], 5), round(p["lon"], 5)] for p in self.layers.get("bell", [])],
            "radii": {"cctv": (self.analysis_args or {}).get("cctv_radius_m", 100),
                      "bell": (self.analysis_args or {}).get("bell_radius_m", 200)},
        }

    def call_tool(self, name, args):
        fn = getattr(self, f"tool_{name}", None)
        if fn is None:
            return {"error": f"알 수 없는 도구: {name}"}
        t0 = time.perf_counter()
        out = fn(**(args or {}))
        self.last_ms = round((time.perf_counter() - t0) * 1000)
        return out


def _fmt(v):
    return v if isinstance(v, str) else f"{v:.1f}"


def _people(v):
    """'10명 미만' for masked values, '12.3명' otherwise."""
    if v is None:
        return "자료 없음"
    if isinstance(v, str):
        return "10명 미만"
    return f"{int(v + 0.5):,}명" if v >= 100 else f"{v:.1f}명"


def _place_key(r):
    return (r["name"], round(r["lat"], 2), round(r["lon"], 2))


def _top_places(rows, n):
    """Top n rows, one per place: the two directions of a stop share a name and sit a few metres apart."""
    out = []
    for r in rows:
        if any(c["name"] == r["name"] and abs(c["lat"] - r["lat"]) < 0.003 and abs(c["lon"] - r["lon"]) < 0.003 for c in out):
            continue
        out.append(r)
        if len(out) >= n:
            break
    return out


def _merge_points(old, new, dedupe):
    """Join point files of one kind. CCTV sites listed in two files (within ~10 m) count once."""
    if not dedupe or not old:
        return old + new
    seen = {(round(p["lat"], 4), round(p["lon"], 4)) for p in old}
    return old + [p for p in new if (round(p["lat"], 4), round(p["lon"], 4)) not in seen]


def _merge_stops(old, new):
    """Several stop files (e.g. center 3-03 and the portal list) -> one list, first id wins."""
    if not old:
        return new
    seen = {s["id"] for s in old}
    return old + [s for s in new if s["id"] not in seen]


def _iyeyo(word):
    ch = word[-1] if word else ""
    return "이에요" if "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 else "예요"


def _dist(m):
    return "주변 5km 안에 없음" if m is None else f"{m:,}m"


def _template_text(r, m, hours, args, daily=False):
    if not r["gaps"]:
        return f"{r['name']} 주변은 이번 기준에서 공백이 확인되지 않았어요."
    verb = "타고 내리지만" if args.get("use_boarding_fallback") else "내리지만"
    lacks = []
    if "eyes" in r["gaps"]:
        lacks.append(f"반경 {args['cctv_radius_m']}m 안에 CCTV가")
    if "bell" in r["gaps"]:
        lacks.append(f"반경 {args['bell_radius_m']}m 안에 비상벨이")
    if "light" in r["gaps"]:
        lacks.append("반경 50m 안에 보안등이")
    late = f"마지막 이용 기록이 {r['last_hour']}시대로 이른 편입니다" if "last_bus" in r["gaps"] else ""
    if lacks and late:
        fact = ", ".join(lacks) + f" 없고, {late}"
    elif lacks:
        fact = ", ".join(lacks) + " 없습니다"
    else:
        fact = late
    asks = []
    fac = [SUGGEST[g] for g in r["gaps"] if g != "last_bus"]
    if fac:
        asks.append(f"{', '.join(fac)} 설치를 검토해 주시기 바랍니다")
    if "last_bus" in r["gaps"]:
        asks.append("막차 시간 연장이나 수요응답형 교통 도입도 함께 검토해 주시기 바랍니다")
    if m["night_demand"] is None:
        return f"{r['name']} 정류장 주변은 {fact}. {' '.join(a + '.' for a in asks)}"
    if daily:
        return (f"{r['name']} 정류장은 하루 평균 {_people(m['night_demand'])}이 타고 갈아타는 곳이지만, "
                f"{fact}. {' '.join(a + '.' for a in asks)}")
    return (f"{r['name']} 정류장은 밤 {hours[0]}~{hours[-1]}시에 하루 평균 {_people(m['night_demand'])}이 {verb}, "
            f"{fact}. {' '.join(a + '.' for a in asks)}")


# --------------------------------------------------------------------------
# Runners
# --------------------------------------------------------------------------
def llm_available():
    if os.environ.get("NIGHTGAP_FORCE_RULES") == "1":
        return False, "규칙 모드가 설정돼 있어요"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "anthropic 패키지가 설치돼 있지 않아요"
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False, "API 키가 설정돼 있지 않아요"
    return True, ""


def run_turn(session: Session, question: str, force_rules: bool = False):
    """Handle one user question. Uses Claude when available, else the rule planner.

    force_rules is used for the example result shown on first load, so opening the
    page never spends API calls.
    """
    session.busy = True
    try:
        if I.classify(question, "") == "data":   # 자료 질문은 계산 없이 바로 답해요
            session.emit("answer", data_answer(session))
            return
        ok, why = llm_available()
        if force_rules:
            ok, why = False, ""
        if ok:
            try:
                session.mode = "llm"
                return _run_llm(session, question)
            except Exception as e:  # network blocked, auth, etc. -> same tools, rule order
                session.emit("warning", f"LLM 연결에 실패해 규칙 모드로 이어서 진행해요 ({type(e).__name__})")
        elif why:
            session.emit("info", f"규칙 모드로 진행해요: {why}")
        session.mode = "rules"
        return _run_rules(session, question)
    except Exception as e:
        session.emit("error", f"처리 중 오류가 났어요: {e}", traceback.format_exc()[-1500:])
    finally:
        session.busy = False


def _run_llm(session: Session, question: str):
    import anthropic

    client = anthropic.Anthropic()
    session.messages.append({"role": "user", "content": question})
    for _ in range(MAX_STEPS):
        kwargs = dict(model=MODEL, max_tokens=16000, system=SYSTEM_PROMPT,
                      tools=[{**t, "strict": True} for t in TOOLS], messages=session.messages,
                      output_config={"effort": EFFORT})
        try:
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                               fallbacks="default", **kwargs)
        except (TypeError, anthropic.BadRequestError):
            resp = client.messages.create(**kwargs)
        if resp.stop_reason == "refusal":
            raise RuntimeError("refusal")
        session.messages.append({"role": "assistant", "content": resp.content})
        for b in resp.content:
            if b.type == "text" and b.text.strip() and resp.stop_reason == "tool_use":
                session.emit("thought", b.text.strip())
        if resp.stop_reason == "pause_turn":
            continue
        calls = [b for b in resp.content if b.type == "tool_use"]
        if not calls:
            final = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            if session.report is None and session.analysis is not None:
                session.tool_finalize_report()
            session.emit("answer", final or "분석을 마쳤어요.")
            return final
        results = []
        for c in calls:
            session.emit("tool", _tool_caption(c.name, c.input), {"name": c.name, "input": c.input})
            try:
                out = session.call_tool(c.name, c.input)
                session.emit("tool_result", _result_caption(c.name, out), {**out, "_ms": getattr(session, "last_ms", None)} if isinstance(out, dict) else out)
                results.append({"type": "tool_result", "tool_use_id": c.id,
                                "content": json.dumps(out, ensure_ascii=False)[:20000]})
            except Exception as e:
                results.append({"type": "tool_result", "tool_use_id": c.id,
                                "content": f"도구 실행 오류: {e}", "is_error": True})
        session.messages.append({"role": "user", "content": results})
    session.emit("warning", "단계 수 한도에 도달해 여기서 마무리해요")
    if session.analysis and not session.report:
        session.tool_finalize_report()


HOUR_PAT = re.compile(r"(밤|저녁|새벽|오후)?\s*(\d{1,2})\s*시")
SKIP_WORDS = {"우리", "밤길", "정류장", "정류소", "버스", "제주", "제주도", "건의서", "상위", "전체", "보여줘", "알려줘",
              "공백", "우선", "개선", "괜찮아", "괜찮을까", "어디", "어디가", "곳", "학원", "학교", "학생", "청소년",
              "끝나고", "내리는데", "손볼", "먼저", "자녀", "아이", "내리", "내리면", "타는", "타", "가는", "오는", "다니",
              "걸어", "집에", "귀가", "밤에", "늦게", "안전해", "안전한", "위험해", "무서워", "어때", "있어", "있나", "예산",
              "설치", "놓는다면", "단다면", "달면", "비상벨", "동네", "지역", "제일", "가장", "얼마나", "많아", "몇", "퇴근길",
              "자정", "새벽", "저녁", "오후", "근처", "주변", "열시", "열한시", "딸이", "아들이", "엄마", "아빠"}


def parse_question(question, stops, towns=None):
    """Rule-mode parsing: place (bus-stop name, 동·읍·면 or city) and hours from plain Korean."""
    q = I.norm(question)
    hours = I.parse_hours(q)
    towns = towns or set()
    town_bases = {re.sub(r"[0-9]?(동|읍|면)$", "", t) for t in towns}
    place = ""
    for tok in re.findall(r"[가-힣0-9]{2,12}", q):
        tok = re.sub(r"(에서는|에서|에는|에도|으로|에|의|은|는|이|가|을|를|쪽|근처|주변|부근|일대|정류장|정류소)$", "", tok)
        if len(tok) < 2 or tok in SKIP_WORDS or tok.isdigit():
            continue
        if tok in ("서귀포", "서귀포시", "제주시"):
            place = tok
            break
        base = re.sub(r"(동|읍|면)$", "", tok) if len(tok) >= 3 else tok
        if tok in towns or (tok.endswith(("동", "읍", "면")) and base in town_bases):
            place = tok
            break
        seeds, _ = T.find_stops(stops or [], tok)
        if seeds:
            place = re.sub(r"(동|읍|면)$", "", tok) if len(tok) >= 3 and not tok.endswith("동") else tok
            break
    return place, hours


STUDENT_PAT = re.compile(r"(학생|청소년|학교|학원|아이|자녀|딸|아들|우리 애)")
BUDGET_PAT = re.compile(r"(\d{1,2})\s*(대|개|곳)")


def parse_budget(question):
    """'CCTV 5대', '비상벨 20개', '예산 1억' -> (count, kind). Defaults to 10 CCTV."""
    count, kind, _ = I.parse_budget(question)
    return count, kind


# ---------------- 자료 알아보기: 올린 파일이 무엇이고, 넣으면 무엇이 달라지는지 ----------------
KIND_INFO = {
    "bus_usage": ("정류장별 시간대 이용 (센터 3-04)", "밤 시간대에 내리는 사람 기준으로 순위를 매기고, 막차가 이른 곳도 함께 찾아요"),
    "bus_stops": ("버스 정류장 위치", "이 정류장 위치를 기준으로 분석해요. 센터 3-03이면 같은 아이디는 3-03을 먼저 써요"),
    "bus_daily_usage": ("정류장별 하루 이용", "정류장마다 하루에 타고 갈아타는 사람 수로 순위를 매겨요"),
    "bus_hourly_total": ("시간대별 버스 승차 (제주 전체)", "밤에 버스를 타는 사람 비율을 다시 계산해요"),
    "cctv": ("CCTV 위치", "기존 CCTV와 합쳐 다시 세요. 같은 위치는 한 곳으로 셈해요"),
    "bell": ("안전비상벨 위치", "기존 비상벨과 합쳐 다시 세요. 같은 위치는 한 곳으로 셈해요"),
    "streetlight": ("보안등·가로등 위치", "정류장 주변이 어두운지(가로등이 닿는지)도 함께 계산해요"),
    "school": ("학교 위치", "학교 주변 정류장을 따로 골라 볼 수 있어요"),
    "grid_pop": ("유동인구", "참고로 확인만 하고, 정류장 순위에는 쓰지 않아요"),
    "dong_pop": ("동별 인구", "참고로 확인만 하고, 정류장 순위에는 쓰지 않아요"),
    "unknown": ("알 수 없는 자료", "정류장·이용·CCTV·비상벨·보안등·학교 자료가 아니라서 분석에 쓰지 않아요. 위도·경도나 정류장 아이디 컬럼이 있는지 확인해 주세요"),
}
_DESC_CACHE = {}


def describe_file(path):
    """파일 하나가 어떤 자료인지와, 넣으면 분석이 어떻게 달라지는지 알려줘요. 원래 값은 돌려주지 않아요."""
    try:
        key = (path, os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        return {"kind": "unknown", "label": KIND_INFO["unknown"][0], "effect": KIND_INFO["unknown"][1], "rows": 0}
    if key not in _DESC_CACHE:
        try:
            prof = T.profile_csv(path)
            ident = T.identify_dataset(prof)
            kind, rows, reasons = ident["kind"], prof["rows"], ident.get("reasons", [])
        except Exception:  # 깨진 파일도 이유를 알려줘요
            kind, rows, reasons = "unknown", 0, ["CSV로 읽을 수 없어요"]
        label, effect = KIND_INFO.get(kind, KIND_INFO["unknown"])
        _DESC_CACHE[key] = {"kind": kind, "label": label, "effect": effect, "rows": rows, "reasons": reasons}
    return _DESC_CACHE[key]


def data_answer(session: Session):
    dirs = session.data_dir if isinstance(session.data_dir, (list, tuple)) else [session.data_dir]
    paths = sorted(p for d in dirs for p in glob.glob(os.path.join(d, "*.csv")))
    groups = {}
    for p in paths:
        groups.setdefault(describe_file(p)["label"], []).append(os.path.basename(p))
    have = ", ".join(f"{k} {len(v)}개" for k, v in groups.items()) or "아직 없어요"
    kinds = {describe_file(p)["kind"] for p in paths}
    missing = []
    if "bus_usage" not in kinds:
        missing.append("센터 3-04(정류장별 시간대 이용)를 넣으면 밤에 내리는 사람 기준으로 바뀌고 막차 공백도 계산해요")
    if "streetlight" not in kinds:
        missing.append("보안등 자료를 넣으면 가로등이 닿지 않는 정류장도 찾아요")
    sample = getattr(session, "source", "") == "sample" or any(os.path.basename(d) == "sample" for d in dirs)
    head = "지금은 가상 데이터로 시연하고 있어요. " if sample else ""
    return (f"{head}지금 쓰고 있는 자료는 {have}예요. "
            "새 자료는 화면 위 '자료' → 'CSV 올리기'를 누르거나 CSV 파일을 화면에 끌어다 놓으면 돼요. "
            "파일 이름이 달라도 Agent가 컬럼을 보고 어떤 자료인지 알아낸 뒤, 기존 자료와 합쳐 바로 다시 분석해요. "
            + ("".join(m + ". " for m in missing)).strip())


def _run_rules(session: Session, question: str):
    s = session
    step = lambda name, args: _rule_call(s, name, args)  # noqa: E731
    pre_kind = I.classify(question, "")
    if pre_kind in ("help", "offtopic"):
        s.emit("answer", (I.OFF_TOPIC if pre_kind == "offtopic" else "") + I.HELP_TEXT)
        return
    info = step("inspect_data", {})
    if info.get("error") or info.get("missing_required"):
        s.emit("answer", "분석에 필요한 버스 자료가 부족해요. " + (info.get("error") or
               "없는 자료: " + ", ".join(info["missing_required"])))
        return
    if not s._usage_path and s.daily:
        s.emit("decision", "정류장별 이용 자료가 있어서, 이용하는 사람이 많은 정류장부터 보도록 순위를 매길게요.")
    elif not s._usage_path:
        s.emit("decision", "이용 현황(3-04)이 없어요. 밤 수요 대신 시설 공백으로 순위를 매기고, 공백이 같으면 반경 500m 안에 정류장이 많은(사람이 다니는) 곳을 앞에 둘게요.")
    count_all = False
    for layer in ("cctv", "bell", "light"):
        if layer in s.layers:
            cov = step("check_coverage", {"layer": layer})
            nc = [w for w in cov.get("warnings", []) if w["code"] == "NO_CRIME_CCTV"]
            if nc:
                count_all = True
                why = nc[0]["detail"].split(". 용도")[0]
    if count_all:
        s.emit("decision", f"{why}. 그래서 용도와 상관없이 모든 CCTV를 셀게요.")
    place, hours = parse_question(question, s.stops, s.towns())
    qtype = I.classify(question, place)
    QT = {"plan": "설치 계획", "compare": "동네끼리 비교", "stats": "전체 현황", "check": f"'{place}' 근처 확인", "rank": "먼저 손볼 곳 찾기"}
    s.emit("decision", f"질문을 '{QT.get(qtype, '먼저 손볼 곳 찾기')}' 질문으로 이해했어요" + (f". '{place}'를 중심으로 볼게요." if place else "."))
    if hours:
        s.emit("decision", f"질문에 나온 시간에 맞춰 {hours[0]}~{hours[-1]}시를 볼게요.")
    student = bool(STUDENT_PAT.search(question)) and bool(s.layers.get("school"))
    if student:
        s.emit("decision", "학생 귀갓길을 물어보셔서, 학교에서 500m 안에 있는 정류장만 볼게요.")
    args = {"hours": hours or [21, 22, 23], "cctv_radius_m": 100, "bell_radius_m": 200,
            "count_all_cctv_purposes": count_all, "use_boarding_fallback": False, "area": place, "near_school": student}
    res = step("run_gap_analysis", args)
    codes = {i["code"] for i in res.get("issues", [])}
    if "THIN_DEMAND" in codes and s._usage_path:
        thin = next(i for i in res["issues"] if i["code"] == "THIN_DEMAND")
        s.emit("decision", f"{thin['detail']}. 시간을 20~23시로 넓히고 타는 기록도 더해서 다시 계산할게요.")
        args.update(hours=[20, 21, 22, 23], use_boarding_fallback=True)
        res = step("run_gap_analysis", args)
    rob = step("check_robustness", {})
    if rob.get("verdict"):
        s.emit("decision", f"기준을 {rob['n']}가지로 바꿔 다시 계산했어요. {rob['stable']}곳은 어떻게 바꿔도 10위 안에 있고, 10곳 중 평균 {rob['avg_kept']}곳이 그대로예요.")
    count, kind, money = I.parse_budget(question)
    if money:
        s.emit("decision", f"예산 {_won(money)}이면 {'비상벨' if kind == 'bell' else 'CCTV'}를 {count}대 달 수 있어요({'비상벨 200만원' if kind == 'bell' else 'CCTV 430만원'} 기준). 이 대수로 위치를 고를게요.")
    pl = step("plan_installations", {"count": count, "kind": kind, "budget_manwon": money or 0})
    if pl.get("picks"):
        cmp_ = pl["compare"]
        what = "CCTV" if kind == "cctv" else "비상벨"
        ratio = cmp_["this_plan"] / max(1.0, cmp_["farthest_from_facility_first"])
        s.emit("decision", f"{what} {pl['count']}대를 어디에 달지 골랐어요. 옆 정류장까지 같이 지킬 수 있는 곳을 고르면 "
                           f"하루 {pl['covered_users']:,.0f}명에게 닿아요. 이용 많은 곳부터 달 때보다 {cmp_['this_plan'] - cmp_['busiest_first']:,.0f}명 더 많고, "
                           f"외진 곳부터 달 때의 {ratio:.0f}배예요.")
    if place:
        step("look_up_place", {"place": place})
    conc = next((i for i in res.get("issues", []) if i["code"] == "CONCENTRATED"), None)
    if conc and not place:
        s.emit("decision", conc["detail"] + ". 지역을 정해 다시 물어보시면 그 지역 안에서 순위를 보여드려요.")
    step("finalize_report", {"top_n": 10, "headline": "", "limitations": [], "recommendations": []})
    ans = _rule_answer(s, qtype, place, kind, count, money)
    s.emit("answer", ans)
    return ans


def _josa(word, pair):
    ch = word.rstrip(")")[-1:] if word else ""
    has = ("가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0) or ch in "013678"  # 영·일·삼·육·칠·팔
    return pair[0] if has else pair[1]


def _won(manwon):
    return f"{manwon / 10000:g}억원" if manwon >= 10000 else f"{manwon:,}만원"


def _rule_answer(s, qtype, place, kind, count, money):
    """Answer shaped to the kind of question. Numbers come from the report only."""
    rep, sm = s.report, s.report["summary"]
    cards = rep["cards"]
    first = cards[0] if cards else None
    scope = (f"'{place}' 근처" if place else "제주 전체") + (" 학교 주변" if s.analysis_args.get("near_school") else "")
    what = "CCTV" if kind == "cctv" else "비상벨"
    plc = (sm.get("placement") or {}).get(kind)
    plan_line = ""
    if plc and plc["picks"]:
        k = min(count, len(plc["picks"]))
        names = ", ".join(p["name"] for p in plc["picks"][:min(k, 3)])
        plan_line = (f"{what} {k}대를 Agent가 고른 위치({names}{' 등' if k > 3 else ''})에 달면 하루 {plc['picks'][k - 1]['cum']:,.0f}명에게 새로 {what}{_josa(what, ('이', '가'))} 닿아요. ")
    ov = sm.get("overview") or {}
    both = (ov.get("cats") or {}).get("both")
    tail = "자세한 내용은 우선 개선과 설치 계획 화면에서 볼 수 있고, 건의서 탭에서 바로 인쇄할 수 있어요."

    if qtype == "plan":
        head = f"예산 {_won(money)}이면 " if money else ""
        return (f"{head}{scope}에서 {what}{_josa(what, ('을', '를'))} 달 곳을 Agent가 골랐어요. " + plan_line +
                ("근처에 CCTV가 이미 있는 곳은 새로 달지 않고 방향만 돌려도 돼요. " if kind == "cctv" else "")
                + "설치 계획 화면에서 정류장마다 직접 바꿔 보실 수 있어요.")
    if qtype == "compare" and ov.get("by_group"):
        g = ov["by_group"]
        top = ", ".join(f"{x['group']}({x['pct']:.0f}%)" for x in g[:3])
        city = [x for x in g if x["group"].endswith("동지역")]
        cline = (" 시내는 " + ", ".join(f"{x['group']} {x['pct']:.0f}%" for x in city) + "예요.") if city else ""
        return (f"CCTV도 비상벨도 없는 정류장 비율이 가장 높은 곳은 {top}이에요.{cline} 읍·면은 정류장 대부분이 비어 있고, "
                f"이용하는 사람 수로 보면 1위는 {first['name']}({first.get('area') or ''})이에요. " + tail) if first else tail
    if qtype == "stats" and both:
        return (f"이용 기록이 있는 제주 정류장 {sm.get('stops_scored', 0):,}곳 중 {both['stops']:,}곳({both['stop_pct']}%)은 100m 안에 CCTV도, "
                f"200m 안에 비상벨도 없어요. 하루 버스 이용의 {both['use_pct']}%가 이런 정류장에서 일어나요. "
                + (f"학교에서 500m 안인 정류장 중에서는 {sm['school']['blind_near']:,}곳이 그래요. " if sm.get("school") else "")
                + plan_line + tail)
    if qtype == "check" and cards:
        pk = place.replace(" ", "")
        by_name = not s.stops_in_town(place)
        target = next((c for c in cards if by_name and pk and pk in c["name"].replace(" ", "")), None)
        if target is None and by_name:      # the place's own stop may not be in the top 10
            rows = sorted((r for r in s.analysis["rows"] if pk in r["name"].replace(" ", "")), key=lambda r: -(r["night_demand"] or 0))
            if rows:
                r = rows[0]
                rad = (s.analysis_args["cctv_radius_m"], s.analysis_args["bell_radius_m"])
                target = {"name": r["name"], "demand": r["night_demand"] if (r["night_demand"] or 0) >= 10 else "10 미만",
                          "cctv_m": r.get("cctv_nearest_m"), "bell_m": r.get("bell_nearest_m"), "gap_codes": r["gaps"], "rx": T.prescribe(r, *rad)}
        target = target or first
        scope = (f"'{place}' 근처" if by_name else f"'{place}'") + (" 학교 주변" if s.analysis_args.get("near_school") else "")
        walk = [m for m in (target.get("cctv_m"), target.get("bell_m")) if m is not None]
        msg = (f"{scope} 정류장을 살펴봤어요. {'가장 먼저 손볼 곳은 ' if not by_name else ''}{target['name']}{_josa(target['name'], ('이에요. 이곳은', '예요. 이곳은')) if not by_name else _josa(target['name'], ('은', '는'))} "
               + (f"하루 {target['demand']:,.0f}명이 이용하는데, " if isinstance(target["demand"], (int, float)) else "")
               + f"가장 가까운 CCTV가 {_dist(target.get('cctv_m'))}, 비상벨이 {_dist(target.get('bell_m'))} 떨어져 있어요"
               + (f"(걸어서 약 {max(1, round(min(walk) / 80))}분). " if walk else ". "))
        if target.get("gap_codes"):
            msg += f"{target['rx']['label']}{_josa(target['rx']['label'], ('이', '가'))} 필요해요. " if target.get("rx") else ""
        else:
            msg += "CCTV와 비상벨이 가까이 있는 편이에요. "
        others = [c["name"] for c in cards if c is not target][:3]
        if others:
            msg += f"이 근처에서 다음으로 손볼 곳은 {', '.join(others)}{_josa(others[-1], ('이에요', '예요'))}. "
        return msg + tail
    # rank
    if place and s.stops_in_town(place):
        scope = f"'{place}'" + (" 학교 주변" if s.analysis_args.get("near_school") else "")
    msg = f"Agent가 {scope}에서 먼저 손볼 정류장 {len(cards)}곳을 골랐어요. "
    if first:
        msg += f"1위는 {first['name']}{_josa(first['name'], ('이에요', '예요'))}. {first['evidence'][0]}. "
        if first.get("rx"):
            msg += f"{first['rx']['label']}{_josa(first['rx']['label'], ('을', '를'))} 추천해요. "
    return msg + plan_line + tail


def _rule_call(s, name, args):
    s.emit("tool", _tool_caption(name, args), {"name": name, "input": args})
    out = s.call_tool(name, args)
    s.emit("tool_result", _result_caption(name, out), {**out, "_ms": getattr(s, "last_ms", None)} if isinstance(out, dict) else out)
    return out


def _tool_caption(name, args):
    return {
        "inspect_data": "올려 둔 파일이 어떤 자료인지 보고 있어요",
        "check_coverage": f"{ {'cctv': 'CCTV', 'bell': '비상벨', 'light': '보안등'}.get((args or {}).get('layer'), '')} 자료가 한쪽에 몰려 있지 않은지 보고 있어요",
        "look_up_place": f"'{(args or {}).get('place', '')}' 주변 정류장을 찾고 있어요",
        "run_gap_analysis": "정류장마다 이용하는 사람과 주변 CCTV·비상벨을 세고 있어요",
        "plan_installations": f"{'비상벨' if (args or {}).get('kind') == 'bell' else 'CCTV'} {(args or {}).get('count', 10)}대를 어디에 달면 가장 많은 사람을 지킬지 고르고 있어요",
        "check_robustness": "기준을 바꿔 한 번 더 계산하고 있어요",
        "finalize_report": "작은 숫자는 가리고 결과를 정리하고 있어요",
    }.get(name, name)


def _result_caption(name, out):
    if out.get("error"):
        return "문제: " + out["error"]
    if name == "inspect_data":
        kinds = [f"{f['file']} → {f['kind']}" for f in out["files"]]
        return "확인한 파일: " + "; ".join(kinds)
    if name == "check_coverage":
        w = out.get("warnings") or []
        return (" / ".join(x["detail"] for x in w)) if w else f"{out['total']}곳, 특이사항 없음"
    if name == "run_gap_analysis":
        t = out["top10"][0] if out["top10"] else None
        msg = f"정류장 {out['stops_scored']:,}곳 계산을 마쳤어요"
        if t:
            msg += f", 1위 {t['name']} ({', '.join(t['gap_labels'])})"
        if out["issues"]:
            msg += " / " + "; ".join(i["detail"] for i in out["issues"])
        return msg
    if name == "check_robustness":
        return f"{out['stable']}곳은 어떻게 바꿔도 10위 안에 있어요 (평균 {out['avg_kept']}곳 그대로)"
    if name == "plan_installations":
        return f"{out['count']}대로 하루 {out['covered_users']:,.0f}명에게 새로 닿아요"
    if name == "look_up_place":
        return f"이름이 맞는 정류장 {len(out.get('matched_stops', []))}곳, 주변 {out.get('nearby_count', 0)}곳"
    if name == "finalize_report":
        return f"정류장 {out['cards']}곳 정리, 10명 미만이라 가린 숫자 {out['masked_values']}개"
    return "완료"
