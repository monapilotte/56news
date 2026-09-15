"""
최준용 등판일지 -> 디스코드

KBO 공식 일자별(Daily) 기록을 읽어, 새로 등판한 경기가 생기면
디스코드로 그날 투구 기록을 보내줍니다. (등판일지)

- API 키 필요 없음 (KBO 공식 페이지 파싱)
- 새 등판이 없으면 조용. 첫 실행은 도배 방지로 기존 기록만 저장.

설정(.env / 환경변수):
  DISCORD_WEBHOOK_URL         알림 보낼 웹훅 (필수)
  DISCORD_WEBHOOK_URL_PITCH   (선택) 등판일지 전용 웹훅. 없으면 위 웹훅 사용.
  PLAYER_ID                   KBO 선수 ID (기본 50556 = 최준용)
  PLAYER_NAME                 표시 이름 (기본 최준용)
"""

import html
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


WEBHOOK = (
    os.getenv("DISCORD_WEBHOOK_URL_PITCH", "").strip()
    or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
)
PLAYER_ID = os.getenv("PLAYER_ID", "50556").strip()
PLAYER_NAME = os.getenv("PLAYER_NAME", "최준용").strip()

STATE_FILE = Path(__file__).with_name("pitch_seen.json")
KBO_DAILY = (
    "https://www.koreabaseball.com/Record/Player/PitcherDetail/Daily.aspx?playerId="
    + PLAYER_ID
)
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_state() -> list:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log(f"상태 파일 읽기 실패({e}). 새로 시작.")
    return []


def save_state(seen: list) -> None:
    try:
        STATE_FILE.write_text(
            json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        log(f"상태 저장 실패: {e}")


def fetch_games() -> list:
    """KBO 일자별 페이지에서 등판 기록 목록을 파싱."""
    try:
        resp = requests.get(KBO_DAILY, headers=HTTP_HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        log(f"KBO 요청 실패: {e}")
        return []

    year = datetime.now().year
    games = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", resp.text, re.S):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        ]
        # 컬럼: 날짜 상대 구분 결과 ERA1 TBF IP H HR BB HBP SO R ER ERA2
        if len(cells) < 15 or not re.match(r"\d{1,2}\.\d{1,2}", cells[0]):
            continue
        (date, opp, typ, res, _era1, tbf, ip, h, hr, bb, hbp, so, r, er, era2) = cells[:15]
        games.append(
            {
                "key": f"{year}-{date}-{opp}-{tbf}-{ip}",
                "date": date,
                "opp": opp,
                "typ": typ,
                "res": res,
                "ip": ip,
                "h": h,
                "bb": bb,
                "so": so,
                "r": r,
                "er": er,
                "era": era2,
            }
        )
    return games


def format_message(g: dict) -> tuple:
    res = f" {g['res']}" if g["res"] else ""
    line = (
        f"⚾ **{PLAYER_NAME}** {g['date']} vs {g['opp']} · {g['typ']}{res} · "
        f"{g['ip']}이닝 {g['r']}실점({g['er']}자책) {g['so']}K {g['bb']}BB · 시즌 ERA {g['era']}"
    )
    embed = {
        "title": f"⚾ {PLAYER_NAME} 등판 · {g['date']} vs {g['opp']}",
        "color": 0x041E42,  # 롯데 남색
        "fields": [
            {"name": "구분", "value": g["typ"] or "-", "inline": True},
            {"name": "결과", "value": g["res"] or "-", "inline": True},
            {"name": "이닝", "value": g["ip"], "inline": True},
            {"name": "실점(자책)", "value": f"{g['r']}({g['er']})", "inline": True},
            {"name": "탈삼진", "value": g["so"], "inline": True},
            {"name": "볼넷", "value": g["bb"], "inline": True},
            {"name": "피안타", "value": g["h"], "inline": True},
            {"name": "시즌 ERA", "value": g["era"], "inline": True},
        ],
        "footer": {"text": "KBO 공식 기록"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return line, embed


def send(g: dict) -> bool:
    line, embed = format_message(g)
    payload = {"content": line[:2000], "embeds": [embed]}
    try:
        resp = requests.post(WEBHOOK, json=payload, timeout=15)
        if resp.status_code == 429:
            retry = resp.json().get("retry_after", 1)
            time.sleep(float(retry) + 0.5)
            resp = requests.post(WEBHOOK, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log(f"디스코드 전송 실패: {e}")
        return False


def main() -> None:
    if not WEBHOOK:
        log("DISCORD_WEBHOOK_URL 이 없습니다.")
        raise SystemExit(1)

    games = fetch_games()
    if not games:
        log("등판 기록을 가져오지 못했습니다.")
        return

    seen = set(load_state())
    first_run = len(seen) == 0

    new_games = [g for g in games if g["key"] not in seen]

    if first_run:
        # 첫 실행: 기존 등판은 알림 없이 기록만 (도배 방지)
        seen.update(g["key"] for g in games)
        save_state(sorted(seen))
        log(f"첫 실행 — 기존 등판 {len(games)}건 기록만 하고 종료.")
        return

    sent = 0
    for g in new_games:  # KBO는 오래된→최신 순이라 그대로 전송
        if send(g):
            seen.add(g["key"])
            sent += 1
            time.sleep(1)

    save_state(sorted(seen))
    log(f"새 등판 {sent}건 전송 완료.")


if __name__ == "__main__":
    main()
