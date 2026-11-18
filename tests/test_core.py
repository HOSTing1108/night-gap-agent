"""재현성 검증용 테스트. 실행: python -m unittest discover tests"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
os.environ["NIGHTGAP_FORCE_RULES"] = "1"

import agent as A  # noqa: E402
import geo  # noqa: E402
import tools as T  # noqa: E402

SAMPLE = os.path.join(ROOT, "data", "sample")


class GeoTest(unittest.TestCase):
    def test_tm_roundtrip_under_1cm(self):
        for crs in ("EPSG:5174", "EPSG:5181", "EPSG:5186"):
            for lat, lon in ((33.4996, 126.5312), (33.2541, 126.5600), (33.9600, 126.2970)):
                x, y = geo.wgs84_to_tm(lat, lon, crs)
                la, lo = geo.tm_to_wgs84(x, y, crs)
                self.assertLess(geo.haversine(lat, lon, la, lo), 0.01, crs)

    def test_haversine_known_distance(self):
        # 제주시청 ~ 서귀포시청 약 27km
        d = geo.haversine(33.4996, 126.5312, 33.2541, 126.5600)
        self.assertAlmostEqual(d / 1000, 27.4, delta=0.5)

    def test_chuja_inside_jeju(self):
        self.assertTrue(geo.in_jeju(33.955, 126.33))
        self.assertFalse(geo.in_jeju(35.41, 128.49))


class ToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kinds = {}
        for f in sorted(os.listdir(SAMPLE)):
            p = os.path.join(SAMPLE, f)
            cls.kinds[T.identify_dataset(T.profile_csv(p))["kind"]] = p

    def test_identify_all_sample_files(self):
        self.assertEqual(set(self.kinds), {"bus_stops", "bus_usage", "cctv", "bell"})

    def test_tm_bells_converted_into_jeju(self):
        p = self.kinds["bell"]
        ident = T.identify_dataset(T.profile_csv(p))
        pts, rep = T.load_points(p, ident["coords"], "bell")
        self.assertEqual(rep["crs"], "EPSG:5174")
        self.assertEqual(rep["outside_jeju"], 0)

    def test_planted_hotspot_ranks_first(self):
        stops = T.load_stops(self.kinds["bus_stops"])
        dem, _ = T.night_demand(self.kinds["bus_usage"])
        c = T.load_points(self.kinds["cctv"], T.identify_dataset(T.profile_csv(self.kinds["cctv"]))["coords"], "cctv")[0]
        b = T.load_points(self.kinds["bell"], T.identify_dataset(T.profile_csv(self.kinds["bell"]))["coords"], "bell")[0]
        res = T.compute_gaps(stops, dem, c, b)
        top10 = [r["name"] for r in res["rows"][:10]]
        self.assertTrue(any("신도시" in n for n in top10[:3]), top10)
        # 점수는 공백이 없으면 0
        self.assertTrue(all(r["score"] == 0 for r in res["rows"] if not r["gaps"]))

    def test_export_masks_small_values(self):
        rows = [{"name": "a", "night_demand": 3.2}, {"name": "b", "night_demand": 12.0}]
        masked, log = T.export_check(rows)
        self.assertEqual(masked[0]["night_demand"], "10 미만")
        self.assertEqual(masked[1]["night_demand"], 12.0)
        self.assertEqual(len(log), 1)

    def test_coverage_flags_missing_crime_cctv(self):
        pts = [{"lat": 33.5, "lon": 126.5, "purpose": "교통단속", "city": "제주시"},
               {"lat": 33.25, "lon": 126.56, "purpose": "생활방범", "city": "서귀포시"}]
        rep = T.check_coverage(pts, "cctv")
        self.assertIn("NO_CRIME_CCTV", [w["code"] for w in rep["warnings"]])


class AgentRuleModeTest(unittest.TestCase):
    def test_question_parsing(self):
        stops = T.load_stops(os.path.join(SAMPLE, "3-03_bus_station_info.csv"))
        place, hours = A.parse_question("우리 애가 밤 10시에 노형에서 버스 내리는데 괜찮아?", stops)
        self.assertEqual(place, "노형")
        self.assertEqual(hours, [22, 23])

    def test_full_turn_produces_report(self):
        s = A.Session([SAMPLE])
        A.run_turn(s, "제주 전체 밤길 공백 상위 10곳")
        self.assertIsNotNone(s.report)
        self.assertEqual(len(s.report["cards"]), 10)
        types = [e["type"] for e in s.events]
        for t in ("tool", "tool_result", "answer"):
            self.assertIn(t, types)
        self.assertNotIn("error", types)

    def test_missing_bus_data_is_reported(self):
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        try:
            for f in ("cctv_jeju.csv", "safety_bell_jeju.csv"):
                shutil.copy(os.path.join(ROOT, "data", "public", f), d)
            s = A.Session([d])
            A.run_turn(s, "상위 10곳")
        finally:
            shutil.rmtree(d)
        self.assertIsNone(s.report)
        self.assertIn("버스 자료가 부족", s.events[-1]["text"])

    def test_portal_stop_file_without_usage_runs_facility_only(self):
        """공공데이터포털 정류소 현황(한글 컬럼, CP949)만 있어도 시설 공백 순위가 나와야 해요."""
        import csv
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        try:
            rows = list(csv.DictReader(open(os.path.join(SAMPLE, "3-03_bus_station_info.csv"), encoding="utf-8-sig")))
            with open(os.path.join(d, "제주특별자치도_버스정류소현황.csv"), "w", encoding="cp949", newline="") as f:
                w = csv.writer(f)
                w.writerow(["정류소아이디", "정류소명", "위도", "경도"])
                for r in rows:
                    w.writerow([r["bus_station_id"], r["bus_station_name"], r["latitude"], r["longitude"]])
            for f in os.listdir(SAMPLE):
                if "cctv" in f or "bell" in f:
                    shutil.copy(os.path.join(SAMPLE, f), d)
            s = A.Session([d])
            A.run_turn(s, "상위 10곳")
            self.assertIsNotNone(s.report)
            self.assertTrue(s.report["summary"]["facility_only"])
            self.assertIsNone(s.report["cards"][0]["demand"])
            self.assertTrue(any("이용 현황" in x for x in s.report["limitations"]))
        finally:
            shutil.rmtree(d)

    def test_robustness_reports_kept_counts(self):
        s = A.Session([SAMPLE])
        A.run_turn(s, "제주 전체 밤길 공백 상위 10곳")
        rob = s.report["robustness"]
        self.assertEqual(rob["n"], 6)
        self.assertTrue(all(0 <= v["kept"] <= v["of"] for v in rob["variants"]))
        self.assertIn(rob["verdict"], ("튼튼해요", "보통이에요", "흔들려요"))


if __name__ == "__main__":
    unittest.main()


class PickAndPlanTest(unittest.TestCase):
    def test_prescription_rules(self):
        self.assertEqual(T.prescribe({"gaps": ["eyes"], "cctv_nearest_m": 150})["code"], "adjust_cctv")
        self.assertEqual(T.prescribe({"gaps": ["eyes", "bell"], "cctv_nearest_m": 900})["code"], "new_cctv")
        self.assertEqual(T.prescribe({"gaps": ["bell"], "bell_nearest_m": 400})["code"], "bell")
        self.assertEqual(T.prescribe({"gaps": []})["code"], "none")

    def test_one_device_covers_both_sides_of_road(self):
        rows = [{"id": "a", "name": "A", "lat": 33.5, "lon": 126.5, "gaps": ["eyes"], "night_demand": 50},
                {"id": "b", "name": "A 맞은편", "lat": 33.5003, "lon": 126.5, "gaps": ["eyes"], "night_demand": 40},
                {"id": "c", "name": "C", "lat": 33.6, "lon": 126.6, "gaps": ["eyes"], "night_demand": 60}]
        plan = T.plan_placement(rows, 1)
        self.assertEqual(plan["picks"][0]["cum"], 90)          # A+B (90) beats C alone (60)
        self.assertEqual(plan["picks"][0]["covered_stops"], 2)

    def test_greedy_never_worse_than_plain_orders(self):
        s = A.Session(SAMPLE)
        A.run_turn(s, "제주 전체 10곳", force_rules=True)
        for k in (1, 5, 10):
            b = T.placement_benchmark(s.analysis["rows"], k)
            self.assertGreaterEqual(b["greedy"], b["demand_first"])
            self.assertGreaterEqual(b["greedy"], b["farthest_first"])
        self.assertIn("placement", s.report["summary"])
        self.assertIn("rx", s.report["cards"][0])

    def test_intent_cases_accuracy(self):
        """평소 말투 질문 40개: 장소, 시간, 대수, 시설, 질문 유형을 맞게 읽는지."""
        import json
        import intent as I
        s = A.Session([os.path.join(ROOT, "data", "input"), os.path.join(ROOT, "data", "public")])
        s.tool_inspect_data()
        towns = s.towns()
        cases = json.load(open(os.path.join(ROOT, "tests", "intent_cases.json"), encoding="utf-8"))
        ok, bad = 0, []
        for c in cases:
            place, hours = A.parse_question(c["q"], s.stops, towns)
            count, kind = A.parse_budget(c["q"])
            qtype = I.classify(c["q"], place)
            good = (place == c.get("place", place) and (hours or None) == c.get("hours", hours or None)
                    and count == c.get("count", count) and kind == c.get("kind", kind) and qtype == c.get("type", qtype))
            ok += good
            if not good:
                bad.append(c["q"])
        self.assertGreaterEqual(ok / len(cases), 0.95, f"{ok}/{len(cases)} {bad}")

    def test_answers_follow_question_type(self):
        s = A.Session([os.path.join(ROOT, "data", "input"), os.path.join(ROOT, "data", "public")])
        A.run_turn(s, "오늘 날씨 어때?", force_rules=True)
        self.assertIn("답하기 어려워요", s.events[-1]["text"])
        s = A.Session([os.path.join(ROOT, "data", "input"), os.path.join(ROOT, "data", "public")])
        A.run_turn(s, "어느 동네가 제일 비어 있어?", force_rules=True)
        self.assertIn("비율이 가장 높은 곳", [e for e in s.events if e["type"] == "answer"][-1]["text"])
        s = A.Session([os.path.join(ROOT, "data", "input"), os.path.join(ROOT, "data", "public")])
        A.run_turn(s, "예산 1억이면 CCTV를 어디에 달아야 해?", force_rules=True)
        self.assertEqual(s.placement["cctv"]["count"], 10000 // 430)


class SchoolLayerTest(unittest.TestCase):
    def test_school_file_identified_and_used(self):
        p = os.path.join(ROOT, "data", "public", "school_jeju.csv")
        if not os.path.isfile(p):
            self.skipTest("school file not present")
        self.assertEqual(T.identify_dataset(T.profile_csv(p))["kind"], "school")
        s = A.Session([os.path.join(ROOT, "data", "input"), os.path.join(ROOT, "data", "public")])
        A.run_turn(s, "우리 애가 학원 끝나고 밤 10시에 내리는 학교 근처 정류장 괜찮아?", force_rules=True)
        self.assertTrue(s.analysis_args["near_school"])
        self.assertEqual(s.analysis_args["area"], "")
        self.assertTrue(all(c["school"]["n500"] >= 1 for c in s.report["cards"]))


class FlexibleDataTest(unittest.TestCase):
    def test_uploaded_files_are_described(self):
        import tempfile
        self.assertEqual(A.describe_file(os.path.join(SAMPLE, "cctv_standard_jeju.csv"))["kind"], "cctv")
        self.assertEqual(A.describe_file(os.path.join(SAMPLE, "3-04_bus_station_usage.csv"))["kind"], "bus_usage")
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("이름,메모\n가,나\n")
        try:
            d = A.describe_file(f.name)
            self.assertEqual(d["kind"], "unknown")
            self.assertTrue(d["effect"])
        finally:
            os.remove(f.name)

    def test_data_questions_answered_without_analysis(self):
        import intent as I
        for q in ("어떤 자료 써?", "내 자료 넣으려면 어떻게 해?", "CSV 올리면 뭐가 바뀌어?"):
            self.assertEqual(I.classify(q, ""), "data", q)
        self.assertEqual(I.classify("제주 전체에서 먼저 손볼 정류장 10곳 알려줘", ""), "rank")
        s = A.Session([SAMPLE])
        A.run_turn(s, "어떤 자료 써?", force_rules=True)
        ans = [e["text"] for e in s.events if e["type"] == "answer"][-1]
        self.assertIn("CSV 올리기", ans)
        self.assertIsNone(s.report)
