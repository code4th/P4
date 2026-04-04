# P1 Main-Thread Catch-Up

Date: 2026-04-04
Audience: Main thread / manager thread
Purpose: AI-agent fork thread で進んだ内容を、短時間で把握するための要約

## 1. 結論

このフォークスレッドで、P1 の「最小外部コア」は完成扱いにしてよい段階まで進んだ。

完成の意味は、以下 4 要素が最小形でそろったこと。

- 運用ルールの自己改変
- 外界への観測インターフェース
- bounded な外界アクション
- 会話インターフェース

これは「何でも自由にできる完全自律」ではない。
ただし、`P1_MASTER.md` が要求していた「OpenClaw 外部にある、比較可能・監査可能・巻き戻し可能な最小成長ループ」は成立している。

重要な補正:

このフォークで作ったものは「外部コアとしての成立」であって、最終的に欲しい「日常的に使う P1 の前面インターフェース」ではない。
メインスレッドは、ここから先を `OpenClaw` 上の別 agent interface として実装する前提で進めるべきである。

## 2. このフォークで追加・確定したこと

### 2.1 すでに成立した外部コア

`p1-core/` で以下が通る。

- `observation -> summarize/classify/draft_lessons`
- `candidate/deferred/active/retired` の知識状態管理
- proposal snapshot 保存と比較
- governance review
- cloud approval の取り込み
- policy apply / rollback
- bounded autonomous experiment
- experiment feedback の再評価反映
- governance feedback の蓄積
- OpenClaw bridge 向け report 出力

### 2.2 新たに main thread が把握すべき追加点

フォークスレッドで、以下が増えた。

- `policy_store` による versioned policy state
- `governance_store` による長短期統治の状態保存
- `experiment_runner` による bounded autonomous action 実行
- `chat_agent` / `conversation_store` による会話面
- `world_store` による world observation / world action request 面
- operator CLI による統合操作
- end-to-end lifecycle test

## 3. コア完成の現在定義

今の P1 コアは、以下を満たす。

- OpenClaw を control plane として使う
- P1 本体を OpenClaw に埋め込まない
- 中核ロジックを `p1-core/` に閉じる
- 低リスク変更だけ bounded に自律実行する
- 高リスク変更は approval 前提にする
- 変更を snapshot 化し、比較と rollback を可能にする
- conversation / world / policy / governance を append-first で記録する

ただし、ここでいう完成は「制度層の最小完成」である。
会話・思考・ツール実行の主回路を `OpenClaw` 側の separate agent interface に乗せることは、次の主要工程として残っている。

## 4. main thread が最初に見るべきファイル

読む順番はこの順でよい。

1. [P1_MASTER.md](/Users/satojunichi/Documents/openclaw/handoff/P1_MASTER.md)
2. [p1-main-thread-catchup-2026-04-04.md](/Users/satojunichi/Documents/openclaw/handoff/p1-main-thread-catchup-2026-04-04.md)
3. [p1-canonical-handoff-2026-04-04.md](/Users/satojunichi/Documents/openclaw/handoff/p1-canonical-handoff-2026-04-04.md)
4. [p1-core/runbooks/p1-bootstrap-runbook.md](/Users/satojunichi/Documents/openclaw/p1-core/runbooks/p1-bootstrap-runbook.md)

## 5. 実装済みの main capabilities

### 5.1 自己改変

- proposal 生成
- evaluation / governance review
- policy apply
- policy rollback

主ファイル:

- [growth_loop.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/pipeline/growth_loop.py)
- [policy_engine.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/policy_engine.py)
- [policy_store.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/policy_store.py)
- [governor.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/governor.py)

### 5.2 外界観測

- conversation 由来でも operator 由来でも world observation を追記可能
- `state/world/observations.jsonl` に保存

主ファイル:

- [world_store.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/world_store.py)
- [cli.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/cli.py)

### 5.3 外界アクション

- bounded action request を保存
- bounded autonomous experiment は action note を実際に作成
- 再実行は prior experiment outcome を見て抑制可能

主ファイル:

- [experiment_runner.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/experiment_runner.py)
- [world_store.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/world_store.py)
- [evaluator.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/evaluator.py)

### 5.4 会話

- `p1_core.cli chat` で会話可能
- transcript は `state/conversation/transcript.jsonl` に保存

主ファイル:

- [chat_agent.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/chat_agent.py)
- [conversation_store.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/conversation_store.py)
- [cli.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/cli.py)

## 6. operator が知るべき実行面

主要コマンド:

- `python3 -m p1_core.cli --root /tmp/p1 ingest --text "..."`
- `python3 -m p1_core.cli --root /tmp/p1 chat --message "..."`
- `python3 -m p1_core.cli --root /tmp/p1 observe --text "..."`
- `python3 -m p1_core.cli --root /tmp/p1 action --kind note --payload "..."`
- `python3 -m p1_core.cli --root /tmp/p1 status`
- `python3 -m p1_core.cli --root /tmp/p1 rollback --policy-snapshot-id <id>`

OpenClaw bridge 側は従来どおり薄い read-only bridge のままでよい。

## 7. 検証状況

このフォークスレッドで確認済み:

- unit test 通過
- lifecycle end-to-end test 通過
- proposal rollback / policy rollback 通過
- real Ollama backend を使った ingest / worker health / summarize 確認
- conversation / world interfaces の state 出力確認

## 8. main thread から見た残作業

外部コア自体の必須未実装穴は、いったん大きくは残っていない。

ただし、プロダクトとしての P1 には大きな残作業がある。

残るのは次フェーズの品質向上:

- OpenClaw 上の separate P1 agent interface
- P1 の主会話ループを OpenClaw 側から使う接続
- OpenClaw 実行結果を external core へ還流する接続
- governance threshold の調整
- bounded external action の種類拡張
- operator review flow の改善
- 将来の専用 UI / 常設会話面の整備

## 9. git 上の目印

このフォークスレッドで main thread が把握すべき主なコミット:

- `49cd178` versioned policy state
- `25ad9d2` governance profile integration
- `c5af152` unified operator CLI
- `36efb6b` end-to-end lifecycle test
- `cc846e6` real Ollama verification
- `185a06f` proposal rollback acceptance 拡張
- `a1cbcbf` experiment governance feedback loop
- `f1a9148` conversation and world interfaces

## 10. main thread への伝え方

メインスレッドへは、次の一文で十分に要点が通る。

「P1 の外部コアは成立したが、日常的に使う前面はまだ仮置きである。次は OpenClaw 上でメインとは別の P1 agent interface を正面入口として実装し、その背後に今ある external core を接続する。」
