#!/usr/bin/env python3
import json
import os
import time
import sys
import urllib.request
import urllib.error

API_URL = "https://api.tavily.com/search"
MAX_ATTEMPTS = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def main() -> int:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        print(json.dumps({"error": "TAVILY_API_KEY is not set"}, ensure_ascii=False))
        return 2
    query = " ".join(sys.argv[1:]).strip() or "latest major world news and market moves"
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "topic": "news",
        "max_results": 8,
        "include_answer": False,
        "include_raw_content": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "openclaw-social-agent/1.0"},
        method="POST",
    )
    body = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                body = res.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS or attempt == MAX_ATTEMPTS:
                print(
                    json.dumps(
                        {"error": f"Tavily request failed with HTTP {exc.code}", "attempt": attempt},
                        ensure_ascii=False,
                    )
                )
                return 1
        except urllib.error.URLError as exc:
            if attempt == MAX_ATTEMPTS:
                print(json.dumps({"error": f"Tavily request failed: {exc.reason}", "attempt": attempt}, ensure_ascii=False))
                return 1
        time.sleep(attempt)
    if body is None:
        print(json.dumps({"error": "Tavily request failed without a response"}, ensure_ascii=False))
        return 1
    sys.stdout.write(body)
    if not body.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
