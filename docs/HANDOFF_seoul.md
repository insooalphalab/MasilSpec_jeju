# 인수인계: 마실스펙_제주 → 서울판 시작

> 새 대화의 첫 메시지에 이 파일을 지정하면 된다:
> `docs/HANDOFF_seoul.md 읽고 이어서 서울판 시작하자. 1단계(지역 설정 분리)부터.`
>
> 작성 2026-11-12. 프로젝트 전체 설명은 `README.md`, 원 명세는 `docs/ai_agent.md`.

---

## 0. 한 줄 요약
제주판(마실스펙_제주)은 완성 단계. 같은 폴더에서 **지역 설정만 분리**해 서울판(마실스펙_서울)을 만든다.
제주판 동작은 그대로 유지해야 한다.

---

## 1. 제주판 현재 상태 (그대로 둘 것)

| 항목 | 상태 |
|---|---|
| 점수 | H3 res9 육상 23,439셀 × 12지표 × 8테마 (`config/themes.yaml`), `python -m samchun.pipeline` |
| 검증 | AI Hub 2023 여행로그 (밤문화 +4.7 1위, 등산 +1.1 2위 …), 가족 테마는 상가정보 아이 시설 상관 +0.46 |
| 대시보드 | `app/dashboard/template.html` 단일 템플릿, 정적 HTML(`example.html`) + 로컬 서버(`samchun.server`, 검색 1~2초 + LLM 문장 나중 채움) |
| 예시 4곳 | 제주시청(가족·OFYB) · 성산일출봉(오션·OFYW) · 영실매표소(마운틴·MSQB) · 가시리(고요·MFQW) — `dashboard.EXAMPLES` |
| 삼춘 브리핑 | Ollama `gemma4:12b-it-qat`, 생각 모드 끔(`reasoning_effort: none`), 제주어 규칙 + `fix_dialect` 교정 |
| MBTI | 16유형 (`config/masil_mbti_16.md`), 제주 소재 캐릭터 (`config/masil_mbti_art.yaml`), 일러스트 `data/raw/jeju_cardimage/NN.png` |
| git | `main` 커밋 5개, origin = github.com/insooalphalab/MasilSpec_jeju — **아직 푸시 안 함** (사용자가 PC 앞에서 `git push -u origin main`) |
| 테스트 | `python -m pytest -q` 11개 통과 |

## 2. 사용자 선호·원칙 (꼭 지킬 것)

- 한국어로 답한다. 결과는 표·그림 중심, 기술 용어 칩은 화면 아래로 숨긴다.
- 사용자는 **원격으로** 자주 본다 → localhost 화면 대신 `scripts/screenshot.py` 캡처·md 비교 파일을 SendUserFile 로 보낸다.
- 디자인: JTP 팔레트(`template.html` 의 :root 토큰), 그래프 우선, 정적 HTML 이 서버 없이 동작해야 함, 지도 점수 격자 기본 ON + 종류별 대표 지점 1곳.
- 가중치를 바꿀 땐 **변경 전 점수를 `data/processed/backup/` 에 백업**하고 검증 지표 전후를 보고한 뒤 적용. 큰 결정은 대안 2~3개 비교 후 추천.
- 보안: `.env` 값은 절대 출력·커밋하지 않는다. 브이월드 키는 서버 안에서만 (HTML 에 넣지 않음). 사용자 이메일 등 개인정보를 외부 요청에 넣지 않는다.
- 포켓몬 등 타사 IP 는 쓰지 않는다 (제주판에서 전부 제거함). 캐릭터는 지역 고유 소재로.
- Windows + PowerShell 5.1 환경: `&&` 불가, 한글 경로(`G:\내 드라이브\…`), 파이썬 실행 시 `$env:PYTHONPATH="src"` 또는 `pip install -e .`.

## 3. 서울판 계획 (합의된 방향)

### 1단계 — 지역 설정 분리 (제주판 무변경이 완료 조건)
```
config/regions/jeju/   themes.yaml · datasets.yaml · masil_mbti_16.md · masil_mbti_art.yaml · region.yaml(이름·bbox·H3·경계·예시)
config/regions/seoul/  같은 구성
data/raw/<region>/…  data/interim/<region>/…  data/processed/<region>/…
```
- `samchun.config` 에 현재 지역 개념 추가 (환경변수 `SAMCHUN_REGION` 또는 `--region`, 기본 jeju).
- 하드코딩된 제주 전용 값 정리 대상: `config.RAW_DIR` 등 경로, `rentcar.JEJU_BBOX`, `agent.resolve` 의 '제주특별자치도' 접두·마을 중심 추정, `features` 의 제주 전용 소스(클린하우스·양돈장·오름·한라산 고도·AWS 결빙), `tiles`/`with_basemap` 패딩, `dashboard.EXAMPLES`, 앱 이름·페르소나('제주 삼춘'·제주어 규칙).
- 완료 확인: 분리 후 `pipeline` 을 다시 돌려 `data/processed/backup/` 의 최신 scores 와 **완전히 같은지** 비교.

### 2단계 — 서울 시범판 (자치구 2~3곳 먼저, 예: 마포·강남·은평)
테마 대응 (사용자에게 제안했던 안):

| 제주 | 서울 |
|---|---|
| 오션 액티비티 | 한강·수변 (한강공원, 하천 산책로, 수상레저) |
| 오름·마운틴 | 산·공원 (북한산·관악산 등산로 입구, 근린공원, 둘레길) |
| 클린하우스 접근성 | 재활용 정거장 또는 생활편의로 교체 |
| 악취(양돈장) | 대기질·도로 소음으로 교체 |
| 대중교통(버스만) | 버스 + 지하철역 거리·노선 수 |
| 마을(리·동) 검색 | 행정동·법정동 |

- 나머지 테마(가족·소셜·워케이션·고요·시니어)는 이름 유지, 지표만 서울 데이터로.
- MBTI 는 첫 축만 바꿈: O/M(오션/마운틴) → 강·수변 / 산·숲. 16유형 이름·대표 동네·캐릭터는 새로. **해치 등 서울시 공식 캐릭터는 상표 확인 필요**.
- 페르소나: '제주 삼춘' 대신 서울판 화자 (사용자와 정할 것). 사투리 교정(`fix_dialect`)은 지역 설정으로 끌 수 있게.
- 데이터: 서울 열린데이터광장(키 필요 시 `.env` 에 추가 — 사용자가 직접 입력), 소상공인 상가정보(전국), 도로교통공단 사고다발(전국, `DATA_GO_KR_KEY`), 브이월드 도로중심선(전국, `Vworld2Ddata_API_KEY`), OSM, AWS 지형.
- 검증: AI Hub 여행로그에 수도권 데이터셋이 있는지 확인. 가족 테마는 제주와 같이 상가정보 아이 시설 상관으로.

## 4. 이미 알고 있는 함정

- Gemma 4 기본 '생각 모드' → 한 문장 24초. 로컬 호출엔 `reasoning_effort: "none"` (이미 `agent._chat` 에 있음).
- `ollama` 설치 직후 열려 있던 터미널은 PATH 를 모름 → 새 창.
- 브이월드 `/req/data` (2D 데이터)는 `Vworld_API_KEY` 로는 거부, `Vworld2Ddata_API_KEY` 로만 됨. 도로중심선 레이어 `LT_L_N3A0020000` (rvwd 도로폭, rdln 차로수).
- 도로명주소 검색 API(`JUSO_API_KEY`)는 좌표·도로폭을 주지 않음 → 주소 정규화 보조로만.
- 국토교통부 교통약자/기상상태 사고 API 는 인증은 되나 0건 → 쓰지 않음.
- 셀별 '좁은 길 비율'은 농로 때문에 변별력 없음 → '폭 6m 이상 도로까지 거리' 사용 (서울은 다시 판단).
- PowerShell 에서 `git commit -F -` 로 here-string 전달 안 됨, `Set-Content -Encoding utf8` 은 BOM 을 붙임 → 커밋 메시지는 Write 도구로 파일을 만들어 `-F 파일`.
- Claude in Chrome 확장은 연결 안 되어 있음 → UI 확인은 헤드리스 크롬 CDP 스크립트(websockets)로 했음 (`scripts/screenshot.py` 는 캡처만).

## 5. 새 대화에서 첫 할 일 체크리스트

1. `README.md`, 이 파일, `src/samchun/config.py` 읽기 (메모리 MEMORY.md 도 자동으로 로드됨).
2. `git status` · `git log --oneline -5` 로 상태 확인 (푸시는 사용자 몫).
3. 1단계 설계안을 짧게 제안 → 사용자 확인 후 구현.
4. 제주 점수 동일성 검증 → 커밋.
5. 서울 시범 자치구·화자 이름을 사용자에게 확인 후 2단계.
