"""AI Hub '국내 여행로그 데이터(제주도 및 도서지역)' 2차년도(2023 여행) — 실제 여행객 행동으로 점수 검증.

    python -m samchun.travellog          # 압축 해제 + 테마 점수 검증 리포트

원자료: data/raw/aihub_travellog/** (TL/VL_csv.zip, Other.zip). 압축 안 한글 파일명이 깨져 있어
영문 테이블명(tn_visit_area_info_H 등)으로 data/interim/aihub_travellog/2023/ 에 푼다.
GPS zip 은 용량이 커서 아직 쓰지 않는다.
"""
import re
import zipfile
from functools import lru_cache
from pathlib import Path

import pandas as pd

from samchun.config import INTERIM_DIR, PROCESSED_DIR, RAW_DIR

SRC = RAW_DIR / "aihub_travellog"
DST = INTERIM_DIR / "aihub_travellog" / "2023"
NON_PLACE_TYPES = {9, 21, 22, 23, 24}  # 역·터미널, 집, 친구집, 사무실, 숙소 — 방문 '장소'에서 제외


def _clean(name: str) -> str:
    base = name.strip("/").split("/")[-1]
    stem, ext = base.rsplit(".", 1)
    head = re.match(r"[A-Za-z0-9_]+", stem).group(0).rstrip("_")
    return f"{head}{'_H' if stem.endswith('_H') and not head.endswith('_H') else ''}.{ext}"


def extract(include_gps: bool = False) -> Path:
    for z in sorted(SRC.rglob("*.zip")):
        if "gps" in z.stem and not include_gps:
            continue
        sub = DST / z.stem
        if sub.exists() and any(sub.iterdir()):
            continue
        sub.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as f:
            for info in f.infolist():
                if info.is_dir():
                    continue
                with f.open(info) as r, open(sub / _clean(info.filename), "wb") as w:
                    while chunk := r.read(1 << 20):
                        w.write(chunk)
    return DST


def _table(name: str) -> pd.DataFrame:
    extract()
    return pd.concat([pd.read_csv(DST / part / f"{name}.csv", encoding="utf-8-sig", low_memory=False)
                      for part in ("TL_csv", "VL_csv")], ignore_index=True)


@lru_cache
def visits() -> pd.DataFrame:
    """제주 안의 '장소' 방문 (집·숙소·터미널 제외) + H3 셀 + 여행객 세그먼트 플래그."""
    import h3

    from samchun.rentcar import JEJU_BBOX
    from samchun.spatial import H3_RES

    v = _table("tn_visit_area_info_H")
    lat0, lat1, lng0, lng1 = JEJU_BBOX
    v = v[v["Y_COORD"].between(lat0, lat1) & v["X_COORD"].between(lng0, lng1)]
    v = v[~v["VISIT_AREA_TYPE_CD"].isin(NON_PLACE_TYPES)].copy()
    v["h3"] = [h3.latlng_to_cell(a, b, H3_RES) for a, b in zip(v["Y_COORD"], v["X_COORD"])]

    travel = _table("tn_travel_H")
    master = _table("tn_traveller_master_H")
    comp = _table("tn_companion_info_H")
    t = travel.merge(master[["TRAVELER_ID", "AGE_GRP"]], on="TRAVELER_ID", how="left")
    kids = set(comp.loc[comp["COMPANION_AGE_GRP"] == 1, "TRAVEL_ID"])  # 동반자 9세 이하
    mission = t["TRAVEL_MISSION"].fillna("").astype(str).str.split(";")
    has = lambda codes: mission.map(lambda m: bool(set(m) & codes))  # noqa: E731
    seg = pd.DataFrame({
        "TRAVEL_ID": t["TRAVEL_ID"],
        "seg_family_kids": t["TRAVEL_ID"].isin(kids),
        "seg_senior": t["AGE_GRP"] >= 60,
        "seg_hiking": has({"28"}),            # 등반 여행
        "seg_nightlife": has({"7"}),          # 유흥/오락(나이트라이프)
        "seg_wellness": has({"10", "21"}),    # 온천/스파, 웰니스
        "seg_outdoor_sport": has({"5"}),      # 야외 스포츠·레포츠
    })
    return v.merge(seg, on="TRAVEL_ID", how="left")


SEGMENT_THEME = {
    "seg_family_kids": "family",
    "seg_senior": "senior",
    "seg_hiking": "mountain",
    "seg_nightlife": "social",
    "seg_wellness": "quiet_culture",
    "seg_outdoor_sport": "ocean",
}


def validate() -> pd.DataFrame:
    """세그먼트별: 그 여행객들이 방문한 셀의 테마 점수 평균 − 전체 여행객 방문 셀 평균 (lift).

    기대: 각 세그먼트는 자기 테마(SEGMENT_THEME)에서 lift 가 양수이고, 8개 테마 중 상위권이어야 한다.
    """
    scores = pd.read_parquet(PROCESSED_DIR / "scores.parquet")
    v = visits()
    v = v[v["h3"].isin(scores.index)]
    s = scores.loc[v["h3"]].reset_index(drop=True)
    base = s.mean()
    rows = []
    for seg, theme in SEGMENT_THEME.items():
        mask = v[seg].fillna(False).to_numpy()
        lift = s[mask].mean() - base
        rows.append({"segment": seg, "theme": theme, "visits": int(mask.sum()),
                     "travellers": v.loc[mask, "TRAVEL_ID"].nunique(),
                     "own_lift": round(lift[theme], 1), "own_rank": int(lift.rank(ascending=False)[theme]),
                     "best_theme": lift.idxmax(), "best_lift": round(lift.max(), 1)})
    return pd.DataFrame(rows)


def cell_stats() -> pd.DataFrame:
    """셀별 2023 여행객 방문 수·평균 만족도(5점, 방문 5건 이상)·재방문 의향."""
    v = visits()
    g = v.groupby("h3")
    out = pd.DataFrame({
        "tl_visits": g.size(),
        "tl_family_visits": v[v["seg_family_kids"].fillna(False).astype(bool)].groupby("h3").size(),  # 9세 이하 동반 여행
        "tl_satisfaction": g["DGSTFN"].mean(),
        "tl_revisit": g["REVISIT_INTENTION"].mean(),
    })
    out["tl_family_visits"] = out["tl_family_visits"].fillna(0)
    out.loc[out["tl_visits"] < 5, ["tl_satisfaction", "tl_revisit"]] = None
    return out.round(2)


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    v = visits()
    print(f"제주 장소 방문 {len(v):,}건 / 여행 {v['TRAVEL_ID'].nunique():,}건 / 셀 {v['h3'].nunique():,}개")
    print(validate().to_string(index=False))
