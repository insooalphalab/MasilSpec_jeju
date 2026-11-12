"""마실스펙_제주 대시보드 로컬 서버: 예시 탭 + 도로명 주소 검색.

    python -m samchun.server            # http://127.0.0.1:8800
    python -m samchun.server --llm      # 검색 결과 브리핑을 LLM(Ollama)으로

GET /                         대시보드 (예시 4곳 미리 계산)
GET /api/view?q=주소&theme=    주소(도로명·지번) 또는 '위도,경도' → view JSON (템플릿 문장, 1~3초)
GET /api/brief?q=위도,경도&theme=  --llm 일 때 삼춘 브리핑·한줄평만 LLM 으로 (20~30초) → 화면이 나중에 채움
브이월드 키는 서버 안에서만 쓰고 브라우저로 내보내지 않는다.
"""
import json
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from samchun import agent, dashboard, tiles

STATE = {"examples": [], "llm": False}
TILE = re.compile(r"/tiles/(\d+)/(\d+)/(\d+)\.png")  # /tiles/{z}/{y}/{x}.png (WMTS 순서)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, ctype: str) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, dashboard.html(STATE["examples"], server=True), "text/html")
        if m := TILE.fullmatch(u.path):
            return self._tile(*map(int, m.groups()))
        if u.path == "/api/view":
            qs = parse_qs(u.query)
            q = (qs.get("q") or [""])[0].strip()
            theme = (qs.get("theme") or [""])[0] or None
            if not q:
                return self._send(400, json.dumps({"error": "주소를 입력해 주세요"}, ensure_ascii=False), "application/json")
            if theme and theme not in agent.THEME_LABEL:
                return self._send(400, json.dumps({"error": f"알 수 없는 테마: {theme}"}, ensure_ascii=False), "application/json")
            try:
                # 점수·지도는 바로 (1~3초). LLM 문장은 화면이 /api/brief 로 따로 받아 채운다 (20~30초)
                v = dashboard.view(q, theme, use_llm=False)
                v["title"] = v["meta"]["address"]  # '…리 (마을 중심 추정)' 표시가 제목에 남도록
                v["meta"]["llm_pending"] = STATE["llm"]
                return self._send(200, json.dumps(v, ensure_ascii=False, default=str), "application/json")
            except ValueError as e:  # 주소 변환 실패 / 제주 밖
                return self._send(404, json.dumps({"error": str(e)}, ensure_ascii=False), "application/json")
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), "application/json")
        if u.path == "/api/brief":
            qs = parse_qs(u.query)
            q = (qs.get("q") or [""])[0].strip()
            theme = (qs.get("theme") or [""])[0] or None
            if not q or not STATE["llm"] or (theme and theme not in agent.THEME_LABEL):
                return self._send(400, json.dumps({"error": "bad request"}), "application/json")
            try:
                return self._send(200, json.dumps(dashboard.llm_parts(q, theme), ensure_ascii=False), "application/json")
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), "application/json")
        self._send(404, "not found", "text/plain")

    def _tile(self, z: int, y: int, x: int) -> None:
        """브이월드 배경지도(white) 중계 + 디스크 캐시. 키는 브라우저로 나가지 않는다."""
        if not 6 <= z <= 19:
            return self._send(404, "zoom", "text/plain")
        data = tiles.tile(z, y, x)
        if data is None:
            return self._send(404, "tile", "text/plain")
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        if "/tiles/" not in (args[0] if args else ""):
            sys.stderr.write(f"[samchun] {self.address_string()} {fmt % args}\n")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--llm", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    STATE["llm"] = a.llm
    print("예시 계산 중 (데이터 로딩 포함 1~2분)...", flush=True)
    STATE["examples"] = dashboard.example_views(a.llm)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"준비 완료 → http://127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
