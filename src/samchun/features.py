"""12대 마스터 지표 (ai_agent.md §4.2, §5).

각 지표 함수는 grid(H3 셀)를 받아 셀별 원시값 Series(index=h3)를 돌려주고,
`normalize` 가 0~100 "높을수록 좋음"으로 맞춘다. 결과는 data/processed/indicators.parquet.
"""
import geopandas as gpd
import pandas as pd

from samchun.collect.hub import load_raw
from samchun.config import RAW_DIR
from samchun.geocode import geocode, village_centers
from samchun.rentcar import spot_cells
from samchun.spatial import (
    CRS_METRIC, MAJOR_ROADS, count_within, line_length_in_cells, load_roads, nearest_dist_m, points_from_latlng,
)


def normalize(raw: pd.Series, higher_is_better: bool = True, method: str = "percentile") -> pd.Series:
    """원시값 → 0~100. 역방향 지표(소음·악취·사고)는 higher_is_better=False 로 반전."""
    if method == "percentile":
        # 동점은 원시값이 작은 쪽 순위로 묶는다: '0곳'인 셀들은 0점에 가깝게 → 역방향이면 반전돼 100점.
        # (예: 주점 0곳 셀 = 음주권 0점, 축사 0곳 셀 = 악취 안전 100점)
        z = (raw.rank(method="min") - 1) / max(raw.notna().sum() - 1, 1) * 100
    else:  # minmax
        span = raw.max() - raw.min()
        z = (raw - raw.min()) / span * 100 if span else raw * 0 + 50.0
    return z if higher_is_better else 100 - z


def cleanhouses():
    """제주시(좌표 제공) + 서귀포시(인근주소 지오코딩) 클린하우스."""
    jeju = load_raw("cleanhouse_jeju").dropna(subset=["위도 좌표", "경도 좌표"])
    jeju = points_from_latlng(jeju, "위도 좌표", "경도 좌표")[["geometry"]]
    sgp = pd.read_csv(RAW_DIR / "cleanhouse_seogwipo" / "api.csv", encoding="utf-8-sig")
    sgp = _geocoded(sgp, "인근주소")[["geometry"]]
    return pd.concat([jeju, sgp], ignore_index=True).pipe(gpd.GeoDataFrame, crs=jeju.crs)


def _cleanhouse_dist(grid) -> pd.Series:
    """가장 가까운 클린하우스까지 거리(m)."""
    return nearest_dist_m(grid, cleanhouses())


def _oreum_count_4km(grid) -> pd.Series:
    """반경 4km 내 오름 수 (ai_agent.md §2 테마 8)."""
    df = load_raw("oreum_location").dropna(subset=["latitude", "longitude"])
    return count_within(grid, points_from_latlng(df, "latitude", "longitude"), 4000)


def pig_farms() -> pd.DataFrame:
    """영업 중인 양돈장 (마을 단위 근사 좌표).

    원자료의 지번이 가려져 있어 농장을 마을(리·동) 중심에 찍는다. 오차 1km 안팎.
    """
    lv = load_raw("livestock")
    pig = lv[(lv["openStatus"] == "영업/정상") & (lv["bizSmallType"] == "돼지") & (lv["bizLargeType"] == "가축사육업")].copy()
    pig["village"] = pig["addressJibun"].str.extract(r"^(제주특별자치도 \S+시 (?:\S+[읍면] )?\S+?[리동가])(?=\s|$)")[0]
    return pig.join(village_centers(pig["village"])[["lat", "lng"]], on="village").dropna(subset=["lat", "lng"])


def odor_facilities():
    """악취관리지역·악취배출시설 신고 사업장 (2019, 정확한 지번 → 지오코딩)."""
    df = pd.read_csv(RAW_DIR / "odor_facility" / "api.csv", encoding="utf-8-sig")
    return _geocoded(df, "소재지 (대표주소)")


ASOS_STATIONS = {184: (33.5141, 126.5297), 185: (33.2938, 126.1628), 188: (33.3868, 126.8802), 189: (33.2462, 126.5653)}


def summer_wind_roses() -> pd.DataFrame:
    """ASOS 4곳 여름(6~8월, 2023~25) 16방위 풍향 빈도 (바람이 '불어오는' 방향, 무풍 0.5m/s 미만 제외).

    행=지점, 열=방위 0(N)~15(NNW). 관측 결과 제주시는 여름에도 북동풍이 최다(15%),
    남서풍은 서귀포(23%)·성산 등 남부에서 우세 — 명세의 '남서풍' 일괄 가정은 맞지 않는다.
    """
    w = pd.read_csv(RAW_DIR / "kma_wind" / "summer_hourly.csv")
    w = w[w["ws"] >= 0.5]
    w["sec"] = ((w["wd36"] * 10 + 11.25) // 22.5 % 16).astype(int)
    return w.groupby("stn")["sec"].value_counts(normalize=True).unstack(fill_value=0).reindex(columns=range(16), fill_value=0)


def _downwind_weighted_count(grid, sources, radius: float) -> pd.Series:
    """반경 내 오염원 수를 풍향으로 가중해 센다. 오염원이 셀의 바람 위쪽(여름에 그쪽에서 바람이 자주 불어옴)일수록 큼.

    가중치 = 16 x P(바람이 셀→오염원 방향에서 불어옴), 가장 가까운 ASOS 지점의 여름 풍향 기준. 평균 1.
    """
    import numpy as np

    roses = summer_wind_roses()
    st = pd.DataFrame(ASOS_STATIONS, index=["lat", "lng"]).T.rename_axis("stn").reset_index()
    st = points_from_latlng(st, "lat", "lng")
    near = gpd.sjoin_nearest(grid[["geometry"]], st[["stn", "geometry"]])
    station = near[~near.index.duplicated()]["stn"]

    buf = grid[["geometry"]].copy()
    buf["geometry"] = buf.geometry.buffer(radius)
    pairs = gpd.sjoin(buf, sources[["geometry"]].rename_axis("src").reset_index(), predicate="contains")
    if pairs.empty:
        return pd.Series(0.0, index=grid.index)
    cx, cy = grid.geometry.x.reindex(pairs.index).to_numpy(), grid.geometry.y.reindex(pairs.index).to_numpy()
    src_xy = sources.geometry
    sx, sy = src_xy.x.to_numpy()[pairs["src"].to_numpy()], src_xy.y.to_numpy()[pairs["src"].to_numpy()]
    bearing = (np.degrees(np.arctan2(sx - cx, sy - cy)) + 360) % 360  # 셀에서 본 오염원 방향 (EPSG:5179: x=동, y=북)
    sector = ((bearing + 11.25) // 22.5 % 16).astype(int)
    stn = station.reindex(pairs.index).to_numpy()
    w = 16 * roses.to_numpy()[[roses.index.get_loc(s) for s in stn], sector]
    return pd.Series(w, index=pairs.index).groupby(level=0).sum().reindex(grid.index, fill_value=0.0)


def _odor_exposure(grid) -> pd.Series:
    """악취 노출 = 1.5km 내 악취관리 사업장 x3 + 3km 내 영업 양돈장(마을 단위 근사) x1, 각각 여름 풍향 가중.

    정확한 위치의 악취관리 사업장(58곳)을 명세대로 1.5km로 세고, 지번이 가려진 양돈장 전수(256곳)는
    위치 오차를 감안해 3km·낮은 가중치로 보탠다. 바람 아래쪽 셀일수록 크게 잡힌다.
    """
    facilities = odor_facilities().reset_index(drop=True)
    pigs = points_from_latlng(pig_farms(), "lat", "lng").reset_index(drop=True)
    return (3 * _downwind_weighted_count(grid, facilities, 1500)
            + _downwind_weighted_count(grid, pigs, 3000)).round(1)


def _geocoded(df: pd.DataFrame, col: str):
    g = df.join(geocode(df[col]), on=df[col].str.strip()).dropna(subset=["lat", "lng"])
    return points_from_latlng(g, "lat", "lng")


def _bus_stops():
    df = pd.read_csv(RAW_DIR / "tago_bus" / "stops.csv")
    return points_from_latlng(df, "gpslati", "gpslong")


def _bus_stop_dist(grid) -> pd.Series:
    """가장 가까운 버스 정류소까지 거리(m)."""
    return nearest_dist_m(grid, _bus_stops())


def stop_route_counts() -> pd.DataFrame:
    """정류소별 지나는 노선 수 (노선번호 기준, 방향·변형 노선은 하나로). 간선·급행은 trunk 로 따로 센다."""
    rs = pd.read_csv(RAW_DIR / "tago_bus" / "route_stops.csv")
    rs["trunk"] = rs["routetp"].isin(["간선버스", "급행버스"])
    g = rs.groupby("nodeid")
    out = pd.DataFrame({
        "lat": g["gpslati"].first(), "lng": g["gpslong"].first(),
        "routes": g["routeno"].nunique(),
        "trunk_routes": rs[rs["trunk"]].groupby("nodeid")["routeno"].nunique(),
    }).fillna({"trunk_routes": 0})
    return out.reset_index()


def _routes_within(grid, radius: float, col: str) -> pd.Series:
    """반경 내 정류소들을 지나는 노선 수의 합 대신, 반경 내 정류소 중 최대 노선 수를 쓴다 (같은 노선 중복 방지)."""
    st = points_from_latlng(stop_route_counts(), "lat", "lng")
    buf = grid[["geometry"]].copy()
    buf["geometry"] = buf.geometry.buffer(radius)
    j = gpd.sjoin(buf, st[[col, "geometry"]], predicate="contains")
    return j.groupby(level=0)[col].max().reindex(grid.index, fill_value=0)


def _transit(grid) -> pd.Series:
    """대중교통 배차망: 도보 350m 내 정류소의 (간선·급행 노선 수 x2 + 전체 노선 수) 최대값.

    TAGO 노선 API의 배차간격(intervaltime)이 제주는 0으로 비어 있어, 노선 수로 운행 빈도를 대신한다.
    """
    return 2 * _routes_within(grid, 350, "trunk_routes") + _routes_within(grid, 350, "routes")


# 수질 조사 대상 해변 → 비짓제주 콘텐츠 제목 (제주관광공사 공식 좌표)
_BEACH_VJ_TITLE = {
    "곽지": "곽지해수욕장", "금능": "금능해수욕장", "김녕": "김녕해수욕장", "사계": "사계해변", "삼양": "삼양해수욕장",
    "세화": "세화해수욕장", "소금막": "소금막해변", "쇠소깍": "하효쇠소깍해변", "신양섭지": "신양섭지해수욕장",
    "월정": "월정리해수욕장", "이호테우": "이호테우해수욕장", "중문색달": "중문색달해수욕장", "평대": "평대해변",
    "표선": "표선해수욕장", "하도": "하도해변", "함덕": "함덕해수욕장", "협재": "협재해수욕장", "화순금모래": "화순금모래해수욕장",
}


def beaches() -> pd.DataFrame:
    """수질 조사 대상 해변 18곳: 이름, 좌표(비짓제주), 수질 적합률(%)."""
    from samchun.collect.visitjeju import load

    water = load_raw("beach_water")
    judged = water[water["suitabilityFlag"] != "기준미적용"]
    ok = (judged["suitabilityFlag"] == "적합").groupby(judged["beachName"]).mean() * 100
    vj = load().drop_duplicates("title").set_index("title")
    rows = []
    for name in sorted(water["beachName"].unique()):
        t = _BEACH_VJ_TITLE[name]
        rows.append({"beach": name, "lat": vj.at[t, "lat"], "lng": vj.at[t, "lng"], "water_ok_pct": ok.get(name)})
    return pd.DataFrame(rows)


def _beach_dist(grid) -> pd.Series:
    """가장 가까운 수질 조사 해변까지 거리(m). 수질은 2021년 전 해변 적합이라 변별력이 없어 거리만 쓴다."""
    return nearest_dist_m(grid, points_from_latlng(beaches(), "lat", "lng"))


def _rentcar_cells(kind: str, col: str = "monthly_mean") -> pd.Series:
    from samchun.rentcar import grid_counts, to_h3

    return to_h3(grid_counts(kind), [col])[col]


def _traffic_load(grid) -> pd.Series:
    """차량 통행 부하 (높을수록 시끄러움). 렌터카 월평균 통행량 순위와 간선도로 길이 순위의 평균.

    dB가 아니다: 측정소 35곳(도심만)으로 검증했을 때 dB 설명력이 R² 0.2 미만이라,
    "차가 많이 다니는 정도"의 상대 순위로만 쓴다. 렌터카는 2020~21년 표본(약 3,400대)이고
    도민 차량은 간선도로 길이로 대신한다.
    """
    move = _rentcar_cells("move").reindex(grid.index, fill_value=0.0)
    major = load_roads()
    major = major[major["highway"].isin(MAJOR_ROADS)]
    road_len = line_length_in_cells(grid, major)
    return (move.rank(pct=True) + road_len.rank(pct=True)) / 2 * 100


ACCESSIBLE_TAGS = {"단차없음", "장애인 화장실", "수동 휠체어 대여 가능", "전동 휠체어 대여 가능", "유모차 대여 서비스", "유모차대여", "단독접근가능"}
KIDS_TAGS = {"어린이 출입가능", "유아의자", "유모차 대여 서비스", "유모차대여", "키즈룸", "키즈카페"}


def _vj(category: str | None = None, any_tags: set | None = None):
    """비짓제주 콘텐츠 중 조건에 맞는 장소 (EPSG:5179 점)."""
    from samchun.collect.visitjeju import load, tags

    d = load()
    if category:
        d = d[d["category"] == category]
    if any_tags:
        d = d[tags(d).map(lambda t: bool(t & any_tags))]
    return points_from_latlng(d, "lat", "lng")


def _vj_has(g, wanted: set) -> pd.Series:
    from samchun.collect.visitjeju import tags

    return tags(g).map(lambda t: bool(t & wanted))


def local_food_restaurants():
    """제주도 지정 향토음식점 (공공데이터포털 API, 주소 지오코딩)."""
    df = pd.read_csv(RAW_DIR / "local_food" / "api.csv", encoding="utf-8-sig")
    df["addr"] = df["소재지"].str.replace(r"\s*\(.*\)\s*$", "", regex=True)
    return _geocoded(df, "addr")


def model_restaurants():
    """모범음식점 (제주도 지정, 2026-04, 도로명주소 지오코딩)."""
    df = pd.read_csv(RAW_DIR / "model_restaurant" / "api.csv", encoding="utf-8-sig")
    return _geocoded(df, "소재지(도로명)")


def _local_food_700m(grid) -> pd.Series:
    """도보 700m 내 맛집 점수 = 향토음식점 x3 + 모범음식점 x2 + 비짓제주 추천 음식점 x1.

    도 지정(향토·모범)을 '도민 인증'으로 가중. 돼지고기 인증점은 허브 파일이라 미반영.
    """
    return (3 * count_within(grid, local_food_restaurants(), 700)
            + 2 * count_within(grid, model_restaurants(), 700)
            + count_within(grid, _vj("음식점"), 700))


def _barrier_free(grid) -> pd.Series:
    """보행 무단차 (0~100) = '접근성 확인 장소 1km 수' 순위와 '평지(평균 경사 낮음)' 순위의 평균.

    유모차·휠체어·노약자 보행의 실제 장애는 턱과 경사 — 턱은 장소 태그로, 경사는 지형으로 대신한다.
    """
    flat = -_terrain(grid)["slope_mean_deg"]
    return pd.concat([_pct(_accessible_places_1km(grid)), _pct(flat)], axis=1).mean(axis=1) * 100


def _travellog_stats(grid) -> dict:
    """AI Hub 여행로그 2023: 셀별 실제 여행객 방문 수·만족도·재방문 의향 (방문 5건 미만 셀은 만족도 비움)."""
    from samchun.travellog import cell_stats

    st = cell_stats().reindex(grid.index)
    st[["tl_visits", "tl_family_visits"]] = st[["tl_visits", "tl_family_visits"]].fillna(0)
    return {k: v for k, v in st.items()}


def kto(contenttype: int | None = None, title_pattern: str | None = None):
    """한국관광공사 국문 관광정보 콘텐츠 (mapx=경도, mapy=위도)."""
    df = pd.read_csv(RAW_DIR / "tourapi" / "contents.csv", encoding="utf-8-sig")
    if contenttype:
        df = df[df["contenttypeid"] == contenttype]
    if title_pattern:
        df = df[df["title"].str.contains(title_pattern, na=False)]
    return points_from_latlng(df.dropna(subset=["mapx", "mapy"]), "mapy", "mapx")


def kto_barrier_free():
    """관광공사 무장애 여행 정보 중 휠체어·유모차·엘리베이터·장애인 화장실 정보가 있는 장소."""
    df = pd.read_csv(RAW_DIR / "tourapi" / "barrier_free.csv", encoding="utf-8-sig")
    has = df[["wheelchair", "stroller", "elevator", "restroom"]].notna().any(axis=1)
    return points_from_latlng(df[has].dropna(subset=["mapx", "mapy"]), "mapy", "mapx")


def _accessible_places_1km(grid) -> pd.Series:
    """반경 1km 내 접근성 확인 장소 수 = 비짓제주 태그(단차없음·장애인 화장실·휠체어/유모차 대여) + 관광공사 무장애 정보.

    명세의 '보행로 단차 2cm 이하율'은 섬 전체 보행로 실측 데이터가 없어 장소 단위 접근성으로 대신한다.
    두 출처가 같은 장소를 겹쳐 셀 수 있다 (접근성 정보가 '여러 곳에서 확인된' 장소로 해석).
    """
    return count_within(grid, _vj(any_tags=ACCESSIBLE_TAGS), 1000) + count_within(grid, kto_barrier_free(), 1000)


MARINE = r"서프|서핑|다이브|다이빙|스쿠버|스노클|카약|패들|요트|제트|보트|잠수|해양|씨워크|투명"


def marine_leisure():
    """해양레저 업체: 관광공사 레포츠 중 해양 키워드 + 제주도 레저사업체현황."""
    kto_m = kto(28, MARINE)[["geometry"]]
    biz = _geocoded(pd.read_csv(RAW_DIR / "leisure_business" / "api.csv", encoding="utf-8-sig"), "소재지")[["geometry"]]
    return gpd.GeoDataFrame(pd.concat([kto_m, biz], ignore_index=True), crs=CRS_METRIC)


def _beach_leisure(grid) -> pd.Series:
    """청정 해변·레저권 (0~100) = '가까운 해수욕장' 순위와 '1km 내 해양레저 업체 수' 순위의 평균.

    수질은 2021년 전 해변 적합이라 변별력이 없어 빠져 있다.
    """
    near = -_beach_dist(grid)
    leisure = count_within(grid, marine_leisure(), 1000)
    return pd.concat([_pct(near), _pct(leisure)], axis=1).mean(axis=1) * 100


def _congestion(grid) -> pd.Series:
    """반경 500m 내 관광지의 향후 30일 평균 집중률(%) 최댓값 (관광공사 예측, 제목으로 좌표 매칭)."""
    import re

    norm = lambda x: re.sub(r"[\s()\[\]·,]", "", str(x))  # noqa: E731
    g = pd.read_csv(RAW_DIR / "tourapi" / "congestion_forecast.csv", encoding="utf-8-sig")
    rate = g.groupby("tAtsNm")["cnctrRate"].mean()
    c = pd.read_csv(RAW_DIR / "tourapi" / "contents.csv", encoding="utf-8-sig").dropna(subset=["mapx", "mapy"])
    c = c.assign(k=c["title"].map(norm)).drop_duplicates("k").set_index("k")
    pts = pd.DataFrame({"rate": rate.values, "k": rate.index.map(norm)}).join(c[["mapx", "mapy"]], on="k").dropna()
    pts = points_from_latlng(pts, "mapy", "mapx")
    buf = grid[["geometry"]].copy()
    buf["geometry"] = buf.geometry.buffer(500)
    j = gpd.sjoin(buf, pts[["rate", "geometry"]], predicate="contains")
    return j.groupby(level=0)["rate"].max().reindex(grid.index).round(0)


# ---- 소상공인 상가(상권) 정보 ----
DINING = {"한식", "중식", "일식", "서양식", "동남아시아"}
PUBS = {"요리 주점", "생맥주 전문"}  # 유흥·무도 주점 제외
DELIVERY = {"치킨", "피자", "김밥/만두/분식", "버거", "족발/보쌈"}


def stores(mcls: set | None = None, scls: set | None = None):
    df = pd.read_csv(RAW_DIR / "stores" / "stores.csv", low_memory=False)
    if mcls:
        df = df[df["indsMclsNm"].str.strip().isin(mcls)]
    if scls:
        df = df[df["indsSclsNm"].str.strip().isin(scls)]
    return points_from_latlng(df, "lat", "lon")


def _walk_drink(grid) -> pd.Series:
    """대리 프리 음주권: 도보 700m 내 주점 x2 + 식당. 1km 내 주점이 0이면 0 (명세 §2 테마3)."""
    pubs = stores(scls=PUBS)
    raw = 2 * count_within(grid, pubs, 700) + count_within(grid, stores(mcls=DINING), 700)
    return raw.where(count_within(grid, pubs, 1000) > 0, 0)


def _delivery(grid) -> pd.Series:
    """배달앱 고립 방어: 반경 3km 내 배달형 업종(치킨·피자·분식·버거·족발 + 중식) 점포 수.

    배달 등록 여부 데이터는 없어 업종으로 대신한다.
    """
    return count_within(grid, stores(scls=DELIVERY), 3000) + count_within(grid, stores(mcls={"중식"}), 3000)


# ---- 응급의료 ----
def emergency_rooms():
    df = pd.read_csv(RAW_DIR / "emergency_hospital" / "api.csv", encoding="utf-8-sig")
    df["addr"] = df["소재지"].str.replace(r"\s*\(.*\)\s*$", "", regex=True)
    return _geocoded(df, "addr")


def _er_drive(grid) -> pd.Series:
    """응급의료 골든타임: 가장 가까운 응급의료기관(6곳)까지 예상 주행 시간(분, 자유주행)."""
    from samchun.routing import drive_minutes

    return drive_minutes(grid, emergency_rooms())


def clinics(night_pediatric: bool = False):
    """국립중앙의료원 병·의원 (제주 1,040곳). night_pediatric=True 면 '야간·휴일 소아 진료' 기관만.

    야간·휴일 소아 = 소아청소년과 진료 + 평일 21시 이후 마감 + 일요일 또는 공휴일 진료 (달빛어린이병원 성격).
    """
    df = pd.read_csv(RAW_DIR / "clinics" / "clinics.csv", low_memory=False)
    df = df[df["dutyDivNam"].isin(["의원", "병원", "종합병원", "보건소"])]
    if night_pediatric:
        close = lambda n: pd.to_numeric(df.get(f"dutyTime{n}c"), errors="coerce")
        weekday = pd.concat([close(i) for i in range(1, 6)], axis=1).max(axis=1)
        df = df[df["pediatric"] & (weekday >= 2100) & (close(7).notna() | close(8).notna())]
    return points_from_latlng(df, "wgs84Lat", "wgs84Lon")


def _night_peds_drive(grid) -> pd.Series:
    """야간·휴일 소아 진료 기관까지 예상 주행 시간(분). 응급실(6곳)과 합쳐 가장 가까운 곳 기준."""
    from samchun.routing import drive_minutes

    targets = pd.concat([clinics(night_pediatric=True)[["geometry"]], emergency_rooms()[["geometry"]]], ignore_index=True)
    return drive_minutes(grid, gpd.GeoDataFrame(targets, crs=CRS_METRIC)).round(1)


# ---- 사고·결빙 ----
def accident_hotspots(types: set | None = None):
    df = pd.read_csv(RAW_DIR / "accident_areas" / "jeju.csv")
    # 표준데이터는 2024년까지 → 도로교통공단 보행노인 API의 이후 연도분을 덧붙인다
    taas = RAW_DIR / "accident_areas" / "jeju_oldman.csv"
    if taas.exists():
        o = pd.read_csv(taas)
        o = o[o["searchYearCd"] > df["acdntYear"].max()]
        df = pd.concat([df, pd.DataFrame({"acdntTypeSe": "보행노인", "acdntYear": o["searchYearCd"],
                                          "latitude": o["la_crd"], "longitude": o["lo_crd"]})], ignore_index=True)
    if types:
        df = df[df["acdntTypeSe"].isin(types)]
    return points_from_latlng(df, "latitude", "longitude")


def car_accident_spots(since: int = 2019):
    """도로교통공단 지자체별 교통사고 다발지역 (전체 차량 사고, 연도별 상위 지점). 사상자 수(caslt_cnt) 포함."""
    df = pd.read_csv(RAW_DIR / "accident_areas" / "jeju_lg.csv")
    return points_from_latlng(df[df["searchYearCd"] >= since], "la_crd", "lo_crd")


def _weighted_within(grid, pts, radius: float, col: str) -> pd.Series:
    buf = grid[["geometry"]].copy()
    buf["geometry"] = buf.geometry.buffer(radius)
    j = gpd.sjoin(buf, pts[[col, "geometry"]], predicate="contains")
    return j.groupby(level=0)[col].sum().reindex(grid.index, fill_value=0)


def freezing_hotspots():
    df = pd.read_csv(RAW_DIR / "accident_areas" / "jeju_freezing.csv").drop_duplicates("spot_cd", keep="last")
    return points_from_latlng(df, "la_crd", "lo_crd")


def icing_sections():
    """행안부 상습결빙구간 (전북 프로젝트에서 복사, 1100도로·516도로 13구간) 기점."""
    df = pd.read_csv(RAW_DIR / "from_jeonbuk" / "mois_habitual_icing_jeju.csv", encoding="utf-8-sig")
    return points_from_latlng(df, "기점 위도(WGS84(4326))", "기점 경도(WGS84(4326))")


NARROW_M = 3.0  # 명세 '3m 이하 외길'


def road_width_cells(step_m: float = 20) -> pd.DataFrame:
    """수치지도 도로중심선(브이월드, 도로폭 rvwd) → H3 셀별 도로 길이·좁은 길(≤3m) 비율·폭 중앙값.

    선을 step_m 간격 점으로 쪼개 점이 떨어진 셀에 길이를 나눠 준다 (셀 경계 교차 계산 없이 빠르게).
    결과는 data/interim/road_width_cells.parquet 에 캐시.
    """
    import h3
    import numpy as np
    from shapely import wkt

    from samchun.config import INTERIM_DIR
    from samchun.spatial import CRS_GEO, H3_RES

    cache = INTERIM_DIR / "road_width_cells.parquet"
    src = RAW_DIR / "road_centerline" / "jeju.csv"
    if cache.exists() and cache.stat().st_mtime > src.stat().st_mtime:
        return pd.read_parquet(cache)
    df = pd.read_csv(src, usecols=["rvwd", "wkt"])
    g = gpd.GeoDataFrame(df, geometry=df.pop("wkt").map(wkt.loads), crs=CRS_GEO).to_crs(CRS_METRIC).explode(index_parts=False)
    rows = []
    for w, line in zip(g["rvwd"].to_numpy(), g.geometry):
        n = max(int(line.length // step_m), 1)
        pts = [line.interpolate((i + 0.5) * line.length / n) for i in range(n)]
        rows += [(p.x, p.y, w, line.length / n) for p in pts]
    pts = pd.DataFrame(rows, columns=["x", "y", "w", "len"])
    ll = gpd.GeoSeries(gpd.points_from_xy(pts["x"], pts["y"]), crs=CRS_METRIC).to_crs(CRS_GEO)
    pts["cell"] = [h3.latlng_to_cell(p.y, p.x, H3_RES) for p in ll]
    pts["narrow_len"] = pts["len"] * (pts["w"] <= NARROW_M)
    out = pts.groupby("cell").agg(road_len_m=("len", "sum"), narrow_len_m=("narrow_len", "sum"))
    out["narrow_road_pct"] = (out["narrow_len_m"] / out["road_len_m"] * 100).round(1)
    # 길이 가중 폭 중앙값
    pts = pts.sort_values(["cell", "w"])
    cum = pts.groupby("cell")["len"].cumsum() / pts["cell"].map(out["road_len_m"])
    out["road_width_median_m"] = pts[cum >= 0.5].groupby("cell")["w"].first()
    out = out.drop(columns="narrow_len_m").round(1)
    cache.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(cache)
    return out


WIDE_M = 6.0  # 교행이 편한 2차로급


def wide_roads():
    """폭 6m 이상 도로중심선 (EPSG 미터 좌표)."""
    from shapely import wkt

    from samchun.spatial import CRS_GEO

    df = pd.read_csv(RAW_DIR / "road_centerline" / "jeju.csv", usecols=["rvwd", "wkt"])
    df = df[df["rvwd"] >= WIDE_M]
    return gpd.GeoDataFrame(df[["rvwd"]], geometry=df["wkt"].map(wkt.loads), crs=CRS_GEO).to_crs(CRS_METRIC)


def _wide_road_dist(grid) -> pd.Series:
    """셀 중심 → 가장 가까운 폭 6m 이상 도로까지 거리(m). 멀수록 좁은 외길을 오래 타야 한다."""
    return nearest_dist_m(grid, wide_roads())


def _pct(s: pd.Series) -> pd.Series:
    """0~1 백분위 (작은 값끼리 동점 → 낮은 순위)."""
    return (s.rank(method="min") - 1) / max(s.notna().sum() - 1, 1)


def _terrain(grid) -> pd.DataFrame:
    from samchun.terrain import terrain_cells

    return terrain_cells().reindex(grid.index)


def _road_risk(grid) -> pd.Series:
    """안심 도로 위험도 (0~100, 높을수록 위험) = 아래 순위의 가중 평균 (사고·경사·고도 1 : 외길 0.5).

    1) 사고: 반경 1km 내 보행 사고다발 + 결빙 사고다발 x2 + 상습결빙구간 x2 + 차량 사고다발(2019~) 사상자/10
    2) 경사: 셀 안 90백분위 경사 (급경사 도로 대리)
    3) 고도: 겨울 안개·결빙 (AWS 11~2월 실측: 안개 시간 비율이 저지대 <1% → 어리목 973m 23%)
    4) 외길: 폭 6m 이상 도로까지 거리 (브이월드 수치지도 도로중심선 도로폭). 2026-11-12 추가 —
       여행로그 검증 변화 없음, 테마 점수 셀 평균 변화 0.2~1.4점.
    """
    acc = (count_within(grid, accident_hotspots(), 1000)
           + 2 * count_within(grid, freezing_hotspots(), 1000)
           + 2 * count_within(grid, icing_sections(), 1000)
           + _weighted_within(grid, car_accident_spots(), 1000, "caslt_cnt") / 10)  # 차량 사고: 사상자 10명 = 다발지점 1곳
    t = _terrain(grid)
    parts = [(_pct(acc), 1), (_pct(t["slope_p90_deg"]), 1), (_pct(t["elev_m"]), 1),
             (_pct(_wide_road_dist(grid).reindex(grid.index)), 0.5)]
    return sum(p * w for p, w in parts) / sum(w for _, w in parts) * 100


# 지표 키 → (원시값 계산 함수, 높을수록 좋은가). 구현되는 대로 등록한다.
REGISTRY: dict = {
    "i01_odor_safety": (_odor_exposure, False),
    "i02_cleanhouse": (_cleanhouse_dist, False),
    "i03_quietness": (_traffic_load, False),
    "i04_emergency": (_er_drive, False),
    "i05_local_food": (_local_food_700m, True),
    "i06_walk_drink": (_walk_drink, True),
    "i07_delivery": (_delivery, True),
    "i08_barrier_free": (_barrier_free, True),
    "i09_transit": (_transit, True),
    "i10_beach_leisure": (_beach_leisure, True),
    "i11_oreum_forest": (_oreum_count_4km, True),
    "i12_safe_road": (_road_risk, False),
}


def build_facts(grid) -> pd.DataFrame:
    """12대 지표 밖의 실측값 (행=H3 셀). §2 테마별 가·감점과 에이전트 fact_telemetry 의 재료."""
    museum = load_raw("museum").dropna(subset=["latitude", "longitude"])
    wifi = load_raw("wifi_ap").dropna(subset=["latitude", "longitude"])
    olle = gpd.read_file(RAW_DIR / "hayoung_olle" / "hayoung-olle.shp").to_crs(CRS_METRIC)
    stops = _bus_stops()
    library = _geocoded(load_raw("library"), "address")
    health = _geocoded(load_raw("health_subcenter"), "위치")
    culture = pd.concat([library[["geometry"]], points_from_latlng(museum, "latitude", "longitude")[["geometry"]]])
    return pd.DataFrame(
        {
            "library_dist_m": nearest_dist_m(grid, library),
            "culture_1km": count_within(grid, culture, 1000),  # 도서관+박물관+미술관
            # 보건지소·진료소 자료는 제주시만 있어 서귀포 셀은 비운다
            "health_subcenter_dist_m": nearest_dist_m(grid, health).where(grid["sggnm"] == "제주시"),
            "museum_dist_m": nearest_dist_m(grid, points_from_latlng(museum, "latitude", "longitude")),
            "museum_1km": count_within(grid, points_from_latlng(museum, "latitude", "longitude"), 1000),
            "wifi_ap_100m": count_within(grid, points_from_latlng(wifi, "latitude", "longitude"), 100),
            "hayoung_olle_dist_m": nearest_dist_m(grid, olle),
            "bus_stop_350m": count_within(grid, stops, 350),
            "bus_stop_dist_m": nearest_dist_m(grid, stops),
            "bus_routes_350m": _routes_within(grid, 350, "routes"),
            "trunk_routes_350m": _routes_within(grid, 350, "trunk_routes"),
            "odor_facility_1500m": count_within(grid, odor_facilities(), 1500),
            "pig_farms_3km": count_within(grid, points_from_latlng(pig_farms(), "lat", "lng"), 3000),
            "model_restaurant_700m": count_within(grid, model_restaurants(), 700),
            "hot_spring_dist_m": nearest_dist_m(grid, _geocoded(pd.read_csv(RAW_DIR / "hot_spring" / "api.csv", encoding="utf-8-sig"), "이용시설위치")),
            "leisure_biz_1km": count_within(grid, _geocoded(pd.read_csv(RAW_DIR / "leisure_business" / "api.csv", encoding="utf-8-sig"), "소재지"), 1000),
            # 렌터카 (2020~21 표본 약 3,400대)
            "rentcar_move_monthly": _rentcar_cells("move"),  # 월평균 통행 대수
            # 생활 편의 (소상공인 상가정보 2026-09)
            "convenience_700m": count_within(grid, stores(scls={"편의점"}), 700),
            "pharmacy_700m": count_within(grid, stores(scls={"약국"}), 700),
            "clinic_700m": count_within(grid, stores(mcls={"의원"}), 700),
            "cafe_500m": count_within(grid, stores(scls={"카페"}), 500),
            "pub_1km": count_within(grid, stores(scls=PUBS), 1000),
            "night_pharmacy_dist_m": nearest_dist_m(grid, _geocoded(pd.read_csv(RAW_DIR / "night_pharmacy" / "api.csv", encoding="utf-8-sig"), "소재지")),
            # 사고·결빙
            "child_accident_1km": count_within(grid, accident_hotspots({"보행어린이", "스쿨존어린이"}), 1000),
            "elder_accident_1km": count_within(grid, accident_hotspots({"보행노인"}), 1000),
            "car_accident_casualties_1km": _weighted_within(grid, car_accident_spots(), 1000, "caslt_cnt"),
            "car_accident_deaths_1km": _weighted_within(grid, car_accident_spots(), 1000, "dth_dnv_cnt"),
            "icing_section_dist_m": nearest_dist_m(grid, icing_sections()),
            # 도로 폭 (브이월드 수치지도 도로중심선)
            "wide_road_dist_m": _wide_road_dist(grid),
            "narrow_road_pct": road_width_cells()["narrow_road_pct"],
            "road_width_median_m": road_width_cells()["road_width_median_m"],
            # 지형 (AWS Terrain Tiles z12)
            **{k: v.round(1) for k, v in _terrain(grid).items()},
            "winter_fog_zone": (_terrain(grid)["elev_m"] >= 300).astype(int),  # 명세 '해발 300m 이상 결빙·안개'
            # 병·의원 (국립중앙의료원)
            "clinic_nemc_700m": count_within(grid, clinics(), 700),
            "night_peds_drive_min": _night_peds_drive(grid),
            # 비짓제주·향토음식점
            "local_food_1km": count_within(grid, local_food_restaurants(), 1000),
            "vj_food_700m": count_within(grid, _vj("음식점"), 700),
            "vj_wifi_cafe_500m": count_within(grid, _vj("음식점", {"카페"}).pipe(lambda g: g[_vj_has(g, {"무료 WIFI", "공공와이파이존"})]), 500),
            "vj_kids_1km": count_within(grid, _vj(any_tags=KIDS_TAGS), 1000),
            "vj_lodging_1km": count_within(grid, _vj("숙박"), 1000),
            # 관광공사 TourAPI · AI Hub 여행로그(2023)
            "beach_dist_m": _beach_dist(grid).round(0),
            "marine_leisure_1km": count_within(grid, marine_leisure(), 1000),
            "kto_barrier_free_1km": count_within(grid, kto_barrier_free(), 1000),
            "congestion_rate_500m": _congestion(grid),
            **_travellog_stats(grid),
            **{f"rentcar_{k}": v for k, v in spot_cells().items()},  # 핫플 방문·숙박·새벽 주차 차량 수
        }
    ).reindex(grid.index).round(0)


def build_raw(grid) -> pd.DataFrame:
    """구현된 지표의 원시값 (행=H3 셀). 검증·에이전트 fact_telemetry 용."""
    return pd.DataFrame({k: fn(grid) for k, (fn, _) in REGISTRY.items()}).reindex(grid.index)


def build_indicators(raw: pd.DataFrame) -> pd.DataFrame:
    """원시값 → 0~100 지표. 미구현 지표 열은 없고, scoring 단계에서 중립값(50)으로 채워진다."""
    return pd.DataFrame({k: normalize(raw[k], REGISTRY[k][1]) for k in raw.columns})
