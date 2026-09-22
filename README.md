# 뉴스 → 디스코드 알림 봇

특정 인물 관련 새 뉴스가 올라오면,
디스코드 채널로 **`📰 제목 · 언론사`** 알림을 보내주는 봇이다.
동명이인(농구선수·배우)과 "이름만 언급된 명단기사"를 걸러내고, 사진기사·인터뷰·경기기사만 골라준다.

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

## 3. 한계 / 참고

- 네이버 **검색 인덱싱 지연**: 갓 나온 기사는 검색에 뜨기까지 몇 분~수십 분 걸림 →
  알림도 그만큼 늦을 수 있음(대부분 다음 실행에 들어옴).
- API는 **본문 전체가 아니라 요약 조각(description)**만 준다. 그래서 사진기사 감지는
  요약의 문법("~고 있다") + 제목 태그로 판단한다.
- 언론사 이름은 원문 주소 도메인으로 추정(`news_bot.py`의 `OUTLET_NAMES`).
- `DAILY_CALL_CAP`(기본 10000)로 하루 호출을 무료 한도(25,000) 아래로 제한한다.
