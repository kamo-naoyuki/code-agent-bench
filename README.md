# Agent に命令して実行する方法

## このチャットから頼む

この作業場所を開いたチャットで、次のように依頼してください。

### 小さな問題を1回解かせる

```text
Copilot の claude-haiku-4.5 で smoke-fix-calc を1回実行して。
利用枠の消費を承認します。
終了したら、採点結果・所要時間・使用量・エラーの有無を教えて。
```

これで、接続済みの Copilot を起動し、テスト用コンテナで問題を解かせ、採点まで進める依頼になります。
モデル名は利用したいものに変更してください。

### 保存済みの結果だけを見る

```text
jobs/ に保存されている直近の Copilot の結果をまとめて。
新しい agent の実行や AI の呼び出しはしないで。
```

### 実行前に内容だけ確認する

```text
Copilot で smoke-fix-calc を1回実行したい。
まだ実行せず、使うモデル、制限時間、利用枠を消費する処理を説明して。
```

### 複数の agent を比較する

```text
Copilot・Claude Code・Codex に同じ問題を1回ずつ解かせたい。
まず接続の実装状況を確認して、未対応のものがあれば報告して。
まだ実行や利用枠の消費はしないで。
```

現在、実際の問題で動作確認済みなのは Copilot です。
Claude Code / Codex は接続の実装が必要なので、まだ3つをまとめて実行できる状態ではありません。

## 命令するときに指定するもの

- **誰に**: Copilot / Claude Code / Codex。
- **どのモデルで**: 対象 CLI で使えるモデル名。
- **何を**: 問題名やタスクのパス。
- **何回**: まずは1回。
- **実行してよいか**: 実行するなら利用枠の消費を承認。説明だけなら「まだ実行しない」と指定。

「接続部分を実装して」ではなく、**「この agent で、この問題を解かせて」**と依頼すれば、実装ではなく実行の指示になります。

## ターミナルから直接実行する

チャットを介さずに、接続済みの Copilot に命令する場合は以下を実行します。
**Copilot の利用枠を消費します。**

```bash
cd /data/store5/2026/kamo/code_agent_test
source bench/env.sh

JOB_NAME="copilot-smoke-$(date +%Y%m%d-%H%M%S)"

PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" harbor run \
  -p tasks/dev/smoke-fix-calc \
  -a bench.agents.copilot_host:CopilotHostAgent \
  -m claude-haiku-4.5 \
  -k 1 -n 1 \
  --job-name "$JOB_NAME"

python3 bench/phase0/summarize_job.py "jobs/$JOB_NAME"
```

| 指定 | 意味 |
|---|---|
| `-p` | 解かせる問題のフォルダ |
| `-a` | 起動する agent の接続実装 |
| `-m` | 使うモデル |
| `-k 1` | 同じ問題を1回実行 |
| `-n 1` | 同時実行は1件 |
| `--job-name` | 結果の保存名 |

agent に渡す具体的な問題文は、タスク内の [instruction.md](tasks/dev/smoke-fix-calc/instruction.md) にあります。
`-p` でタスクを選ぶと、Harbor がその問題文を agent に渡します。
既存問題で成績を比較するときは、問題文や隠しテストを変更しないでください。

結果は `jobs/<保存名>/` に残ります。
この smoke タスクでは、集計の `rew` が `1.0` なら採点成功です。
エラーや警告がある場合は、採点だけでなく原因も確認してください。

## API を直接使う入口

CLI の既存ログインではなく、**API キーでモデルを呼び出す経路**もあります。
入口は [run_step4.sh](bench/phase0/run_step4.sh) です。
この経路では Copilot / Claude Code / Codex ではなく、`mini-swe-agent` が API を使って問題を解きます。
CLI のサブスクリプション利用枠とは別に、API 提供元の課金が発生します。
モックでの配線確認は済んでいますが、実 API での動作確認はまだです。

### このチャットから頼む

まず、次のように命令してください。

```text
CLI ではなく API 直接利用の経路で実行したい。
mini-swe-agent を使い、モデルAは「利用するAPIモデル名」、
モデルBは「比較するAPIモデル名」、問題数1、各1回、同時実行1件。
本番分の予算目安は合計1ドル。
まず dry-run で設定と価格登録を確認して。
事前確認分の追加費用と予算制限の注意点も教えて。
まだ実 API は呼ばないで。API キーは表示しないで。
```

内容を確認して実行する場合は、続けて指示します。

```text
確認した設定で API 実行して。
事前確認分を含めた API 課金を承認します。
終了したら、採点結果・所要時間・API の推定費用・エラーの有無を教えて。
```

API キーをチャットに貼らないでください。
利用する提供元に応じて、実行元の環境に `OPENAI_API_KEY` や `ANTHROPIC_API_KEY` を設定しておきます。
この経路では agent がコンテナ内から API に接続するため、認証情報がコンテナに渡ります。
認証情報をホストに残す Copilot 経路とは異なります。

### ターミナルから実行する

以下のモデル名の `<...>` 部分は、提供元で利用可能な実際の ID に置き換えてください。
別の提供元を使う場合は、`openai/` / `anthropic/` の接頭辞も変更してください。

```bash
cd /data/store5/2026/kamo/code_agent_test
source bench/env.sh

export MODEL_A='openai/<利用可能なモデルID>'
export MODEL_B='anthropic/<利用可能なモデルID>'
export BUDGET_USD=1

N_TASKS=1 K=1 N_CONCURRENT=1 DRY_RUN=1 \
  bash bench/phase0/run_step4.sh
```

`DRY_RUN=1` は API を呼びません。価格が未登録のモデルは、この時点で拒否されます。
確認後、同じシェルで実行を承認する場合だけ次を使います。

```bash
N_TASKS=1 K=1 N_CONCURRENT=1 CONFIRM_SPEND=yes \
  bash bench/phase0/run_step4.sh
```

**予算についての注意:**

- `BUDGET_USD` は本番試行に配分する費用制限です。請求額の厳密な上限ではありません。
- 既定で、本番前に小さな動作確認を各モデル1回実行します。その費用制限は各0.25ドルで、本番予算の外です。
- 上の1ドルの例は、本番1ドル＋事前確認2回で計0.50ドル、合計1.50ドル分の制限設定になります。ただし、API 応答後の費用判定や再試行などで超過する可能性があります。
- 価格表と実際の請求額が異なる場合もあります。提供元側でも利用上限を設定できる場合は併用してください。
- 価格未登録を許可する `ALLOW_UNPRICED=yes` は、費用制限が効かなくなるため通常は使わないでください。

本番の結果は `jobs/p0-step4-<日時>-a/` と `jobs/p0-step4-<日時>-b/`、
集計は `reports/phase0-step4-<日時>.txt` に保存されます。
終了コードだけで成功と判断せず、集計の採点・エラーと、提供元側の利用明細を確認してください。

## 1コマンド実行と、中断後の再開（Phase 2）

```bash
# 正解済みデータで1問実行。AI は呼ばない
bash bench/run --config configs/dev-oracle.json \
  --task smoke-fix-calc --job-name my-check

# Ctrl+C などで中断した同じ実行を再開
bash bench/run --resume my-check
```

問題のコピー、設定・版情報、結果は `jobs/my-check/` にまとめて保存されます。
すでに完了した試行は再実行しません。
Copilot を使う設定では、利用枠消費の承認として `--confirm-spend` が必要です。
詳細と有料実行の例は [実行・再開の手順書](bench/PIPELINE.md) を参照してください。

## 評価問題の動作だけを確認する（AI の利用枠を消費しない）

Phase 1 の初期10問は、[dev-v1](suites/dev-v1.json) に登録しています。
agent に解かせる前に、用意した正解と「何もしない場合」で採点が正しいか確認できます。

```text
初期10問の品質チェックを実行して。
AI は呼ばず、oracle と nop だけで正解・未修正・別解を確認して。
各問の正解を別コンテナで10回ずつ検証して、結果をまとめて。
```

ターミナルから実行する場合:

```bash
# 静的チェックだけ
bash bench/validate-tasks --static-only

# 10問 ×（未修正1回＋別解1回＋正解10回）＝120試行
bash bench/validate-tasks --oracle-runs 10 --concurrency 3
```

AI の API / CLI は呼びませんが、Docker と計算資源、イメージ取得の通信は使います。
採点テストが通っても、問題文とテストの整合性の独立レビューは別途必要です。
規約・検証内容は [タスクの手順書](tasks/README.md) にあります。

## 関連資料

- [全体計画](PLAN.md)
- [これまでの検証結果](reports/phase0.md)
- [Phase 1 の10問・自動検証結果](reports/phase1.md)
- [Phase 2 の実行・再開検証結果](reports/phase2.md)
- [接続実装の共通ルール](bench/agents/IMPLEMENTATION_RULES.md)
