"""좌표계 통일과 H3 그리드 (ai_agent.md §4.1)."""
import geopandas as gpd
import pandas as pd

from samchun.config import INTERIM_DIR, RAW_DIR, themes_config

_G = themes_config()["grid"]
H3_RES: int = _G["h3_resolution"]
CRS_METRIC: str = _G["crs_metric"]
CRS_GEO: str = _G["crs_geo"]


def points_from_latlng(df: pd.DataFrame, lat: str, lng: str) -> gpd.GeoDataFrame:
    """위경도 컬럼을 가진 표 → 거리 계산용 좌표계(EPSG:5179) GeoDataFrame."""
    geom = gpd.points_from_xy(df[lng], df[lat], crs=CRS_GEO)
    return gpd.GeoDataFrame(df, geometry=geom).to_crs(CRS_METRIC)


def load_boundary() -> gpd.GeoDataFrame:
    """제주 행정동 경계 (vuski/admdongkor 전국 행정동 GeoJSON에서 제주만 추출, 캐시)."""
    cache = INTERIM_DIR / "jeju_dong.parquet"
    if cache.exists():
        return gpd.read_parquet(cache)
    src = sorted((RAW_DIR / "boundary").glob("HangJeongDong_*.geojson"))[-1]
    g = gpd.read_file(src)
    g = g[g["sidonm"].str.startswith("제주")][["adm_nm", "adm_cd2", "sggnm", "geometry"]].reset_index(drop=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    g.to_parquet(cache)
    return g


def latlng_to_cell(lat: float, lng: float) -> str:
    import h3

    return h3.latlng_to_cell(lat, lng, H3_RES)


def build_grid(boundary: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """제주 육상 경계 폴리곤 → H3 res9 셀 GeoDataFrame(index=h3, geometry=셀 중심, EPSG:5179)."""
    import h3

    cells: set[str] = set()
    for geom in boundary.to_crs(CRS_GEO).geometry.explode(index_parts=False):
        cells |= set(h3.geo_to_cells(geom, H3_RES))
    cells_sorted = sorted(cells)
    latlng = [h3.cell_to_latlng(c) for c in cells_sorted]
    df = pd.DataFrame({"lat": [p[0] for p in latlng], "lng": [p[1] for p in latlng]}, index=pd.Index(cells_sorted, name="h3"))
    grid = points_from_latlng(df, "lat", "lng")
    # 셀 중심이 속한 행정동 속성(있으면)을 붙인다
    attrs = [c for c in ("adm_nm", "sggnm") if c in boundary.columns]
    if attrs:
        j = gpd.sjoin(grid, boundary.to_crs(CRS_METRIC)[attrs + ["geometry"]], predicate="within", how="left")
        grid[attrs] = j[~j.index.duplicated()][attrs]
    return grid


def nearest_dist_m(grid: gpd.GeoDataFrame, points: gpd.GeoDataFrame) -> pd.Series:
    """셀 중심 → 가장 가까운 시설까지 거리(m). 둘 다 EPSG:5179."""
    j = gpd.sjoin_nearest(grid[["geometry"]], points[["geometry"]], distance_col="dist_m")
    return j.groupby(level=0)["dist_m"].min()


def count_within(grid: gpd.GeoDataFrame, points: gpd.GeoDataFrame, radius_m: float) -> pd.Series:
    """셀 중심 반경 radius_m 안의 시설 수. 둘 다 EPSG:5179."""
    buf = grid[["geometry"]].copy()
    buf["geometry"] = buf.geometry.buffer(radius_m)
    j = gpd.sjoin(buf, points[["geometry"]], predicate="contains")
    return j.groupby(level=0).size().reindex(grid.index, fill_value=0)


MAJOR_ROADS = ("trunk", "primary", "secondary", "tertiary", "trunk_link", "primary_link", "secondary_link", "tertiary_link")


def load_roads() -> gpd.GeoDataFrame:
    """OpenStreetMap 도로망 (Overpass로 받은 data/raw/osm_roads/jeju_roads.json, 캐시). EPSG:5179."""
    cache = INTERIM_DIR / "osm_roads.parquet"
    if cache.exists():
        return gpd.read_parquet(cache)
    import json

    from shapely.geometry import LineString

    with open(RAW_DIR / "osm_roads" / "jeju_roads.json", encoding="utf-8") as f:
        elements = json.load(f)["elements"]
    rows = [
        {"osm_id": e["id"], "highway": e["tags"].get("highway"), "name": e["tags"].get("name"),
         "lanes": e["tags"].get("lanes"), "geometry": LineString([(p["lon"], p["lat"]) for p in e["geometry"]])}
        for e in elements if e["type"] == "way" and len(e.get("geometry", [])) > 1
    ]
    g = gpd.GeoDataFrame(rows, crs=CRS_GEO).to_crs(CRS_METRIC)
    cache.parent.mkdir(parents=True, exist_ok=True)
    g.to_parquet(cache)
    return g


def line_length_in_cells(grid: gpd.GeoDataFrame, lines: gpd.GeoDataFrame) -> pd.Series:
    """H3 셀 폴리곤 안에 들어간 선(도로) 길이 합(m)."""
    import h3
    from shapely.geometry import Polygon

    polys = gpd.GeoDataFrame(
        geometry=[Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)]) for c in grid.index],
        index=grid.index, crs=CRS_GEO,
    ).to_crs(CRS_METRIC)
    pieces = gpd.overlay(lines[["geometry"]], polys.reset_index(), how="intersection", keep_geom_type=True)
    return pieces.assign(len_m=pieces.length).groupby("h3")["len_m"].sum().reindex(grid.index, fill_value=0.0)
