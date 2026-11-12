"""한국관광공사 TourAPI (공공데이터포털 B551011, DATA_GO_KR_KEY) → data/raw/tourapi/

    python -m samchun.collect.tourapi

- 지역 필터는 구 areaCode(39)가 아니라 법정동 코드 lDongRegnCd=50 을 써야 제주 전체가 나온다 (39는 324건뿐).
- 두루누비는 코리아둘레길(해파랑·남파랑 등)만 있고 제주 올레가 없어 수집하지 않는다.
"""
import time
from pathlib import Path

import pandas as pd
import requests

from samchun.config import RAW_DIR, env

BASE = "https://apis.data.go.kr/B551011"
OUT = RAW_DIR / "tourapi"
JEJU_SIGNGU = {"50110": "제주시", "50130": "서귀포시"}


def _get(path: str, **params) -> dict:
    for i in range(4):
        try:
            res = requests.get(f"{BASE}/{path}", params={"serviceKey": env("DATA_GO_KR_KEY"), "MobileOS": "ETC",
                                                          "MobileApp": "samchun", "_type": "json", **params}, timeout=60)
            if res.status_code == 403:
                raise PermissionError(f"{path}: 활용신청 필요")
            res.raise_for_status()
            body = res.json()["response"]["body"]
            return body if isinstance(body, dict) else {}
        except (requests.RequestException, ValueError, KeyError):
            if i == 3:
                raise
            time.sleep(2 ** i)
    return {}


def _all(path: str, page_size: int = 1000, **params) -> pd.DataFrame:
    rows, page = [], 1
    while True:
        body = _get(path, numOfRows=page_size, pageNo=page, **params)
        items = body.get("items")
        got = items.get("item", []) if isinstance(items, dict) else []
        rows += got if isinstance(got, list) else [got]
        if page * page_size >= int(body.get("totalCount", 0)) or not got:
            return pd.DataFrame(rows)
        page += 1


def _save(df: pd.DataFrame, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    df.to_csv(p, index=False, encoding="utf-8-sig")
    return p


def fetch_contents() -> Path:
    """국문 관광정보: 제주 전체 콘텐츠 (관광지·문화시설·레포츠·숙박·음식점·쇼핑 등, 좌표 mapx/mapy, 분류 cat1~3)."""
    return _save(_all("KorService2/areaBasedList2", lDongRegnCd="50"), "contents.csv")


def fetch_barrier_free() -> Path:
    """무장애 여행 정보: 제주 목록 + 장소별 상세 (휠체어·유모차·엘리베이터·장애인 화장실·주차 등)."""
    lst = _all("KorWithService2/areaBasedList2", lDongRegnCd="50")
    details = []
    for cid in lst["contentid"]:
        body = _get("KorWithService2/detailWithTour2", contentId=cid)
        item = (body.get("items") or {}).get("item", [{}])
        details.append(item[0] if isinstance(item, list) else item)
    det = pd.DataFrame(details)
    keep = ["contentid", "contenttypeid", "title", "addr1", "mapx", "mapy", "cat1", "cat2", "cat3"]
    return _save(lst[keep].merge(det, on="contentid", how="left"), "barrier_free.csv")


def fetch_congestion() -> Path:
    """관광지 집중률 방문자 추이 예측: 제주시·서귀포시 관광지별 향후 30일 일별 집중률(%)."""
    frames = [_all("TatsCnctrRateService/tatsCnctrRatedList", areaCd="50", signguCd=cd) for cd in JEJU_SIGNGU]
    return _save(pd.concat(frames, ignore_index=True), "congestion_forecast.csv")


if __name__ == "__main__":
    for fn in (fetch_contents, fetch_barrier_free, fetch_congestion):
        try:
            p = fn()
            print(f"OK   {fn.__name__}: {len(pd.read_csv(p)):,}행 → {p.name}")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {fn.__name__}: {type(e).__name__} {str(e)[:120]}")
