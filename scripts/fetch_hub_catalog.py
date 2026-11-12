import math
import time
import pandas as pd
import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://jejudatahub.net/data/list/1",
    "Accept": "application/json, text/plain, */*",
}

base_url = "https://jejudatahub.net/api/data"

# 1. 먼저 1회 요청으로 전체 데이터 건수(1,259개) 및 키 구조 파악
test_params = {
    "includedKeywords": "",
    "excludedKeywords": "",
    "categories": "",
    "userId": "3907",
    "dataType": "",
    "orderBy": "createAt",
    "authYn": "false",
    "autoUpdateYn": "false",
    "keyword": "",
    "keywordOrTagFlag": "keyword",
    "isPaging": "true",
    "start": 0,
    "pageNumber": 1,
    "length": 50,  # 50개 단위 페이징
}

res = requests.get(base_url, headers=headers, params=test_params)
res_json = res.json()

# 데이터 리스트 키 자동 판별 ('data', 'list', 'content' 등)
target_key = None
for key in ["data", "list", "content", "items", "results"]:
    if key in res_json and isinstance(res_json[key], list):
        target_key = key
        break

if not target_key:
    print("데이터 배열 키를 찾을 수 없습니다. 응답 내용:", res_json)
    exit()

# 전체 건수 확인 (DataTables 표준: recordsTotal 또는 totCnt)
total_count = res_json.get(
    "recordsTotal",
    res_json.get("recordsFiltered", res_json.get("totCnt", 1259)),
)
page_size = 50
total_pages = math.ceil(total_count / page_size)

print(
    f"데이터 키: '{target_key}' / 전체 대상 건수: {total_count}개 / 총 {total_pages}페이지 수집 시작"
)

all_items = []
all_items.extend(res_json[target_key])

# 2. 2페이지부터 끝까지 순회 수집
for page in range(2, total_pages + 1):
    params = test_params.copy()
    params["pageNumber"] = page
    params["start"] = (page - 1) * page_size
    params["length"] = page_size

    r = requests.get(base_url, headers=headers, params=params)
    if r.status_code == 200:
        page_data = r.json().get(target_key, [])
        all_items.extend(page_data)
        print(
            f"[{page}/{total_pages}] 수집 완료 (현재 누적: {len(all_items)}건)"
        )
    else:
        print(f"[{page}페이지] 요청 실패 (상태 코드: {r.status_code})")

    time.sleep(0.3)  # 부하 방지용 짧은 딜레이

# 3. 엑셀 파일 저장
df = pd.DataFrame(all_items)
from pathlib import Path

output_filename = Path(__file__).resolve().parents[1] / "data" / "reference" / "jejudatahub_list_all.xlsx"
df.to_excel(output_filename, index=False)

print(
    f"\n수집 완료: 총 {len(df)}개 항목이 '{output_filename}'으로 저장되었습니다."
)