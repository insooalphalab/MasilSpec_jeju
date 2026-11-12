import pytest

from samchun.agent import THEME_LABEL, josa

PROCESSED = pytest.importorskip("samchun.config").PROCESSED_DIR


def test_josa():
    assert josa("오션", "은/는") == "오션은"
    assert josa("마운틴 아웃도어", "은/는") == "마운틴 아웃도어는"
    assert josa("휴양", "이/가") == "휴양이"
    assert josa("노마드", "이/가") == "노마드가"


def test_clean_brief():
    from samchun.agent import clean_brief

    raw = "조캐야, 인사말이여.\n\n## 삼춘 총평 — 두괄식 2~3문장.\n본문\n### 삼춘의 칭찬\n- 좋수다"
    assert clean_brief(raw) == "## 삼춘 총평\n본문\n## 삼춘의 칭찬\n- 좋수다"


def test_fix_dialect():
    from samchun.agent import fix_dialect

    assert fix_dialect("맛은 보장하수다.") == "맛은 보장하주게."
    assert fix_dialect("정신 차려야 한수다.") == "정신 차려야 허주게."
    assert fix_dialect("상위 1%나 됩수다. 위험이 아주 크수다.") == "상위 1%나 되우다. 위험이 아주 크우다."
    assert fix_dialect("사고 위험이 하영 많수다. 조심허곡.") == "사고 위험이 아주 많수다. 조심허주."
    assert fix_dialect("| 하수다 | 1 |") == "| 하수다 | 1 |"  # 표는 그대로


@pytest.mark.skipif(not (PROCESSED / "scores.parquet").exists(), reason="파이프라인 산출물 없음")
def test_template_brief_uses_only_payload_numbers():
    from samchun.agent import build_payload, template_brief

    p = build_payload("33.3938,126.2392")  # 협재 (좌표라 지오코딩 호출 없음)
    text = template_brief(p)
    assert set(p.scores) == set(THEME_LABEL)
    for head in ("## 삼춘 총평", "## 8대 테마 점수", "## 삼춘의 칭찬", "## 삼춘의 팩트 경고", "## 현실 처방전"):
        assert head in text
    import re

    assert not re.search(r"(상위|하위) 0%", text)
