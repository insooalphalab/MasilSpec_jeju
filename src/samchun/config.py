"""경로와 YAML 설정 로딩."""
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"              # 원천 그대로 (데이터셋 키별 하위 폴더)
INTERIM_DIR = DATA_DIR / "interim"      # 정제·좌표계 통일된 GeoParquet
PROCESSED_DIR = DATA_DIR / "processed"  # H3 셀 단위 지표·점수 테이블
CATALOG_XLSX = DATA_DIR / "reference" / "jejudatahub_list_all.xlsx"  # scripts/fetch_hub_catalog.py 로 재수집


def env(name: str) -> str:
    """.env 값 읽기 (따옴표·공백·줄 끝 ' # 주석' 제거). 없으면 KeyError."""
    import re

    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        k, sep, v = line.partition("=")
        v = re.split(r"\s+#", v, maxsplit=1)[0].strip().strip("\"'")
        if sep and k.strip() == name and v:
            return v
    raise KeyError(f".env 에 {name} 값이 없습니다")


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache
def themes_config() -> dict:
    return _load("themes.yaml")


@lru_cache
def datasets_config() -> dict:
    return _load("datasets.yaml")


def theme_keys() -> list[str]:
    return [t["key"] for t in themes_config()["themes"]]


@lru_cache
def weight_matrix() -> pd.DataFrame:
    """행=12대 지표, 열=8대 테마, 값=가중치(합 1.0)."""
    cfg = themes_config()
    rows = {k: v["weights"] for k, v in cfg["indicators"].items()}
    return pd.DataFrame.from_dict(rows, orient="index", columns=theme_keys()) / 100.0
