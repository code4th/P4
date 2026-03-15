#!/usr/bin/env python3
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET

FEEDS = [
    ("NHK", "https://www3.nhk.or.jp/rss/news/cat0.xml"),
    ("Reuters World", "https://feeds.reuters.com/Reuters/worldNews"),
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews"),
]


def fetch_xml(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "openclaw-social-agent/1.0"})
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read()


def parse_rss(source: str, raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    items = []
    for item in root.findall(".//item")[:8]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if title or link:
            items.append({"source": source, "title": title, "link": link, "published": pub})
    return items


def main() -> int:
    out = []
    for source, url in FEEDS:
        try:
            raw = fetch_xml(url)
            out.extend(parse_rss(source, raw))
        except Exception as exc:
            out.append({"source": source, "error": str(exc), "feed": url})
    json.dump({"items": out}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
