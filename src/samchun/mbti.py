"""마실스펙 MBTI 16유형 (config/masil_mbti_16.md).

4쌍의 마주보는 테마 점수를 비교해 4글자 코드를 만든다 (MD §4 규칙):
    O/M = 오션 ≥ 마운틴 · F/S = 가족 ≥ 시니어 · Y/Q = 소셜 ≥ 고요 · W/B = 워케이션 ≥ 뚜벅이

캐릭터는 제주 고유 소재 (config/masil_mbti_art.yaml), 카드 가운데 일러스트는 data/raw/jeju_cardimage/NN.png.
일러스트가 없는 유형은 같은 레이아웃에 그림 프롬프트만 적은 스케치 카드로 그린다.

    python -m samchun.mbti     # 유형 목록·이미지 변환 확인
"""
import base64
import re
from functools import lru_cache
from pathlib import Path

from samchun.config import CONFIG_DIR, ROOT

SPEC = CONFIG_DIR / "masil_mbti_16.md"
ART = CONFIG_DIR / "masil_mbti_art.yaml"  # 제주 고유 소재 캐릭터·일러스트 프롬프트
IMG_DIR = ROOT / "app" / "dashboard" / "mbti"

# (축 이름, 앞 글자 테마, 뒤 글자 테마, 앞 글자, 뒤 글자)
AXES = [
    ("자연 환경", "ocean", "mountain", "O", "M"),
    ("생활 세대", "family", "senior", "F", "S"),
    ("에너지 성향", "social", "quiet_culture", "Y", "Q"),
    ("라이프스타일", "workation", "bus_walker", "W", "B"),
]
LETTER = {"O": "Ocean", "M": "Mountain", "F": "Family", "S": "Senior",
          "Y": "Youth", "Q": "Quiet", "W": "Workation", "B": "Bus-walker"}


def _field(block: str, label: str) -> str:
    m = re.search(rf"\*\*{label}\*\*:\s*(.+)", block)
    return re.sub(r"[*]", "", m.group(1)).strip().strip('"“”') if m else ""


@lru_cache
def types() -> dict[str, dict]:
    """MD 도감 → {코드: {no, code, name, oneliner, features, regions, character, reason, letters}}."""
    text = SPEC.read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r"^### (\d+)\. ([OM][FS][YQ][WB])형 — \"(.+?)\"\n(.*?)(?=^### |^---|\Z)", text, re.M | re.S):
        no, code, name, block = m.groups()
        char = re.search(r"\*\*캐릭터\*\*:\s*\*\*(.+?)\*\*", block)
        reason = re.search(r"\*콘셉트\*:\s*(.+)", block)
        out[code] = {
            "no": int(no), "code": code, "name": name,
            "oneliner": _field(block, "한 문장 정의"),
            "features": _field(block, "지역 특징"),
            "regions": _field(block, "대표 예시 지역"),
            "character": char.group(1).strip() if char else "",
            "reason": reason.group(1).strip() if reason else "",
            "letters": [LETTER[c] for c in code],
        }
    return out


def code_for(scores: dict[str, float]) -> tuple[str, list[dict]]:
    """테마 점수 → (코드, 축별 근거)."""
    code, axes = "", []
    for name, a, b, la, lb in AXES:
        win = la if scores[a] >= scores[b] else lb
        code += win
        axes.append({"axis": name, "a": a, "b": b, "la": la, "lb": lb,
                     "sa": round(scores[a]), "sb": round(scores[b]), "win": win})
    return code, axes


ILLU_SRC = ROOT / "data" / "raw" / "jeju_cardimage"  # 카드 가운데 일러스트 원본 (NN.png, 정사각형)
ILLU_DIR = IMG_DIR / "illu"


def _illu(code: str, no: int, px: int = 600) -> Path | None:
    """카드 가운데 일러스트 → 화면용 JPEG (px 정사각형). 원본이 더 새로우면 다시 변환."""
    src = ILLU_SRC / f"{no:02d}.png"
    dst = ILLU_DIR / f"{no:02d}_{code}_{px}.jpg"
    if src.exists() and (not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime):
        from PIL import Image

        ILLU_DIR.mkdir(parents=True, exist_ok=True)
        im = Image.open(src).convert("RGB")
        im.thumbnail((px, px), Image.LANCZOS)
        im.save(dst, "JPEG", quality=82, optimize=True, progressive=True)
    return dst if dst.exists() else None


def _uri(p: Path | None) -> str | None:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode() if p else None


@lru_cache
def catalog(illu_px: int = 600) -> dict[str, dict]:
    """대시보드용 16유형 도감 (illu: 가운데 일러스트 data URI, 없으면 프롬프트 스케치)."""
    import yaml

    art = yaml.safe_load(ART.read_text(encoding="utf-8")) if ART.exists() else {}
    return {code: t | {"image": None, "art": art.get(code), "illu": _uri(_illu(code, t["no"], illu_px))}
            for code, t in types().items()}


CARD_DIR = IMG_DIR / "cards"
CARD_W, CARD_H = 360, 537  # 카드뉴스 원본 비율 1696:2528


def _browser() -> str:
    import shutil

    for p in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              shutil.which("chrome"), shutil.which("chromium"), shutil.which("msedge")]:
        if p and Path(p).exists():
            return p
    raise RuntimeError("크롬/엣지를 찾지 못했습니다")


def card_page(code: str) -> str:
    """대시보드 template.html 의 카드 CSS·JS 를 그대로 잘라 카드 1장짜리 페이지를 만든다."""
    import json

    tpl = (ROOT / "app" / "dashboard" / "template.html").read_text(encoding="utf-8")
    head = tpl[:tpl.index("<style>")]
    root = re.search(r":root\{.*?\n\}", tpl, re.S).group(0)
    css = tpl[tpl.index("/* 스케치 카드"):tpl.index("\n.mbti-modal{")]
    js = tpl[tpl.index("const LETTER_TAG"):tpl.index("function mbtiBlock")]
    t = json.dumps(catalog(1200)[code] | {"image": None}, ensure_ascii=False).replace("</", "<\\/")  # PNG 저장용 고해상도
    return f"""{head}<style>{root}
*{{box-sizing:border-box}}html,body{{margin:0;background:transparent}}
body{{width:{CARD_W}px;height:{CARD_H}px;font:14px/1.6 var(--sans);-webkit-font-smoothing:antialiased}}
{css}
.mcard{{height:{CARD_H}px;box-shadow:none;cursor:default}}
</style></head><body><div id="c"></div><script>
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}}[c]));
{js}
document.getElementById("c").innerHTML = mbtiCard({t});
</script></body></html>"""


def export_cards(out_dir: Path = CARD_DIR, scale: int = 3) -> list[Path]:
    """16유형 스케치 카드 → PNG (가로 360×scale px) 각각 저장. 헤드리스 크롬/엣지 사용."""
    import subprocess
    import tempfile

    out_dir.mkdir(parents=True, exist_ok=True)
    exe, done = _browser(), []
    with tempfile.TemporaryDirectory() as tmp:
        for code, t in sorted(catalog().items(), key=lambda kv: kv[1]["no"]):
            page = Path(tmp) / f"{code}.html"
            page.write_text(card_page(code), encoding="utf-8")
            png = out_dir / f"{t['no']:02d}_{code}.png"
            subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                            f"--user-data-dir={Path(tmp) / 'profile'}",
                            f"--window-size={CARD_W},{CARD_H}", f"--force-device-scale-factor={scale}",
                            "--default-background-color=00000000", "--virtual-time-budget=6000",
                            f"--screenshot={png}", page.as_uri()],
                           check=True, capture_output=True, timeout=120)
            done.append(png)
    return done


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    if "--export" in sys.argv:  # python -m samchun.mbti --export → app/dashboard/mbti/cards/NN_CODE.png
        for p in export_cards():
            print(p)
        sys.exit()
    cat = catalog()
    for c, t in cat.items():
        print(f"{t['no']:02d} {c} {t['name']:22s} {t['character']:22s} 이미지 {'O' if t['image'] else '-'}"
              f"{' ' + str(len(t['image']) // 1024) + 'KB' if t['image'] else ''}")
