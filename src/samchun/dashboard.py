"""출력 대시보드: 위치 1곳 → 화면용 view JSON → 단일 HTML.

    python -m samchun.dashboard                                   # 예시 4곳 → app/dashboard/example.html
    python -m samchun.dashboard --llm --out app/dashboard/example_llm.html   # 삼춘 브리핑을 LLM 으로
    python -m samchun.dashboard "33.3938,126.2392" --theme social  # 원하는 위치

view JSON 구조 (대시보드 섹션 순서와 같다):
  meta        요청 ID·주소·행정동·H3·좌표·관심 테마·브리핑 모드
  headline    관심 테마(없으면 최고 테마) 점수/백분위, 최고·최저 테마
  themes      8대 테마 점수·백분위 (+ 극성 쌍)
  breakdown   관심 테마 점수 = Σ(지표 정규화 점수 × 가중치) — 지표별 기여도
  alerts      규칙 위반(높음) / 하위 15% 팩트(주의)
  strengths   상위 15% 팩트
  perspectives 테마 점수와 실제 여행객 행동이 엇갈리는 지점
  prescriptions 반경 3km 안의 더 나은 셀 (좌표·거리·방향·점수)
  map         주변 반경 약 2.5km 셀의 관심 테마 점수 (헥사곤 SVG용)
  facts       실측 팩트 전체 (값·단위·도내 백분위·관련 테마)
  brief_md    삼춘 브리핑 원문 (LLM 또는 템플릿)
  data_limits 데이터 한계
"""
import json
import math
from functools import lru_cache
from pathlib import Path

import pandas as pd

from samchun import agent, mbti, pois
from samchun.config import PROCESSED_DIR, ROOT, themes_config, weight_matrix

OUT_DIR = ROOT / "app" / "dashboard"
TEMPLATE = OUT_DIR / "template.html"
MAP_K = 10  # 주변 헥사곤 링 수 (res9 기준 약 2.5km)


def _indicators() -> pd.DataFrame:
    return pd.read_parquet(PROCESSED_DIR / "indicators.parquet")


@lru_cache
def _overall() -> dict[str, pd.Series]:
    """셀별 종합 점수(8개 테마 평균)와 도내 백분위."""
    s = agent._tables()["scores"].mean(axis=1)
    return {"score": s, "pct": (s.rank(pct=True) * 100).round(0)}


def _rx_struct(cell: str, theme: str, radius_km: float = 3.0) -> dict | None:
    """agent.prescription 과 같은 규칙, 문장 대신 구조화된 값."""
    import h3

    t = agent._tables()
    k = math.ceil(radius_km * 1000 / (h3.average_hexagon_edge_length(9, "m") * 1.5))
    cand = [c for c in h3.grid_disk(cell, k) if c in t["scores"].index and c != cell]
    if not cand:
        return None
    sc = t["scores"].loc[cand, theme]
    best = sc.idxmax()
    here_score = float(t["scores"].at[cell, theme])
    if sc[best] <= here_score + 5:
        return None
    here, there = h3.cell_to_latlng(cell), h3.cell_to_latlng(best)
    bearing = math.degrees(math.atan2(there[1] - here[1], there[0] - here[0])) % 360
    return {
        "theme": theme, "theme_label": agent.THEME_LABEL[theme],
        "h3": best, "lat": round(there[0], 5), "lng": round(there[1], 5),
        "dist_km": round(h3.great_circle_distance(here, there, unit="km"), 1),
        "direction": ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][int((bearing + 22.5) // 45) % 8],
        "dong": str(t["grid"].at[best, "adm_nm"]).replace("제주특별자치도 ", ""),
        "score": round(float(sc[best])), "here_score": round(here_score),
    }


def _map(cell: str) -> dict:
    """주변 셀 경계 + 8개 테마 점수 (s: THEME_LABEL 순서 배열, 바다는 None) — 화면에서 테마 전환용."""
    import h3

    t = agent._tables()
    keys = list(agent.THEME_LABEL)
    lat0, lng0 = h3.cell_to_latlng(cell)
    kx = 111_320 * math.cos(math.radians(lat0))  # 경도 1도 → m
    cells = []
    for c in h3.grid_disk(cell, MAP_K):
        b = h3.cell_to_boundary(c)
        poly = [[round((lng - lng0) * kx), round((lat0 - lat) * 110_540)] for lat, lng in b]  # SVG 대체용 (m)
        land = c in t["scores"].index
        cells.append({"h3": c, "poly": poly, "ll": [[round(a, 6), round(o, 6)] for a, o in b],
                      "s": [round(float(t["scores"].at[c, k])) for k in keys] if land else None})
    return {"center": cell, "center_ll": [round(lat0, 6), round(lng0, 6)], "cells": cells,
            "radius_m": round(max(h3.great_circle_distance((lat0, lng0), h3.cell_to_latlng(c), unit="m")
                                  for c in h3.grid_ring(cell, MAP_K)))}


def view(location: str, theme: str | None = None, use_llm: bool = False) -> dict:
    text, p, mode = agent.brief(location, theme, use_llm=use_llm)
    cell = p.h3_cell
    cfg = themes_config()
    labels = agent.THEME_LABEL
    focus = theme or max(p.scores, key=p.scores.get)
    ordered = sorted(p.scores, key=lambda k: -p.scores[k])

    # 테마별 기여도: 지표 점수(0~100) × 가중치 — 8개 테마 모두 (화면에서 전환)
    W = weight_matrix()
    ind = _indicators().loc[cell]
    names = {k: v["name"] for k, v in cfg["indicators"].items()}

    def breakdown_for(th: str) -> list[dict]:
        rows = []
        for key in W.index:
            w = float(W.at[key, th])
            if w == 0:
                continue
            s = float(ind.get(key, 50) if pd.notna(ind.get(key)) else 50)
            rows.append({"key": key, "name": names.get(key, key), "weight": round(w * 100),
                         "indicator": round(s), "contrib": round(s * w, 1)})
        return sorted(rows, key=lambda b: -abs(b["weight"]))

    by_theme = {th: {"breakdown": breakdown_for(th),
                     "rules": agent.evidence(cell, th).rules,
                     "rx": _rx_struct(cell, th)} for th in labels}

    facts = []
    for key, (label, unit, sign, themes) in agent.FACTS.items():
        v = p.fact_telemetry.get(key)
        if v is None:
            continue
        r = agent._fact_rank(key).get(cell)
        facts.append({"key": key, "label": label, "unit": unit, "value": v, "display": agent._fmt(v, unit),
                      "rank": None if sign == 0 or pd.isna(r) else float(r),
                      "themes": list(themes), "focus": focus in themes})

    targets = [focus] + ([ordered[-1]] if ordered[-1] != focus else [])
    rx = [by_theme[th]["rx"] for th in targets if by_theme[th]["rx"]]

    from samchun.geocode import reverse_road

    avg = agent._tables()["scores"].mean()
    ov = _overall()  # 셀별 종합 점수 = 8개 테마 평균
    return {
        "meta": {"request_id": p.request_id, "address": p.address_name, "adm_name": p.adm_name,
                 "road_address": reverse_road(p.coordinate.lat, p.coordinate.lng),
                 "h3": cell, "lat": p.coordinate.lat, "lng": p.coordinate.lng,
                 "focus_theme": focus, "focus_label": labels[focus], "focus_given": theme is not None,
                 "mode": mode},
        "headline": {"score": p.scores[focus], "pct": p.percentiles[focus],
                     "best": ordered[0], "worst": ordered[-1], "top3": ordered[:3],
                     "overall": round(float(ov["score"][cell]), 1), "overall_pct": float(ov["pct"][cell]),
                     "overall_avg": round(float(ov["score"].mean()), 1),
                     "oneliner": agent.llm_oneliner(p.scores, p.strengths) if mode == "llm" else None},
        "themes": [{"key": k, "label": labels[k], "no": i + 1, "score": p.scores[k], "pct": p.percentiles[k],
                    "avg": round(float(avg[k]), 1)}
                   for i, k in enumerate(labels)],
        "polar_pairs": cfg.get("polar_pairs", []),
        "mbti": dict(zip(("code", "axes"), mbti.code_for(p.scores))),  # 마실스펙 MBTI (도감은 html() 에서 한 번만)
        "by_theme": by_theme,  # 테마 전환용: breakdown·rules·rx
        "breakdown": by_theme[focus]["breakdown"],
        "alerts": [{"level": "high", "text": s} for s in p.rule_flags] + [{"level": "mid", "text": s} for s in p.warnings],
        "strengths": p.strengths,
        "perspectives": p.perspectives,
        "prescriptions": rx,
        "map": (mp := _map(cell)),
        # 지도 지점: 테마별 가점/감점 = 효과 x 가중치 부호 (화면에서 계산)
        "poi_layers": {k: {"label": lb, "indicator": ind_key, "effect": eff} for k, (lb, ind_key, eff) in pois.LAYERS.items()},
        "pois": pois.near(*mp["center_ll"], mp["radius_m"] + 300),
        "poi_nearest": pois.nearest_each(*mp["center_ll"]),
        "facts": facts,
        "brief_md": text,
        "data_limits": p.data_limits,
    }


def llm_parts(location: str, theme: str | None = None) -> dict:
    """검색 화면을 먼저 띄운 뒤 채울 LLM 문장만 따로 (삼춘 브리핑 + 한줄평). 실패하면 mode='template'."""
    import requests

    p = agent.build_payload(location, theme)
    try:
        return {"brief_md": agent.llm_brief(p), "oneliner": agent.llm_oneliner(p.scores, p.strengths), "mode": "llm"}
    except requests.RequestException:
        return {"brief_md": None, "oneliner": None, "mode": "template"}


EXAMPLES = [
    ("협재해수욕장 앞", "33.3938,126.2392", "social"),
    ("성판악 탐방로 입구", "33.3846,126.6196", "mountain"),
    ("서귀포 원도심", "33.2490,126.5600", "senior"),
    ("구좌 관광지대 (가족)", "33.4444,126.7789", "family"),  # 아이 동반 방문 26건인데 가족 점수 하위권 → 관점 차이 예시
]


def html(views: list[dict], server: bool = False) -> str:
    data = json.dumps(views, ensure_ascii=False, default=str).replace("</", "<\\/")
    types = json.dumps(mbti.catalog(), ensure_ascii=False).replace("</", "<\\/")
    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("/*__DATA__*/[]", data)
            .replace("/*__MBTI__*/{}", types)
            .replace("/*__SERVER__*/false", "true" if server else "false"))


def example_views(use_llm: bool = False) -> list[dict]:
    return [view(loc, th, use_llm) | {"title": name} for name, loc, th in EXAMPLES]


def with_basemap(v: dict, z: int = 15) -> dict:
    """정적 HTML 용: 지도 영역의 브이월드 배경지도를 이미지 한 장으로 내장 (서버·외부 타일 없이 표시)."""
    from samchun.tiles import basemap

    pts = [p for c in v["map"]["cells"] for p in c["ll"]]
    lats, lngs = [p[0] for p in pts], [p[1] for p in pts]
    plat, plng = 0.006, 0.03  # 지도 칸이 가로로 넓어 좌우 여유를 더 둔다
    bm = basemap(min(lats) - plat, min(lngs) - plng, max(lats) + plat, max(lngs) + plng, z=z)
    return v | {"map": v["map"] | {"basemap": bm}} if bm else v


def render(views: list[dict], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html([with_basemap(v) for v in views]), encoding="utf-8")
    return out


if __name__ == "__main__":
    import argparse
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("location", nargs="?")
    ap.add_argument("--theme")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT_DIR / "example.html")
    a = ap.parse_args()
    if a.location:
        vs = [view(a.location, a.theme, a.llm) | {"title": a.location}]
    else:
        vs = example_views(a.llm)
    print(render(vs, a.out))
