"""README용 Agent 파이프라인 그림(docs/img/pipeline.svg)을 만들어요.

실행: python tools/make_pipeline_svg.py
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "img", "pipeline.svg")

FONT = "'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR','Noto Sans CJK KR',sans-serif"
ORANGE, BLUE, INK = "#f0a33a", "#5b8ad6", "#2b2f36"
PEACH_F, PEACH_S = "#fde9cf", "#f5c27a"
GREEN_F, GREEN_S = "#dcefd9", "#8fc28a"

W, H = 1700, 560
out = []
add = out.append


def text(x, y, s, size=17, weight=400, anchor="middle", color=INK, lines_gap=21):
    for i, line in enumerate(s.split("\n")):
        add(f'<text x="{x}" y="{y + i * lines_gap}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}" fill="{color}">{line}</text>')


def chip(x, y, w, h, s, kind="peach", size=17, weight=500):
    f, st = (PEACH_F, PEACH_S) if kind == "peach" else (GREEN_F, GREEN_S)
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{f}" stroke="{st}" stroke-width="2.2"/>')
    n = s.count("\n") + 1
    text(x + w / 2, y + h / 2 + 6 - (n - 1) * 10, s, size, weight, lines_gap=20)


def arrow(x1, y1, x2, y2, color=ORANGE, width=8, head="ah"):
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}" '
        f'stroke-linecap="round" marker-end="url(#{head})"/>')


def bot(x, y, s=1.0, badge=None):
    add(f'<use href="#bot" xlink:href="#bot" x="{x - 40 * s}" y="{y - 40 * s}" width="{80 * s}" height="{80 * s}"/>')
    if badge == "lens":
        add(f'<circle cx="{x + 30 * s}" cy="{y + 22 * s}" r="{11 * s}" fill="#fff" stroke="{INK}" stroke-width="3"/>'
            f'<line x1="{x + 38 * s}" y1="{y + 30 * s}" x2="{x + 48 * s}" y2="{y + 40 * s}" stroke="{INK}" stroke-width="4" stroke-linecap="round"/>')
    if badge == "gear":
        add(f'<circle cx="{x + 32 * s}" cy="{y - 26 * s}" r="{10 * s}" fill="#fff" stroke="{INK}" stroke-width="3" stroke-dasharray="4 3"/>'
            f'<circle cx="{x + 32 * s}" cy="{y - 26 * s}" r="{3.5 * s}" fill="{INK}"/>')
    if badge == "map":
        add(f'<path d="M{x + 22 * s} {y + 14 * s} l10 -5 l10 5 l10 -5 v24 l-10 5 l-10 -5 l-10 5 z" fill="#fff" stroke="{INK}" stroke-width="2.4"/>'
            f'<circle cx="{x + 37 * s}" cy="{y + 22 * s}" r="3" fill="{ORANGE}"/>')
    if badge == "doc":
        add(f'<rect x="{x + 24 * s}" y="{y + 6 * s}" width="{24 * s}" height="{30 * s}" rx="3" fill="#fff" stroke="{INK}" stroke-width="2.4"/>'
            f'<line x1="{x + 29 * s}" y1="{y + 15 * s}" x2="{x + 43 * s}" y2="{y + 15 * s}" stroke="{INK}" stroke-width="2"/>'
            f'<line x1="{x + 29 * s}" y1="{y + 22 * s}" x2="{x + 43 * s}" y2="{y + 22 * s}" stroke="{INK}" stroke-width="2"/>'
            f'<line x1="{x + 29 * s}" y1="{y + 29 * s}" x2="{x + 39 * s}" y2="{y + 29 * s}" stroke="{INK}" stroke-width="2"/>')
    if badge == "coin":
        add(f'<circle cx="{x + 34 * s}" cy="{y + 24 * s}" r="{12 * s}" fill="#ffd27a" stroke="{INK}" stroke-width="2.6"/>')
        text(x + 34 * s, y + 30 * s, "₩", 15, 700)


def cylinder(x, y, w, h, s):
    add(f'<path d="M{x} {y + 8} v{h - 16} a{w / 2} 8 0 0 0 {w} 0 v{-(h - 16)}" fill="#eef4fc" stroke="{BLUE}" stroke-width="2"/>'
        f'<ellipse cx="{x + w / 2}" cy="{y + 8}" rx="{w / 2}" ry="8" fill="#ffffff" stroke="{BLUE}" stroke-width="2"/>')
    text(x + w / 2, y + h / 2 + 9, s, 13.5, 500, lines_gap=17)


def bracket_down(x1, x2, y, label):
    m = (x1 + x2) / 2
    add(f'<path d="M{x1} {y - 14} v14 H{m - 10} l10 14 l10 -14 H{x2} v-14" fill="none" stroke="{BLUE}" stroke-width="3" stroke-linejoin="round"/>')
    text(m, y + 56, label, 22, 700, color="#1f2a44")


# ---------- defs ----------
add(f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="{FONT}">
<defs>
  <marker id="ah" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="3.2" markerHeight="3.2" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="{ORANGE}"/></marker>
  <marker id="bh" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="{BLUE}"/></marker>
  <symbol id="bot" viewBox="0 0 80 80">
    <line x1="40" y1="5" x2="40" y2="15" stroke="{INK}" stroke-width="3"/><circle cx="40" cy="5" r="4" fill="{ORANGE}" stroke="{INK}" stroke-width="2"/>
    <rect x="17" y="15" width="46" height="28" rx="9" fill="#f5f7fb" stroke="{INK}" stroke-width="3"/>
    <rect x="11" y="23" width="6" height="12" rx="2" fill="{INK}"/><rect x="63" y="23" width="6" height="12" rx="2" fill="{INK}"/>
    <circle cx="31" cy="29" r="4.2" fill="{INK}"/><circle cx="49" cy="29" r="4.2" fill="{INK}"/>
    <path d="M33 37 q7 4 14 0" fill="none" stroke="{INK}" stroke-width="2.4" stroke-linecap="round"/>
    <rect x="22" y="47" width="36" height="27" rx="7" fill="#e8eef8" stroke="{INK}" stroke-width="3"/>
    <rect x="31" y="53" width="18" height="10" rx="2.5" fill="{ORANGE}"/>
    <path d="M22 54 L10 64 M58 54 L70 64" stroke="{INK}" stroke-width="3.4" stroke-linecap="round"/>
  </symbol>
</defs>
<rect width="{W}" height="{H}" fill="#ffffff"/>
<rect x="12" y="62" width="{W - 24}" height="{H - 76}" rx="44" fill="#ffffff" stroke="#c8d8f2" stroke-width="3.5"/>
<rect x="52" y="22" width="600" height="72" rx="13" fill="#dce8f8" stroke="#a6bfe6" stroke-width="2.5"/>''')
text(352, 70, "안심하차 Agent · 질문에서 건의서까지", 30, 800, color="#14213d")

# ---------- 1단계: 질문 이해 ----------
add(f'<rect x="58" y="160" width="62" height="44" rx="9" fill="#fff" stroke="{INK}" stroke-width="3"/>'
    f'<path d="M72 204 l-6 12 l16 -12" fill="#fff" stroke="{INK}" stroke-width="3" stroke-linejoin="round"/>'
    f'<rect x="92" y="190" width="58" height="40" rx="9" fill="#fff" stroke="{INK}" stroke-width="3"/>'
    f'<path d="M136 230 l6 11 l-16 -11" fill="#fff" stroke="{INK}" stroke-width="3" stroke-linejoin="round"/>'
    f'<line x1="70" y1="176" x2="106" y2="176" stroke="{INK}" stroke-width="2.6"/><line x1="70" y1="187" x2="98" y2="187" stroke="{INK}" stroke-width="2.6"/>'
    f'<line x1="104" y1="206" x2="138" y2="206" stroke="{INK}" stroke-width="2.6"/><line x1="104" y1="216" x2="128" y2="216" stroke="{INK}" stroke-width="2.6"/>')
text(104, 275, "도민 질문", 18, 600)
text(104, 300, "“밤 10시 노형에서\n내리는데 괜찮아?”", 13.5, 400, color="#5a6372", lines_gap=18)
arrow(160, 210, 205, 210)
bot(268, 205, 1.05, "lens")
text(268, 275, "질문 이해 Agent", 18, 600)
add(f'<path d="M366 118 q-14 0 -14 14 v66 q0 12 -12 12 q12 0 12 12 v66 q0 14 14 14" fill="none" stroke="{BLUE}" stroke-width="3.2"/>')
for i, s in enumerate(["동네 · 장소", "시간대", "대상 (학생 · 우리 애)", "예산 · 대수"]):
    chip(380, 120 + i * 50, 168, 40, s, size=16)
add(f'<path d="M562 118 q14 0 14 14 v66 q0 12 12 12 q-12 0 -12 12 v66 q0 14 -14 14" fill="none" stroke="{BLUE}" stroke-width="3.2"/>')

# ---------- 2단계: 자료 점검 ----------
arrow(596, 210, 640, 210)
bot(700, 200, 1.05, "lens")
text(700, 268, "inspect_data", 17, 700)
text(700, 290, "어떤 자료인지 알아보기", 14.5, 400, color="#4a5262")
cylinder(612, 340, 58, 62, "센터\n3-03·04")
cylinder(674, 340, 58, 62, "데이터\n허브")
cylinder(736, 340, 58, 62, "공공\n포털")
add(f'<line x1="703" y1="336" x2="703" y2="302" stroke="{BLUE}" stroke-width="3" marker-end="url(#bh)"/>')
arrow(770, 200, 815, 200)
bot(875, 195, 1.05, "gear")
text(875, 262, "check_coverage", 17, 700)
text(875, 284, "자료 쏠림 점검", 14.5, 400, color="#4a5262")
add(f'<path d="M800 300 v10 H950 v-10" fill="none" stroke="{BLUE}" stroke-width="2.6"/>'
    f'<line x1="875" y1="310" x2="875" y2="322" stroke="{BLUE}" stroke-width="2.6"/>')
chip(806, 324, 138, 36, "◆ 이용 자료 고르기", "green", 14.5, 600)
chip(806, 366, 138, 36, "◆ 모든 CCTV 세기", "green", 14.5, 600)

# ---------- 3단계: 공백 계산과 검증 ----------
arrow(950, 195, 995, 195)
bot(1058, 192, 1.05, "map")
text(1058, 258, "run_gap_analysis", 17, 700)
text(1058, 280, "CCTV 100m · 비상벨 200m", 14.5, 400, color="#4a5262")
chip(978, 326, 160, 30, "look_up_place", "green", 14, 600)
chip(978, 360, 160, 30, "학교 500m 안만", "green", 14, 600)
chip(978, 394, 160, 30, "◆ 수요 적으면 시간 넓히기", "green", 13, 600)
add(f'<line x1="1058" y1="326" x2="1058" y2="296" stroke="{BLUE}" stroke-width="3" marker-end="url(#bh)"/>')
arrow(1128, 192, 1172, 192)
# 다시 계산 고리
add(f'<path d="M1196 140 a52 30 0 0 1 92 -2" fill="none" stroke="#3f7fae" stroke-width="5" marker-end="url(#bh)"/>'
    f'<path d="M1288 262 a52 30 0 0 1 -92 2" fill="none" stroke="#3f7fae" stroke-width="5" marker-end="url(#bh)"/>')
text(1242, 106, "기준 바꿔 다시", 14.5, 600, color="#3f7fae")
bot(1242, 196, 1.05, "gear")
text(1242, 296, "check_robustness", 17, 700)
text(1242, 318, "기준 바꿔도 n/4", 14.5, 400, color="#4a5262")

# ---------- 4단계: 계획과 보고 ----------
arrow(1312, 192, 1356, 192)
bot(1418, 190, 1.05, "coin")
text(1418, 256, "plan_installations", 17, 700)
text(1418, 278, "예산별 설치 위치", 14.5, 400, color="#4a5262")
arrow(1488, 190, 1532, 190)
bot(1592, 188, 1.05, "doc")
text(1592, 254, "finalize_report", 17, 700)
text(1592, 276, "10명 미만 가리기", 14.5, 400, color="#4a5262")
arrow(1592, 292, 1592, 318, width=6)
chip(1362, 330, 104, 50, "먼저 손볼\n10곳", "green", 14.5, 700)
chip(1474, 330, 96, 50, "설치\n계획", "green", 14.5, 700)
chip(1578, 330, 96, 50, "3D 지도\n건의서", "green", 14.5, 700)
add(f'<path d="M1414 326 V318 H1626 V326" fill="none" stroke="{ORANGE}" stroke-width="4"/>'
    f'<line x1="1522" y1="318" x2="1522" y2="326" stroke="{ORANGE}" stroke-width="4"/>')

# ---------- 단계 괄호 ----------
bracket_down(40, 584, 452, "1단계 · 질문 이해")
bracket_down(600, 960, 452, "2단계 · 자료 점검")
bracket_down(976, 1330, 452, "3단계 · 공백 계산과 검증")
bracket_down(1346, 1676, 452, "4단계 · 계획과 보고")
add("</svg>")

open(OUT, "w", encoding="utf-8").write("\n".join(out))
print("saved", OUT)
