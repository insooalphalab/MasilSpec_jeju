"""마실스펙_제주 (Streamlit 판): 주소/좌표 → 8대 테마 점수 + 제주 삼춘 브리핑.

    streamlit run app/streamlit_app.py

LLM 서버(.env 의 LLM_BASE_URL, 기본 Ollama)가 없으면 템플릿 브리핑으로 자동 전환된다.
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from samchun.agent import THEME_LABEL, brief  # noqa: E402
from samchun.config import PROCESSED_DIR  # noqa: E402

st.set_page_config(page_title="마실스펙_제주", layout="wide")
st.title("마실스펙_제주 — 마실 나가듯 보는 동네 스펙")

if not (PROCESSED_DIR / "scores.parquet").exists():
    st.error("점수 테이블이 없습니다. 먼저 `python -m samchun.pipeline` 을 실행하세요.")
    st.stop()

with st.sidebar:
    location = st.text_input("주소 또는 '위도,경도'", value="33.3938,126.2392")
    theme_opt = st.selectbox("관심 테마", ["(전체)"] + list(THEME_LABEL), format_func=lambda k: THEME_LABEL.get(k, k))
    use_llm = st.toggle("LLM 브리핑 (꺼지면 템플릿)", value=True)
    go = st.button("삼춘한테 물어보기", type="primary")

if go:
    theme = None if theme_opt == "(전체)" else theme_opt
    with st.spinner("삼춘이 동네 둘러보는 중..."):
        try:
            text, payload, mode = brief(location, theme, use_llm=use_llm)
        except ValueError as e:
            st.error(str(e))
            st.stop()

    left, right = st.columns([2, 3])
    with left:
        st.subheader(payload.adm_name.replace("제주특별자치도 ", "") or payload.address_name)
        st.map(pd.DataFrame({"lat": [payload.coordinate.lat], "lon": [payload.coordinate.lng]}), zoom=12)
        scores = pd.Series(payload.scores).rename(index=THEME_LABEL).sort_values()
        st.bar_chart(scores, horizontal=True)
        with st.expander("§6.2 입력 페이로드"):
            st.json(payload.model_dump())
    with right:
        st.caption(f"생성 방식: {'LLM' if mode == 'llm' else '템플릿 (LLM 서버 없음 또는 꺼짐)'}")
        st.markdown(text)
