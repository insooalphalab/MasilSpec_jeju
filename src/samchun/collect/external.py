"""외부 보완 데이터 수집기 (config/datasets.yaml 의 external 그룹) → data/raw/<key>/.

    python -m samchun.collect.external      # 승인된 것만 수집

API 키는 .env 의 DATA_GO_KR_KEY. 서비스별로 공공데이터포털 활용신청이 따로 필요하다.
"""
import time
from pathlib import Path

import pandas as pd
import requests

from samchun.config import RAW_DIR, env

DATA_GO_KR = "https://apis.data.go.kr"
JEJU_CITY_CODE = 39  # TAGO 도시코드


def _data_go_kr(path: str, params: dict, page_size: int = 1000) -> pd.DataFrame:
    """공공데이터포털 표준 응답(response.body.items.item)을 끝까지 페이징."""
    items: list[dict] = []
    page = 1
    while True:
        res = requests.get(
            f"{DATA_GO_KR}/{path}",
            params={"serviceKey": env("DATA_GO_KR_KEY"), "_type": "json", "numOfRows": page_size, "pageNo": page, **params},
            timeout=60,
        )
        if res.status_code == 403:
            raise PermissionError(f"{path}: 서비스키가 이 API에 등록되지 않았습니다 (활용신청 확인)")
        res.raise_for_status()
        resp = res.json()["response"]
        body = resp.get("body")
        if not isinstance(body, dict):  # 노선에 따라 body 가 빈 문자열로 오는 경우 → 결과 없음
            return pd.DataFrame(items)
        raw = body.get("items")
        got = raw.get("item", []) if isinstance(raw, dict) else []  # 결과 없으면 items 가 "" 로 온다
        items.extend(got if isinstance(got, list) else [got])
        if page * page_size >= body.get("totalCount", 0):
            return pd.DataFrame(items)
        page += 1


def _save(df: pd.DataFrame, key: str, name: str) -> Path:
    out = RAW_DIR / key / name
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return out


def fetch_tago_stops() -> Path:
    """제주 전체 버스 정류소 (nodeid, nodenm, gpslati, gpslong)."""
    df = _data_go_kr("1613000/BusSttnInfoInqireService/getSttnNoList", {"cityCode": JEJU_CITY_CODE})
    return _save(df, "tago_bus", "stops.csv")


def fetch_tago_routes() -> Path:
    """제주 전체 버스 노선 (routeid, routeno, routetp, 기종점, 첫차/막차). 배차 간격 계산의 재료."""
    df = _data_go_kr("1613000/BusRouteInfoInqireService/getRouteNoList", {"cityCode": JEJU_CITY_CODE})
    return _save(df, "tago_bus", "routes.csv")


def fetch_tago_route_stops() -> Path:
    """노선별 경유 정류소 (routeid, nodeid, nodeord, 좌표). 제주 노선은 배차간격(intervaltime)이 0으로 비어 있어
    정류소별 '지나는 노선 수'로 대중교통 접근성을 잰다."""
    routes = pd.read_csv(RAW_DIR / "tago_bus" / "routes.csv")
    frames = []
    for rid in routes["routeid"]:
        df = _data_go_kr("1613000/BusRouteInfoInqireService/getRouteAcctoThrghSttnList",
                         {"cityCode": JEJU_CITY_CODE, "routeId": rid}, page_size=500)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True).merge(routes[["routeid", "routeno", "routetp"]], on="routeid", how="left")
    return _save(out, "tago_bus", "route_stops.csv")


def fetch_clinics() -> Path:
    """국립중앙의료원 병·의원 찾기: 제주 전체 + 소아청소년과(QD=D002) 표시. 요일별 진료시간(dutyTime1s~8c) 포함.

    dutyTime{n}: 1~7 = 월~일, 8 = 공휴일. s=시작, c=마감 (HHMM).
    """
    url = "B552657/HsptlAsembySearchService/getHsptlMdcncListInfoInqire"
    allc = _data_go_kr(url, {"Q0": "제주특별자치도"}, page_size=500)
    peds = _data_go_kr(url, {"Q0": "제주특별자치도", "QD": "D002"}, page_size=500)
    allc["pediatric"] = allc["hpid"].isin(peds["hpid"])
    return _save(allc, "clinics", "clinics.csv")


def fetch_nemc_emergency() -> Path:
    """제주 응급의료기관 목록 (좌표, 응급실 운영 여부)."""
    df = _data_go_kr("B552657/ErmctInfoInqireService/getEgytListInfoInqire", {"Q0": "제주특별자치도"}, page_size=100)
    return _save(df, "nemc_emergency", "hospitals.csv")


def fetch_stores() -> Path:
    """소상공인 상가(상권) 정보: 제주 전체 영업 중 점포 (업종 대·중·소분류, 좌표, 층)."""
    items: list[dict] = []
    page = 1
    while True:
        for attempt in range(3):
            try:
                res = requests.get(
                    f"{DATA_GO_KR}/B553077/api/open/sdsc2/storeListInDong",
                    params={"serviceKey": env("DATA_GO_KR_KEY"), "divId": "ctprvnCd", "key": "50",
                            "numOfRows": 1000, "pageNo": page, "type": "json"},
                    timeout=120,
                )
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
        if res.status_code == 403:
            raise PermissionError("상가(상권)정보 API 활용신청 확인")
        res.raise_for_status()
        body = res.json()["body"]
        items.extend(body.get("items") or [])
        if page * 1000 >= int(body.get("totalCount", 0)):
            break
        page += 1
    return _save(pd.DataFrame(items), "stores", "stores.csv")


def fetch_accident_areas() -> Path:
    """전국교통사고다발지역 표준데이터 → 제주만 (사고유형·연도·사상자·좌표·다발지역 폴리곤 EPSG:5179 원 반경)."""
    url = "https://api.data.go.kr/openapi/tn_pubr_public_acdnt_area_api"
    items, page = [], 1
    while True:
        res = requests.get(url, params={"serviceKey": env("DATA_GO_KR_KEY"), "pageNo": page, "numOfRows": 1000, "type": "json"}, timeout=120)
        res.raise_for_status()
        body = res.json()["response"]["body"] if "response" in res.json() else res.json()["body"]
        got = (body.get("items") or {}).get("item", [])
        items.extend(got)
        if page * 1000 >= int(body.get("totalCount", 0)) or not got:
            break
        page += 1
    df = pd.DataFrame(items)
    df = df[df["ctprvnSignguNm"].str.startswith("제주", na=False)]
    return _save(df, "accident_areas", "jeju.csv")


TAAS = {
    "freezing": "frequentzoneFreezing/getRestFrequentzoneFreezing",
    "oldman": "frequentzoneOldman/getRestFrequentzoneOldman",
    "lg": "frequentzoneLg/getRestFrequentzoneLg",  # 지자체별 (전체 차량 사고)
}


def fetch_freezing_hotspots(years=range(2012, 2026)) -> Path:
    return fetch_taas("freezing", years)


def fetch_taas(kind: str, years=range(2012, 2026)) -> Path:
    """도로교통공단 사고다발지역 API (제주시 110 · 서귀포시 130, 연도별). kind: freezing | oldman."""
    rows = []
    for y in years:
        for gugun in ("110", "130"):
            res = requests.get(
                f"{DATA_GO_KR}/B552061/{TAAS[kind]}",
                params={"serviceKey": env("DATA_GO_KR_KEY"), "searchYearCd": str(y), "siDo": "50", "guGun": gugun,
                        "type": "json", "numOfRows": 100, "pageNo": 1},
                timeout=60,
            )
            if res.status_code == 403:
                raise PermissionError(f"{kind} 사고다발지역 API 활용신청 확인")
            rows += [dict(i, searchYearCd=y) for i in (res.json().get("items") or {}).get("item", [])]
    return _save(pd.DataFrame(rows), "accident_areas", f"jeju_{kind}.csv")


MOLIT = {
    "weak": ("WEAK_TRAFFIC", "T_OD_WEAK_TRAFFIC_BRN_INFO", ("고령자", "어린이", "보행자", "자전거")),
    "weather": ("WTHR_CNDTN", "T_OD_WTHR_CNDTN_BRN_INFO", ("맑음", "흐림", "비", "안개", "눈")),
}


def fetch_molit(kind: str, years=range(2019, 2026)) -> Path:
    """국토교통부 교통약자 사고다발지점 / 기상상태별 사고누적지점 (제주).

    2026-11-11 확인: 인증은 통과하나 어느 지역·연도로도 0건 반환 (서비스 측 데이터 미적재로 추정).
    """
    svc, op, types = MOLIT[kind]
    rows = []
    for y in years:
        for t in types:
            for sgg in ("제주시", "서귀포시"):
                res = requests.get(
                    f"{DATA_GO_KR}/1613000/{svc}/{op}",
                    params={"serviceKey": env("DATA_GO_KR_KEY"), "pageNo": 1, "numOfRows": 1000, "resultType": "json",
                            "crtr_yr": str(y), "brnch_se_nm": t, "mtrpl_lcgv_nm": "제주", "basic_lcgv_nm": sgg},
                    timeout=60,
                )
                res.raise_for_status()
                got = (res.json()["response"]["body"].get("items") or {}).get("item", [])
                rows += [dict(i, crtr_yr=y, brnch_se_nm=t) for i in got]
    if not rows:
        raise LookupError(f"molit {kind}: 0건 (서비스 데이터 미적재 또는 승인 반영 전)")
    return _save(pd.DataFrame(rows), "accident_areas", f"jeju_molit_{kind}.csv")


def fetch_road_centerline(step: float = 0.05) -> Path:
    """브이월드 2D 데이터 API · 수치지도 도로중심선 (LT_L_N3A0020000) — 도로폭 rvwd(m)·차로수 rdln.

    키: .env 의 Vworld2Ddata_API_KEY. 제주 범위를 step° 칸으로 나눠 칸마다 1000건씩 페이지를 넘긴다.
    칸 경계에 걸친 도로는 두 번 받힐 수 있어 ufid 로 중복 제거. 결과: road_centerline/jeju.csv (선형은 WKT, EPSG:4326).
    """
    import numpy as np

    key, rows = env("Vworld2Ddata_API_KEY"), {}
    for x0 in np.arange(126.10, 127.00, step):
        for y0 in np.arange(33.10, 33.60, step):
            page = 1
            while True:
                for i in range(4):
                    try:
                        j = requests.get("https://api.vworld.kr/req/data", params={
                            "service": "data", "request": "GetFeature", "data": "LT_L_N3A0020000", "key": key,
                            "geomFilter": f"BOX({x0:.3f},{y0:.3f},{x0 + step:.3f},{y0 + step:.3f})",
                            "size": 1000, "page": page, "format": "json", "geometry": "true", "attribute": "true",
                            "crs": "EPSG:4326"}, timeout=60).json()["response"]
                        break
                    except (requests.RequestException, ValueError, KeyError):
                        if i == 3:
                            raise
                        time.sleep(2 ** i)
                if j.get("status") != "OK":  # NOT_FOUND = 바다
                    break
                for f in j["result"]["featureCollection"]["features"]:
                    p, g = f["properties"], f["geometry"]
                    lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
                    wkt = "MULTILINESTRING(" + ",".join(
                        "(" + ",".join(f"{x:.6f} {y:.6f}" for x, y in ln) + ")" for ln in lines) + ")"
                    rows[p["ufid"]] = {"ufid": p["ufid"], "rddv": p.get("rddv"), "rdln": p.get("rdln"),
                                       "rvwd": p.get("rvwd"), "onsd": p.get("onsd"), "rdnm": p.get("rdnm"), "wkt": wkt}
                if page >= int(j["page"]["total"]):
                    break
                page += 1
    return _save(pd.DataFrame(rows.values()), "road_centerline", "jeju.csv")


def copy_from_jeonbuk() -> list[Path]:
    """전북 결빙 프로젝트(../jyunbuk_ice)에서 제주 부분만 복사: 상습결빙구간, 기상청 관측지점 메타, AWS 현천(안개·눈) 시간자료."""
    src = RAW_DIR.parents[2] / "jyunbuk_ice"
    out_dir = RAW_DIR / "from_jeonbuk"
    out_dir.mkdir(parents=True, exist_ok=True)
    ice = pd.read_csv(src / "ex" / "mois_habitual_icing.csv", encoding="utf-8-sig")
    ice = ice[ice["관리청"].astype(str).str.contains("제주")]
    meta = None
    for enc in ("utf-8-sig", "cp949"):
        try:
            meta = pd.read_csv(src / "META_관측지점정보_20261028100657.csv", encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    meta = meta[meta["위도"].between(33.1, 34.05) & meta["경도"].between(126.1, 127.0)]
    ww = pd.read_parquet(src / "kma" / "aws_ww1_hourly.parquet")
    ww = ww[ww["LAT"].between(33.1, 34.05) & ww["LON"].between(126.1, 127.0)]
    paths = [out_dir / "mois_habitual_icing_jeju.csv", out_dir / "kma_stations_jeju.csv", out_dir / "aws_ww1_hourly_jeju.parquet"]
    ice.to_csv(paths[0], index=False, encoding="utf-8-sig")
    meta.to_csv(paths[1], index=False, encoding="utf-8-sig")
    ww.to_parquet(paths[2])
    return paths


# 공공데이터포털 파일데이터의 자동 변환 API (odcloud). namespace 별 최신 기준일 버전을 저장한다.
ODCLOUD = {
    "local_food": "15043695", "night_pharmacy": "15043697", "emergency_hospital": "15056000",
    "odor_facility": "15056020", "model_restaurant": "15043694", "cleanhouse_seogwipo": "15056472",
    "beach_info": "15056354", "leisure_business": "15045336", "hot_spring": "15045469",
}


def fetch_odcloud(key: str) -> Path:
    ns = ODCLOUD[key]
    paths = list(requests.get(f"https://infuser.odcloud.kr/oas/docs?namespace={ns}/v1", timeout=30).json()["paths"])
    best = None
    for p in paths:
        res = requests.get(f"https://api.odcloud.kr/api{p}", params={"serviceKey": env("DATA_GO_KR_KEY"), "page": 1, "perPage": 5000, "returnType": "JSON"}, timeout=60)
        if res.status_code == 401:
            raise PermissionError(f"{key}: 공공데이터포털 활용신청 필요 ({ns})")
        res.raise_for_status()
        df = pd.DataFrame(res.json()["data"])
        ref = str(df.get("데이터기준일자", pd.Series([""])).max())
        if best is None or ref > best[0]:
            best = (ref, df)
    return _save(best[1], key, "api.csv")


JEJU_ASOS = {184: "제주", 185: "고산", 188: "성산", 189: "서귀포"}


def fetch_summer_wind(years=(2023, 2024, 2025), months=(6, 7, 8)) -> Path:
    """기상청 API허브 지상관측 시간자료 → 제주 ASOS 4곳 여름 풍향·풍속 (WD 36방위, WS m/s)."""
    import calendar
    import io

    rows = []
    for y in years:
        for m in months:
            last = calendar.monthrange(y, m)[1]
            for stn in JEJU_ASOS:
                res = requests.get(
                    "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm3.php",
                    params={"tm1": f"{y}{m:02d}010000", "tm2": f"{y}{m:02d}{last}2300", "stn": stn, "help": 0,
                            "authKey": env("KMA_APIHUB_KEY")},
                    timeout=120,
                )
                res.raise_for_status()
                body = [l for l in res.text.splitlines() if l and not l.startswith("#")]
                df = pd.read_csv(io.StringIO("\n".join(body)), sep=r"\s+", header=None, usecols=[0, 1, 2, 3],
                                 names=["tm", "stn", "wd36", "ws"])
                rows.append(df)
    out = pd.concat(rows, ignore_index=True)
    out = out[(out["wd36"] >= 0) & (out["ws"] >= 0)]
    return _save(out, "kma_wind", "summer_hourly.csv")


# TODO: fetch_forest_oreum(등산로 SHP), fetch_kma_weather(풍향·결빙), fetch_juso_buildings(도로 폭)

if __name__ == "__main__":
    for fn in (fetch_tago_stops, fetch_tago_routes, fetch_tago_route_stops, fetch_nemc_emergency, fetch_clinics, fetch_stores, fetch_accident_areas, fetch_freezing_hotspots, copy_from_jeonbuk, fetch_summer_wind,
               lambda: fetch_taas("oldman"), lambda: fetch_taas("lg"), lambda: fetch_molit("weak"), lambda: fetch_molit("weather"),
               *[(lambda k=k: fetch_odcloud(k)) for k in ODCLOUD]):
        try:
            out = fn()
            print(f"OK   {getattr(fn, '__name__', '')}: {len(pd.read_csv(out)):,}행 → {out}")
        except Exception as e:
            print(f"FAIL {fn.__name__}: {type(e).__name__} {str(e).replace(env('DATA_GO_KR_KEY'), '<KEY>')[:120]}")
