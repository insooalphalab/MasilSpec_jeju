import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

pytest.importorskip("h3")

from samchun.spatial import CRS_GEO, build_grid, count_within, nearest_dist_m, points_from_latlng


@pytest.fixture(scope="module")
def grid():
    # 협재 인근 약 2km x 2km 사각형
    boundary = gpd.GeoDataFrame(geometry=[box(126.23, 33.385, 126.25, 33.405)], crs=CRS_GEO)
    return build_grid(boundary)


def test_grid_cell_count(grid):
    # 면적 약 4.1km² / 셀 약 0.1km² → 40개 안팎
    assert 25 < len(grid) < 60
    assert grid.index.is_unique


def test_nearest_and_count(grid):
    pts = points_from_latlng(pd.DataFrame({"lat": [33.3938], "lng": [126.2392]}), "lat", "lng")
    d = nearest_dist_m(grid, pts)
    assert len(d) == len(grid)
    assert d.min() < 200 and d.max() < 2500
    n = count_within(grid, pts, 500)
    assert n.max() == 1 and n.min() == 0
