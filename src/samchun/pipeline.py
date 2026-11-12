"""전체 파이프라인: 경계 → H3 그리드 → 지표 → 테마 점수.

    python -m samchun.pipeline

산출물 (data/processed/): grid.parquet, raw.parquet, facts.parquet, indicators.parquet, scores.parquet
"""
from samchun.config import PROCESSED_DIR
from samchun.features import build_facts, build_indicators, build_raw
from samchun.scoring import score_themes
from samchun.spatial import build_grid, load_boundary


def run() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    grid = build_grid(load_boundary())
    grid.to_parquet(PROCESSED_DIR / "grid.parquet")
    raw = build_raw(grid)
    raw.to_parquet(PROCESSED_DIR / "raw.parquet")
    build_facts(grid).to_parquet(PROCESSED_DIR / "facts.parquet")
    indicators = build_indicators(raw)
    indicators.to_parquet(PROCESSED_DIR / "indicators.parquet")
    scores = score_themes(indicators)
    scores.to_parquet(PROCESSED_DIR / "scores.parquet")
    print(f"셀 {len(grid):,}개 / 지표 {indicators.shape[1]}개 구현 / 저장: {PROCESSED_DIR}")


if __name__ == "__main__":
    run()
