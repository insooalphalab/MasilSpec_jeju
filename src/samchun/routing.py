"""도로망 주행 시간 (OSM 도로, 등급별 자유주행 속도).

셀 중심 → 가장 가까운 도로 꼭짓점까지는 직선으로 15km/h(진입로·골목), 이후 도로망 최단 시간.
일방통행·신호·혼잡은 반영하지 않은 자유주행 추정이라 실제보다 짧게 나온다.
부속섬(우도·추자·가파·마라·비양)은 본섬 도로망의 가장 가까운 지점까지 직선 15km/h로 잇는다 —
배편 대기를 반영하지 않으므로 실제보다 짧다.
"""
from functools import lru_cache

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

from samchun.spatial import load_roads

SPEED_KMH = {
    "motorway": 80, "trunk": 70, "primary": 70, "secondary": 60, "tertiary": 50,
    "motorway_link": 40, "trunk_link": 40, "primary_link": 40, "secondary_link": 40, "tertiary_link": 40,
    "unclassified": 40, "residential": 30, "living_street": 20, "service": 20,
}
ACCESS_KMH = 15


@lru_cache
def road_graph():
    """(꼭짓점 좌표 배열 EPSG:5179, 희소 인접행렬 [분], KDTree)."""
    roads = load_roads()
    index: dict[tuple, int] = {}
    xy: list[tuple] = []
    src, dst, w = [], [], []
    for geom, hw in zip(roads.geometry, roads["highway"]):
        speed = SPEED_KMH.get(hw, 30) * 1000 / 60  # m/분
        coords = list(geom.coords)
        ids = []
        for c in coords:
            key = (round(c[0], 1), round(c[1], 1))
            if key not in index:
                index[key] = len(xy)
                xy.append(key)
            ids.append(index[key])
        for (a, b), (p, q) in zip(zip(ids[:-1], ids[1:]), zip(coords[:-1], coords[1:])):
            minutes = np.hypot(q[0] - p[0], q[1] - p[1]) / speed
            src += [a, b]
            dst += [b, a]
            w += [minutes, minutes]
    xy = np.array(xy)
    n = len(xy)
    graph = coo_matrix((w, (src, dst)), shape=(n, n)).tocsr()
    # 끊어진 조각 도로·부속섬 도로에 붙으면 무한대가 되므로, 스냅은 가장 큰 연결요소(본섬)에만 한다
    _, label = connected_components(graph, directed=False)
    main = np.flatnonzero(label == np.bincount(label).argmax())
    return xy, graph, cKDTree(xy[main]), main


def drive_minutes(origins: gpd.GeoDataFrame, targets: gpd.GeoDataFrame) -> pd.Series:
    """각 origin(점, EPSG:5179)에서 가장 가까운 target 까지 예상 주행 시간(분)."""
    xy, graph, tree, main = road_graph()
    t_xy = np.column_stack([targets.geometry.x, targets.geometry.y])
    t_d, t_idx = tree.query(t_xy)
    t_idx = main[t_idx]
    # 도착지 쪽 진입 시간까지 포함하려면 다중 출발 dijkstra 후 각 target 진입시간을 더해야 하므로
    # target 별로 따로 계산해 최솟값을 취한다 (target 수가 적을 때 적합).
    best = None
    for node, extra in zip(t_idx, t_d / (ACCESS_KMH * 1000 / 60)):
        dist = dijkstra(graph, directed=False, indices=node) + extra
        best = dist if best is None else np.minimum(best, dist)
    o_xy = np.column_stack([origins.geometry.x, origins.geometry.y])
    o_d, o_idx = tree.query(o_xy)
    o_idx = main[o_idx]
    return pd.Series(best[o_idx] + o_d / (ACCESS_KMH * 1000 / 60), index=origins.index)
