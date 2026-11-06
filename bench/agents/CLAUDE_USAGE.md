# claude-host の使い方

ホストにログイン済みの Claude Code CLI に、Harbor のテスト用コンテナで問題を解かせる外部型 agent です。
AI が使えるのは、コンテナ内でコマンドを実行する `mcp__sandbox__bash` だけです。

```
claude (ホスト) --MCP stdio--> sandbox_mcp.py --unix socket--> ClaudeHostAgent --environment.exec--> コンテナ
```

確認に使った CLI の版は 2.1.296 です。版を上げたら、自動テストを実行し直してください。

## 実行

実行すると Claude の利用枠を消費します。

```bash
source bench/env.sh
PYTHONPATH="$PWD" harbor run \
  -p tasks/dev/smoke-fix-calc \
  -a bench.agents.claude_host:ClaudeHostAgent \
  -m '<モデル名>' -n 1 --job-name claude-host-smoke
```

- `<モデル名>` は `claude --model` が受け付ける値です。省略するとエラーになります。
- 別のモデルへの自動切り替え（`--fallback-model`）は使いません。CLI が別のモデルを使ったと報告した時点で、`model_mismatch` として止めます。
- 別名（`opus` など）でも系統が違えば止めます。系統を判定できない値（`best` など）は確認できないので、実際のモデル名を記録するだけです。比較の計測では完全なモデル名を指定してください。
- 思考量は `--ak reasoning_effort=<low|medium|high|xhigh|max>` で指定します。省略すると CLI の既定値になり、その値は結果に記録されません。比較の計測では明示してください。

ログインの状態は `claude auth status` で確認できます。未ログインなら、この agent はタスクを始めずに `auth` で止まります。その場合は `claude auth login` を実行してください。

## 隔離の方法

| 対象 | 設定 |
|---|---|
| 組み込みツール（シェル、ファイル、Web、別 agent など） | `--tools ""` |
| 他の MCP サーバー | `--strict-mcp-config` |
| ユーザー設定（フック、プラグインなど） | `--setting-sources ""` |
| 指示ファイル、メモリ | `--setting-sources ""`、`CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`、`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` |
| スキル | `--disable-slash-commands` |
| 権限 | `--allowedTools mcp__sandbox__bash`、`--permission-mode dontAsk`（許可確認の無視は使わない） |
| 過去セッション | `--no-session-persistence`、毎回空の一時ディレクトリで実行 |
| 親セッションの設定 | `ANTHROPIC_*`、`CLAUDE_*`、`MCP_*` などの環境変数を除去。残すのは `CLAUDE_CONFIG_DIR` と `CLAUDE_CODE_OAUTH_TOKEN` だけ |

`--bare` は OAuth ログインを読まないため使えません。

実行時にも検査します。CLI の `init` イベントが示すツール、MCP サーバー、権限モードが想定と違えば、ただちに止めて `tool_policy_violation` にします。

存在しないツールを AI が呼んだだけなら、CLI がエラーを返して何も実行されないので、計測は有効のままです。回数を `n_unavailable_tool_attempts` に記録します。

## 結果の見方

ログは `jobs/<ジョブ名>/<試行名>/agent/` に保存されます。

| ファイル | 内容 |
|---|---|
| `claude-events.jsonl` | CLI の出力そのまま（`init` のツール一覧を含む） |
| `claude-stderr.txt` | CLI の標準エラー |
| `claude-run-config.json` | 実行コマンド。環境変数は名前だけで、値は保存しない |
| `claude-prompt.txt` | AI に渡した文章 |
| `claude-final-message.txt` | AI の最後の応答 |
| `sandbox-tool-calls.jsonl` | コンテナで実行したコマンド。キャンセルされて完了しなかったものは載らないので、`claude-events.jsonl` か `trajectory.json` を見る |
| `trajectory.json` | ATIF 形式の実行記録 |

失敗の原因は、`result.json` の `agent_result.metadata.failure_kind` で区別します。

| `failure_kind` | 意味 | 例外 |
|---|---|---|
| なし（`null`） | CLI が正常に終了した。問題を解けたかどうかは報酬で判断する | なし |
| `auth` | 未ログイン、認証失敗 | `AuthenticationError` |
| `permission_denied` | CLI がツール呼び出しを拒否した、または API が 403 を返した | `PermissionDeniedError` |
| `tool_policy_violation` | 隔離の検査に失敗した | `PermissionDeniedError` |
| `model_not_found` / `model_mismatch` | モデルが使えない、または別のモデルが使われた | `ModelNotFoundError` |
| `usage_limit` / `rate_limit` | サブスクリプションの利用上限 / 流量制限 | `ApiUsageLimitError` / `ApiRateLimitError` |
| `api_connection` / `api_server` | 接続失敗 / サーバー側のエラー | `ApiConnectionError` / `ApiInternalServerError` |
| `context_window` | 入力がコンテキストの上限を超えた | `ContextWindowExceededError` |
| `cli_startup` / `mcp_startup` | CLI / MCP サーバーの起動失敗 | `AgentStartupError` |
| `cli_error` / `malformed_output` | その他の異常終了 / `result` イベントのない出力 | `NonZeroAgentExitCodeError` |
| `timeout_or_cancelled` | Harbor の制限時間切れ、またはキャンセル | Harbor が決める |

## 使用量

| 項目 | 記録先 | 備考 |
|---|---|---|
| 入力トークン | `n_input_tokens` | キャッシュの読み書きを含む（Harbor の定義） |
| キャッシュ読み取り | `n_cache_tokens` | |
| キャッシュ書き込み | `metadata.cache_write_tokens` | |
| 出力トークン | `n_output_tokens` | |
| モデル別の内訳 | `model_usage`、`metadata.per_model_tokens` | |
| 実際の料金 | `cost_usd` は常に `null` | CLI から実際の請求額は取得できない |
| CLI の参考金額 | `metadata.cli_list_price_estimate_usd_not_charged` | CLI が報告した `total_cost_usd`。請求額として扱わない |
| サブスクリプションの利用率 | `claude-events.jsonl` の `rate_limit_event` | CLI が出力した値がそのまま残る。`result.json` には集計しない |

CLI が報告しなかった値は `null` のままで、0 では埋めません。

タイムアウトなどで `result` イベントがないと、メッセージごとの値を合計します。この場合は `metadata.usage_is_partial` が `true` になり、実行中だった要求のぶんは含まれません。

## テスト

モデルも実ログインも使いません。Harbor の Python には pytest がないので、`unittest` で実行します。

```bash
"$(dirname "$(readlink -f "$(command -v harbor)")")/python" -m unittest bench.agents.tests.test_claude_host -v
```

`RealCliIsolationTest` だけは実際の `claude` を動かします。偽の `HOME`、仮の API キー、ローカルの偽 API サーバーを使い、CLI がモデルに送る内容を検査します。

## 既知の制限

- **多重防御の一部は個別に証明していません。** テストから `--tools` または `--setting-sources` を外すと失敗しますが、`CLAUDE_CODE_DISABLE_CLAUDE_MDS` または `--disable-slash-commands` を1つ外しただけでは失敗しません。他の設定でも防がれるためです。
- **CLI は通常の記録ファイルを更新します。** 偽の `HOME` での実行では、`~/.claude.json` とそのバックアップ、`~/.claude/.last-cleanup`、`~/.cache/claude-cli-nodejs/` の MCP ログが作られました。設定内容（確認した範囲では `mcpServers`）は変わらず、会話記録も残りませんでした。
- **コンテナ内のコマンドが残る可能性があります。** 制限時間切れではブリッジが実行中の `environment.exec` をキャンセルし、Harbor は `docker compose exec` のクライアントを終了します。コンテナ内のプロセスまで止まるかは未確認です。
- **管理者設定（managed settings）は無効化できません。** 現在このホストに `/etc/claude-code` はありません。追加されて想定外のツールが増えた場合は、実行時の検査で止まります。
