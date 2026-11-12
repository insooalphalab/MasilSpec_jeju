"""브이월드 배경지도 타일 (white: 밝은 회색 + 한글 지명) — 서버 중계와 정적 HTML 내장 공용.

- tile(z, y, x): 타일 PNG 바이트 (data/interim/vworld_tiles 디스크 캐시)
- basemap(bbox): 영역을 덮는 타일을 이어 붙인 JPEG data URI + 정확한 경계 → Leaflet imageOverlay 용.
  정적 HTML 에 넣으면 서버·인터넷·외부 타일 정책과 무관하게 지도가 보인다. 키는 HTML 에 들어가지 않는다.
"""
import base64
import io
import math

import requests

from samchun.config import INTERIM_DIR, env

TILE_DIR = INTERIM_DIR / "vworld_tiles"
LAYER = "white"


def tile(z: int, y: int, x: int) -> bytes | None:
    p = TILE_DIR / str(z) / str(y) / f"{x}.png"
    if p.exists():
        return p.read_bytes()
    try:
        r = requests.get(f"https://api.vworld.kr/req/wmts/1.0.0/{env('Vworld_API_KEY')}/{LAYER}/{z}/{y}/{x}.png", timeout=15)
    except requests.RequestException:
        return None
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(r.content)
    return r.content


def _xy(lat: float, lng: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    return (lng + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n


def _lat(y: float, z: int) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2 ** z))))


def basemap(south: float, west: float, north: float, east: float, z: int = 15, quality: int = 72) -> dict | None:
    """bbox 를 덮는 z 레벨 타일을 이어 붙여 JPEG data URI 로. 실패하면 None."""
    from PIL import Image

    x0, y0 = map(math.floor, _xy(north, west, z))
    x1, y1 = map(math.floor, _xy(south, east, z))
    img = Image.new("RGB", ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256), (240, 240, 240))
    got = 0
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            b = tile(z, ty, tx)
            if b:
                img.paste(Image.open(io.BytesIO(b)).convert("RGB"), ((tx - x0) * 256, (ty - y0) * 256))
                got += 1
    if not got:
        return None
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    n = 2 ** z
    return {"img": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(),
            "bounds": [[_lat(y1 + 1, z), x0 / n * 360 - 180], [_lat(y0, z), (x1 + 1) / n * 360 - 180]],
            "zoom": z, "attribution": "배경지도 © 브이월드(국토교통부)"}
