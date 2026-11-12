"""지도용 지점(POI) 레이어: 각 지표를 올리거나 깎는 실제 시설·위험 지점.

    python -m samchun.pois      # data/interim/pois.parquet 다시 만들기

레이어마다 (설명, 지표, 효과) — 효과 +1 = 이 지점이 가까이 많을수록 그 지표 점수가 오름, -1 = 내려감.
테마에서의 가점/감점 = 효과 x (그 테마에서 지표 가중치의 부호).
예) 버스 정류소: 대중교통 지표 +1 → 뚜벅이(+30%)엔 가점, 오름·마운틴(-15%)엔 감점.
"""
from functools import lru_cache

import pandas as pd

from samchun.config import INTERIM_DIR

CACHE = INTERIM_DIR / "pois.parquet"

LAYERS: dict[str, tuple[str, str, int]] = {
    "odor": ("악취관리 사업장", "i01_odor_safety", -1),
    "pig": ("양돈장 (마을 단위 추정)", "i01_odor_safety", -1),
    "cleanhouse": ("클린하우스", "i02_cleanhouse", 1),
    "er": ("응급실", "i04_emergency", 1),
    "local_food": ("도 지정 향토음식점", "i05_local_food", 1),
    "model_rest": ("모범음식점", "i05_local_food", 1),
    "pub": ("주점", "i06_walk_drink", 1),
    "delivery": ("배달 음식점", "i07_delivery", 1),
    "barrier_free": ("무장애 장소", "i08_barrier_free", 1),
    "bus": ("버스 정류소", "i09_transit", 1),
    "beach": ("해수욕장", "i10_beach_leisure", 1),
    "marine": ("해양레저 업체", "i10_beach_leisure", 1),
    "oreum": ("오름", "i11_oreum_forest", 1),
    "acc_ped": ("보행사고 다발지점", "i12_safe_road", -1),
    "acc_car": ("차량 사고다발지점", "i12_safe_road", -1),
    "freezing": ("결빙 사고지점", "i12_safe_road", -1),
}
NAME_COLS = ["name", "title", "의료기관", "placeName", "acdntAreaLcNm", "업소명", "사업장명", "상호명", "bizesNm", "dutyName", "nodenm", "beach", "oreumName",
             "oreum_name", "spot_nm", "village", "업체명", "시설명", "명칭"]


def _ll(g) -> pd.DataFrame:
    """GeoDataFrame(어떤 CRS든) 또는 lat/lng DataFrame → lat, lng, name."""
    if hasattr(g, "geometry") and getattr(g, "crs", None) is not None:
        w = g.to_crs(4326)
        out = pd.DataFrame({"lat": w.geometry.y.to_numpy(), "lng": w.geometry.x.to_numpy()}, index=g.index)
    else:
        out = g[["lat", "lng"]].copy()
    col = next((c for c in NAME_COLS if c in g.columns), None)
    out["name"] = g[col].astype(str).to_numpy() if col else ""
    return out.reset_index(drop=True)


def _loaders() -> dict:
    from samchun import features as F
    from samchun.collect.hub import load_raw
    from samchun.spatial import points_from_latlng

    oreum = load_raw("oreum_location").dropna(subset=["latitude", "longitude"])
    return {
        "odor": F.odor_facilities,
        "pig": F.pig_farms,
        "cleanhouse": F.cleanhouses,
        "er": F.emergency_rooms,
        "local_food": F.local_food_restaurants,
        "model_rest": F.model_restaurants,
        "pub": lambda: F.stores(scls=F.PUBS),
        "delivery": lambda: F.stores(scls=F.DELIVERY),
        "barrier_free": lambda: pd.concat([_ll(F._vj(any_tags=F.ACCESSIBLE_TAGS)), _ll(F.kto_barrier_free())]),
        "bus": lambda: F.stop_route_counts().assign(name=lambda d: "지나는 노선 " + d["routes"].astype(int).astype(str) + "개"),
        "beach": F.beaches,
        "marine": F.marine_leisure,
        "oreum": lambda: points_from_latlng(oreum, "latitude", "longitude"),
        "acc_ped": F.accident_hotspots,
        "acc_car": F.car_accident_spots,
        "freezing": F.freezing_hotspots,
    }


def build() -> pd.DataFrame:
    frames = []
    for key, fn in _loaders().items():
        try:
            g = fn()
            d = g if {"lat", "lng", "name"} <= set(g.columns) and not hasattr(g, "crs") else _ll(g)
            frames.append(d.assign(layer=key))
            print(f"  {key:13s} {len(d):6,d}")
        except Exception as e:  # noqa: BLE001 — 한 레이어 실패가 전체를 막지 않게
            print(f"  {key:13s} 실패: {type(e).__name__} {e}")
    out = pd.concat(frames, ignore_index=True).dropna(subset=["lat", "lng"])
    out["name"] = out["name"].fillna("").astype(str).replace({"nan": ""})
    out[["layer", "lat", "lng", "name"]].to_parquet(CACHE)
    return out


@lru_cache
def load() -> pd.DataFrame:
    return pd.read_parquet(CACHE) if CACHE.exists() else build()


def near(lat: float, lng: float, radius_m: float, cap: int = 120) -> list[list]:
    """반경 안의 지점: [layer, lat, lng, name, 거리m] (레이어별 가까운 순 cap 개)."""
    import numpy as np

    p = load()
    dy = (p["lat"].to_numpy() - lat) * 110_540
    dx = (p["lng"].to_numpy() - lng) * 111_320 * np.cos(np.radians(lat))
    d = p.assign(dist=np.hypot(dx, dy))
    d = d[d["dist"] <= radius_m].sort_values("dist").groupby("layer").head(cap)
    return [[r.layer, round(r.lat, 6), round(r.lng, 6), r.name, round(r.dist)] for r in d.itertuples()]


def nearest_each(lat: float, lng: float) -> dict[str, list]:
    """레이어별 가장 가까운 지점: {layer: [이름, 거리m]} — 지도 범위 밖(응급실 등) 안내용."""
    import numpy as np

    p = load()
    dy = (p["lat"].to_numpy() - lat) * 110_540
    dx = (p["lng"].to_numpy() - lng) * 111_320 * np.cos(np.radians(lat))
    d = p.assign(dist=np.hypot(dx, dy)).sort_values("dist").drop_duplicates("layer")
    return {r.layer: [r.name, round(r.dist)] for r in d.itertuples()}


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    df = build()
    print(f"→ {CACHE} ({len(df):,}개)")
