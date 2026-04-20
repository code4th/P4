from __future__ import annotations

import argparse
import json


AGENT_NAME = "P2自己改善エージェント"
STREAM_STYLE = "plain"
OPERATOR_GUIDANCE = [
    "観測しやすいこと",
    "失敗理由を残すこと",
]


def describe_agent() -> dict[str, object]:
    return {
        "agent_name": AGENT_NAME,
        "stream_style": STREAM_STYLE,
        "operator_guidance": list(OPERATOR_GUIDANCE),
    }


def render_operator_message() -> str:
    return "P2 は自己改善ループを実行中です。"


def self_check() -> int:
    payload = describe_agent()
    if not payload["agent_name"]:
        return 1
    if len(payload["operator_guidance"]) < 2:
        return 1
    if "自己改善" not in render_operator_message():
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="P2 self improvement demo agent")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--describe", action="store_true")
    args = parser.parse_args()
    if args.check:
        return self_check()
    if args.describe:
        print(json.dumps(describe_agent(), ensure_ascii=False, indent=2))
        return 0
    print(render_operator_message())
    print(json.dumps(describe_agent(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
