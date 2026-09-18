"""
뉴스 -> 디스코드 알림 봇

키워드가 포함된 새 뉴스 기사가 올라오면
디스코드 채널(웹훅)로 제목 + 링크를 보내줍니다.

뉴스 소스는 .env 의 NEWS_SOURCE 로 선택합니다:
  - naver  : NAVER API HUB 검색 API (하루 25,000건 무료, 인증키 필요, 최신순 정확)
  - google : 구글 뉴스 RSS (API 키 불필요)

필요한 것:
  1. 디스코드 채널 웹훅 URL          -> 채널 설정 > 연동 > 웹훅
  2. (naver 사용 시) NAVER API HUB 인증키 -> Ncloud 콘솔 > NAVER API HUB

설정은 같은 폴더의 .env 파일에서 읽습니다. (.env.example 참고)
"""

import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv가 없어도 OS 환경변수로 동작하도록 허용
    pass


# ---------------------------------------------------------------------------
# 설정 로드
# ---------------------------------------------------------------------------
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

# 뉴스 소스: "naver"(NAVER API HUB) 또는 "google"(구글 뉴스 RSS)
NEWS_SOURCE = os.getenv("NEWS_SOURCE", "google").strip().lower()

# NAVER API HUB 인증키
NCP_API_KEY_ID = os.getenv("NCP_API_KEY_ID", "").strip()
NCP_API_KEY = os.getenv("NCP_API_KEY", "").strip()

# 감시할 키워드들: 콤마로 구분 (예: "삼성전자, 금리, 인공지능")
KEYWORDS = [k.strip() for k in os.getenv("KEYWORDS", "").split(",") if k.strip()]

# 검사 주기(초). 기본 300초 = 5분
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))

# 검색어당 한 번에 확인할 최대 기사 수 (경기날 도배 대비 넉넉히. 네이버 최대 100)
DISPLAY = int(os.getenv("DISPLAY", "50"))

# 하루 API 호출 상한 (네이버 무료 한도 25,000 훨씬 아래로 안전망). 0이면 무제한.
DAILY_CALL_CAP = int(os.getenv("DAILY_CALL_CAP", "10000"))

# 1회만 실행하고 종료 (GitHub Actions 같은 스케줄러용). --once 인자로도 켜짐.
RUN_ONCE = os.getenv("RUN_ONCE", "").strip().lower() in ("1", "true", "yes") or (
    "--once" in sys.argv
)

# 이미 본 기사 기록을 저장할 파일
STATE_FILE = Path(__file__).with_name("seen.json")

# 고급 설정 파일 (있으면 KEYWORDS 대신 이걸 사용)
KEYWORDS_FILE = Path(__file__).with_name("keywords.json")

# 링크 하나당 얼마나 오래 기억할지 (개수 상한) — 너무 커지지 않게 정리
MAX_REMEMBER_PER_KEYWORD = 500

# 구글 뉴스 RSS (한국어/한국 지역)
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"

# NAVER API HUB 뉴스 검색 엔드포인트
NAVER_HUB_NEWS = "https://naverapihub.apigw.ntruss.com/search/v1/news"

# 하루 호출 카운터 (자정에 리셋)
_call_day = None
_call_count = 0

# 서버가 봇 트래픽을 막지 않도록 브라우저 UA를 흉내
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_targets() -> list:
    """감시 대상 목록을 만든다.

    keywords.json 이 있으면 그걸(필터 포함 고급 설정) 사용하고,
    없으면 .env 의 KEYWORDS(단순 목록)를 사용한다.

    각 대상 형식:
      {
        "query":       검색할 문자열 (하나) 또는 문자열 리스트 (여러 개 합침),
        "label":       디스코드에 표시할 이름,
        "include_any": [이 중 하나라도 제목/요약에 있어야 통과] (비면 통과),
        "exclude_any": [이 중 하나라도 있으면 버림],
      }
    """
    if KEYWORDS_FILE.exists():
        try:
            raw = json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log(f"keywords.json 을 읽지 못했습니다: {e}")
            sys.exit(1)
        targets = []
        for item in raw:
            # query 는 문자열 하나 또는 리스트 모두 허용
            q = item.get("query") or item.get("queries") or item.get("label") or ""
            queries = [q] if isinstance(q, str) else list(q)
            queries = [s.strip() for s in queries if s and s.strip()]
            if not queries:
                continue
            targets.append(
                {
                    "queries": queries,
                    "label": (item.get("label") or queries[0]).strip(),
                    "include_any": [w.lower() for w in item.get("include_any", [])],
                    "exclude_any": [w.lower() for w in item.get("exclude_any", [])],
                    # 제목에 이 단어가 있으면 통과 (언급만 된 잡기사 거르기)
                    "title_any": [w.lower() for w in item.get("title_any", [])],
                    # 제목엔 없어도 요약에 이름+이 동작단어가 있으면 통과 (경기내용 기사 살리기)
                    "body_any": [w.lower() for w in item.get("body_any", [])],
                    # 기사 주소에 이 조각이 있으면 제외 (예: /basketball/ = 농구 카테고리)
                    "link_exclude": [w.lower() for w in item.get("link_exclude", [])],
                }
            )
        return targets

    # .env 의 단순 키워드
    return [
        {"queries": [k], "label": k, "include_any": [], "exclude_any": [],
         "title_any": [], "body_any": [], "link_exclude": []}
        for k in KEYWORDS
    ]


def validate_config(targets: list) -> None:
    missing = []
    if not DISCORD_WEBHOOK_URL:
        missing.append("DISCORD_WEBHOOK_URL")
    if not targets:
        missing.append("KEYWORDS (또는 keywords.json)")
    if NEWS_SOURCE == "naver":
        if not NCP_API_KEY_ID:
            missing.append("NCP_API_KEY_ID")
        if not NCP_API_KEY:
            missing.append("NCP_API_KEY")
    if missing:
        log("설정이 비어 있습니다: " + ", ".join(missing))
        log(".env 파일을 확인하세요. (.env.example 참고)")
        sys.exit(1)


# ---------------------------------------------------------------------------
# 상태(이미 본 기사) 저장/로드
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log(f"상태 파일을 읽지 못했습니다({e}). 새로 시작합니다.")
    return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        log(f"상태 파일 저장 실패: {e}")


# ---------------------------------------------------------------------------
# 구글 뉴스 검색 (RSS)
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    """HTML 태그와 엔티티 제거"""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# 주요 언론사 도메인 -> 한글 이름 (없으면 도메인 그대로 표시)
OUTLET_NAMES = {
    "chosun.com": "조선일보", "donga.com": "동아일보", "joongang.co.kr": "중앙일보",
    "hani.co.kr": "한겨레", "khan.co.kr": "경향신문", "yna.co.kr": "연합뉴스",
    "yonhapnewstv.co.kr": "연합뉴스TV", "news1.kr": "뉴스1", "newsis.com": "뉴시스",
    "mk.co.kr": "매일경제", "hankyung.com": "한국경제", "sedaily.com": "서울경제",
    "mt.co.kr": "머니투데이", "edaily.co.kr": "이데일리", "fnnews.com": "파이낸셜뉴스",
    "kbs.co.kr": "KBS", "imbc.com": "MBC", "sbs.co.kr": "SBS", "ytn.co.kr": "YTN",
    "jtbc.co.kr": "JTBC", "nocutnews.co.kr": "노컷뉴스", "hankookilbo.com": "한국일보",
    "seoul.co.kr": "서울신문", "kmib.co.kr": "국민일보", "segye.com": "세계일보",
    "osen.co.kr": "OSEN", "xportsnews.com": "엑스포츠뉴스", "sportschosun.com": "스포츠조선",
    "sportsseoul.com": "스포츠서울", "spotvnews.co.kr": "SPOTV뉴스", "mydaily.co.kr": "마이데일리",
    "sportalkorea.com": "스포탈코리아", "busan.com": "부산일보", "kookje.co.kr": "국제신문",
    "news.jtbc.co.kr": "JTBC", "biz.chosun.com": "조선비즈", "chosunbiz.com": "조선비즈",
    "stoo.com": "스포츠투데이", "isplus.com": "일간스포츠", "mhnse.com": "문화뉴스",
    "interfootball.co.kr": "인터풋볼", "sportsw.kr": "스포츠W", "sportstoday.co.kr": "스포츠투데이",
    "sportsq.co.kr": "스포츠Q", "gettyimagesbank.com": "게티이미지", "newspim.com": "뉴스핌",
    "asiae.co.kr": "아시아경제", "heraldcorp.com": "헤럴드경제", "dailian.co.kr": "데일리안",
    "moneys.co.kr": "머니S", "wowtv.co.kr": "한국경제TV", "tvchosun.com": "TV조선",
}


def source_from_url(url: str) -> str:
    """기사 URL의 도메인으로 언론사 이름 추정"""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    if not host:
        return ""
    host = re.sub(r"^(www|news|sports|m|it|biz|star)\.", "", host)
    for domain, name in OUTLET_NAMES.items():
        if host == domain or host.endswith("." + domain) or domain.endswith("." + host):
            return name
    # 매핑에 없으면 대표 도메인만 (예: example.co.kr)
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-3:]) if parts[-2] in ("co", "or", "go", "ne") else ".".join(parts[-2:])
    return host


def pubdate_key(article: dict):
    """pubDate 문자열을 정렬용 datetime 으로 변환 (실패 시 아주 과거)"""
    try:
        return parsedate_to_datetime(article["pubDate"])
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def under_daily_cap() -> bool:
    """오늘 호출 수가 상한 미만이면 True (그리고 카운트 증가). 자정에 리셋."""
    global _call_day, _call_count
    today = datetime.now().date()
    if _call_day != today:
        _call_day = today
        _call_count = 0
    if DAILY_CALL_CAP and _call_count >= DAILY_CALL_CAP:
        return False
    _call_count += 1
    return True


def fetch_news(query: str, label: str) -> list:
    """소스 설정에 따라 알맞은 검색 함수로 분기 (하루 상한 적용)"""
    if not under_daily_cap():
        log(f"[{label}] 하루 호출 상한({DAILY_CALL_CAP}) 도달 — 자정까지 대기.")
        return []
    if NEWS_SOURCE == "naver":
        return fetch_news_naver(query, label)
    return fetch_news_google(query, label)


def fetch_merged(queries: list, label: str) -> list:
    """여러 검색어 결과를 합치고 링크 기준으로 중복 제거"""
    merged = {}
    for q in queries:
        for a in fetch_news(q, label):
            merged.setdefault(a["id"], a)  # 먼저 나온 것 유지
        if len(queries) > 1:
            time.sleep(0.3)  # 서버 배려용 짧은 간격
    return list(merged.values())


def passes_filter(article: dict, include_any: list, exclude_any: list,
                  title_any: list = None, body_any: list = None,
                  link_exclude: list = None) -> bool:
    """관련성 필터.

    - exclude_any: 하나라도 있으면 버림 (농구·배우 등)
    - include_any: 있으면 그 중 하나는 제목/요약에 있어야 함
    - 관련성(주인공 여부):
        title_any 가 지정되면 →
          제목에 title_any 단어가 있으면 통과,
          없으면 body_any 가 지정된 경우에 한해
          "요약에 title_any 이름 + 투구동작(body_any)"이 같이 있으면 통과.
        (언급만 된 잡기사는 버리고, 경기내용 기사는 살림)
    """
    title = article["title"].lower()
    desc = article["description"].lower()
    text = title + " " + desc

    # 주소(카테고리) 기반 제외 — 예: /basketball/ 은 무조건 농구
    if link_exclude:
        link = (article.get("link", "") + " " + article.get("id", "")).lower()
        if any(pat in link for pat in link_exclude):
            return False

    if exclude_any and any(word in text for word in exclude_any):
        return False
    if include_any and not any(word in text for word in include_any):
        return False

    if title_any:
        if any(word in title for word in title_any):
            return True
        if body_any:
            name_in_desc = any(word in desc for word in title_any)
            action_in_desc = any(word in desc for word in body_any)
            return name_in_desc and action_in_desc
        return False
    return True


def fetch_news_naver(query: str, label: str) -> list:
    """NAVER API HUB 뉴스 검색"""
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NCP_API_KEY_ID,
        "X-NCP-APIGW-API-KEY": NCP_API_KEY,
    }
    params = {"query": query, "display": min(DISPLAY, 100), "sort": "date"}
    try:
        resp = requests.get(NAVER_HUB_NEWS, headers=headers, params=params, timeout=15)
        if resp.status_code == 429:
            log(f"[{label}] 네이버 호출 한도 초과(429).")
            return []
        resp.raise_for_status()
    except requests.RequestException as e:
        log(f"[{label}] 네이버 API 요청 실패: {e}")
        return []

    try:
        items = resp.json().get("items", [])
    except ValueError:
        log(f"[{label}] 네이버 응답 파싱 실패: {resp.text[:150]}")
        return []

    articles = []
    for item in items:
        key = item.get("link") or item.get("originallink", "")
        if not key:
            continue
        articles.append(
            {
                "id": key,
                "title": clean_text(item.get("title", "(제목 없음)")) or "(제목 없음)",
                "link": item.get("link") or item.get("originallink", ""),
                # 언론사 이름은 원문 주소 도메인으로 추정
                "source": source_from_url(item.get("originallink") or item.get("link", "")),
                "description": clean_text(item.get("description", "")),
                "pubDate": item.get("pubDate", ""),
            }
        )
    return articles


def fetch_news_google(query: str, label: str) -> list:
    url = GOOGLE_NEWS_RSS.format(query=quote(query))
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log(f"[{label}] 구글 뉴스 요청 실패: {e}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        log(f"[{label}] RSS 파싱 실패: {e}")
        return []

    articles = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        key = link or guid
        if not key:
            continue

        raw_title = clean_text(item.findtext("title") or "(제목 없음)")
        # 구글 뉴스 제목은 "제목 - 언론사" 형식 → 언론사 분리
        source = (item.findtext("source") or "").strip()
        title = raw_title
        if source and raw_title.endswith(f"- {source}"):
            title = raw_title[: -len(f"- {source}")].strip()
        elif " - " in raw_title:
            title, source = raw_title.rsplit(" - ", 1)
            title, source = title.strip(), source.strip()

        articles.append(
            {
                "id": key,
                "title": title or "(제목 없음)",
                "link": link or guid,
                "source": source,
                "description": clean_text(item.findtext("description") or ""),
                "pubDate": (item.findtext("pubDate") or "").strip(),
            }
        )
        if len(articles) >= DISPLAY:
            break
    return articles


# ---------------------------------------------------------------------------
# 디스코드 전송
# ---------------------------------------------------------------------------
def send_to_discord(keyword: str, article: dict) -> bool:
    source = article.get("source", "")
    title = article["title"]
    # 알림(푸시) 첫 줄에 바로 보이도록 본문에 [키워드] 제목 · 언론사 를 넣는다
    content = f"📰 {title}"
    if source:
        content += f" · {source}"

    desc = article["description"]
    if desc and desc[:20] in title:
        desc = ""
    embed = {
        "title": title[:256],
        "url": article["link"],
        "description": desc[:300],
        "color": 0x03C75A,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if source:
        embed["footer"] = {"text": source[:2048]}
    payload = {
        "content": content[:2000],
        "embeds": [embed],
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        # 429 = 레이트리밋
        if resp.status_code == 429:
            retry = resp.json().get("retry_after", 1)
            log(f"디스코드 레이트리밋. {retry}초 대기 후 재시도.")
            time.sleep(float(retry) + 0.5)
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log(f"디스코드 전송 실패: {e}")
        return False


# ---------------------------------------------------------------------------
# 한 번의 검사 사이클
# ---------------------------------------------------------------------------
def check_once(targets: list, state: dict, first_run_labels: set) -> None:
    for target in targets:
        label = target["label"]
        seen = set(state.get(label, []))

        fetched = fetch_merged(target["queries"], label)
        if not fetched:
            continue

        # include/exclude 필터로 원하는 기사만 남김
        articles = [
            a
            for a in fetched
            if passes_filter(
                a,
                target["include_any"],
                target["exclude_any"],
                target.get("title_any"),
                target.get("body_any"),
                target.get("link_exclude"),
            )
        ]
        if not articles:
            continue

        # 오래된 것부터 보내도록 발행시각 오름차순 정렬
        new_articles = [a for a in articles if a["id"] not in seen]
        new_articles.sort(key=pubdate_key)

        if label in first_run_labels:
            # 첫 실행: 기존 기사는 알림 없이 '본 것'으로만 기록 (도배 방지)
            for a in articles:
                seen.add(a["id"])
            log(f"[{label}] 첫 실행 — 기존 기사 {len(articles)}건 기록만 하고 넘어갑니다.")
        else:
            sent = 0
            for a in new_articles:
                if send_to_discord(label, a):
                    seen.add(a["id"])
                    sent += 1
                    time.sleep(1)  # 디스코드 배려용 짧은 간격
            if sent:
                log(f"[{label}] 새 기사 {sent}건 전송 완료.")

        # 메모리 상한 관리 (최신 위주로 남김)
        if len(seen) > MAX_REMEMBER_PER_KEYWORD:
            # 이번에 받은 최신 기사들을 우선 유지
            keep_ids = [a["id"] for a in articles]
            trimmed = set(keep_ids)
            for sid in seen:
                if len(trimmed) >= MAX_REMEMBER_PER_KEYWORD:
                    break
                trimmed.add(sid)
            seen = trimmed

        state[label] = list(seen)

    save_state(state)


# ---------------------------------------------------------------------------
# 메인 루프
# ---------------------------------------------------------------------------
def main() -> None:
    targets = load_targets()
    validate_config(targets)

    src_name = "NAVER API HUB" if NEWS_SOURCE == "naver" else "구글 뉴스 RSS"
    log(f"{src_name} -> 디스코드 봇 시작")
    src = "keywords.json" if KEYWORDS_FILE.exists() else ".env"
    log(f"설정 출처: {src}")
    log("감시 대상: " + ", ".join(t["label"] for t in targets))
    log(f"검사 주기: {CHECK_INTERVAL}초")

    state = load_state()
    # 상태에 아직 없는 대상 = 첫 실행 (도배 방지 대상)
    first_run_labels = {t["label"] for t in targets if t["label"] not in state}

    if RUN_ONCE:
        log("1회 실행 모드 (RUN_ONCE)")
        check_once(targets, state, first_run_labels)
        log("완료. 종료합니다.")
        return

    try:
        while True:
            check_once(targets, state, first_run_labels)
            first_run_labels = set()  # 첫 사이클 이후엔 정상 알림
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        log("종료합니다.")


if __name__ == "__main__":
    main()
