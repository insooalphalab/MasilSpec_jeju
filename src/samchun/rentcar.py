"""렌터카 텔레메트리 (2020~2021, 월별 50m 격자 · 체류 거점) 집계.

    python -m samchun.rentcar        # data/interim/ 에 50m 격자 집계 parquet 생성

원자료 (data/raw/rentcar_*/, 각 24개월):
- 이동빈도[50m 격자]: 해당 칸을 지나간 렌터카 대수 (oid_count). 좌표는 EPSG:5179 격자 중심.
- 체류빈도[50m 격자]: 해당 칸에 머문 렌터카 대수.
- 체류거점: 차량별 정차 지점 (위경도, 시각, 체류 초).

주의: 체류빈도 격자는 공항 남서쪽(도두동·용담2동) 렌터카 반납·대기 구역이 상위를 차지한다.
관광 핫플 판별에는 체류거점의 '서로 다른 차량 수'(spot_cells)를 쓴다.

2020~2021년은 코로나 시기라 관광 교통량이 평소와 다를 수 있다.
"""
import re
from pathlib import Path

import pandas as pd

from samchun.config import INTERIM_DIR, RAW_DIR

GRID_KINDS = {"move": "rentcar_move", "stay": "rentcar_stay"}
SUMMER = (6, 7, 8)


def _month(path: Path) -> tuple[int, int]:
    y, m = re.search(r"(\d{4})_(\d{2})", path.name).groups()
    return int(y), int(m)


def build_grid_counts(kind: str) -> pd.DataFrame:
    """50m 격자별 집계: 24개월 월평균, 여름(6~8월) 월평균, 관측 월수. 값이 있는 칸만."""
    frames = []
    for p in sorted((RAW_DIR / GRID_KINDS[kind]).glob("*.csv")):
        y, m = _month(p)
        df = pd.read_csv(p, usecols=["xcoord", "ycoord", "oid_count"], encoding="utf-8-sig").dropna()
        frames.append(df.assign(year=y, month=m))
    allm = pd.concat(frames, ignore_index=True)
    n_months = allm[["year", "month"]].drop_duplicates().shape[0]
    n_summer = allm[allm["month"].isin(SUMMER)][["year", "month"]].drop_duplicates().shape[0]
    g = allm.groupby(["xcoord", "ycoord"])
    out = pd.DataFrame(
        {
            "monthly_mean": g["oid_count"].sum() / n_months,
            "summer_mean": allm[allm["month"].isin(SUMMER)].groupby(["xcoord", "ycoord"])["oid_count"].sum() / n_summer,
            "months_seen": g.size(),
        }
    ).fillna({"summer_mean": 0.0}).reset_index()
    return out


def grid_counts(kind: str) -> pd.DataFrame:
    """캐시된 50m 격자 집계 (없으면 생성)."""
    cache = INTERIM_DIR / f"rentcar_{kind}_50m.parquet"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        build_grid_counts(kind).to_parquet(cache)
    return pd.read_parquet(cache)


def stay_spots() -> pd.DataFrame:
    """체류 거점 전체 (oid, 시각, 위경도, 체류 초)."""
    cache = INTERIM_DIR / "rentcar_spots.parquet"
    if not cache.exists():
        frames = [pd.read_csv(p, encoding="utf-8-sig") for p in sorted((RAW_DIR / "rentcar_spot").glob("*.csv"))]
        df = pd.concat(frames, ignore_index=True)
        df["time"] = pd.to_datetime(df["time"])
        df.rename(columns={"Diff": "stay_sec"}).to_parquet(cache)
    return pd.read_parquet(cache)


JEJU_BBOX = (33.1, 34.05, 126.1, 127.0)  # lat_min, lat_max, lng_min, lng_max (추자도 포함)


def spot_cells() -> pd.DataFrame:
    """체류 거점 → H3 셀별 '서로 다른 차량 수' (24개월 누적).

    - visit_cars: 20분 이상 정차한 차량 수 = 관광 핫플·상권 활기 (공항 차고지 왜곡 없음)
    - overnight_cars: 8시간 이상 정차 = 숙소 밀집
    - dawn_cars: 04~07시에 정차 시작 = 일출·새벽 등반 들머리 주차
    """
    import h3

    from samchun.spatial import H3_RES

    cache = INTERIM_DIR / "rentcar_spot_cells.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    s = stay_spots()
    lat0, lat1, lng0, lng1 = JEJU_BBOX
    s = s[s["latitude"].between(lat0, lat1) & s["longitude"].between(lng0, lng1)].copy()
    s["h3"] = [h3.latlng_to_cell(a, b, H3_RES) for a, b in zip(s["latitude"], s["longitude"])]
    g = s.groupby("h3")["oid"]
    out = pd.DataFrame(
        {
            "visit_cars": g.nunique(),
            "overnight_cars": s[s["stay_sec"] >= 8 * 3600].groupby("h3")["oid"].nunique(),
            "dawn_cars": s[s["time"].dt.hour.between(4, 6)].groupby("h3")["oid"].nunique(),
        }
    ).fillna(0).astype(int)
    out.to_parquet(cache)
    return out


def to_h3(df: pd.DataFrame, value_cols: list[str], how: str = "sum") -> pd.DataFrame:
    """50m 격자 집계 → H3 res9 셀 집계 (격자 중심이 속한 셀 기준)."""
    import h3
    import pyproj

    from samchun.spatial import CRS_METRIC, H3_RES

    lng, lat = pyproj.Transformer.from_crs(CRS_METRIC, "EPSG:4326", always_xy=True).transform(
        df["xcoord"].to_numpy(), df["ycoord"].to_numpy()
    )
    cells = [h3.latlng_to_cell(a, b, H3_RES) for a, b in zip(lat, lng)]
    return df[value_cols].groupby(pd.Index(cells, name="h3")).agg(how)


if __name__ == "__main__":
    for k in GRID_KINDS:
        d = grid_counts(k)
        print(f"{k}: 값 있는 50m 칸 {len(d):,}개 / 월평균 중앙값 {d['monthly_mean'].median():.1f}대, 최대 {d['monthly_mean'].max():,.0f}대")
    s = stay_spots()
    print(f"spots: {len(s):,}건 / 차량 {s['oid'].nunique():,}대 / {s['time'].min():%Y-%m} ~ {s['time'].max():%Y-%m}")
