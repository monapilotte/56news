# 키워드 뉴스 → 디스코드 알림 봇

지정한 인물·키워드에 대한 새 뉴스가 올라오면 디스코드 채널로 **`📰 제목 · 언론사`** 알림을 보내는 봇.
동명이인·단순 언급 기사를 걸러내고, 관련 기사(경기·인터뷰·사진 등)만 선별한다.
예시 설정은 야구선수 **최준용(롯데 자이언츠)** 을 대상으로 구성되어 있으며, `keywords.json`만 바꾸면 다른 인물·주제에도 그대로 쓸 수 있다.

- **뉴스 소스**: NAVER API HUB 검색 API (하루 25,000건 무료)
- **실행**: GitHub Actions — 서버·PC 없이 24시간 동작
- **알림**: 디스코드 웹훅

---

## 기능

- 키워드로 새 뉴스 수집 → 디스코드 알림 (제목·언론사·링크)
- 다층 필터로 정확도 확보 (동명이인·잡기사 제거, 사진기사 감지)
- 중복 방지(`seen.json`) · 첫 실행 시 기존 기사 무음 기록(도배 방지)
- 언론사명 자동 추정, 하루 호출량 상한
- 소스 전환 가능: `NEWS_SOURCE=naver`(NAVER API HUB) / `google`(구글 뉴스 RSS, 키 불필요)

## 동작 방식

```
스케줄러 --주기적 트리거--> GitHub Actions --> news_bot.py
                                                 |
                    NAVER API HUB 검색 --기사--> [필터] --통과--> 디스코드 웹훅
                                                 |
                                           seen.json (중복 방지 상태)
```

GitHub의 내장 `schedule` 트리거는 지연·누락이 잦으므로, 외부 크론(예: cron-job.org)이
`workflow_dispatch` API로 워크플로를 정시에 깨우는 구성을 권장한다.

## 파일 구성

| 파일 | 역할 |
|------|------|
| `news_bot.py` | 본체: 검색 → 필터 → 웹훅 (`--once` 로 1회 실행) |
| `keywords.json` | 감시 대상 + 필터 규칙 (대부분의 조정은 여기서) |
| `.env` / `.env.example` | 비밀값·설정 (`.env` 는 커밋 금지) |
| `requirements.txt` | 의존성 (`requests`, `python-dotenv`) |
| `.github/workflows/news.yml` | GitHub Actions 워크플로 |
| `seen.json` | 발송 기록 (자동 관리) |

## 빠른 시작 (로컬)

```bash
pip install -r requirements.txt
cp .env.example .env      # 값 채우기
python news_bot.py        # 5분 주기 실행
python news_bot.py --once # 1회 실행 후 종료
```

## 설정 레퍼런스 (`keywords.json`)

감시 대상은 객체 배열로 정의한다. 각 필드:

| 필드 | 설명 |
|------|------|
| `query` | 검색 문자열. 문자열 또는 리스트(각각 검색 후 합침·중복제거) |
| `label` | 알림/상태파일 표시 이름 |
| `title_any` | 관련성 기준 이름(제목·본문에서 탐색) |
| `exclude_any` | 하나라도 있으면 제외 (동명이인·잡음) |
| `include_any` | 지정 시 하나는 필수 |
| `link_exclude` | 기사 주소에 이 조각이 있으면 제외 (예: `/basketball/`) |
| `context_any` | 주제 맥락 단어. 하나는 있어야 통과 |
| `context_skip_link` | 이 주소면 `context_any` 검사 면제 (예: `/kbaseball/`) |
| `photo_markers` | 제목에 있으면 사진기사로 인정하는 태그(`[포토]` 등) |
| `photo_desc_endings` | 요약이 이 어미로 끝나면 사진 캡션으로 인정 (예: `"고 있다"`) |
| `body_any` | 관련성 통과용 동작 단어 |
| `min_name_mentions` | 요약에 이름이 이 횟수 이상이면 통과(중간 모드) |
| `require_action_in_body` | `true` 면 엄격 모드(이름+동작 필수) |

## 필터 파이프라인

기사 하나가 아래 순서로 검사되며, 먼저 걸리면 탈락한다.

1. `link_exclude` — 주소 기반 제외 (예: 다른 카테고리)
2. `exclude_any` — 금지어 제외 (동명이인)
3. `include_any` — 필수어(설정 시)
4. `context_any` — 주제 맥락 단어 필요 (단 `context_skip_link` 주소는 면제)
5. 관련성 판정 — 다음 중 하나면 통과:
   - 제목에 이름이 있음
   - 사진기사로 감지됨(`photo_markers` 태그 또는 요약이 `photo_desc_endings` 로 끝남)
   - 엄격 모드: 이름 + `body_any` 동작어
   - 중간 모드: 이름이 `min_name_mentions` 회 이상, 또는 `body_any` 동작어
   - 기본: 이름만 있어도 통과

## 배포 (GitHub Actions)

1. 저장소에 코드·설정 업로드 (**`.env` 제외**)
2. **Settings → Secrets and variables → Actions** 에 등록:
   `NCP_API_KEY_ID`, `NCP_API_KEY`, `DISCORD_WEBHOOK_URL`
3. 워크플로가 매 실행마다 `news_bot.py --once` 를 돌리고 `seen.json` 을 커밋한다
4. 정시 실행이 필요하면 외부 크론에서 아래를 주기적으로 POST:
   - `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/news.yml/dispatches`
   - 헤더: `Authorization: Bearer <PAT>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`
   - 본문: `{"ref":"main"}`
   - PAT은 해당 저장소 **Actions: Read and write** 권한

## API 키 발급

- **디스코드 웹훅**: 채널 편집 → 연동 → 웹훅 → URL 복사
- **NAVER API HUB**: 네이버 클라우드 플랫폼 콘솔 → NAVER API HUB → 애플리케이션 등록 → 인증정보
  (검색 API는 구 개발자센터가 아닌 API HUB에서 발급)

## 튜닝

- 엉뚱한 기사가 옴 → `exclude_any` 에 해당 단어 추가
- 와야 할 기사가 안 옴 → `min_name_mentions` 를 낮추거나 `body_any` 에 단어 추가
- 알림이 너무 많음 → `min_name_mentions` 를 올리거나 `body_any` 를 비움/축소
- 다른 인물·주제 추가 → `keywords.json` 배열에 객체 추가

## 한계

- 네이버 검색 인덱싱 지연으로, 갓 게시된 기사는 알림이 수 분~수십 분 늦을 수 있다.
- 검색 API는 본문 전체가 아닌 요약(description)만 제공하므로, 사진기사 감지는
  제목 태그와 요약 문법(`"고 있다"`)에 기반한다.
