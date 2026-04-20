# P2 Core

P2 Core は、bootstrapped workspace 上で自己改善ループを回す最小カーネルです。

現行の v0 は、自己改善を継続するための最小構成に絞っています。

- 現在版を候補版へ分離する
- モデルに 1 ファイル単位の全文置換を提案させる
- `unittest` で候補版を検証する
- 昇格または却下を決める
- 自己診断、履歴、詳細ログ、ダッシュボードで状態を追跡する
- 必要なら bootstrap 直後へ初期状態リセットする

Quick start:

```bash
cd /Users/satojunichi/Documents/openclaw/p2-core
python3 -m unittest discover -s tests
python3 -m p2_core.cli bootstrap --root /tmp/p2-demo --force
python3 -m p2_core.cli status --root /tmp/p2-demo
python3 -m p2_core.cli run-loop --root /tmp/p2-demo --model qwen3-coder:latest
python3 -m p2_core.cli show-history --root /tmp/p2-demo
python3 -m p2_core.cli show-attempt --root /tmp/p2-demo --candidate-id c0001
python3 -m p2_core.cli reset --root /tmp/p2-demo --mode initial
python3 -m p2_core.cli dashboard --root /tmp/p2-demo --host 127.0.0.1 --port 8897
python3 -m p2_core.cli watchdog --root /tmp/p2-demo --model qwen3-coder:latest  # watchdog 起動
```
