"""비짓제주 관광정보 OPEN API (제주관광공사) → data/raw/visitjeju/contents.csv

    python -m samchun.collect.visitjeju

키: .env 의 VisitJeju_API_KEY. 가이드는 http 주소지만 실제로는 https 만 응답한다.
콘텐츠(관광지·음식점·숙박·쇼핑 등)마다 좌표, 분류, 태그(접근성·편의시설 포함), 소개가 있다.
"""
import time

import pandas as pd
import requests

from samchun.config import RAW_DIR, env

URL = "https://api.visitjeju.net/vsjApi/contents/searchList"


def _flat(item: dict) -> dict:
    lab = lambda k: (item.get(k) or {}).get("label")
    return {
        "contentsid": item.get("contentsid"), "category": lab("contentscd"), "title": item.get("title"),
        "region1": lab("region1cd"), "region2": lab("region2cd"),
        "lat": item.get("latitude"), "lng": item.get("longitude"),
        "address": item.get("address"), "roadaddress": item.get("roadaddress"),
        "tag": item.get("tag"), "alltag": item.get("alltag"), "introduction": item.get("introduction"),
        "phoneno": item.get("phoneno"),
    }


def fetch_all(locale: str = "kr") -> pd.DataFrame:
    key, rows, page = env("VisitJeju_API_KEY"), [], 1
    while True:
        res = requests.get(URL, params={"apiKey": key, "locale": locale, "page": page}, timeout=60)
        res.raise_for_status()
        body = res.json()
        if str(body.get("result")) not in ("00", "200"):
            raise RuntimeError(f"visitjeju: {body.get('result')} {body.get('resultMessage')}")
        rows += [_flat(i) for i in body.get("items", [])]
        if page >= int(body.get("pageCount") or 1):
            return pd.DataFrame(rows)
        page += 1
        time.sleep(0.1)


def load() -> pd.DataFrame:
    """제주 소재 콘텐츠만, 좌표 보정 포함 (축제/행사·정보 제외).

    원자료 좌표가 비었거나 (0,0)·도외인 경우 주소를 지오코딩해 채운다 (lat_src='geocode').
    """
    from samchun.geocode import geocode
    from samchun.rentcar import JEJU_BBOX

    d = pd.read_csv(RAW_DIR / "visitjeju" / "contents.csv", encoding="utf-8-sig")
    d = d[~d["category"].isin(["축제/행사", "정보", "기타", "미등록 콘텐츠"])]
    addr = d["roadaddress"].fillna(d["address"])
    d = d[addr.str.contains("제주", na=False)].copy()
    lat0, lat1, lng0, lng1 = JEJU_BBOX
    ok = d["lat"].between(lat0, lat1) & d["lng"].between(lng0, lng1)
    d["lat_src"] = ok.map({True: "api", False: "geocode"})
    need = d.loc[~ok]
    g = geocode(need["roadaddress"].fillna(need["address"]))
    d.loc[~ok, ["lat", "lng"]] = g.reindex(need["roadaddress"].fillna(need["address"]).str.strip()).to_numpy()
    return d.dropna(subset=["lat", "lng"]).reset_index(drop=True)


def tags(d: pd.DataFrame) -> pd.Series:
    """콘텐츠별 태그 집합 (alltag + tag, 쉼표 구분)."""
    raw = (d["alltag"].fillna("") + "," + d["tag"].fillna("")).str.replace("，", ",")
    return raw.str.split(r"\s*,\s*").map(lambda xs: {x.strip() for x in xs if x.strip()})


if __name__ == "__main__":
    df = fetch_all()
    out = RAW_DIR / "visitjeju" / "contents.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"{len(df):,}건 → {out}")
    print(df["category"].value_counts().to_string())
