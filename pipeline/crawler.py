"""RSS 爬蟲。重試/間隔/單一來源失敗不中斷。每來源最多 30 篇；last_fetched 為 NULL 時取 10。"""
import time
import requests
import feedparser

RETRYABLE = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
RETRY_DELAY = 5
REQUEST_INTERVAL = 2
UA = {"User-Agent": "ai-technique-archive/1.0 (+https://github.com/BROKEN0730/ai-technique-archive)"}


def _fetch(url):
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=UA, timeout=30)
        except requests.RequestException as e:
            print(f"  [warn] request error {url}: {e}")
            return None
        if resp.status_code == 200:
            return resp.content
        if resp.status_code in (404, 403):
            print(f"  [skip] {resp.status_code} {url}")
            return None
        if resp.status_code in RETRYABLE and attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
            continue
        print(f"  [skip] status {resp.status_code} {url}")
        return None
    return None


def crawl_source(source):
    """回傳 list[dict]：url,title,raw_content,source_id,language。"""
    raw = _fetch(source["url"])
    time.sleep(REQUEST_INTERVAL)
    if not raw:
        return []
    feed = feedparser.parse(raw)
    limit = 30 if source.get("last_fetched") else 10
    out = []
    for e in feed.entries[:limit]:
        url = e.get("link")
        title = e.get("title")
        if not url or not title:
            continue
        body = e.get("summary", "") or ""
        if e.get("content"):
            body = e["content"][0].get("value", body)
        out.append({
            "url": url,
            "title": title.strip(),
            "raw_content": _strip_html(body)[:2000],   # raw_content 最多 2000 字元
            "source_id": source["id"],
            "language": source.get("language", "en"),
        })
    return out


def _strip_html(html):
    import re
    return re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ").strip()
