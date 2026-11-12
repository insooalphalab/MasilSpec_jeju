"""HTML 을 헤드리스 크롬/엣지로 열어 PNG 로 캡처 (원격에서 결과 확인용).

    python scripts/screenshot.py app/dashboard/example.html docs/screenshots/dashboard.png
    python scripts/screenshot.py app/dashboard/example.html out.png --size 1400x3000
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from samchun.mbti import _browser  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("html", type=Path)
ap.add_argument("out", type=Path)
ap.add_argument("--size", default="1400x2600", help="창 크기 가로x세로 (세로가 캡처 높이)")
a = ap.parse_args()
w, h = a.size.lower().split("x")
a.out.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    subprocess.run([_browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={tmp}",
                    f"--window-size={w},{h}", "--virtual-time-budget=10000", f"--screenshot={a.out.resolve()}",
                    a.html.resolve().as_uri()], check=True, capture_output=True, timeout=180)
print(a.out)
