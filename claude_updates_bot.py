"""
Claude Updates Bot
- RSS 피드에서 새 업데이트 감지
- Claude API로 핵심 요약 생성 (한국어)
- Slack 채널에 요약 메시지 전송
"""

import os
import json
import hashlib
import feedparser
import anthropic
import requests
from datetime import datetime, timezone
from pathlib import Path

# ─── 설정 ───────────────────────────────────────────────
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# 모니터링할 RSS 피드 목록
RSS_FEEDS = [
    {
        "name": "Claude Code Releases",
        "url": "https://github.com/anthropics/claude-code/releases.atom",
        "type": "release",
    },
    {
        "name": "Anthropic Engineering Blog",
        "url": "https://www.anthropic.com/engineering/rss.xml",
        "type": "blog",
    },
    # Anthropic 공식 블로그 RSS가 생기면 여기 추가
    # {
    #     "name": "Anthropic Blog",
    #     "url": "https://www.anthropic.com/blog/rss.xml",
    #     "type": "blog",
    # },
]

# 이미 처리한 항목 기록 파일
SEEN_FILE = Path(__file__).parent / "seen_entries.json"


def load_seen_entries() -> set:
    """이미 처리한 엔트리 ID 로드"""
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen_entries(seen: set):
    """처리한 엔트리 ID 저장"""
    SEEN_FILE.write_text(json.dumps(list(seen), ensure_ascii=False, indent=2))


def fetch_new_entries(feed_config: dict, seen: set) -> list:
    """RSS 피드에서 새로운 엔트리만 가져오기"""
    feed = feedparser.parse(feed_config["url"])
    new_entries = []

    for entry in feed.entries[:5]:  # 최근 5개만 확인
        entry_id = hashlib.md5(
            (entry.get("id", "") or entry.get("link", "")).encode()
        ).hexdigest()

        if entry_id not in seen:
            new_entries.append(
                {
                    "id": entry_id,
                    "title": entry.get("title", "제목 없음"),
                    "link": entry.get("link", ""),
                    "content": entry.get("summary", "")
                    or entry.get("content", [{}])[0].get("value", ""),
                    "published": entry.get("published", ""),
                    "feed_name": feed_config["name"],
                    "feed_type": feed_config["type"],
                }
            )

    return new_entries


def summarize_with_claude(entry: dict) -> str:
    """Claude API로 업데이트 내용 한국어 요약"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # 콘텐츠가 너무 길면 잘라내기
    content = entry["content"][:8000]

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": f"""다음은 "{entry['feed_name']}"의 새로운 업데이트입니다.
핵심 내용을 한국어로 간결하게 요약해주세요.

## 규칙
- 주요 변경사항/새 기능을 bullet point로 정리
- 기술적으로 중요한 포인트 위주
- 개발자가 알아야 할 breaking change가 있으면 강조
- 전체 3~8줄 이내로 요약
- Slack mrkdwn 포맷 사용 (*bold*, _italic_, `code`)

## 제목
{entry['title']}

## 내용
{content}
""",
            }
        ],
    )

    return message.content[0].text


def post_to_slack(entry: dict, summary: str):
    """Slack Webhook으로 요약 메시지 전송"""
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🔔 {entry['feed_name']}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*<{entry['link']}|{entry['title']}>*",
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": summary,
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"📅 {entry['published']} | 🤖 Claude가 요약함",
                }
            ],
        },
    ]

    payload = {"blocks": blocks, "unfurl_links": False}

    response = requests.post(
        SLACK_WEBHOOK_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
    )

    if response.status_code != 200:
        print(f"[ERROR] Slack 전송 실패: {response.status_code} - {response.text}")
    else:
        print(f"[OK] Slack 전송 완료: {entry['title']}")


def run():
    """메인 실행 함수"""
    if not SLACK_WEBHOOK_URL:
        print("[ERROR] SLACK_WEBHOOK_URL 환경변수를 설정해주세요.")
        return
    if not ANTHROPIC_API_KEY:
        print("[ERROR] ANTHROPIC_API_KEY 환경변수를 설정해주세요.")
        return

    seen = load_seen_entries()
    total_new = 0

    for feed_config in RSS_FEEDS:
        print(f"\n[INFO] 피드 확인 중: {feed_config['name']}")
        try:
            new_entries = fetch_new_entries(feed_config, seen)
        except Exception as e:
            print(f"[WARN] 피드 파싱 실패 ({feed_config['url']}): {e}")
            continue

        if not new_entries:
            print(f"  → 새로운 항목 없음")
            continue

        for entry in new_entries:
            print(f"  → 새 항목 발견: {entry['title']}")
            try:
                summary = summarize_with_claude(entry)
                post_to_slack(entry, summary)
                seen.add(entry["id"])
                total_new += 1
            except Exception as e:
                print(f"  [ERROR] 처리 실패: {e}")

    save_seen_entries(seen)
    print(f"\n[DONE] 총 {total_new}개 새 업데이트 처리 완료")


if __name__ == "__main__":
    run()
