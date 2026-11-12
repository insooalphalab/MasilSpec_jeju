"""브이월드 지오코더 (주소 → 위경도) + 디스크 캐시.

키는 .env 의 Vworld_API_KEY. 결과는 data/interim/geocode_cache.csv 에 쌓여 재호출하지 않는다.
"""
import time

import pandas as pd
import requests

from samchun.config import INTERIM_DIR, env

URL = "https://api.vworld.kr/req/address"
CACHE = INTERIM_DIR / "geocode_cache.csv"


def _call(address: str, kind: str, tries: int = 4) -> tuple[float, float] | None:
    for i in range(tries):
        try:
            res = requests.get(
                URL,
                params={"service": "address", "request": "getcoord", "version": "2.0", "crs": "epsg:4326", "format": "json",
                        "refine": "true", "simple": "true", "type": kind, "address": address, "key": env("Vworld_API_KEY")},
                timeout=30,
            )
            body = res.json()["response"]  # 가끔 깨진 응답이 와서 파싱 실패도 재시도
            break
        except (requests.RequestException, ValueError, KeyError):
            if i == tries - 1:
                raise
            time.sleep(2 ** i)
    if body["status"] != "OK":
        return None
    p = body["result"]["point"]
    return float(p["y"]), float(p["x"])


def geocode(addresses, kinds=("road", "parcel")) -> pd.DataFrame:
    """주소 목록 → DataFrame(index=주소, lat, lng). 실패한 주소는 NaN."""
    addresses = pd.Series(addresses).dropna().str.strip().unique()
    cache = pd.read_csv(CACHE, index_col="address") if CACHE.exists() else pd.DataFrame(columns=["lat", "lng"]).rename_axis("address")
    new = {}

    def flush():
        nonlocal cache, new
        if new:
            add = pd.DataFrame.from_dict(new, orient="index", columns=["lat", "lng"]).rename_axis("address")
            cache = add if cache.empty else pd.concat([cache, add])
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            cache.to_csv(CACHE, encoding="utf-8-sig")
            new = {}

    try:
        for a in addresses:
            if a in cache.index or a in new:
                continue
            hit = next((r for r in (_call(a, k) for k in kinds) if r), None)
            new[a] = hit or (float("nan"), float("nan"))
            if len(new) >= 50:  # 중간 저장: 도중에 끊겨도 진행분은 남는다
                flush()
    finally:
        flush()
    return cache.loc[addresses]


_JUSO: dict[str, list[dict]] = {}


def juso_search(keyword: str, n: int = 5) -> list[dict]:
    """행안부 도로명주소 검색 API (JUSO_API_KEY): 오탈자·옛 주소·행정동 이름 → 정식 주소 후보.

    좌표는 주지 않는다 → 결과의 roadAddrPart1/jibunAddr 를 브이월드 지오코더에 넘긴다. 키가 없거나 실패하면 [].
    """
    if keyword in _JUSO:
        return _JUSO[keyword]
    try:
        res = requests.get("https://business.juso.go.kr/addrlink/addrLinkApi.do",
                           params={"confmKey": env("JUSO_API_KEY"), "currentPage": 1, "countPerPage": n,
                                   "keyword": keyword, "resultType": "json"}, timeout=15)
        body = res.json()["results"]
        out = body.get("juso") or [] if body["common"]["errorCode"] == "0" else []
    except (KeyError, FileNotFoundError, requests.RequestException, ValueError):
        out = []
    _JUSO[keyword] = out
    return out


_REV: dict[tuple[float, float], str] = {}


def reverse_road(lat: float, lng: float) -> str:
    """좌표 → 가장 가까운 도로명 주소 (없으면 지번, 실패하면 빈 문자열). 메모리 캐시."""
    key = (round(lat, 5), round(lng, 5))
    if key in _REV:
        return _REV[key]
    out = ""
    for kind in ("road", "parcel"):
        try:
            res = requests.get(URL, params={"service": "address", "request": "getAddress", "version": "2.0",
                                            "crs": "epsg:4326", "format": "json", "type": kind,
                                            "point": f"{lng},{lat}", "key": env("Vworld_API_KEY")}, timeout=15)
            body = res.json()["response"]
            if body["status"] == "OK":
                out = body["result"][0]["text"]
                break
        except (requests.RequestException, ValueError, KeyError, IndexError):
            continue
    _REV[key] = out
    return out


def village_centers(villages, lots=(1, 300, 600, 1000, 1500, 2000, 2500)) -> pd.DataFrame:
    """'○○읍 ○○리' 같은 마을 이름 → 대략적인 중심 좌표.

    마을 경계 데이터가 없어, 마을 안 여러 지번을 지오코딩한 평균을 중심으로 쓴다 (오차 1km 안팎).
    """
    villages = pd.Series(villages).dropna().str.strip().unique()
    rows = {}
    for v in villages:
        pts = geocode([f"{v} {n}" for n in lots], kinds=("parcel",)).dropna()
        rows[v] = (pts["lat"].mean(), pts["lng"].mean(), len(pts))
    return pd.DataFrame.from_dict(rows, orient="index", columns=["lat", "lng", "n_lots"]).rename_axis("village")
