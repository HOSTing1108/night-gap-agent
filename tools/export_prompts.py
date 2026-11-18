"""Write docs/prompts.md from the prompt and tool definitions in app/agent.py."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
import agent as A  # noqa: E402

lines = ["# Prompt와 Tool 정의", "",
         "이 문서는 `tools/export_prompts.py`가 `app/agent.py`에서 그대로 뽑아 만들어요. 코드와 내용이 항상 같아요.", "",
         "## 사용 모델", "",
         f"- 기본 모델: `{A.MODEL}` (환경변수 `NIGHTGAP_MODEL`로 변경)",
         f"- 추론 강도(effort): `{A.EFFORT}` (환경변수 `NIGHTGAP_EFFORT`)",
         "- 호출 방식: Anthropic Python SDK의 Messages API, 도구 호출 루프 (최대 "
         f"{A.MAX_STEPS}단계), 도구 스키마 `strict: true`",
         "- 안전 거절 시 서버 측 대체 모델 사용(`fallbacks: \"default\"`), 그래도 실패하면 규칙 모드로 전환", "",
         "## System Prompt", "", "```text", A.SYSTEM_PROMPT, "```", "", "## Tools", ""]
for t in A.TOOLS:
    lines += [f"### `{t['name']}`", "", t["description"], "", "```json",
              json.dumps(t["input_schema"], ensure_ascii=False, indent=2), "```", ""]
lines += ["## 규칙 모드의 실행 순서", "",
          "API 키가 없거나 외부 접속이 막히면 같은 도구를 아래 순서와 분기로 실행해요.", "",
          "1. `inspect_data` → 필수 버스 자료가 없으면 무엇이 없는지 알리고 멈춤",
          "2. 불러온 위치 자료마다 `check_coverage` → 방범 CCTV가 0곳인 시가 있으면 모든 목적의 CCTV를 셈",
          "3. 질문에서 시간(\"밤 10시\")과 지명(정류장 이름과 맞는 낱말)을 읽음",
          "4. `run_gap_analysis` → `THIN_DEMAND`면 20~23시와 승차 보정으로 다시 실행",
          "5. 지명이 있으면 `look_up_place`, 상위 10곳이 한 지역에 몰리면 안내",
          "6. `finalize_report` → 정해진 문장 틀로 건의 문장 작성", ""]
open(os.path.join(ROOT, "docs", "prompts.md"), "w", encoding="utf-8").write("\n".join(lines))
print("docs/prompts.md written")
