"""지형 고도·경사 (AWS Open Data 'Terrain Tiles', Terrarium PNG, 키 불필요).

    python -m samchun.terrain      # 타일 받아 H3 셀별 고도·경사 집계 → data/interim/terrain_cells.parquet

원천: https://registry.opendata.aws/terrain-tiles/ (SRTM·국가 DEM 등 합성). 줌 12 ≈ 픽셀 32m (제주 위도).
고도(m) = R*256 + G + B/256 - 32768.
"""
import io
import math

import numpy as np
import pandas as pd
import requests

from samchun.config import INTERIM_DIR, RAW_DIR

ZOOM = 12
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
BBOXES = [(33.10, 33.60, 126.10, 127.00), (33.90, 34.05, 126.20, 126.40)]  # 본섬·부속섬, 추자도


def _tile_xy(lat: float, lng: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lng + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def _tile_bounds(x: int, y: int, z: int):
    n = 2 ** z
    lng0, lng1 = x / n * 360 - 180, (x + 1) / n * 360 - 180
    lat0 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat0, lat1, lng0, lng1


def download_tiles() -> list[tuple[int, int]]:
    out = RAW_DIR / "terrain" / str(ZOOM)
    out.mkdir(parents=True, exist_ok=True)
    tiles = set()
    for lat0, lat1, lng0, lng1 in BBOXES:
        x0, y1 = _tile_xy(lat0, lng0, ZOOM)
        x1, y0 = _tile_xy(lat1, lng1, ZOOM)
        tiles |= {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}
    for x, y in sorted(tiles):
        p = out / f"{x}_{y}.png"
        if not p.exists():
            r = requests.get(TILE_URL.format(z=ZOOM, x=x, y=y), timeout=60)
            r.raise_for_status()
            p.write_bytes(r.content)
    return sorted(tiles)


def _decode(path) -> np.ndarray:
    from PIL import Image

    a = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)
    return a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768


def build_cells() -> pd.DataFrame:
    """H3 res9 셀별 평균 고도(m), 평균·90백분위 경사(도). 바다 픽셀(고도 ≤ 0)은 제외."""
    import h3

    from samchun.spatial import H3_RES

    frames = []
    for x, y in download_tiles():
        elev = _decode(RAW_DIR / "terrain" / str(ZOOM) / f"{x}_{y}.png")
        lat0, lat1, lng0, lng1 = _tile_bounds(x, y, ZOOM)
        size = elev.shape[0]
        # 픽셀 중심 좌표 (웹 메르카토르 행 → 위도)
        n = 2 ** ZOOM
        rows = (y + (np.arange(size) + 0.5) / size)
        lats = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * rows / n))))
        lngs = lng0 + (np.arange(size) + 0.5) / size * (lng1 - lng0)
        # 경사: 픽셀 간격(m)으로 중앙차분
        dy = (lat1 - lat0) / size * 111_320
        dx = (lng1 - lng0) / size * 111_320 * np.cos(np.radians(lats))[:, None]
        gy, gx = np.gradient(elev)
        slope = np.degrees(np.arctan(np.hypot(gx / dx, gy / dy)))
        lat_g, lng_g = np.meshgrid(lats, lngs, indexing="ij")
        land = elev > 0
        frames.append(pd.DataFrame({"lat": lat_g[land], "lng": lng_g[land], "elev": elev[land], "slope": slope[land]}))
    px = pd.concat(frames, ignore_index=True)
    px["h3"] = [h3.latlng_to_cell(a, b, H3_RES) for a, b in zip(px["lat"], px["lng"])]
    g = px.groupby("h3")
    return pd.DataFrame({
        "elev_m": g["elev"].mean(),
        "slope_mean_deg": g["slope"].mean(),
        "slope_p90_deg": g["slope"].quantile(0.9),
    })


def terrain_cells() -> pd.DataFrame:
    cache = INTERIM_DIR / "terrain_cells.parquet"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        build_cells().to_parquet(cache)
    return pd.read_parquet(cache)


if __name__ == "__main__":
    t = terrain_cells()
    print(f"셀 {len(t):,}개")
    print(t.describe().round(1).to_string())
