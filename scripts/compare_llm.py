"""예시 위치: LLM 브리핑 vs 템플릿 브리핑 + 근거에 없는 숫자 검사 + 한줄평 → 마크다운.

    python scripts/compare_llm.py                       # → docs/llm_vs_template.md
    python scripts/compare_llm.py --out 다른경로.md

Ollama(또는 LLM_BASE_URL 서버)가 켜져 있어야 한다. 프롬프트·사투리 규칙을 고친 뒤 회귀 확인용.
"""
import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")
from samchun import agent, dashboard  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out", type=Path, default=ROOT / "docs" / "llm_vs_template.md")
a = ap.parse_args()

lines = ["# 삼춘 브리핑: LLM vs 템플릿", ""]
for name, loc, th in dashboard.EXAMPLES:
    p = agent.build_payload(loc, th)
    evid = agent.build_messages(p)[1]["content"]
    t0 = time.time()
    llm = agent.llm_brief(p)
    dt = time.time() - t0
    one = agent.llm_oneliner(p.scores, p.strengths)
    bad = sorted(set(re.findall(r"\d+(?:\.\d+)?", llm)) - set(re.findall(r"\d+(?:\.\d+)?", evid)) - {"1", "2", "3", "8"})
    lines += [f"## {name} ({th})", f"- 생성 {dt:.0f}초 · 근거에 없는 숫자: {bad or '없음'}", f"- 한줄평: {one}", "",
              "### LLM", llm, "", "### 템플릿", agent.template_brief(p), "", "---", ""]
    print(name, f"{dt:.0f}s", "근거 밖 숫자:", bad or "없음", "| 한줄평:", one, flush=True)
a.out.parent.mkdir(parents=True, exist_ok=True)
a.out.write_text("\n".join(lines), encoding="utf-8")
print(a.out)
