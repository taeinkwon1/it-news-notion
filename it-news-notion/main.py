"""매주 해외 IT 뉴스 RSS를 모아 노션 데이터베이스에 업로드한다.

필요 환경변수:
  NOTION_TOKEN        노션 내부 통합(Integration) 토큰
  NOTION_DATABASE_ID  업로드할 데이터베이스 ID

선택 환경변수 (노션 DB 속성 이름, 기본값은 괄호 안):
  NOTION_PROP_TITLE (Name)  제목 속성
  NOTION_PROP_URL   (URL)   URL 속성
  NOTION_PROP_SOURCE(Source) select 속성
  NOTION_PROP_DATE  (Date)  date 속성
  DAYS_BACK         (7)     며칠 전 기사까지 가져올지
  MAX_PER_SOURCE    (10)    출처별 최대 기사 수
"""

import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

FEEDS = {
    "TechCrunch": "https://techcrunch.com/feed/",
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/index",
    "Hacker News": "https://news.ycombinator.com/rss",
}

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
ATOM = "{http://www.w3.org/2005/Atom}"
USER_AGENT = "Mozilla/5.0 (compatible; it-news-notion/1.0)"

PROP_TITLE = os.environ.get("NOTION_PROP_TITLE", "Name")
PROP_URL = os.environ.get("NOTION_PROP_URL", "URL")
PROP_SOURCE = os.environ.get("NOTION_PROP_SOURCE", "Source")
PROP_DATE = os.environ.get("NOTION_PROP_DATE", "Date")
DAYS_BACK = int(os.environ.get("DAYS_BACK", "7"))
MAX_PER_SOURCE = int(os.environ.get("MAX_PER_SOURCE", "10"))


def http(url, method="GET", headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_date(text):
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)  # RSS: RFC 822
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))  # Atom: ISO 8601
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fetch_feed(source, url):
    root = ET.fromstring(http(url))
    items = []
    for it in root.iter("item"):  # RSS 2.0
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "url": (it.findtext("link") or "").strip(),
            "date": parse_date(it.findtext("pubDate")),
        })
    for entry in root.iter(f"{ATOM}entry"):  # Atom
        link = entry.find(f"{ATOM}link[@rel='alternate']")
        if link is None:
            link = entry.find(f"{ATOM}link")
        items.append({
            "title": (entry.findtext(f"{ATOM}title") or "").strip(),
            "url": link.get("href", "").strip() if link is not None else "",
            "date": parse_date(entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated")),
        })
    for item in items:
        item["source"] = source
    return [i for i in items if i["title"] and i["url"]]


def notion_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def already_uploaded(token, db_id, url):
    body = {"filter": {"property": PROP_URL, "url": {"equals": url}}, "page_size": 1}
    res = json.loads(http(f"{NOTION_API}/databases/{db_id}/query", "POST", notion_headers(token), body))
    return bool(res.get("results"))


def create_page(token, db_id, item):
    props = {
        PROP_TITLE: {"title": [{"text": {"content": item["title"][:2000]}}]},
        PROP_URL: {"url": item["url"]},
        PROP_SOURCE: {"select": {"name": item["source"]}},
    }
    if item["date"]:
        props[PROP_DATE] = {"date": {"start": item["date"].isoformat()}}
    body = {"parent": {"database_id": db_id}, "properties": props}
    http(f"{NOTION_API}/pages", "POST", notion_headers(token), body)


def main():
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not token or not db_id:
        sys.exit("NOTION_TOKEN, NOTION_DATABASE_ID 환경변수가 필요합니다.")

    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
    uploaded = skipped = failed = 0

    for source, url in FEEDS.items():
        try:
            items = fetch_feed(source, url)
        except (urllib.error.URLError, ET.ParseError) as e:
            print(f"[{source}] 피드 수집 실패: {e}")
            failed += 1
            continue

        # 날짜가 없는 항목(예: Hacker News)은 수집 시점 기사로 간주
        recent = [i for i in items if i["date"] is None or i["date"] >= cutoff][:MAX_PER_SOURCE]
        print(f"[{source}] {len(recent)}건")

        for item in recent:
            try:
                if already_uploaded(token, db_id, item["url"]):
                    skipped += 1
                    continue
                create_page(token, db_id, item)
                uploaded += 1
            except urllib.error.HTTPError as e:
                print(f"  노션 업로드 실패: {item['title']} ({e.code} {e.read().decode(errors='replace')})")
                failed += 1

    print(f"완료: 업로드 {uploaded}, 중복 건너뜀 {skipped}, 실패 {failed}")
    if uploaded == 0 and failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
