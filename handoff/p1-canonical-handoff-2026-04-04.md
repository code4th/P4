# P1 Canonical Handoff

Date: 2026-04-04

## 1. 最上位目的

このプロジェクトの主目的は AI フレームワークを作ることではない。

主目的は、LLM が以下を通じて長期的に自律成長できる中核ループを作ること。

- 観測
- 知識化
- 批評
- 提案
- 評価
- 統治
- 自己改変

現段階では、知識を増やすことよりも、知識の扱い方と運用ルールを改善できる制度を先に作る。

## 2. 固定方針

以下は採用済みであり、ここからブレないこと。

- OpenClaw は暫定実行基盤として使う
- ただし OpenClaw にロックインしない
- 中核ロジックは OpenClaw の外に置く
- OpenClaw は control plane として扱う
- P1 本体は OpenClaw に埋め込まない
- ローカル LLM は補助脳として使う
- クラウド LLM は高品質判断系として使う
- 最初に自己改変させる対象は知識ではなく運用ルール
- 変更は比較・監査・巻き戻し可能でなければならない
- コア完成の定義には、低リスクな自己改善実験を自分で回し、結果を次の更新に反映できることを含める
- したがって「候補を出せる」だけでは未完成であり、「安全な小実験を自律実行できる」段階までを完成条件に含める

以下は現時点で非採用。

- 初手で独自フレームワーク全面実装
- 初手で OpenClaw 本体大改造
- 初手で本体 LLM の重み更新
- 初手で完全自律の自由改変
- OpenClaw 内部のエージェントに別エージェントを内部生成させる運用

## 3. P1 の定義

P1 は OpenClaw の中の人格ではない。

P1 は OpenClaw を利用する独立個体であり、以下を満たす。

- 独立した会話インターフェースを持つ
- OpenClaw を通じて動くが、本体は外部コア
- ローカル LLM 補助脳を使える
- 学び候補抽出、知識状態管理、批評ログ、運用ルール変更提案ができる
- 将来的に自律成長の中核個体になる

## 4. OpenClaw との責務分離

OpenClaw が持つ責務:

- 入出力
- ツール実行
- OS 操作
- 実行時の堅牢化
- transport と presentation

P1 外部コアが持つ責務:

- 知識状態
- policy / critic / proposer / evaluator / governor
- cross-track judgment
- approval gate の前段
- 研究結果や運用結果の圧縮

禁止事項:

- OpenClaw 側に独自 policy engine を育てない
- OpenClaw 側で P1 判断を再実装しない
- `keeper_adapter` に判断ロジックを持ち込まない

## 5. 実装済みの現在地

2026-04-04 時点で、以下を追加済み。

### 5.1 `p1-core/`

外部コアの最小ワークスペースを追加。

主な役割:

- P1 本体の外部化
- ローカル worker の保持
- bootstrap と runbook の保持
- report 出力の保持

重要ファイル:

- [p1-core/README.md](/Users/satojunichi/Documents/openclaw/p1-core/README.md)
- [p1-core/docs/architecture.md](/Users/satojunichi/Documents/openclaw/p1-core/docs/architecture.md)

### 5.2 ローカル LLM worker

Ollama を前提にした JSON HTTP worker を追加。

endpoint:

- `/summarize`
- `/classify`
- `/draft_lessons`
- `/health`

重要ファイル:

- [p1-core/p1_core/worker/ollama_worker.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/worker/ollama_worker.py)
- [p1-core/p1_core/worker/service.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/worker/service.py)
- [p1-core/p1_core/worker/ollama_client.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/worker/ollama_client.py)

固定仕様:

- 入出力は JSON
- エラー応答を返す
- リクエストとレスポンスを JSONL でログ保存する
- 不確実性を潰さずに候補抽出する

### 5.3 P1 bootstrap

OpenClaw 内部生成に依存せず、外部から P1 workspace を生成する scaffold を追加。

生成対象:

- `profile.json`
- `config.json`
- `prompt.md`
- `runbook.md`
- `state/reports/`
- `state/knowledge/`
- `state/policies/`
- `state/proposals/`
- `state/archive/`
- `logs/`

重要ファイル:

- [p1-core/p1_core/bootstrap/bootstrap_p1.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/bootstrap/bootstrap_p1.py)

### 5.4 外部コア骨格

以下の最小骨格を追加。

- event log
- knowledge store
- policy engine
- critic
- proposer
- evaluator
- governor

重要ファイル:

- [p1-core/p1_core/core/knowledge_store.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/knowledge_store.py)
- [p1-core/p1_core/core/policy_engine.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/policy_engine.py)
- [p1-core/p1_core/core/critic.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/critic.py)
- [p1-core/p1_core/core/proposer.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/proposer.py)
- [p1-core/p1_core/core/evaluator.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/evaluator.py)
- [p1-core/p1_core/core/governor.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/governor.py)

### 5.5 OpenClaw adapter 接続面

既存 `keeper_adapter` は薄い bridge として維持する。

今回、`p1-core` 側から以下を出力する report writer を追加し、現在の read contract に合わせた。

- `state/reports/daily/*-glance.json`
- `state/reports/daily/*-daily.json`
- `state/health.json`

重要ファイル:

- [p1-core/p1_core/reporting/report_writer.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/reporting/report_writer.py)
- [p1-core/p1_core/reporting/write_example_reports.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/reporting/write_example_reports.py)
- [keeper_adapter/bridge.py](/Users/satojunichi/Documents/openclaw/keeper_adapter/bridge.py)
- [handoff/p1-openclaw-bridge-spec-2026-03-30.md](/Users/satojunichi/Documents/openclaw/handoff/p1-openclaw-bridge-spec-2026-03-30.md)

### 5.6 最小成長ループ

1 本の観測テキストから以下を実行する最小ループを追加。

- `draft_lessons`
- `classify`
- `summarize`
- candidate knowledge 永続化
- knowledge state transition
- critic / evaluator / governor による `active / deferred / retired` 判定
- proposal snapshot 永続化
- proposal snapshot 比較
- governance review
- cloud-side evaluation request queue
- glance / daily / health report 出力
- event log 追記

重要ファイル:

- [p1-core/p1_core/pipeline/growth_loop.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/pipeline/growth_loop.py)

出力先:

- `state/knowledge/knowledge.jsonl`
- `state/events/event-log.jsonl`
- `state/proposals/latest-proposals.json`
- `state/proposals/snapshots/*.json`
- `state/reports/daily/*-glance.json`
- `state/reports/daily/*-daily.json`
- `state/health.json`

## 6. 現在の directory 境界

中核として新設した領域:

- `/Users/satojunichi/Documents/openclaw/p1-core`

既存の OpenClaw 側薄い bridge:

- `/Users/satojunichi/Documents/openclaw/keeper_adapter`

既存 research handoff:

- `/Users/satojunichi/Documents/openclaw/handoff`

この分離は維持すること。

## 7. 再現手順

### 7.1 テスト

```bash
cd /Users/satojunichi/Documents/openclaw/p1-core
python3 -m unittest discover -s tests
```

### 7.2 P1 workspace 生成

```bash
cd /Users/satojunichi/Documents/openclaw/p1-core
python3 -m p1_core.bootstrap.bootstrap_p1 --root /Users/satojunichi/.openclaw/workspace/systems/p1
```

### 7.3 ローカル worker 起動

```bash
cd /Users/satojunichi/Documents/openclaw/p1-core
python3 -m p1_core.worker.ollama_worker --port 8765
```

### 7.4 health 確認

```bash
curl http://127.0.0.1:8765/health
```

### 7.5 初期 report 生成

```bash
cd /Users/satojunichi/Documents/openclaw/p1-core
python3 -m p1_core.reporting.write_example_reports --root /Users/satojunichi/.openclaw/workspace/systems/p1
```

### 7.6 最小成長ループ実行

```bash
cd /Users/satojunichi/Documents/openclaw/p1-core
python3 -m p1_core.pipeline.growth_loop --root /Users/satojunichi/.openclaw/workspace/systems/p1 --input-text "example observation"
```

### 7.7 OpenClaw 側 bridge から読む

```bash
cd /Users/satojunichi/Documents/openclaw
python3 -m keeper_adapter.cli status
python3 -m keeper_adapter.cli approvals
python3 -m keeper_adapter.cli report --kind daily
```

### 7.8 外部コア operator CLI から扱う

```bash
cd /Users/satojunichi/Documents/openclaw/p1-core
python3 -m p1_core.cli status
python3 -m p1_core.cli approvals
python3 -m p1_core.cli state
python3 -m p1_core.cli ingest --input-text "example observation"
python3 -m p1_core.cli rollback --target proposals --snapshot-id 2026-04-04-proposals
python3 -m p1_core.cli rollback --target policies --snapshot-id baseline-policy
```

## 8. 検証済み事項

この turn で確認済み:

- `p1-core` 単体テスト成功
- bootstrap scaffolder による workspace 生成成功
- example report 生成成功
- growth loop による knowledge / proposal / report / event 生成成功
- knowledge state の `deferred` 遷移成功
- proposal snapshot の履歴化と比較差分生成成功
- governance review の snapshot / daily report 反映成功
- counterexample または high-risk 提案が自動で `deferred` に落ちることを確認
- counterexample がない新規候補が `active` に進むことを確認
- previous snapshot と重複する候補が `retired` に進むことを確認
- approval-gated proposal が `state/cloud_evaluation/requests/` に request を積むことを確認
- cloud review `approve` / `reject` が state transition に反映されることを確認
- approval 済み proposal が `state/policies/latest-policy.json` と `state/policies/snapshots/` を更新することを確認
- policy rollback 後に `latest-policy.json` が復元 snapshot を指すことを確認
- governance profile によって low-risk autonomy を停止できることを確認
- daily report に `Short-Horizon Governance` と `Long-Horizon Governance` が出力されることを確認
- unified operator CLI から status / approvals / state を読めることを確認
- end-to-end lifecycle test で ingest -> policy apply -> CLI visibility -> rollback を確認
- end-to-end lifecycle test で proposal rollback まで含めて operator surface の整合を確認
- 実 Ollama `qwen3:4b-instruct` で `p1_core.cli ingest` と worker `/summarize` が通ることを確認
- low-risk autonomous proposal が `state/experiments/actions/*.json` に bounded action note を書くことを確認
- prior experiment outcome が次回 rerun を `deferred` に戻すことを確認
- rollback 実行後に `status` が `rollback_applied` を返すことを確認
- `OPENCLAW_P1_ROOT=/tmp/p1-core-smoke` を使った `keeper_adapter` 読み取り成功
- `OPENCLAW_P1_ROOT=/tmp/p1-core-loop-smoke` を使った growth loop 出力の `keeper_adapter` 読み取り成功
- `OPENCLAW_P1_ROOT=/private/tmp/p1-core-loop-smoke-2` を使った state transition 後出力の `keeper_adapter` 読み取り成功
- `OPENCLAW_P1_ROOT=/private/tmp/p1-core-rollback-smoke-2` を使った rollback 後の `keeper_adapter status` 読み取り成功

確認できた contract:

- `status` は `glance` を読める
- `approvals` は `tuningSummary.approvalPending` を読める
- `report --kind daily` は `daily` を読める

## 9. ロールバック原則

絶対に守ること:

- ログを消さない
- 反例を消さない
- 保留を消さない
- 変更は比較可能な形で残す
- OpenClaw 側判断ロジックを増やさない

実務ロールバック:

1. worker を止める
2. `python3 -m p1_core.pipeline.growth_loop --root <p1-root> --rollback-snapshot-id <snapshot-id>` で proposal snapshot を復元する
3. `state/proposals/latest-proposals.json` が復元 snapshot を指すことを確認する
4. `keeper_adapter.cli status` と `report --kind daily` が `rollback_applied` を返すことを確認する
5. 失敗成果物を `state/archive/` に退避する
6. OpenClaw 側の bridge は判断系を増やさず据え置く

## 10. 次フェーズの優先順

次にやるべきことは以下の順。

1. experiment feedback を長期統治ルール側へ接続する

## 11. コア完成条件

このプロジェクトでいう「P1 コア完成」は、以下を満たした時点とする。

- 観測、知識化、批評、提案、評価、統治、自己改変候補管理が外部コアで閉じる
- `raw / candidate / deferred / active / retired` を管理できる
- proposal と knowledge state の比較、監査、巻き戻しができる
- 高リスク変更は承認待ちに回せる
- 低リスク改善は自律的に実行できる
- 小さな実験を自分で回し、その結果を次の更新に反映できる

したがって、現状は「最小外部コア骨格は成立しているが、コア完成にはまだ至っていない」と位置づける。

## 12. 今はやらないこと

以下は今やらない。

- OpenClaw 本体への深い埋め込み
- OpenClaw 内部での P1 実体生成
- 自動承認つきの自己改変
- 重み更新前提の自己成長
- OpenClaw 側への policy engine 拡張

## 13. この資料の位置づけ

このファイルは 2026-04-04 時点の canonical handoff である。
ただし、現在の唯一の master document は [P1_MASTER.md](/Users/satojunichi/Documents/openclaw/handoff/P1_MASTER.md) とする。

P1 の外部コア化について迷った場合は、まず master document を優先すること。
個別の補助資料は以下。

- Master document: [P1_MASTER.md](/Users/satojunichi/Documents/openclaw/handoff/P1_MASTER.md)
- Manager 原文固定版: [handoff/p1-manager-handoff-source-2026-04-04.md](/Users/satojunichi/Documents/openclaw/handoff/p1-manager-handoff-source-2026-04-04.md)
- bridge 契約: [handoff/p1-openclaw-bridge-spec-2026-03-30.md](/Users/satojunichi/Documents/openclaw/handoff/p1-openclaw-bridge-spec-2026-03-30.md)
- operating rule: [handoff/p1-openclaw-operating-rule-2026-03-29.md](/Users/satojunichi/Documents/openclaw/handoff/p1-openclaw-operating-rule-2026-03-29.md)
- research 側 handoff: [handoff/p1-keeper-handoff-2026-03-29.md](/Users/satojunichi/Documents/openclaw/handoff/p1-keeper-handoff-2026-03-29.md)
- 今回の実装メモ: [handoff/p1-external-core-plan-2026-04-04.md](/Users/satojunichi/Documents/openclaw/handoff/p1-external-core-plan-2026-04-04.md)
