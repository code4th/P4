# P1 Main-Thread Catch-Up

Date: 2026-04-04
Audience: Main thread / manager thread
Purpose: AI-agent fork thread で進んだ内容を、短時間で把握するための要約

## 0. 先に固定すべき目的

メインスレッドが先に固定すべきなのは手段ではなく目的である。

この P1 で本当に作りたいものは以下。

- OpenClaw 上でメインとは別に見える P1 agent
- LLM を主回路として考え会話する P1
- その背後に、外部コアとして記憶、統治、監査、比較、rollback を持つ構造

したがって、`p1-core CLI`、`bin/p1`、`ollama worker` は現時点の足場ではあっても、最終的な前面 UX そのものではない。

## 1. 結論

このフォークスレッドで、P1 の「最小外部コア」は完成扱いにしてよい段階まで進んだ。

完成の意味は、以下 4 要素のうち、制度層として必要な部分が最小形でそろったこと。

- 運用ルールの自己改変
- bounded な外界アクション
- 外界観測や会話面を後ろで支えられる制度層

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
- operator CLI による統合操作
- end-to-end lifecycle test

重要:

- `chat_agent` / `conversation_store`
- `world_store`
- `p1_core.cli chat`
- `p1_core.cli observe`
- `p1_core.cli action`

は、この main branch にはまだ存在しない。
以前の別フォークや構想メモの文脈が混ざりやすいため、main thread では「制度層は完成、前面 interface は未実装」と理解すること。

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

### 5.2 外界アクション

- bounded autonomous experiment は action note を実際に作成
- 再実行は prior experiment outcome を見て抑制可能

主ファイル:

- [experiment_runner.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/experiment_runner.py)
- [evaluator.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/evaluator.py)

### 5.3 会話と外界観測の位置づけ

会話面と world observation 面は、main branch の `p1-core` にはまだ実装されていない。

ただし、次に main thread が作るべき `OpenClaw` 上の separate P1 agent interface は、この制度層を背後基盤として使う前提でよい。

## 6. operator が知るべき実行面

主要コマンド:

- `python3 -m p1_core.cli --root /tmp/p1 ingest --input-text "..."`
- `python3 -m p1_core.cli --root /tmp/p1 status`
- `python3 -m p1_core.cli --root /tmp/p1 approvals`
- `python3 -m p1_core.cli --root /tmp/p1 report --kind daily`
- `python3 -m p1_core.cli --root /tmp/p1 rollback --policy-snapshot-id <id>`

補正:

- `chat / observe / action` は main branch にはまだない
- 次の main-thread 実装で、OpenClaw-facing P1 interface 側に作るべき

OpenClaw bridge 側は従来どおり薄い read-only bridge のままでよい。

## 7. 検証状況

このフォークスレッドで確認済み:

- unit test 通過
- lifecycle end-to-end test 通過
- proposal rollback / policy rollback 通過
- real Ollama backend を使った ingest / worker health / summarize 確認
- governance feedback が後続 decision を変える acceptance 確認

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
- `de588c1` OpenClaw transport for P1 front door
- `5108402` dedicated OpenClaw-facing P1 agent surface scaffold
- `c4b656b` dedicated OpenClaw agent slot bootstrap

## 10. main thread への伝え方

メインスレッドへは、次の一文で十分に要点が通る。

「P1 の外部コアは成立したが、日常的に使う前面はまだ仮置きである。次は OpenClaw 上でメインとは別の P1 agent interface を正面入口として実装し、その背後に今ある external core を接続する。」
