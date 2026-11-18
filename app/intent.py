"""Understanding plain Korean questions in rule mode: time, place, budget and what kind of answer is wanted.

Claude mode reads the question itself; these functions make the rule mode answer the same variety of questions.
"""
import re

KO_NUM = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12}
DIGIT_HOUR = re.compile(r"(밤|저녁|새벽|오후|오전|낮)?\s*(\d{1,2})\s*(?:시|:\d{2}|pm|PM)")
KO_HOUR = re.compile(r"(밤|저녁|새벽|오후|오전|낮)?\s*(열한|열두|다섯|여섯|일곱|여덟|아홉|한|두|세|네|열)\s*시")
PM_HOUR = re.compile(r"(\d{1,2})\s*(pm|PM|피엠)")
WORD_HOURS = [
    (re.compile(r"자정|밤\s*12시|열두\s*시\s*넘"), [23]),
    (re.compile(r"밤\s*늦게|늦은\s*밤|늦게|심야|막차|야간\s*자율|야자"), [22, 23]),
    (re.compile(r"퇴근|저녁\s*시간|저녁에|해\s*진\s*뒤|해질녘"), [18, 19, 20]),
]


def norm(q):
    q = (q or "").strip()
    q = q.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return re.sub(r"\s+", " ", q)


def _to24(prefix, h):
    if prefix in ("밤", "저녁", "오후") and h < 12:
        h += 12
    if prefix is None and 6 <= h <= 11:     # 밤길 질문에서 그냥 "10시", "6시"는 저녁·밤 시각으로 봐요
        return h + 12
    return h


def parse_hours(q):
    q = norm(q)
    m = PM_HOUR.search(q)
    if m:
        h = int(m.group(1)) % 12 + 12
        return [x for x in (h, h + 1) if x <= 23]
    m = DIGIT_HOUR.search(q)
    if m:
        h = _to24(m.group(1), int(m.group(2)))
        if 0 <= h <= 23:
            return [x for x in (h, h + 1) if x <= 23]
    m = KO_HOUR.search(q)
    if m:
        h = _to24(m.group(1), KO_NUM[m.group(2)])
        if 0 <= h <= 23:
            return [x for x in (h, h + 1) if x <= 23]
    for pat, hours in WORD_HOURS:
        if pat.search(q):
            return hours
    return None


MONEY = re.compile(r"(?:(\d+(?:\.\d+)?)\s*억)?\s*(?:(\d+)\s*천)?\s*(?:(\d+)\s*백)?\s*(?:(\d+)\s*)?만\s*원?|(\d+(?:\.\d+)?)\s*억\s*원?")
COUNT = re.compile(r"(\d{1,3})\s*(대|개)")


def parse_money(q):
    """'1억', '1억 5천만원', '5천만원', '3000만원', '2.5억' -> 만원 단위 정수, 없으면 None."""
    q = norm(q).replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*억(?:\s*(\d+)\s*천\s*만?)?", q)
    if m:
        v = float(m.group(1)) * 10000 + (int(m.group(2)) * 1000 if m.group(2) else 0)
        return int(v)
    m = re.search(r"(\d+)\s*천\s*만", q)
    if m:
        return int(m.group(1)) * 1000
    m = re.search(r"(\d+)\s*백\s*만", q)
    if m:
        return int(m.group(1)) * 100
    m = re.search(r"(\d{2,6})\s*만\s*원", q)
    if m:
        return int(m.group(1))
    return None


def parse_budget(q, costs=None):
    """-> (count, kind, money). count is taken from 'N대' or from money ÷ unit cost; default 10 CCTV."""
    costs = costs or {"new_cctv": 430, "bell": 200}
    q = norm(q)
    kind = "bell" if ("비상벨" in q or "벨" in q) and "CCTV" not in q.upper() and "씨씨" not in q else "cctv"
    money = parse_money(q)
    m = COUNT.search(q)
    if m:
        count = int(m.group(1))
    elif money:
        count = money // (costs["bell"] if kind == "bell" else costs["new_cctv"])
    else:
        count = 10
    return max(1, min(count, 30)), kind, money


DOMAIN = re.compile(r"정류|버스|CCTV|씨씨|cctv|비상벨|벨|밤|안전|안심|괜찮|무섭|위험|동네|지역|읍|면|학교|학원|설치|예산|건의|내리|하차|타고|귀가|퇴근|가로등|어두|순위|어디|제주|서귀포|상위|\d+\s*곳|손볼|개선|공백|우리\s*애")
HELP = re.compile(r"^(안녕|하이|hello|hi)\b|뭘 할 수|뭐 할 수|무엇을 할 수|사용법|어떻게 써|어떻게 사용|도와줘$|^도움|기능이 뭐|뭐야\??$|누구야")
PLAN = re.compile(r"\d+\s*(대|개)|예산|억|천만|만\s*원|얼마면|어디에\s*(달|놓|설치)|설치\s*(하면|한다면|계획)|놓는다면|단다면|달면")
COMPARE = re.compile(r"어느\s*(동네|지역|읍|면|동)|어디가\s*(제일|가장)|가장\s*(심한|비어|부족)|제일\s*(심한|비어|부족)|지역별|동네별|읍면별|비교")
STATS = re.compile(r"몇\s*(곳|개|군데|%|퍼센트)|얼마나\s*(많|있)|많아|많나|많은가|비율|통계|전체적으로|요약|현황")
OFF = re.compile(r"날씨|맛집|식당|카페|주식|코인|뉴스|영화|드라마|노래|축구|야구|게임|숙소|호텔|관광지\s*추천|여행\s*코스|번역|코딩")
CHECK = re.compile(r"내리는데|내려서|내리면|괜찮|안전해|안전한가|위험해|무섭|있어\??|있나|있을까|있는지|어때|어떤가|어떄|봐\s*줘|알려\s*줘")


DATA = re.compile(r"(자료|데이터|파일|csv).{0,12}(올리|올려|넣|추가|바꾸|바꿔|어떤|무슨|뭐|뭘|어디서|받|쓰고|써\??$|쓰는|쓴|있어)"
                  r"|(올리|올려|넣|추가).{0,8}(자료|데이터|파일|csv)|(어떤|무슨)\s*(자료|데이터)|내\s*(자료|데이터)")


def classify(q, place):
    """help | data | offtopic | plan | compare | stats | check | rank"""
    q = norm(q)
    if not q or HELP.search(q):
        return "help"
    if DATA.search(q.lower()) and not re.search(r"\d+\s*(곳|대)|순위|손볼|예산|건의서", q):
        return "data"
    if OFF.search(q) and not re.search(r"정류|버스|CCTV|cctv|비상벨|밤길", q):
        return "offtopic"
    if not DOMAIN.search(q) and not place:
        return "offtopic"
    if PLAN.search(q):
        return "plan"
    if COMPARE.search(q) and not place:
        return "compare"
    if STATS.search(q) and not place:
        return "stats"
    if place and CHECK.search(q) and not re.search(r"\d+\s*곳|건의서|순위|우선", q):
        return "check"
    return "rank"


HELP_TEXT = ("안녕하세요, 안심하차 Agent예요. 제주 버스 정류장 주변에 CCTV와 비상벨이 있는지 살펴보고, 먼저 손볼 곳과 설치 계획을 알려드려요. "
             "이렇게 물어보세요. \"제주 전체에서 먼저 손볼 정류장 10곳 알려줘\", \"밤 10시에 노형에서 내리는데 괜찮아?\", "
             "\"CCTV 5대를 놓는다면 어디가 좋아?\", \"예산 1억이면 어디에 달아야 해?\", \"어느 동네가 제일 비어 있어?\"")
OFF_TOPIC = "그 질문은 제가 답하기 어려워요. 저는 제주 버스 정류장 밤길(CCTV·비상벨·막차)을 살펴보는 Agent예요. "
