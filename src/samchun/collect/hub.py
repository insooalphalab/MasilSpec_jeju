"""제주데이터허브 원천 파일: 받아야 할 파일 목록(manifest)과 data/raw/<key>/ 로더.

    python -m samchun.collect.hub            # 공개 데이터 중 아직 없는 파일 목록
    python -m samchun.collect.hub --all      # 승인형 포함

파일은 data/raw/<dataset_key>/<원래 파일명> 으로 둔다.
자동 다운로드(download)는 아직 미구현 — 포털에서 수동으로 받거나, 엔드포인트 확인 후 채운다.
"""
import ast
import sys
from pathlib import Path

import pandas as pd

from samchun.collect.catalog import resolve
from samchun.config import RAW_DIR, datasets_config, env

PAGE_URL = "https://jejudatahub.net/data/view/data/{id}"


def _file_infos(cell) -> list[dict]:
    if not isinstance(cell, str):
        return []
    return ast.literal_eval(cell).get("fileInfos", [])


def manifest(key: str, include_auth: bool = False) -> pd.DataFrame:
    """데이터셋 키 → 받아야 할 파일 목록 (본 데이터 `dataFile` 우선, 없으면 `file`)."""
    rows = []
    for r in resolve(key).itertuples():
        if r.authYn and not include_auth:
            continue
        link = ast.literal_eval(r.dataLink)["url"] if isinstance(r.dataLink, str) else None
        if link and not _file_infos(r.dataFile):
            # 링크형: 허브에는 미리보기만 있고 원본은 외부(주로 data.go.kr)에서 받는다
            got = any((RAW_DIR / key).glob("*.csv")) if (RAW_DIR / key).exists() else False
            rows.append({"key": key, "hub_id": r.id, "auth": bool(r.authYn), "file_name": "(외부 링크)",
                         "size_mb": 0.0, "exists": got, "page": link, "file_info_id": None, "file_id": None})
            continue
        for f in _file_infos(r.dataFile) or _file_infos(r.file):
            path = RAW_DIR / key / f["fileName"]
            rows.append(
                {
                    "key": key,
                    "hub_id": r.id,
                    "auth": bool(r.authYn),
                    "file_name": f["fileName"],
                    "size_mb": round((f["fileSize"] or 0) / 1e6, 2),
                    "exists": path.exists(),
                    "page": PAGE_URL.format(id=r.id),
                    "file_info_id": f["id"],
                    "file_id": f["fileId"],
                }
            )
    return pd.DataFrame(rows)


def manifest_all(include_auth: bool = False) -> pd.DataFrame:
    frames = [manifest(k, include_auth) for k in datasets_config()["hub"]]
    return pd.concat([f for f in frames if len(f)], ignore_index=True)


def read_table(path: Path) -> pd.DataFrame:
    """CSV/XLSX 한 개 읽기. 공공데이터 CSV는 cp949 가 많아 인코딩을 차례로 시도한다."""
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path)
    for enc in ("utf-8-sig", "cp949"):
        try:
            return pd.read_csv(path, encoding=enc, low_memory=False)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8/cp949", b"", 0, 1, f"인코딩 판별 실패: {path}")


def load_raw(key: str) -> pd.DataFrame:
    """data/raw/<key>/ 의 표 파일을 모두 읽어 이어 붙인다 (`_source` 컬럼 = 파일명)."""
    files = sorted(p for p in (RAW_DIR / key).glob("*") if p.suffix.lower() in (".csv", ".xlsx", ".xls"))
    if not files:
        raise FileNotFoundError(f"data/raw/{key}/ 에 파일이 없습니다. `python -m samchun.collect.hub` 참고")
    return pd.concat([read_table(p).assign(_source=p.name) for p in files], ignore_index=True)


API_URL = "https://open.jejudatahub.net/api/proxy/{data_key}/{project_key}"


def fetch_api(key: str, params: dict | None = None, limit: int = 100, max_pages: int = 1000) -> Path:
    """오픈 API 데이터셋을 끝까지 페이징해 data/raw/<key>/api.csv 로 저장.

    .env 의 JEJUHUB_API_KEY(프로젝트 키)가 필요하고, 포털에서 해당 데이터가
    그 프로젝트에 추가·승인돼 있어야 한다 (아니면 403).
    """
    import requests

    rows = resolve(key)
    rows = rows[rows["dataApi"].notna()]
    if rows.empty:
        raise ValueError(f"{key}: API 제공 데이터가 아닙니다")
    url = API_URL.format(data_key=rows.iloc[0]["dataKey"], project_key=env("JEJUHUB_API_KEY"))
    items: list[dict] = []
    for page in range(1, max_pages + 1):
        res = requests.get(url, params={**(params or {}), "number": page, "limit": limit}, timeout=60)
        if res.status_code == 403:
            raise PermissionError(f"{key}: 403 — 프로젝트 키가 틀렸거나 이 데이터 사용 승인이 없습니다")
        res.raise_for_status()
        body = res.json()
        items.extend(body.get("data", []))
        if not body.get("hasMore"):
            break
    out = RAW_DIR / key / "api.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(items).to_csv(out, index=False, encoding="utf-8-sig")
    return out


def download(key: str, include_auth: bool = False) -> list[Path]:
    raise NotImplementedError("자동 다운로드 미구현: manifest() 의 page 링크에서 수동으로 받는다")


if __name__ == "__main__":
    m = manifest_all(include_auth="--all" in sys.argv)
    todo = m[~m["exists"]]
    summary = todo.groupby("key").agg(files=("file_name", "count"), total_mb=("size_mb", "sum"), page=("page", "first"))
    print(summary.to_string())
    print(f"\n받을 파일 {len(todo)}개 / {todo['size_mb'].sum():,.0f} MB (이미 있음 {int(m['exists'].sum())}개)")
