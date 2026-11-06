# Codex に渡す実装依頼

以下を Codex の作業セッションに渡してください。

---

Harbor からホストの Codex CLI を呼び出す接続部分を実装してください。
最初に [共通ルール](./IMPLEMENTATION_RULES.md) を読み、必須条件と検証条件に従ってください。
目的は、既存のログインを使い、評価対象の AI にはテスト用コンテナだけを操作させることです。

## 担当ファイル

作成・変更してよいファイルは次の3つだけです。

- `bench/agents/codex_host.py`
- `bench/agents/tests/test_codex_host.py`
- `bench/agents/CODEX_USAGE.md`

Claude Code が別のファイルで並行作業します。共有ファイル、既存の Copilot 実装、計画書、タスクは変更しないでください。
追加ファイルや共通修正が必要なら、必要性を報告してください。

## 実装

- Harbor 外部型 agent `CodexHostAgent` を実装する。agent 名は `codex-host`。
- 現在の `codex --help` と関連サブコマンドのヘルプで、非対話実行、モデル選択、MCP 設定、ツール制限、ログ・使用量の取得方法を確認する。
- [Copilot 実装](./copilot_host.py) と [MCP サーバー](./sandbox_mcp.py) を参考・再利用する。
- ホストの既存ログインを利用する。ユーザーの Codex 設定を変更しない。
- MCP のコンテナ操作以外のツールを無効化する。特にホストのシェル、ファイル読み取り、編集機能が残らないことを確認する。
- 「読み取り専用のホスト環境」だけでは不十分。隠しテストもホスト上にあるため、読み取りも制限する。
- CLI の sandbox や承認設定が、コンテナへの MCP 呼び出しとどう関係するか確認する。ホストの全権限付与で代用しない。
- 通常終了とエラーを分け、Harbor に使用量と ATIF trajectory を保存する。
- モデル名は利用者が明示指定する。黙って別モデルに切り替えない。

## 検証と使い方

モデルを呼ばない自動テストを先に実装・実行してください。
実モデル呼び出しは、共通ルールどおり別途承認があるまで行わないでください。

実装後、次の形式のコマンドを `CODEX_USAGE.md` に記載してください。
`<モデル名>` は実際の CLI が受け付ける値を利用者が指定します。

```bash
source bench/env.sh
PYTHONPATH="$PWD" harbor run \
  -p tasks/dev/smoke-fix-calc \
  -a bench.agents.codex_host:CodexHostAgent \
  -m '<モデル名>' -n 1 --job-name codex-host-smoke
```

CLI で必要なツール制限を実現できない場合は、その根拠を報告してください。
制限を緩めたまま「対応済み」としないでください。
