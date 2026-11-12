"""8대 테마 가중합 스코어링 (ai_agent.md §5)."""
import pandas as pd

from samchun.config import weight_matrix


def score_themes(indicators: pd.DataFrame) -> pd.DataFrame:
    """행=H3 셀, 열=12대 지표(0~100) → 행=H3 셀, 열=8대 테마 점수(0~100).

    없는 지표·결측은 중립값 50으로 채운다. 음수 가중치 때문에 범위를 벗어날 수 있어 0~100으로 자른다.
    """
    w = weight_matrix()
    scores = indicators.reindex(columns=w.index).fillna(50.0) @ w
    return scores.clip(0, 100).round(1)


def percentiles(scores: pd.DataFrame) -> pd.DataFrame:
    """테마별 도내 백분위 (에이전트 2단계 출력용)."""
    return (scores.rank(pct=True) * 100).round(0)
