# 뉴스 → 디스코드 알림 봇 (최준용 덕질용)

특정 인물(기본: **최준용 / 롯데 자이언츠 투수**) 관련 새 뉴스가 올라오면,
디스코드 채널로 **`📰 제목 · 언론사`** 알림을 보내주는 봇이다.
동명이인(농구선수·배우)과 "이름만 스친 잡기사"를 걸러내고, 사진기사·인터뷰·경기기사만 골라준다.

- 뉴스 소스: **NAVER API HUB 검색 API** (하루 25,000건 무료)
- 실행: **GitHub Actions** (cron-job.org가 5분마다 트리거) → PC 꺼도 24시간 동작
- 알림: **디스코드 웹훅**
- 등판일지(선택): KBO 공식 기록에서 최준용 등판 경기를 요약해 전송

---

## 1. 구성 요소

```
[cron-job.org] --5분마다--> [GitHub Actions] --실행--> news_bot.py / pitch_log.py
                                                          |
                          NAVER API HUB 검색 --기사--> [필터] --통과--> [디스코드 웹훅]
                                                          |
                                                    seen.json (중복 방지, 자동 커밋)
```

- GitHub 내부 스케줄은 지연·누락이 심해서, 외부 무료 크론(cron-job.org)이
  워크플로를 `workflow_dispatch` API로 정확히 5분마다 깨운다.

## 2. 파일 설명

| 파일 | 역할 |
|------|------|
| `news_bot.py` | 본체. 검색 → 필터 → 웹훅. `--once`(GitHub용) 지원 |
| `keywords.json` | **감시 대상 + 필터 규칙** (여기만 고치면 대부분 조정 가능) |
| `pitch_log.py` | 등판일지. KBO 일자별 기록 파싱 → 새 등판 요약 전송 |
| `.env` | 비밀값(API 키·웹훅). **업로드 금지**(.gitignore) |
| `.env.example` | .env 템플릿 |
| `requirements.txt` | 파이썬 의존성 (requests, python-dotenv) |
| `.github/workflows/news.yml` | GitHub Actions 워크플로 |
| `seen.json` / `pitch_seen.json` | 이미 보낸 기사/등판 기록 (자동 관리) |

## 3. 필터 동작 (기사 하나가 통과하는 순서)

`news_bot.py`의 `passes_filter()`가 아래 순서로 검사한다. **위에서 걸리면 즉시 탈락.**

1. **`link_exclude`** — 기사 주소에 이 조각이 있으면 제외
   예: `/basketball/` → 농구 카테고리 기사 100% 차단
2. **`exclude_any`** — 제목/요약에 이 단어가 있으면 제외
   예: `농구`, `여준석`, `요르단`, `배우` … (동명이인 제거)
3. **`include_any`** — 지정 시, 이 중 하나는 있어야 함 (현재 미사용)
4. **`context_any` (종목 게이트)** — 야구 맥락 단어가 하나도 없으면 제외.
   단 주소가 **`context_skip_link`(=`/kbaseball/`)** 면 면제(명백한 야구라서).
   → "야구 단어 없는 농구 인터뷰(mnews)" 같은 위장 기사를 잡는다.
5. **관련성 판정** (`title_any` = 감시 이름)
   아래 중 하나면 통과, 아니면 제외:
   - 제목에 이름이 있음
   - **사진기사**로 감지됨:
     - 제목에 `photo_markers` 태그(`[포토]`,`[사진]`,`[MD포토]`…) 있음, 또는
     - 요약이 `photo_desc_endings`(=`"고 있다"`)로 끝남 → 사진 캡션(장면 묘사)
   - `require_action_in_body=true`(엄격): 요약에 이름 + `body_any`(투구동작) 둘 다
   - `min_name_mentions≥2`(중간, 현재): 요약에 이름이 그만큼 나오거나 `body_any` 있음
   - 위 옵션이 없으면(느슨): 요약에 이름만 있어도 통과

> 순서 덕분에 **농구는 앞(1·2·4)에서 먼저 걸러지고**, 사진·관련성 판정은 이미
> "야구로 확인된 기사"에만 적용된다.

### keywords.json 필드 요약

| 필드 | 뜻 |
|------|-----|
| `query` | 네이버에 검색할 문자열(리스트면 각각 검색 후 합침·중복제거) |
| `label` | 디스코드/상태파일에 쓰는 표시 이름 |
| `title_any` | 관련성 기준이 되는 이름(제목/본문에서 이 단어를 찾음) |
| `exclude_any` | 있으면 무조건 제외 (동명이인·잡음) |
| `include_any` | 있으면 그 중 하나는 필수 (선택) |
| `link_exclude` | 주소에 이 조각 있으면 제외 (예: `/basketball/`) |
| `context_any` | 종목 맥락 단어. 하나는 있어야 함(아래 면제 제외) |
| `context_skip_link` | 이 주소면 `context_any` 검사 면제 (예: `/kbaseball/`) |
| `photo_markers` | 제목에 있으면 사진기사로 인정하는 태그 |
| `photo_desc_endings` | 요약이 이 어미로 끝나면 사진 캡션으로 인정 (`"고 있다"`) |
| `body_any` | 관련성 통과용 동작/세리머니 단어(등판·삼진·포옹…) |
| `min_name_mentions` | 중간 모드. 요약에 이름이 이 횟수 이상이면 통과 |
| `require_action_in_body` | `true`면 엄격 모드(이름+동작 필수). 인터뷰는 놓칠 수 있음 |

## 4. 관련성 모드 3단계 (강도 조절)

`keywords.json`에서 한 줄로 조절한다.

| 설정 | 모드 | 특징 |
|------|------|------|
| `"require_action_in_body": true` | **엄격** | 경기내용만. 인터뷰·소감 놓침 |
| `"min_name_mentions": 2` | **중간 (현재)** | 인터뷰 잡고 명단 잡기사 버림 |
| 둘 다 없음 | **느슨** | 이름만 있으면 다 잡음(잡기사 섞임) |

## 5. 설정 & 배포

### 준비물
1. **디스코드 웹훅 URL** — 채널 편집 → 연동 → 웹훅
2. **NAVER API HUB 키** — Ncloud 콘솔 → NAVER API HUB → 앱 등록 → 인증정보
   (검색 API는 예전 developers.naver.com이 아니라 **API HUB에서만** 발급됨)

### 로컬 실행
```bash
pip install -r requirements.txt
python news_bot.py          # 5분 루프
python news_bot.py --once   # 1회 실행 후 종료
```
`.env.example`을 `.env`로 복사해 값을 채운다.

### GitHub Actions 배포 (무료·24시간)
1. 저장소에 `news_bot.py`, `pitch_log.py`, `keywords.json`, `requirements.txt`,
   `seen.json`, `pitch_seen.json`, `.github/workflows/news.yml` 업로드 (**`.env` 제외**)
2. **Settings → Secrets and variables → Actions** 에 등록:
   - `NCP_API_KEY_ID`, `NCP_API_KEY`, `DISCORD_WEBHOOK_URL`
   - (선택) `DISCORD_WEBHOOK_URL_PITCH` — 등판일지 전용 채널
3. **cron-job.org**(무료)에서 5분마다 아래를 POST:
   - URL: `https://api.github.com/repos/<계정>/<저장소>/actions/workflows/news.yml/dispatches`
   - 헤더: `Authorization: Bearer <GitHub PAT>`, `Accept: application/vnd.github+json`,
     `X-GitHub-Api-Version: 2022-11-28`
   - 본문: `{"ref":"main"}`
   - GitHub PAT은 Fine-grained, 해당 저장소 **Actions: Read and write** 권한

## 6. 튜닝 가이드 (자주 하는 조정)

- **엉뚱한 기사가 왔다** → 그 기사에만 있는 단어를 `exclude_any`에 추가
- **농구가 샜다** → 주소가 `/basketball/`이면 이미 차단됨. mnews 등으로 위장했으면
  그 농구 특유 단어(팀명·동료 이름)를 `exclude_any`에 추가
- **와야 할 기사가 안 왔다** → `min_name_mentions`를 낮추거나(2→1),
  그 기사의 핵심 단어를 `body_any`에 추가
- **경기날 도배가 심하다** → `min_name_mentions`를 올리거나(2→3) 엄격 모드로
- **다른 선수/주제 추가** → `keywords.json` 배열에 블록 하나 더 추가

## 7. 한계 / 참고

- 네이버 **검색 인덱싱 지연**: 갓 나온 기사는 검색에 뜨기까지 몇 분~수십 분 걸림 →
  알림도 그만큼 늦을 수 있음(대부분 다음 실행에 들어옴).
- API는 **본문 전체가 아니라 요약 조각(description)**만 준다. 그래서 사진기사 감지는
  요약의 문법("~고 있다") + 제목 태그로 판단한다.
- 언론사 이름은 원문 주소 도메인으로 추정(`news_bot.py`의 `OUTLET_NAMES`).
- `DAILY_CALL_CAP`(기본 10000)로 하루 호출을 무료 한도(25,000) 아래로 제한한다.
- **등판일지(pitch_log.py)** 는 KBO 공식 페이지를 파싱한다. KBO는 robots.txt에서
  자동 수집을 금지하므로, 사용은 개인 판단에 맡긴다(뉴스봇은 정식 API라 무관).
