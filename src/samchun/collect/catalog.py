"""제주데이터허브 카탈로그: 레지스트리(config/datasets.yaml)와 실제 목록을 대조한다.

    python -m samchun.collect.catalog      # 데이터셋별 매칭 건수 · 승인 필요 여부 출력

카탈로그 자체의 재수집은 scripts/fetch_hub_catalog.py 가 담당한다 (→ data/reference/jejudatahub_list_all.xlsx).
"""
from functools import lru_cache

import pandas as pd

from samchun.config import CATALOG_XLSX, datasets_config


@lru_cache
def load_catalog() -> pd.DataFrame:
    return pd.read_excel(CATALOG_XLSX)


def resolve(key: str) -> pd.DataFrame:
    """레지스트리 키에 해당하는 카탈로그 행들 (시리즈 데이터는 여러 행)."""
    spec = datasets_config()["hub"][key]
    cat = load_catalog()
    return cat[cat["title"].str.contains(spec["match"], na=False, regex=False)]


def status() -> pd.DataFrame:
    rows = []
    for key in datasets_config()["hub"]:
        m = resolve(key)
        rows.append(
            {
                "key": key,
                "matched": len(m),
                "open": int((~m["authYn"]).sum()),
                "auth_required": int(m["authYn"].sum()),
                "has_api": int(m["dataApi"].notna().sum()),
            }
        )
    return pd.DataFrame(rows)


if __name__ == "__main__":
    print(status().to_string(index=False))
