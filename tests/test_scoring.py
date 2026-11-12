import pandas as pd
import pytest

from samchun.config import theme_keys, themes_config, weight_matrix
from samchun.features import normalize
from samchun.scoring import score_themes


def test_weights_sum_to_one_per_theme():
    assert weight_matrix().sum().round(6).tolist() == [1.0] * 8


def test_polar_pairs_cover_all_themes_once():
    flat = [k for pair in themes_config()["polar_pairs"] for k in pair]
    assert sorted(flat) == sorted(theme_keys())


def test_uniform_indicators_give_same_score():
    w = weight_matrix()
    ind = pd.DataFrame(70.0, index=["cell_a"], columns=w.index)
    assert score_themes(ind).iloc[0].tolist() == pytest.approx([70.0] * 8)


def test_normalize_ties_at_zero():
    raw = pd.Series([0.0] * 7 + [1.0, 5.0, 9.0])
    good = normalize(raw)  # 많을수록 좋음: 0인 셀은 0점
    assert good.iloc[:7].eq(0).all() and good.iloc[-1] == 100
    bad = normalize(raw, higher_is_better=False)  # 많을수록 나쁨: 0인 셀은 100점
    assert bad.iloc[:7].eq(100).all() and bad.iloc[-1] == 0


def test_normalize_inverts():
    raw = pd.Series([10.0, 20.0, 30.0])
    assert normalize(raw).is_monotonic_increasing
    assert normalize(raw, higher_is_better=False).is_monotonic_decreasing
