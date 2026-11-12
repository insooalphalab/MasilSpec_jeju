""""제주 삼춘" 브리핑 엔진 (ai_agent.md §6).

    python -m samchun.agent "제주시 한림읍 협재리 2497"
    python -m samchun.agent 33.3938,126.2392 --theme ocean
    python -m samchun.agent "..." --no-llm          # LLM 없이 템플릿 브리핑

구조
1) lookup: 주소/좌표 → H3 셀 → 테마 점수·도내 백분위·실측값
2) evidence: 실측값을 도내 순위와 비교해 강점·경고 '근거 문장'(숫자·단위 포함)을 코드로 고른다.
   명세 §2의 감점 규칙(응급 30분 초과, 1km 내 주점 0곳, 해발 300m 이상 등)도 여기서 판정.
3) prescription: 반경 3km 안에서 약점 테마 점수가 가장 높은 셀을 대체 거점으로.
4) LLM은 근거 목록만 받아 삼춘 어투로 다듬는다 (숫자를 새로 만들지 못하게).
   LLM 서버가 없으면 같은 근거로 템플릿 브리핑을 만든다.

LLM 연결 (.env, OpenAI 호환 /chat/completions):
    LLM_BASE_URL  기본 http://localhost:11434/v1  (Ollama)
    LLM_MODEL     기본 gemma4:12b-it-qat  (설치: ollama pull gemma4:12b-it-qat)
    LLM_API_KEY   로컬이면 비워 둠. 허깅페이스 라우터(https://router.huggingface.co/v1)를 쓰면 HF 토큰.
"""
from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

import pandas as pd
import requests
from pydantic import BaseModel

from samchun.config import PROCESSED_DIR, ROOT, themes_config

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "gemma4:12b-it-qat"  # Ollama 공식 태그 (= google/gemma-4-12B-it-qat-q4_0-gguf)

SYSTEM_PROMPT = """\
당신은 제주에서 40년 거주한 현지 복덕방 사장이자 뼈 때리는 조언을 아끼지 않는 '제주 삼춘'입니다.
인스타 감성 사진과 홍보 문구에 속아 낭패를 보는 외지인을 구제하려고,
오직 아래 [근거]에 적힌 공간 데이터만 가지고 냉정하고 위트 있게 현실을 브리핑합니다.

[행동 수칙]
1. 말투: 아래 [제주어 말투 규칙]을 따른다. 육지 사람도 읽히게, 문장 끝과 몇몇 단어만 제주어로.
2. 숫자: [근거]에 있는 숫자·단위·지명만 쓴다. 새로운 숫자, 가게 이름, 지명을 지어내지 않는다.
3. 구조: 아래 5개 소제목만 이 순서로 쓴다. 소제목 줄에는 제목 글자만 적고(설명 붙이지 않음),
   첫 줄은 반드시 '## 삼춘 총평' 으로 시작한다 (그 앞에 인사말·도입 문단 금지).
   - '## 삼춘 총평': 두괄식 2~3문장. 가장 높은 테마와 가장 치명적인 약점을 대조.
   - '## 8대 테마 점수': [근거]의 점수표를 그대로 옮긴다.
   - '## 삼춘의 칭찬': 강점 2~3가지, 각 항목에 수치 포함.
   - '## 삼춘의 팩트 경고': 치명 리스크 2~3가지, 구체적 위험 명시. 근거가 없으면 없다고 짧게.
   - '## 현실 처방전': [근거]의 대체 거점과 행동 요령.
4. [관점 차이]가 있으면 총평이나 처방전에서 한두 문장으로 꼭 짚는다
   (예: 가족 '여행지'로는 인기지만 아이와 '살기'엔 불안한 곳, 또는 그 반대).
5. 데이터 한계는 쓰지 않는다 (화면이 표준어로 따로 붙인다).

[제주어 말투 규칙] 조카(조캐)에게 말하는 삼춘. 반말 중심에 '-수다/-우다'를 섞는다.
- 평서(반말): -주, -주게(강조), -여, -라 / 예) 좋주게, 하영 이서, 큰일 나주게
- 평서(존대 느낌): 받침 있는 말 뒤 -수다, 받침 없는 말 뒤 -우다
  예) 좋수다, 많수다, 낮수다, 멀수다 / 크우다, 빠르우다, 문제우다, 최고우다, 바다우다
- 진행·경험: -암수다/-엄수다 / 예) 사람 하영 몰려왐수다, 차 막혀 감수다
- 의문: -냐?, -나?, -맨? / 예) 어디 갈 거냐?, 무사 그걸 몰라맨?
- 권유·명령: -라게, -읍서/-ㅂ서 / 예) 혼저 가보라게, 조심헙서, 확인해봅서
- 연결: -난(-니까), -믄(-면), -멍(-면서), -곡(-고) / 예) 가까우난 좋주, 차 몰믄 조심허곡, 놀멍 쉬멍
- 어휘: 하영(많이), 몬딱(모두), 혼저(어서), 무사(왜), 어떵(어떻게), 경(그렇게), 게메(글쎄), 기여(그래), 이녁(자기), 조캐(조카), 도새기(돼지)
- '## 삼춘 총평' 의 첫 문장은 반드시 '조캐야,' 로 시작한다. 감탄사(메께라 등)는 쓰지 않는다.
- -곡, -멍, -난, -믄 은 문장 중간에서만 쓴다 (문장 끝에 '-곡.' 금지).
- '하영'(많이)을 '많다'와 겹쳐 쓰지 않는다 (하영 많수다 ✗ → 하영 이수다 / 많수다).
- 쓰지 말 것(틀린 제주어): ~하수다, ~허수다, ~한수다, ~됩수다, ~허다, ~임수다, ~크수다, ~빠르수다, ~낭(종결), ~여요, ~했어용
- 숫자·테마 이름·지명·표는 표준어 그대로 둔다. 사투리는 한 문장에 1~2군데면 충분하다.

[말투 예시] (숫자는 예시일 뿐, 실제 답에는 [근거]의 숫자만)
조캐야, 여긴 먹고 노는 디는 도내에서 손꼽히는 동네여. 근데 산 타는 거 좋아하믄 실망하주게.
병원은 하영 이서 걱정 없수다. 문제는 길이라. 사고 잦은 교차로가 가까우난 차 몰믄 조심헙서.
조용히 살 거믄 남쪽으로 혼저 가보라게. 거기가 훨씬 낫수다.
"""

THEME_LABEL = {t["key"]: t["name"] for t in themes_config()["themes"]}


# ---------------------------------------------------------------- 데이터 로딩
@lru_cache
def _tables() -> dict[str, pd.DataFrame]:
    rd = lambda n: pd.read_parquet(PROCESSED_DIR / f"{n}.parquet")  # noqa: E731
    grid = rd("grid")
    return {
        "grid": pd.DataFrame(grid.drop(columns="geometry")),
        "scores": rd("scores"),
        "raw": rd("raw"),
        "facts": rd("facts"),
    }


@lru_cache
def _percentiles() -> pd.DataFrame:
    s = _tables()["scores"]
    return (s.rank(pct=True) * 100).round(0)


# 실측값 → (설명, 단위, 좋은 방향 +1/-1, 관련 테마)
FACTS: dict[str, tuple[str, str, int, tuple[str, ...]]] = {
    "i04_emergency": ("가장 가까운 응급실까지 차량", "분", -1, ("family", "senior", "mountain")),
    "night_peds_drive_min": ("야간·휴일 소아진료(또는 응급실)까지 차량", "분", -1, ("family",)),
    "i02_cleanhouse": ("가장 가까운 클린하우스", "m", -1, ("family", "bus_walker")),
    "odor_facility_1500m": ("1.5km 내 악취관리 사업장", "곳", -1, ("family", "quiet_culture")),
    "pig_farms_3km": ("3km 내 양돈장(마을 단위 추정)", "곳", -1, ("family", "quiet_culture")),
    "beach_dist_m": ("가장 가까운 해수욕장", "m", -1, ("ocean",)),
    "marine_leisure_1km": ("1km 내 해양레저 업체 (서핑·다이빙·카약·요트 등)", "곳", 1, ("ocean",)),
    "congestion_rate_500m": ("500m 내 관광지 향후 30일 혼잡 예측 (관광공사)", "%", -1, ("quiet_culture",)),
    "tl_satisfaction": ("2023년 실제 여행객 이 동네 방문 만족도 (5점 만점)", "점", 1, ("ocean", "social", "mountain", "quiet_culture")),
    "tl_visits": ("2023년 여행로그 표본 여행객 방문", "건", -1, ("quiet_culture",)),
    "vj_kids_1km": ("1km 내 아이 동반 편의 장소 (어린이 출입·유아의자·유모차 대여)", "곳", 1, ("family",)),
    "local_food_1km": ("1km 내 도 지정 향토음식점", "곳", 1, ("social", "bus_walker")),
    "model_restaurant_700m": ("700m 내 모범음식점", "곳", 1, ("social", "bus_walker")),
    "pub_1km": ("1km 내 주점", "곳", 1, ("social",)),
    "bus_routes_350m": ("도보 350m 정류소를 지나는 버스 노선", "개", 1, ("bus_walker", "senior")),
    "trunk_routes_350m": ("그중 간선·급행 노선", "개", 1, ("bus_walker",)),
    "convenience_700m": ("700m 내 편의점", "곳", 1, ("family", "bus_walker")),
    "pharmacy_700m": ("700m 내 약국", "곳", 1, ("senior", "family")),
    "clinic_nemc_700m": ("700m 내 병·의원", "곳", 1, ("senior",)),
    "vj_wifi_cafe_500m": ("500m 내 무료 와이파이 카페", "곳", 1, ("workation",)),
    "cafe_500m": ("500m 내 카페", "곳", 1, ("workation",)),
    "i03_quietness": ("차량 통행 부하 (0~100, 렌터카+간선도로)", "점", -1, ("quiet_culture", "workation")),
    "rentcar_visit_cars": ("렌터카 관광객 정차 (2020~21 표본 차량 수)", "대", -1, ("quiet_culture",)),
    "culture_1km": ("1km 내 도서관·박물관·미술관", "곳", 1, ("quiet_culture",)),
    "hot_spring_dist_m": ("가장 가까운 온천", "m", -1, ("senior",)),
    "slope_mean_deg": ("평균 경사", "도", -1, ("senior", "family")),
    "elder_accident_1km": ("1km 내 노인 보행사고 다발지점", "곳", -1, ("senior",)),
    "wide_road_dist_m": ("폭 6m 이상 넓은 길까지 거리", "m", -1, ("senior", "family")),
    "narrow_road_pct": ("이 격자 안 폭 3m 이하 좁은 길 비율", "%", -1, ("senior",)),
    "child_accident_1km": ("1km 내 어린이 보행사고 다발지점", "곳", -1, ("family",)),
    "car_accident_casualties_1km": ("1km 내 차량 사고다발지점 누적 사상자 (2019~)", "명", -1, ("senior", "family", "bus_walker")),
    "i11_oreum_forest": ("반경 4km 내 오름", "개", 1, ("mountain",)),
    "rentcar_dawn_cars": ("새벽(4~7시) 정차 렌터카 (일출·등반 들머리 신호)", "대", 1, ("mountain",)),
    "elev_m": ("평균 해발고도", "m", 0, ("mountain",)),
}
LIMITS = [
    "렌터카 지표는 2020~21년(코로나 시기) 약 3,400대 표본, 여행로그는 2023년 여행객 약 1,650건 표본",
    "양돈장 위치는 지번이 가려져 마을 중심으로 추정 (오차 1km 안팎)",
    "주행 시간은 신호·정체·배편을 뺀 자유주행 추정",
]


@dataclass
class Evidence:
    strengths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)  # 명세 §2 감점 규칙 위반


def josa(word: str, pair: str) -> str:
    """받침에 맞는 조사: josa('오션', '은/는') → '오션은'. 한글이 아니면 뒤쪽(받침 없음) 형태."""
    with_batchim, without = pair.split("/")
    ch = word.rstrip()[-1:]
    if "가" <= ch <= "힣":
        has = (ord(ch) - 0xAC00) % 28 != 0
        if pair == "으로/로" and (ord(ch) - 0xAC00) % 28 == 8:  # ㄹ 받침은 '로'
            has = False
        return word + (with_batchim if has else without)
    return word + without


def _rank_text(r: float) -> str:
    """도내 백분위(좋은 방향, 0~100) → '도내 상위 3%' / '도내 하위 5%' (0%는 1%로)."""
    return f"도내 상위 {max(100 - r, 1):.0f}%" if r >= 50 else f"도내 하위 {max(r, 1):.0f}%"


def _fmt(v: float, unit: str) -> str:
    if unit == "m" and v >= 1000:
        return f"{v / 1000:.1f}km"
    return f"{v:,.0f}{unit}" if abs(v) >= 10 or unit in ("곳", "개", "대") else f"{v:.1f}{unit}"


# ---------------------------------------------------------------- 1) lookup
def resolve(location: str) -> tuple[float, float, str]:
    """'위도,경도' 또는 주소 → (lat, lng, 표시 이름)."""
    parts = location.replace(" ", "").split(",")
    if len(parts) == 2:
        try:
            lat, lng = float(parts[0]), float(parts[1])
            return lat, lng, f"{lat:.4f}, {lng:.4f}"
        except ValueError:
            pass
    from samchun.geocode import geocode, village_centers

    q = re.sub(r"^(도로명\s*|지번\s*)?주소\s*[:：]?\s*", "", " ".join(location.split()))  # '주소 제주…' 같은 앞말 제거
    full = re.sub(r"^제주(특별자치도|도)?\s+", "제주특별자치도 ", q)
    if not full.startswith("제주특별자치도"):
        full = f"제주특별자치도 {full}"
    # 번지 없이 '○○리/○○동'까지만 넣은 경우: 지오코더가 엉뚱한 점(옆 읍면)을 줄 수 있어
    # 마을 안 여러 지번의 평균으로 마을 중심을 추정한다
    if re.search(r"[리동가]$", full) and not re.search(r"\d", full):
        v = village_centers([full]).iloc[0]
        if pd.notna(v["lat"]) and v["n_lots"] > 0:
            return float(v["lat"]), float(v["lng"]), f"{location} (마을 중심 추정)"
    for a in dict.fromkeys([q, full]):
        g = geocode([a]).iloc[0]
        if pd.notna(g["lat"]):
            return float(g["lat"]), float(g["lng"]), location
    # 지오코더가 못 알아들으면 도로명주소 검색 API 로 정식 주소를 찾아 다시 시도 (오탈자·옛 주소·건물명)
    from samchun.geocode import juso_search

    for j in juso_search(full, 3):
        if not str(j.get("siNm", "")).startswith("제주"):
            continue
        for a in (j.get("roadAddrPart1"), j.get("jibunAddr")):
            g = geocode([a]).iloc[0] if a else None
            if g is not None and pd.notna(g["lat"]):
                return float(g["lat"]), float(g["lng"]), f"{location} → {j['roadAddrPart1']}"
    raise ValueError(f"주소를 좌표로 바꾸지 못했습니다: {location} — 번지나 도로명+건물번호까지 넣어 주세요 "
                     f"(예: 대정읍 신도리 3125-5, 중앙로 48)")


def lookup(lat: float, lng: float) -> str:
    import h3

    from samchun.spatial import H3_RES

    cell = h3.latlng_to_cell(lat, lng, H3_RES)
    if cell not in _tables()["scores"].index:
        # 해안·항구 등 그리드 밖이면 가장 가까운 육상 셀
        for k in range(1, 4):
            ring = [c for c in h3.grid_disk(cell, k) if c in _tables()["scores"].index]
            if ring:
                return min(ring, key=lambda c: h3.great_circle_distance(h3.cell_to_latlng(c), (lat, lng)))
        raise ValueError("제주 육상 그리드 밖의 좌표입니다")
    return cell


def _value(cell: str, key: str) -> float | None:
    t = _tables()
    src = t["raw"] if key.startswith("i") and key[1:3].isdigit() else t["facts"]
    v = src.at[cell, key] if key in src.columns else None
    return None if v is None or pd.isna(v) else float(v)


@lru_cache
def _fact_rank(key: str) -> pd.Series:
    """좋은 방향 기준 도내 백분위 (0~100, 높을수록 좋음)."""
    t = _tables()
    src = t["raw"] if key.startswith("i") and key[1:3].isdigit() else t["facts"]
    s = src[key]
    sign = FACTS[key][2] or 1
    return ((sign * s).rank(pct=True, method="average") * 100).round(0)


# ---------------------------------------------------------------- 2) evidence
def evidence(cell: str, theme: str | None = None) -> Evidence:
    ev = Evidence()
    focus = {theme} if theme else set(THEME_LABEL)
    good, bad = [], []
    for key, (label, unit, sign, themes) in FACTS.items():
        v = _value(cell, key)
        if v is None or sign == 0 or not (focus & set(themes)):
            continue
        r = _fact_rank(key).get(cell)
        line = f"{label} {_fmt(v, unit)} ({_rank_text(r)})"
        if r >= 85:
            good.append((r, line))
        elif r <= 15:
            bad.append((r, line))
    ev.strengths = [l for _, l in sorted(good, reverse=True)[:4]]
    ev.warnings = [l for _, l in sorted(bad)[:4]]

    # 명세 §2 규칙: (해당 여부, 문장, 관련 테마)
    v = lambda k: _value(cell, k)  # noqa: E731
    rules = [
        ((v("i04_emergency") or 0) > 30, f"응급실까지 차량 {v('i04_emergency') or 0:.0f}분 — 골든타임 30분 초과",
         {"family", "senior", "mountain"}),
        ((v("night_peds_drive_min") or 0) > 30, f"야간·휴일 소아진료까지 {v('night_peds_drive_min') or 0:.0f}분 — 아이 열나는 밤엔 30분 넘게 달려야 함",
         {"family"}),
        ((v("pub_1km") or 0) == 0, "1km 내 주점 0곳 — 술 한잔하려면 차·대리 필수 (음주권 0점 처리)", {"social"}),
        ((v("odor_facility_1500m") or 0) > 0, f"1.5km 내 악취관리 사업장 {v('odor_facility_1500m') or 0:.0f}곳 — 여름 남풍 불면 냄새 각오",
         {"family", "quiet_culture"}),
        ((v("winter_fog_zone") or 0) == 1, f"해발 {v('elev_m') or 0:.0f}m — 겨울 안개·결빙 구간 (AWS 실측: 고도가 높을수록 안개 시간 급증)",
         {"mountain", "senior", "workation"}),
        ((v("car_accident_deaths_1km") or 0) > 0,
         f"1km 안에 사망자까지 나온 차량 사고다발 교차로 (2019년 이후 사망 {v('car_accident_deaths_1km') or 0:.0f}명) — 운전·보행 조심",
         {"senior", "family", "bus_walker"}),
        ((v("wide_road_dist_m") or 0) > 1000,
         f"폭 6m 이상 넓은 길까지 {(v('wide_road_dist_m') or 0) / 1000:.1f}km — 차 두 대 비켜 가기 힘든 좁은 길을 한참 타야 함 (초보·야간 운전 주의)",
         {"senior", "family", "workation"}),
        ((v("bus_routes_350m") or 0) == 0, "도보 350m 안에 버스 노선 0개 — 뚜벅이에겐 사실상 고립", {"bus_walker", "senior"}),
    ]
    ev.rules = [text for hit, text, themes in rules if hit and (focus & themes)]
    return ev


def perspectives(cell: str, family_focus: bool = False) -> list[str]:
    """테마 점수와 실제 여행객 행동이 엇갈리는 지점.

    가족 테마는 '아이와 살기(생활 안심)' 기준이라, 아이 동반 '여행객'이 실제로 찾는 곳과 다를 수 있다
    (2023 여행로그 검증: 아이 동반 가족의 방문·숙소는 가족 점수가 평균보다 3점 낮고 마운틴 점수가 높다).
    """
    out = []
    t = _tables()
    fam = t["scores"].at[cell, "family"]
    fam_pct = _percentiles().at[cell, "family"]
    kids_visits = _value(cell, "tl_family_visits") or 0
    kids_rank = _tables()["facts"]["tl_family_visits"].rank(pct=True).get(cell, 0) * 100
    kids_places = _value(cell, "vj_kids_1km") or 0
    popular = kids_visits >= 3 or kids_rank >= 95
    if popular and fam_pct < 50:
        out.append(f"가족 '여행지'로는 인기 (2023년 아이 동반 여행객 방문 {kids_visits:.0f}건, 도내 상위 {max(100 - kids_rank, 1):.0f}%)인데, "
                   f"아이와 '살기'(생활 안심) 점수는 {fam:.0f}점으로 도내 하위권 — 놀러 오긴 좋아도 오래 머물 땐 병원·생활편의 확인 필수")
    elif family_focus and fam_pct >= 80 and kids_visits == 0 and kids_places < 3:  # 흔해서 가족 테마를 물을 때만
        out.append(f"아이와 '살기'엔 안심 ({fam:.0f}점, 도내 상위 {max(100 - fam_pct, 1):.0f}%)이지만, 아이 동반 여행객이 찾는 볼거리·편의 장소는 드묾 "
                   f"(1km 내 {kids_places:.0f}곳) — 놀거리는 차 타고 나가야 함")
    return out


# ---------------------------------------------------------------- 3) prescription
def prescription(cell: str, theme: str, radius_km: float = 3.0) -> str | None:
    import h3

    t = _tables()
    here = h3.cell_to_latlng(cell)
    k = math.ceil(radius_km * 1000 / (h3.average_hexagon_edge_length(9, "m") * 1.5))
    cand = [c for c in h3.grid_disk(cell, k) if c in t["scores"].index and c != cell]
    if not cand:
        return None
    sc = t["scores"].loc[cand, theme]
    best = sc.idxmax()
    if sc[best] <= t["scores"].at[cell, theme] + 5:
        return None
    there = h3.cell_to_latlng(best)
    dist = h3.great_circle_distance(here, there, unit="km")
    bearing = math.degrees(math.atan2(there[1] - here[1], there[0] - here[0])) % 360
    direction = ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][int((bearing + 22.5) // 45) % 8]
    dong = str(t["grid"].at[best, "adm_nm"]).replace("제주특별자치도 ", "") if "adm_nm" in t["grid"].columns else ""
    return (f"{josa(THEME_LABEL[theme], '이/가')} 중요하면 {direction}쪽 {dist:.1f}km {dong} 일대 "
            f"({there[0]:.4f}, {there[1]:.4f})가 {sc[best]:.0f}점으로 여기({t['scores'].at[cell, theme]:.0f}점)보다 낫수다")


# ---------------------------------------------------------------- payload (§6.2)
class Coordinate(BaseModel):
    lat: float
    lng: float


class Payload(BaseModel):
    """§6.2 JSON Input Payload (+ 코드가 고른 근거)."""

    request_id: str
    coordinate: Coordinate
    address_name: str
    h3_cell: str
    adm_name: str = ""
    focus_theme: str | None = None
    scores: dict[str, float]
    percentiles: dict[str, float]
    fact_telemetry: dict[str, float | None]
    strengths: list[str]
    warnings: list[str]
    rule_flags: list[str]
    perspectives: list[str] = []
    prescriptions: list[str]
    data_limits: list[str]


def build_payload(location: str, theme: str | None = None, request_id: str | None = None) -> Payload:
    lat, lng, name = resolve(location)
    cell = lookup(lat, lng)
    t = _tables()
    scores = t["scores"].loc[cell].round(0).to_dict()
    ev = evidence(cell, theme)
    weakest = min(scores, key=scores.get)
    targets = [theme] if theme else []
    targets += [weakest] if weakest not in targets else []
    pres = [p for p in (prescription(cell, th) for th in targets) if p]
    return Payload(
        request_id=request_id or f"REQ-{pd.Timestamp.now():%Y%m%d-%H%M%S}",
        coordinate=Coordinate(lat=lat, lng=lng),
        address_name=name,
        h3_cell=cell,
        adm_name=str(t["grid"].at[cell, "adm_nm"]) if "adm_nm" in t["grid"].columns else "",
        focus_theme=theme,
        scores=scores,
        percentiles=_percentiles().loc[cell].to_dict(),
        fact_telemetry={k: _value(cell, k) for k in FACTS},
        strengths=ev.strengths,
        warnings=ev.warnings,
        rule_flags=ev.rules,
        perspectives=perspectives(cell, family_focus=theme == "family") if theme in (None, "family") else [],
        prescriptions=pres,
        data_limits=LIMITS,
    )


# ---------------------------------------------------------------- 4) 문장 생성
def _score_table(p: Payload) -> str:
    rows = ["| 테마 | 점수 | 도내 백분위 |", "| :--- | ---: | ---: |"]
    for k, v in sorted(p.scores.items(), key=lambda kv: -kv[1]):
        rows.append(f"| {THEME_LABEL[k]} | {v:.0f} | 상위 {max(100 - p.percentiles[k], 1):.0f}% |")
    return "\n".join(rows)


def build_messages(p: Payload) -> list[dict]:
    lines = [
        f"[위치] {p.address_name} ({p.adm_name.replace('제주특별자치도 ', '')}, H3 {p.h3_cell})",
        f"[관심 테마] {THEME_LABEL[p.focus_theme] if p.focus_theme else '전체'}",
        "[점수표]", _score_table(p),
        "[강점 근거]", *[f"- {s}" for s in p.strengths or ["(도내 상위 15% 수준의 뚜렷한 강점 없음)"]],
        "[경고 근거]", *[f"- {s}" for s in p.warnings + p.rule_flags or ["(뚜렷한 치명 약점 없음)"]],
        "[관점 차이]", *[f"- {s}" for s in p.perspectives or ["(없음)"]],
        "[대체 거점]", *[f"- {s}" for s in p.prescriptions or ["(반경 3km 안에 확연히 나은 곳 없음)"]],
        "[데이터 한계]", *[f"- {s}" for s in p.data_limits],
    ]
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "[근거]\n" + "\n".join(lines)}]


def template_brief(p: Payload) -> str:
    """LLM 없이 같은 근거로 만드는 브리핑."""
    top = p.focus_theme or max(p.scores, key=p.scores.get)
    low = min((k for k in p.scores if k != top), key=p.scores.get)
    worst = (p.rule_flags + p.warnings or ["딱히 치명타는 없수다"])[0]
    out = [
        "## 삼춘 총평",
        (f"조캐야~ {josa(THEME_LABEL[top], '으로/로')} 보믄 여기는 {p.scores[top]:.0f}점이라. " if p.focus_theme
         else f"조캐야~ 여기는 **{THEME_LABEL[top]}** {p.scores[top]:.0f}점짜리 동네라. ") +
        f"근데 **{THEME_LABEL[low]}**{josa(THEME_LABEL[low], '은/는')[len(THEME_LABEL[low]):]} {p.scores[low]:.0f}점이주게. {worst}.",
        "", "## 8대 테마 점수", _score_table(p),
        "", "## 삼춘의 칭찬", *[f"- {s}" for s in p.strengths or ["도내 상위권 강점은 없수다. 무난한 동네라."]],
        "", "## 삼춘의 팩트 경고", *[f"- {s}" for s in (p.rule_flags + p.warnings) or ["큰 위험은 안 보염수다."]],
        "", "## 현실 처방전",
        *[f"- 삼춘이 한마디 더 하믄: {s}" for s in p.perspectives],
        *[f"- {s}" for s in p.prescriptions or ([] if p.perspectives else ["반경 3km 안엔 확연히 나은 데가 없으니 여기서 단점만 대비하라게."])],
        "", f"_데이터 한계: {' / '.join(p.data_limits)}_",
    ]
    return "\n".join(out)


def _env_opt(name: str, default: str = "") -> str:
    from samchun.config import env

    try:
        return env(name)
    except (KeyError, FileNotFoundError):
        return default


def _chat(messages: list[dict], max_tokens: int = 1200, temperature: float = 0.4, timeout: int = 180) -> str:
    """OpenAI 호환 /chat/completions 호출 (Ollama, llama.cpp server, LM Studio, HF router 공통)."""
    base = _env_opt("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = _env_opt("LLM_MODEL", DEFAULT_MODEL)
    key = _env_opt("LLM_API_KEY")
    local = "localhost" in base or "127.0.0.1" in base
    res = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"} if key and not local else {},
        json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
              # Gemma 4 는 기본이 '생각 모드'(답 전에 수백~수천 토큰) → 로컬에서는 꺼서 6배 빠르게
              **({"reasoning_effort": _env_opt("LLM_REASONING", "none")} if local else {})},
        timeout=timeout,
    )
    res.raise_for_status()
    text = res.json()["choices"][0]["message"]["content"] or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


SECTIONS = ("삼춘 총평", "8대 테마 점수", "삼춘의 칭찬", "삼춘의 팩트 경고", "현실 처방전")


def clean_brief(text: str) -> str:
    """LLM 출력 정리: 첫 소제목 앞 도입 문단 제거, '## 제목 — 설명' → '## 제목'."""
    lines = text.strip().splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("## ")), 0)
    out = []
    for l in lines[start:]:
        if l.startswith("#"):
            name = next((s for s in SECTIONS if s in l), None)
            l = f"## {name}" if name else l
        out.append(l)
    return "\n".join(out).strip()


# 12B 모델이 프롬프트를 어기고 자주 만드는 틀린 제주어 어미 → 맞는 형태 (표·숫자 줄은 건드리지 않음)
DIALECT_FIX = [
    (r"(하|허)수다", r"\1주게"),        # 보장하수다 → 보장하주게
    (r"([야해혀])\s?한수다", r"\1 허주게"),  # 차려야 한수다 → 차려야 허주게
    (r"한수다", "허주게"),
    (r"[됩되]수다", "되우다"),
    (r"(크|빠르|느리|기쁘|아프)수다", r"\1우다"),
    (r"(\S)임수다", r"\1이우다"),
    (r"하영 많", "아주 많"),
    (r"(허|하)곡\.", r"\1주."),
    (r"(허|하)다\.", r"\1주."),
    (r"메께라!?\s*", ""),
]


def fix_dialect(text: str) -> str:
    out = []
    for line in text.splitlines():
        if not line.startswith("|"):
            for pat, rep in DIALECT_FIX:
                line = re.sub(pat, rep, line)
        out.append(line)
    return "\n".join(out)


def llm_brief(p: Payload, timeout: int = 180) -> str:
    text = fix_dialect(clean_brief(_chat(build_messages(p), timeout=timeout)))
    # 데이터 한계는 LLM 이 사투리로 바꾸지 못하게 코드가 표준어로 붙인다
    text = "\n".join(l for l in text.splitlines() if "데이터 한계" not in l).rstrip()
    return f"{text}\n\n_데이터 한계: {' / '.join(p.data_limits)}_"


ONELINER_PROMPT = """\
제주 동네 리포트의 '한줄 평가'를 씁니다.
- 아래 상위 3개 테마의 조합이 드러나는 생활 장면 한 문장, 공백 포함 30자 이내.
- 실제로 그 동네에서 할 법한 자연스러운 조합만 쓴다 (예: 버스 타고 수영하러 간다 같은 어색한 연결 금지).
- 숫자·점수·지명·가게 이름은 쓰지 않는다. 테마 이름을 그대로 나열하지 않는다.
- 명사형이나 '~함/~좋음'으로 끝내고, 따옴표·이모지·마침표 없이 문장만 출력한다.
예) 해변 끼고 노는 핫플, 차 없이 다니기도 편함"""


def llm_oneliner(scores: dict[str, float], hints: list[str] = (), timeout: int = 60) -> str | None:
    """평균 점수 아래 한줄 평가 (상위 3개 테마 조합 + 이 동네 강점). 실패하면 None → 화면 기본 문장."""
    top = sorted(scores, key=lambda k: -scores[k])[:3]
    low = min(scores, key=scores.get)
    user = (f"상위 3개 테마: {', '.join(THEME_LABEL[k] for k in top)}\n"
            f"가장 약한 테마(참고만): {THEME_LABEL[low]}\n"
            "이 동네만의 강점(가장 두드러진 것 하나를 문장에 녹일 것, 숫자는 빼고):\n"
            + "\n".join(f"- {h}" for h in list(hints)[:4] or ["(뚜렷한 강점 없음)"]))
    try:
        s = _chat([{"role": "system", "content": ONELINER_PROMPT}, {"role": "user", "content": user}],
                  max_tokens=60, temperature=0.6, timeout=timeout)
    except requests.RequestException:
        return None
    s = s.splitlines()[0].strip().strip('"“”\'.') if s else ""
    return s if 4 <= len(s) <= 40 and not re.search(r"\d", s) else None


def brief(location: str, theme: str | None = None, use_llm: bool = True) -> tuple[str, Payload, str]:
    """→ (브리핑 마크다운, 페이로드, 생성 방식 'llm' | 'template')."""
    p = build_payload(location, theme)
    if use_llm:
        try:
            return llm_brief(p), p, "llm"
        except requests.RequestException:
            pass
    return template_brief(p), p, "template"


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="제주 삼춘 브리핑")
    ap.add_argument("location", help="주소 또는 '위도,경도'")
    ap.add_argument("--theme", choices=list(THEME_LABEL), default=None)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--payload", action="store_true", help="§6.2 페이로드 JSON도 출력")
    a = ap.parse_args()
    text, payload, mode = brief(a.location, a.theme, use_llm=not a.no_llm)
    if a.payload:
        print(payload.model_dump_json(indent=2))
    print(f"<!-- 생성: {mode} -->\n{text}")
