"""매주 해외 IT 뉴스 RSS를 모아 한국어 제목·요약을 붙여 노션 데이터베이스에 업로드한다.

필요 환경변수:
  NOTION_TOKEN        노션 내부 통합(Integration) 토큰
  NOTION_DATABASE_ID  업로드할 데이터베이스 ID
  ANTHROPIC_API_KEY   Claude API 키 (없으면 번역·요약 없이 원문 제목으로 올린다.
                      있으면 요약이 비어 있는 기존 기사도 한국어 제목·요약으로 채운다)

선택 환경변수 (노션 DB 속성 이름, 기본값은 괄호 안. 제목 속성은 자동으로 찾고,
없는 속성은 자동으로 만든다):
  NOTION_PROP_URL      (URL)     URL 속성
  NOTION_PROP_SOURCE   (Source)  select 속성
  NOTION_PROP_DATE     (Date)    date 속성
  NOTION_PROP_ORIGINAL (원제목)  원문 제목 텍스트 속성
  NOTION_PROP_SUMMARY  (요약)    한국어 요약 텍스트 속성
  DAYS_BACK            (7)       며칠 전 기사까지 가져올지
  MAX_PER_SOURCE       (10)      출처별 최대 기사 수
  CLAUDE_MODEL         (claude-opus-5)  번역·요약에 쓸 모델
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser

import anthropic

FEEDS = {
    "TechCrunch": "https://techcrunch.com/feed/",
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/index",
    "Hacker News": "https://news.ycombinator.com/rss",
    "Wired": "https://www.wired.com/feed/rss",
    "MIT Technology Review": "https://www.technologyreview.com/feed/",
    "GeekNews": "https://news.hada.io/rss/news",
}

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
ATOM = "{http://www.w3.org/2005/Atom}"
RSS_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
USER_AGENT = "Mozilla/5.0 (compatible; it-news-notion/1.0)"

PROP_URL = os.environ.get("NOTION_PROP_URL", "URL")
PROP_SOURCE = os.environ.get("NOTION_PROP_SOURCE", "Source")
PROP_DATE = os.environ.get("NOTION_PROP_DATE", "Date")
PROP_ORIGINAL = os.environ.get("NOTION_PROP_ORIGINAL", "원제목")
PROP_SUMMARY = os.environ.get("NOTION_PROP_SUMMARY", "요약")
DAYS_BACK = int(os.environ.get("DAYS_BACK", "7"))
MAX_PER_SOURCE = int(os.environ.get("MAX_PER_SOURCE", "10"))
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5")

# 피드 본문이 이보다 짧으면(예: Hacker News) 기사 페이지를 직접 읽어 요약에 쓴다
MIN_CONTENT_CHARS = 300
# 요약용으로 Claude에 보내는 기사 본문 최대 길이 (긴 기사도 요약에는 앞부분이면 충분)
MAX_CONTENT_CHARS = 12000

SUMMARY_PROMPT = """다음 IT 뉴스 기사의 제목을 자연스러운 한국어로 번역하고, 본문을 한국어로 3~4문장 요약해 주세요.
- 회사명·제품명·고유명사는 원문 표기를 유지해도 됩니다.
- 제목이 이미 한국어면 그대로 두세요.
- 본문이 비어 있거나 부족하면 제목에서 알 수 있는 내용만 한두 문장으로 쓰고, 추측으로 내용을 지어내지 마세요.

<source>{source}</source>
<title>{title}</title>
<content>
{content}
</content>"""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title_ko": {"type": "string"},
        "summary_ko": {"type": "string"},
    },
    "required": ["title_ko", "summary_ko"],
    "additionalProperties": False,
}


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


class _TextExtractor(HTMLParser):
    """HTML에서 script/style 등을 뺀 본문 텍스트만 모은다."""

    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html):
    parser = _TextExtractor()
    parser.feed(unescape(html or ""))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def fetch_feed(source, url):
    root = ET.fromstring(http(url))
    items = []
    for it in root.iter("item"):  # RSS 2.0
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "url": (it.findtext("link") or "").strip(),
            "date": parse_date(it.findtext("pubDate")),
            "content": it.findtext(RSS_CONTENT) or it.findtext("description") or "",
        })
    for entry in root.iter(f"{ATOM}entry"):  # Atom
        link = entry.find(f"{ATOM}link[@rel='alternate']")
        if link is None:
            link = entry.find(f"{ATOM}link")
        items.append({
            "title": (entry.findtext(f"{ATOM}title") or "").strip(),
            "url": link.get("href", "").strip() if link is not None else "",
            "date": parse_date(entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated")),
            "content": entry.findtext(f"{ATOM}content") or entry.findtext(f"{ATOM}summary") or "",
        })
    for item in items:
        item["source"] = source
        item["content"] = html_to_text(item["content"])
    return [i for i in items if i["title"] and i["url"]]


def article_text(item):
    """요약에 쓸 본문. 피드 본문이 짧으면 기사 페이지를 읽어 온다."""
    text = item["content"]
    if len(text) < MIN_CONTENT_CHARS:
        try:
            page = html_to_text(http(item["url"]).decode("utf-8", errors="replace"))
            if len(page) > len(text):
                text = page
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            print(f"  기사 페이지 읽기 실패 ({item['url']}): {e}")
    return text[:MAX_CONTENT_CHARS]


def summarize(client, item):
    """Claude로 한국어 제목과 요약을 만든다. 실패하면 None."""
    prompt = SUMMARY_PROMPT.format(source=item["source"], title=item["title"], content=article_text(item))
    try:
        response = client.beta.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA},
            },
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.RateLimitError as e:
        print(f"  Claude 요청 한도 초과 ({item['title']}): {e.message}")
        return None
    except anthropic.APIStatusError as e:
        print(f"  Claude API 오류 {e.status_code} ({item['title']}): {e.message}")
        return None
    except anthropic.APIConnectionError as e:
        print(f"  Claude 연결 실패 ({item['title']}): {e}")
        return None

    if response.stop_reason != "end_turn":
        print(f"  Claude 응답 중단 ({response.stop_reason}): {item['title']}")
        return None
    text = next((b.text for b in response.content if b.type == "text"), None)
    return json.loads(text) if text else None


def notion_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def prepare_database(token, db_id):
    """DB 속성을 확인해 실제 제목 속성 이름을 찾고, 없는 속성은 만든다."""
    db = json.loads(http(f"{NOTION_API}/databases/{db_id}", headers=notion_headers(token)))
    props = db.get("properties", {})
    print("노션 DB 속성: " + ", ".join(f"{name}({p['type']})" for name, p in props.items()))

    title = next((name for name, p in props.items() if p["type"] == "title"), None)
    if title is None:
        sys.exit("노션 DB에 제목(title) 속성이 없습니다.")

    wanted = {
        PROP_URL: "url",
        PROP_SOURCE: "select",
        PROP_DATE: "date",
        PROP_ORIGINAL: "rich_text",
        PROP_SUMMARY: "rich_text",
    }
    missing = {}
    for name, typ in wanted.items():
        if name not in props:
            missing[name] = {typ: {}}
        elif props[name]["type"] != typ:
            sys.exit(
                f"노션 DB의 '{name}' 속성 유형이 '{props[name]['type']}'입니다. "
                f"'{typ}' 유형이어야 합니다. 노션에서 이 속성 이름을 바꾸거나 삭제하면 "
                f"다음 실행 때 올바른 유형으로 자동 생성됩니다."
            )
    if missing:
        body = {"properties": missing}
        http(f"{NOTION_API}/databases/{db_id}", "PATCH", notion_headers(token), body)
        print("노션 DB에 속성 추가: " + ", ".join(missing))
    return title


def already_uploaded(token, db_id, url):
    body = {"filter": {"property": PROP_URL, "url": {"equals": url}}, "page_size": 1}
    res = json.loads(http(f"{NOTION_API}/databases/{db_id}/query", "POST", notion_headers(token), body))
    return bool(res.get("results"))


def rich_text(text):
    # 노션 텍스트 블록 하나는 2000자까지
    return [{"text": {"content": text[:2000]}}] if text else []


def create_page(token, db_id, title_prop, item, ko):
    props = {
        title_prop: {"title": rich_text(ko["title_ko"] if ko else item["title"])},
        PROP_URL: {"url": item["url"]},
        PROP_SOURCE: {"select": {"name": item["source"]}},
        PROP_ORIGINAL: {"rich_text": rich_text(item["title"])},
        PROP_SUMMARY: {"rich_text": rich_text(ko["summary_ko"] if ko else "")},
    }
    if item["date"]:
        props[PROP_DATE] = {"date": {"start": item["date"].isoformat()}}
    body = {"parent": {"database_id": db_id}, "properties": props}
    http(f"{NOTION_API}/pages", "POST", notion_headers(token), body)


def plain_text(prop):
    return "".join(t.get("plain_text", "") for t in prop.get("title") or prop.get("rich_text") or [])


def pages_without_summary(token, db_id):
    """요약이 비어 있는 페이지(예전에 올렸거나 번역에 실패한 기사)를 모두 가져온다."""
    body = {"filter": {"property": PROP_SUMMARY, "rich_text": {"is_empty": True}}, "page_size": 100}
    while True:
        res = json.loads(http(f"{NOTION_API}/databases/{db_id}/query", "POST", notion_headers(token), body))
        yield from res.get("results", [])
        if not res.get("has_more"):
            break
        body["start_cursor"] = res["next_cursor"]


def backfill(token, db_id, title_prop, client):
    """이미 올라간 기사 중 요약이 없는 것에 한국어 제목과 요약을 채운다."""
    done = failed = 0
    for page in pages_without_summary(token, db_id):
        props = page["properties"]
        # 원제목이 있으면 그걸, 없으면(번역 전 기사) 현재 제목을 원문으로 본다
        original = plain_text(props.get(PROP_ORIGINAL, {})) or plain_text(props[title_prop])
        url = (props.get(PROP_URL) or {}).get("url")
        source = ((props.get(PROP_SOURCE) or {}).get("select") or {}).get("name", "")
        if not original or not url:
            continue
        ko = summarize(client, {"source": source, "title": original, "url": url, "content": ""})
        if ko is None:
            failed += 1
            continue
        update = {
            title_prop: {"title": rich_text(ko["title_ko"])},
            PROP_ORIGINAL: {"rich_text": rich_text(original)},
            PROP_SUMMARY: {"rich_text": rich_text(ko["summary_ko"])},
        }
        try:
            http(f"{NOTION_API}/pages/{page['id']}", "PATCH", notion_headers(token), {"properties": update})
            done += 1
        except urllib.error.HTTPError as e:
            print(f"  노션 수정 실패: {original} ({e.code} {e.read().decode(errors='replace')})")
            failed += 1
    print(f"기존 기사 번역·요약: {done}건 완료, {failed}건 실패")


def main():
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not token or not db_id:
        sys.exit("NOTION_TOKEN, NOTION_DATABASE_ID 환경변수가 필요합니다.")

    client = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        client = anthropic.Anthropic()
    else:
        print("ANTHROPIC_API_KEY가 없어 번역·요약 없이 원문 제목으로 업로드합니다.")

    try:
        title_prop = prepare_database(token, db_id)
    except urllib.error.HTTPError as e:
        sys.exit(f"노션 DB 확인 실패 ({e.code} {e.read().decode(errors='replace')}) - "
                 "DB ID와 통합 연결(••• → 연결)을 확인하세요.")

    if client:
        backfill(token, db_id, title_prop, client)

    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
    uploaded = skipped = failed = untranslated = 0

    for source, url in FEEDS.items():
        try:
            items = fetch_feed(source, url)
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as e:
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
                ko = summarize(client, item) if client else None
                if client and ko is None:
                    untranslated += 1
                create_page(token, db_id, title_prop, item, ko)
                uploaded += 1
            except urllib.error.HTTPError as e:
                print(f"  노션 업로드 실패: {item['title']} ({e.code} {e.read().decode(errors='replace')})")
                failed += 1

    print(f"완료: 업로드 {uploaded} (번역 실패로 원문 제목 {untranslated}), "
          f"중복 건너뜀 {skipped}, 실패 {failed}")
    if uploaded == 0 and failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
