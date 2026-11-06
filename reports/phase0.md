# Phase 0 実施結果(基盤検証と判断ゲート)

| 項目 | 内容 |
|---|---|
| 実施日 | 2026-11-05 |
| 対象 | [PLAN.md](../PLAN.md) §0.3 最短ルート / 付録 B(Phase 0) |
| ホスト | `ttgh4`(Ubuntu 22.04、12 CPU、62 GiB、`/` 空き 1.6 TB)。環境の詳細は [phase0-env.txt](./phase0-env.txt) |
| 結論 | **Path A(Harbor)を暫定採用**。G0・G1 は達成、G7 は API レベルで OK。**Copilot 経路は Haiku 4.5 / Sonnet 5.5 × 公開1タスク × k=2 の Step 4 を完了**(§8)。全4試行の結果・ATIF・usage と2モデルの比較 API を確認した。API キー経路(mini-swe-agent)の G2・G3 はモックで配線のみ確認で、実モデルでの確認は API キーと予算上限の入力待ち。G4 の運用方針の承認も残る |

> このレポートは**基盤の動作確認であり、モデルの順位を決める評価ではない**。§8 には異なる2モデルの公開1タスクでの結果と CLI が報告した利用量を記録したが、これだけで一般的な優劣や請求額は判断しない(計画書 §0.3 の方針どおり)。

---

## 1. ゲート判定

| ID | 判定 | 根拠 |
|---|---|---|
| G0 実行環境と hello-world | **OK** | oracle = 1.0(1m47s)、nop = 0.0(1m09s)。`docker` グループで Docker が使える。ただし compose / buildx プラグインが無く、ユーザー領域へ導入した(§2) |
| G1 自作タスクと公開タスク | **OK** | 自作 [smoke-fix-calc](../tasks/dev/smoke-fix-calc) は lint 違反 0(Q1 の拡張ルール込み、負例で検出を確認)、oracle = 1.0、nop = 0.0(8 テスト中 6 件が fail-to-pass として実際に失敗)。terminal-bench@2.0 先頭 3 件は oracle 3/3 = 1.0、nop 0/3 |
| G2 参照 agent × 2 モデル × k=2 | **部分(配線のみ)** | モック 2 種 × 2 タスク × k=2 = 8 trial が完走。result / ATIF trajectory / usage / cost が揃い、全モックジョブ(15 trial)の usage 合計がモック側の応答合計と完全一致(§3.2)。**実プロバイダでの usage(cached・reasoning の有無)は未確認** |
| G2′ Copilot 経路(API キー無し) | **OK(異なる2モデル × k=2、§8)** | [copilot_host.py](../bench/agents/copilot_host.py) で Haiku 4.5 / Sonnet 5.5 に同じ公開タスクを各2回実行。全4試行の result・ATIF・トークン・利用点数を確認し、例外なし。両モデルの smoke も成功。先行検証では timeout 時の usage 保存、不正モデルの `model_provider_failed` 分類も確認済み(§3.4、§4 の F8〜F12) |
| G3 `--base-url` | **部分(モックで OK)** | `--base-url http://172.17.0.1:PORT/v1` が通り、`allowlist` 下で自動許可された。実 Gateway は未実施。`config_file` 併用時は `--api-format` の明示が必須(§4) |
| G4 運用方針 | **条件付き OK** | テレメトリは `HARBOR_TELEMETRY=off` で全送信経路が止まることをコードで確認。ライセンスは Apache-2.0。Hub・hosted は未使用。**方針の承認が必要な点**: Viewer の無認証 API、LLM API 宛 TLS を傍受するプロキシ、`harbor check` / `analyze` の外部送信(§4 の F3・F4・F9) |
| G5 reward_valid の分離 | 未(Phase 5) | `failure_cause` と `reward_valid` は保存されず読み出し時に計算される(F6) |
| G6 egress 制限 | **機構は OK / 運用は Gateway 前提** | nftables + SNI 判定の sidecar がこのホストで動作し、許可ホスト以外を遮断した。ただしこのサイトでは外向きが社内プロキシ経由のみで、許可した外部 API にも届かない(F3) |
| G7 Viewer のタスク別比較 | **OK(API で確認。UI の目視は未実施)** | `GET /api/compare` がタスク × (agent, model) の格子で `avg_reward` / `avg_duration_ms` / `avg_cost_usd` / `avg_tokens` / `n_trials` を返す。CI やペア比較は無いので Phase 5 の統計は別途必要。ブラウザからホストの待受ポートに届かず UI は見ていない |

**Path の判断(§3.2)**: G0〜G2 と G4 を満たせば Path A に確定する、という基準に対し、G2 の実モデル確認と G4 の方針承認が残る。両方が済むまでは**暫定採用**とする。Harbor 自体に Path B へ切り替える理由は見つからなかった。

---

## 2. 実行環境と版固定

| 項目 | 内容 |
|---|---|
| 権限 | `sudo` はパスワード要求で使えない。ユーザーは `docker` グループに属し、Docker は動作する(権限は実質 root 相当) |
| Docker | 29.1.3(Ubuntu の `docker.io`)。**compose / buildx プラグインが無かった**ため、公式バイナリを SHA256 検証のうえ `~/.docker/cli-plugins/` に配置した(Compose **v5.6.1**、buildx **v0.38.1**)。ホーム配下は NFS なので他ホストでも有効 |
| Harbor | **0.25.0**(`uv tool install "harbor==0.25.0" --python 3.12`、Python 3.12.15)。stable のみ使用 |
| 参照 agent | mini-swe-agent **2.4.6**。コンテナ内で Harbor が導入する。依存は `UV_EXCLUDE_NEWER=2026-10-15T00:00:00Z` で固定(litellm **1.105.1**、openai 2.54.0。F1 の回避策) |
| ベース image | `python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30`(自作タスクの agent 用・採点用とも digest 固定) |
| テレメトリ | `HARBOR_TELEMETRY=off`([bench/env.sh](../bench/env.sh))。Harbor 起動前に必ず source する |
| ネットワーク | 直接の外向き通信は github.com 以外で遮断。外部へは社内プロキシ経由のみ。シェルは `proxy-k`、Docker の設定(`~/.docker/config.json`)は `proxy-u` |
| 実行場所 | リポジトリ直下 `/data/store5/2026/kamo/code_agent_test`(git 管理外のまま。`git init` は実施していない) |

---

## 3. 実施内容と結果

### 3.1 Step 2・3(G0・G1)

| 実行 | job(`jobs/`) | 結果 | 所要 |
|---|---|---|---|
| hello-world oracle / nop | `p0-g0-hello-oracle` / `-nop` | 1.0 / 0.0 | 1m47s / 1m09s(採点時に apt と uv をネットから取得する旧形式タスク) |
| 自作タスク oracle / nop | `p0-g1-smoke-oracle` / `-nop` | 1.0 / 0.0 | 1m07s / 41s |
| terminal-bench@2.0 先頭 3 件 oracle / nop | `p0-g1-tb3-oracle` / `-nop` | 3/3 = 1.0 / 0/3 | 2m25s / 49s(並列 3) |

先頭 3 件は `break-filter-js-from-html`、`gpt2-codegolf`、`llm-inference-batching-scheduler`。

自作タスクは計画書 §5.1 の規約どおり、別 verifier(hidden tests を焼き込んだ `tests/Dockerfile`)、非 root の agent、agent フェーズ `allowlist`、採点 `no-network`、`/app` の成果物受け渡しで構成した。採点は低権限ユーザーで pytest を実行し、reward は root の `test.sh` だけが書く(§5.2-2)。

### 3.2 モック LLM による G2・G3・G7 の配線確認

実モデルの API キーが環境に無いため、tool call を返す OpenAI 互換のモック([mock_llm_server.py](../bench/phase0/mock_llm_server.py))で、Harbor → mini-swe-agent → `--base-url` → 採点 → Viewer の経路を通した。

| 検証 | 結果 |
|---|---|
| 2 モデル × 2 タスク(自作・公開 hello-world)× k=2([設定](../configs/phase0-mock-rehearsal.yaml)) | 8/8 完走。solver は平均 1.0・Pass@2 = 1.0、lazy は 0.0・0.0。Harbor は k 指定時に `pass_at_k` を出力する(pass^k に当たる指標は結果に見当たらない) |
| トークン集計 | Harbor の usage と、モックが返した usage が**全モックジョブで完全一致**。15 trial の合計が入力 20,541 / 出力 715 / キャッシュ 3,944 で、モック側の 23 call(ホスト側のプローブ 8 call は除外)と同じ。ATIF v1.7 の `final_metrics` とも一致 |
| コスト集計 | 価格を与えたモックで trial の `cost_usd` が単価 × トークンと一致(例 0.001989)。job 合計 0.010674 も 4 種の合計と一致 |
| 公開タスクでの agent 導入 | terminal-bench 先頭 3 件で mini-swe-agent の導入と実行が成功(`p0-mock-tb3-lazy`、例外 0) |
| 上限の効き方(mock-loop) | `step_limit=3` は 3 call、`cost_limit=0.002` も 3 call(実コスト 0.0028)で `LimitsExceeded` |
| `allowlist` 下の接続 | `--base-url` の host は自動許可され、接続元 `172.18.0.x` のコンテナからホストの 172.17.0.1 に到達 |

### 3.3 その他の検証

| 検証 | 結果 |
|---|---|
| 成果物の削除・リネーム(§5.2-4) | **解決**。ディレクトリ成果物は復元先を空にしてから展開する(`trial/artifact_handler.py` の `empty_dirs`)。verifier image に元コードを焼き込んでも反映されることを実験で確認(`/tmp` の変種、現在は削除済み) |
| `harbor lint` | 違反時は終了コード 1。負例 4 種(`unpinned-image`、`agent-image-test-leak`、`root-agent`、`unpinned-python-dependency`)を検出 |
| Step 4 スクリプトの通し実行(モック、課金なし) | プリフライト 2 + 本番 2 + 集計が終了コード 0 で完走(約 10.5 分)。**初回は、全プロキシを `proxy-k` に上書きしたため apt が止まり、プリフライトが `AgentSetupTimeoutError` を検出して終了コード 5 で課金前に停止した**。HTTPS だけの上書きに直して解決(F3)。通し実行をしていなければ、実モデルの予算を使ってから気づいていた |
| `harbor check` / `analyze` | **未実行**。既定は agent = claude-code、model = claude-sonnet-4-6(check)/ claude-haiku-4-5(analyze)で、タスクのソースや trajectory を LLM に送る |

---

### 3.4 Copilot(API キー無し)経路: ホストの Copilot CLI を agent にする

きっかけは「API キーを入力する代わりに、いま使っている code agent から直接実行できないか」という要望。次の 3 方式を比べ、**(H) を実装した**。

| 方式 | 認証情報の置き場 | 判定 |
|---|---|---|
| (D) このチャットから別セッションへ委譲(`create_session`) | ホスト | **不採用**。セッションはホスト上で動き、サンドボックス・採点分離・usage 記録が無い。hidden tests がホストのファイルシステムにあり、agent から読めてしまう(§7 の Cheating)。モデルの固定もできない |
| (P) Harbor 標準の `copilot-cli` agent(サンドボックス内で CLI を動かす) | **サンドボックス内**(`COPILOT_GITHUB_TOKEN` などを渡す) | 可能。コンテナへの導入は `--install-only` で確認した(Copilot CLI 1.0.105)。ただし GitHub トークン(fine-grained PAT、"Copilot Requests" 権限)が必要で、LLM 駆動のサンドボックスにトークンが入る。コンテナはこのセッションの認証を引き継げない |
| **(H) ホスト側 agent(実装)** | **ホストのみ**(CLI が既存ログインを使う) | **採用**。キー不要、認証情報はサンドボックスに入らず、サンドボックスは外向き通信なしで動く |

```text
copilot(ホスト・既存ログイン) ─MCP stdio→ sandbox_mcp.py ─unix socket→ copilot_host.py ─environment.exec→ サンドボックス
```

実装: [copilot_host.py](../bench/agents/copilot_host.py)(Harbor の外部型 agent。`-a bench.agents.copilot_host:CopilotHostAgent`)と [sandbox_mcp.py](../bench/agents/sandbox_mcp.py)(`bash` 1 つだけを公開する標準ライブラリのみの MCP サーバー)。

| 検証 | 結果 |
|---|---|
| ホストの CLI が既存ログインで非対話実行できる | **OK**。`claude-haiku-4.5` が `OK` を返し、0.33 プレミアムリクエスト。空の `COPILOT_HOME` では `No authentication information found` で失敗する(ログインは `COPILOT_HOME` 内。F12) |
| MCP の配線(偽ブリッジ。ホストでは何も実行しない) | モデルに見えるツール名は `sandbox-bash`。権限は `--allow-tool 'sandbox(bash)'`(`sandbox-bash` では拒否された)。**拒否されるとモデルは出力を捏造した** → agent は拒否を数え(`n_permission_denied`)、1 件でもあれば trial を無効にする(F9) |
| 隔離 | `--available-tools` が無いと、モデルにはホストの `bash`・`view`・`edit`・`web_fetch`・`grep`・`glob`・`task`・`session_store_sql` などが見える。`--available-tools sandbox-bash` で 1 つだけになることを、モデル自身にツール名を列挙させて確認した(F9) |
| smoke タスク(Harbor、`allowlist` で許可ホスト 0) | 最終版のコードで reward 1.0 を 3 回(Step 4 スクリプトのプリフライト 2 回と最終回帰 1 回)。モデルは 5〜8 回の sandbox 呼び出しで修正と確認を行い、hidden tests 8/8、別 verifier が採点した。ATIF の trajectory(11 ステップ前後)も `harbor view` 用に生成される。`metadata` に sandbox の所要時間も記録される(最終回帰: sandbox 0.9 秒に対しモデル待ち 19.1 秒) |
| 課金 | プレミアムリクエストは**プロンプト単位**: 7 回のモデル呼び出し・5 回のツール呼び出しを含む trial 全体で 0.33(F10) |
| usage | `--usage-output-file` が最良で、入力 26,225(= 非キャッシュ 17,354 + キャッシュ read 4,180 + write 4,691)、出力 1,309、nano-AIU、モデル別の内訳が取れる。OTel のファイル出力は fallback。`--stream off` のイベントにはトークンが出ない(F10) |
| 失敗の分類 | 不正なモデル名は `ModelNotFoundError` → `model_provider_failed`(reward 無効、採点しない、29 秒で終了。課金なし)(F11) |
| timeout | agent timeout を 18 秒に短縮して実行: `AgentTimeoutError`(`agent_exhausted_budget`、reward 有効)。SIGTERM で CLI が集計ファイルを書くため使用量は記録され(入力 17,760、出力 862、0.33)、プロセスと一時ディレクトリは残らなかった(F11) |
| Step 4 スクリプトのガード | `run_step4_copilot.sh`: 未設定・12 trial 超過・`CONFIRM_SPEND` 無し・利用不可モデル・予算超過(偽の 3 倍プローブ)を拒否(終了コード 1・2・4・3・6)。`DRY_RUN=1` はモデルを一切呼ばない |
| Step 4 スクリプトの通し実行(公開タスク) | `MODEL_A=MODEL_B=claude-haiku-4.5`、terminal-bench@2.0 の先頭 1 件(`gpt2-codegolf`、hard)、k=1、agent timeout を 0.1 倍に短縮(予算上限 2)。**終了コード 0、約 6.8 分**。倍率プローブ 0.33 → 予算計算 1.65(≤ 2)→ プリフライト 2 件(smoke、reward 1.0)→ 本番 2 件 → 集計と `reports/` への保存。本番は 1 件が 90 秒の timeout(`AgentTimeoutError`、reward 有効の 0.0、19 回のモデル呼び出し、入力 175,299 / うちキャッシュ 152,646、出力 7,592)、1 件は自己終了して採点され 0.0(入力 40,263、出力 3,443)。どちらも **0.33 プレミアムリクエスト**。実消費は見積りと一致した(0.33 × 5 = 1.65)。同じモデル・同じタスクでも挙動が大きく違う(19 回 vs 自己終了)ため、k ≥ 3 が必要(D5)。reward は短縮 timeout と hard タスクの結果で、モデルの評価ではない |

> **使った Copilot のクォータ**: 調査・検証の全体で **6.27 プレミアムリクエスト**(`claude-haiku-4.5` の 0.33 × 19 プロンプト)。内訳は、ツール配線・隔離の確認 7、Harbor での smoke・timeout・最終版の回帰確認 6、倍率プローブの確認 1、Step 4 スクリプトの通し実行 5。不正なモデル名での呼び出しは課金されなかった。

---

## 4. 計画書に影響する発見(重要度順)

### F1. Harbor 0.25.0 + mini-swe-agent は、そのままだと全 trial が失敗し、agent の失敗に見える

- litellm 1.106.0 以降は `tools` 付きの `completion` が、optional のはずの `mcp` を import する。Harbor 0.25.0 の導入コマンド(`--with litellm --with orjson --with fastapi`)には `mcp` が無く、**最初の LLM 呼び出しで失敗**する。litellm 1.105.6 までは正常、1.106.4 以降は失敗。
- 失敗は `AgentTimeoutError`(`agent_exhausted_budget`)として記録され、reward は有効で agent の失敗として分母に入る。**インフラ起因の失敗が agent の失敗に混ざる**実例(D7・§6.2 に直結)。対照実験 `p0-mock-control-no-pin`(trajectory なし、入力トークンなし、reward 0.0)。
- 上流で既知: [mini-swe-agent#1003](https://github.com/SWE-agent/mini-swe-agent/issues/1003)、Harbor の修正 [PR#3867](https://github.com/harbor-framework/harbor/pull/3867)(`litellm[mcp]` を導入。2026-10-26 に main へマージ済みだが **0.25.0 stable には未収録**で、`0.25.1.dev*` の nightly のみ)。
- **回避策(採用)**: `--ae UV_EXCLUDE_NEWER=2026-10-15T00:00:00Z`。stable のまま依存が日付で固定される。`--ae` はインストールにも効くことを実機で確認した。stable に修正が入ったら外す。
- **恒久対策**: trajectory と入力トークンが無い trial を `harness_error` 候補として分離する([summarize_job.py](../bench/phase0/summarize_job.py) に実装)。大きな job の前に hello-world でプリフライトする([run_step4.sh](../bench/phase0/run_step4.sh) に実装)。

### F2. 予算上限は既定で無効(§6.1・§8.2)

- Harbor 経由の mini-swe-agent は `cost_limit="0"`(無制限)、`step_limit: 0`、`wall_time_limit_seconds: 0`。trajectory の `agent_config` で確認した。
- 有効化は `--ak cost_limit=<USD>` と `--ak config_file=configs/phase0-mini-swe-agent.yaml`([設定](../configs/phase0-mini-swe-agent.yaml)、step 50・1200 秒)。
- 注意点:
  1. `cost_limit` は呼び出しの前に判定するため、最大 1 call 分超過する。
  2. 価格が未登録のモデルは cost が 0 になり、`cost_limit` が発動しない。`run_step4.sh` は未登録モデルを拒否する。
  3. 上限による停止は Harbor には**例外なしの通常終了**として記録される。`failure_cause` では識別できないので、trajectory の `info.exit_status == "LimitsExceeded"` で判定する(§6.2 の「予算超過」の取り方を修正)。類似の上流 issue: [Harbor#3851](https://github.com/harbor-framework/harbor/issues/3851)。
  4. `config_file` や `litellm_model_registry` を指定すると `--base-url` が無効になる(`doesn't support --base-url overrides`)。Gateway と併用するには `--api-format openai_chat_completions` などの明示が必要。

### F3. このサイトでは、プロキシが LLM 接続と egress 制限の両方に効く(G3・G6・§4.3)

- コンテナに自動注入されるプロキシ(`proxy-u`)は、**LLM API(api.anthropic.com / api.openai.com)宛の TLS だけを傍受**し、コンテナが CA を信頼しないため `CERTIFICATE_VERIFY_FAILED` になる。シェルの `proxy-k` は傍受しないが、**平文 HTTP の apt ミラー(archive.ubuntu.com、deb.debian.org など)がタイムアウトする**(ホストから `curl -x` で確認。HTTPS の astral.sh・GitHub・PyPI はどちらも通る)。2 つのプロキシは相補的で、全プロキシを `proxy-k` に上書きすると agent の導入(apt)が止まる(通し実行で `AgentSetupTimeoutError`、360 秒)。**HTTPS だけ** `--ae HTTPS_PROXY=<proxy-k> --ae https_proxy=<proxy-k>` で上書きし、HTTP は Docker 既定のままにする。上書きすると TLS 検証付きで LLM API に届く(api.anthropic.com は 404、api.openai.com は 421 を返す=到達)。`run_step4.sh` は既定でホストのシェルのプロキシを HTTPS にだけ使う。
- `allowlist` の仕組みは、全 TCP を透過プロキシ(gost)へ振り向け、**SNI / Host で許可判定**する。許可ホストのみ通過し(172.17.0.1 と、許可した github.com は 200)、他は遮断され、DNS 解決は通る。
- ただし許可判定の後、sidecar は**社内プロキシを経由せず直接接続**する。直接接続できない外部ホスト(LLM API など)は、許可してもタイムアウトする。
- したがって、このサイトで private タスクを `allowlist` で動かすには、**ホスト上の Gateway(docker0 の IP など)が実質必須**になる。§4.3 の「任意」は「private タスクでは必須」に読み替える。Gateway はコンテナに API キーを置かない点でも §8.3 に合う。公開タスクなら `public` モードで `proxy-k` 経由の API 呼び出しが使える。

### F4. Viewer の API は無認証で、強い操作を含む(§8.3・G4)

- `harbor view` は `127.0.0.1:8080-8089` で待ち受ける。API にはジョブ起動(`POST /api/run`)、削除(`DELETE /api/jobs/{name}`)、Hub への送信(`POST .../upload`)、LLM による解析(`POST .../summarize`)がある。認証なしで `GET /api/jobs` が応答することを確認した(起動・削除・送信・解析は実行していない)。
- 共有サーバーでは、同一ホストの他ユーザーも `127.0.0.1` に接続できる。Docker 権限は実質 root 相当なので、他人にコンテナ起動を許すことになりかねない。
- 運用: 常時起動しない。必要なときだけ起動して即停止するか、`jobs/` を手元の PC に同期して閲覧する。`--host 0.0.0.0` は使わない。今回起動した Viewer は停止済み。

### F5. 採点と成果物の設計(§5.2)

- ディレクトリを成果物にすると、復元先は空にされるので、削除・リネームは採点側に反映される。一方、**`exclude` したもの(`node_modules` など)は採点側の `/app` から消える**。Node 系タスクでは依存を `/app` の外に置くか、除外しない。単一ファイルを成果物にすると、削除されたファイルは収集できず、採点側の元ファイルが残る。ディレクトリ単位で渡す。
- 公開 hello-world のように shared verifier で採点時にネットから取得するタスクは、時間(72 秒)と flaky の要因になる。自作タスクは verifier image に焼き込み、オフライン(`no-network`)で採点が約 20〜30 秒。

### F6. 集計まわり(§6.2・§6.4・G5)

- `failure_cause` と `reward_valid` は `result.json` に保存されず、`TrialResult` の property として `exception_type` から計算される。`bench ingest` では Harbor の Python モデルを使うか、対応表を持つ。Harbor の版に依存する。
- job の時刻は naive なローカル時刻、trial の時刻は UTC(`Z`)で表記が違う。取り込み時に正規化する。

### F7. 時間の見積り(§8.2)

- `agent_setup`(apt・uv・Python・mini-swe-agent の導入)が **単独で約 90 秒、4〜7 並列で 110〜310 秒**かかり、trial 時間の大半を占める。モックの 1 trial は並列時 3〜6 分(LLM 時間はほぼ 0)。実モデルではこれに LLM の時間が加わる。Phase 3 で agent を image に焼き込み、`skip_install` を使うか検討する。ホスト側 agent(F8)はサンドボックスに何も導入しないため `agent_setup` が 0 秒で、smoke タスクは 1 trial 約 1 分(うち agent 約 20〜30 秒)。

### F8. API キー無しで Step 4 を実行できる: ホストの Copilot CLI を agent にする(§3.4)

- ホストの `copilot` は既存ログインで非対話実行できる。これを Harbor の外部型 agent にし、サンドボックスには MCP の `bash` 1 つだけを橋渡しする。認証情報はホストに残り、サンドボックスは外向き通信なしで動く。**F3(プロキシが LLM API への到達と egress 制限を両立させない)も回避できる**。モデルがホストで動くため、`[agent] network_mode = "allowlist"`(許可ホスト 0)で完全に遮断できる。
- 制約(計画書へ反映済み):
  1. scaffold が Copilot CLI になる(§4.2 のモード B)。mini-swe-agent を固定した参照 agent の順位とは混ぜない。
  2. モデル ID は Copilot カタログの alias で、snapshot を固定できない(§6.1 の例外として記録する)。サーバー側のモデル更新、実験フラグ(`--no-experimental` で抑える)、ホストの CLI の更新(`--no-auto-update` は付けるが、ホストの CLI 版の管理は別)で再現性が下がる。
  3. コストは USD ではなく**プレミアムリクエスト**(旧課金方式)と nano-AIU。§8.2 の USD 見積りは測れない。
  4. 公開タスクの多くは `[agent] network_mode` が既定(public)で、サンドボックスから Web に出られる。公開ベンチは校正用で、汚染は前提として扱う(§5.4-B)。private タスクは許可ホスト 0 の `allowlist` で遮断できる。
  5. 認証は `COPILOT_HOME` にあり、各 trial がユーザーのセッション一覧にセッションを残す(F12)。

### F9. 隔離は `--available-tools` が要。可視性と権限は別レイヤー

- 何も絞らないと、モデルにはホストの組み込みツール(`bash`・`view`・`create`・`edit`・`web_fetch`・`grep`・`glob`・`task`・`sql`・**`session_store_sql`(過去のセッションを読める)** など)が見える。hidden tests はホスト上にあるので、そのままでは不正の経路になる。
- 対策(いずれも実機で確認): `--available-tools sandbox-bash`(可視性。モデルに列挙させて 1 つだけと確認)、`--allow-tool 'sandbox(bash)'`(権限。書式は `<MCP サーバー名>(ツール名)`)、`--deny-tool shell / write / url`(念のため)、空の作業ディレクトリ、`--disable-builtin-mcps`・`--no-custom-instructions`・`--no-ask-user`・`--no-remote`。権限のフラグは、可視性で除外したツールを公開しない。
- 権限が拒否されたとき、**モデルはツール出力を捏造して「成功した」ように答えた**(偽ブリッジのテストで、実際のコマンドは届いていない)。agent は `tool.execution_complete` の `denied` を数え、1 件でもあれば `PermissionDeniedError` で trial を無効にする。
- 実行時には、ユーザーの指示ファイル(`copilot instruction list` は空の作業ディレクトリで 0 件)、メモリ(`-p` では既定で無効)、ユーザー設定の MCP(WorkIQ など。ヘッドレスでは無効)は読み込まれなかった。

### F10. 課金はプロンプト単位。予算は倍率で正確に管理できる

- プレミアムリクエストは、モデル呼び出しやツール呼び出しの数に依存せず、**1 プロンプト = モデルの倍率 × 1**。`claude-haiku-4.5` は 0.33。したがって `trial 数 × 倍率` が正確な予算になる。`run_step4_copilot.sh` は各モデルを最小プロンプトで 1 回プローブして倍率を測り(1 倍率分を消費)、予算超過なら開始前に拒否する。
- このアカウントは旧課金方式(プレミアムリクエスト)。新方式の単位 nano-AIU(1 call あたり数億)も記録される。`--max-ai-credits` は新方式向けで、ここでは使っていない。
- usage の取得元の優先順位: (1) `--usage-output-file`(最終統計。キャッシュ内訳つき)、(2) `COPILOT_OTEL_FILE_EXPORTER_PATH`(`chat` スパン。内容は記録しない既定のまま)、(3) `result` イベントのプレミアムリクエストのみ。

### F11. Copilot 側の失敗を agent の失敗に混ぜない。timeout でも使用量を残す

- Harbor の例外分類はクラス名で決まる。agent が `ModelNotFoundError`・`AuthenticationError`・`ApiRateLimitError` などの名前の例外を投げると、`model_provider_failed`(reward 無効、採点しない)として扱われる(D7・§6.2)。stderr の内容から分類する。
- timeout でキャンセルされたとき、いきなり SIGKILL すると CLI は集計ファイルを書かない。SIGTERM を先に送り(最大 8 秒待つ)、書かせてから後始末する。使用量の記録は `finally` に置き、timeout の trial でも残るようにした。

### F12. 正式評価ではベンチ専用の Copilot プロファイルを使う

- ログインは `COPILOT_HOME`(既定 `~/.copilot`)の中にあり、空の `COPILOT_HOME` では認証に失敗する。このため Phase 0 の実行はユーザーのプロファイルを使い、**trial ごとにユーザーのセッション一覧へセッションが増える**(タイトルはラッパープロンプトの冒頭)。今回の検証分は削除した(§7)。
- 正式評価では、ベンチ専用のプロファイルを 1 回作る: `export COPILOT_HOME=<専用ディレクトリ>` のうえ `copilot login`(ヘッドレスでは device code フロー。ブラウザでコードを承認するだけで、キーの入力は不要)。agent と、倍率のプローブは `COPILOT_HOME` をそのまま引き継ぐ。これでユーザーの設定・メモリ・セッション一覧と切り離せ、再現性も上がる。

---

## 5. 実モデルで Step 4 を行うために必要な入力

経路は 2 つある。**API キーが無い場合は A**。scaffold が違うため(F8)、2 つの結果を同じ順位表に混ぜない。

### 5.1 A. Copilot 経路(API キー不要。実装・検証済み)

実行は [run_step4_copilot.sh](../bench/phase0/run_step4_copilot.sh) だけで済む。**Copilot のクォータを消費するので、次の 2 つをユーザーが決める**。

1. 比較する 2 つのモデル ID(`copilot --model` の値。例: `claude-haiku-4.5`)。倍率はスクリプトが測る(0.33 倍のモデルは 12 trial で約 4 プレミアムリクエスト)。
2. 予算上限 `BUDGET_PREMIUM_REQUESTS`(プローブ + プリフライト + 本番の合計。超過するなら開始前に拒否される)。

```bash
source bench/env.sh
# 推奨: ベンチ専用プロファイル(F12)。1 回だけ: export COPILOT_HOME=$PWD/.copilot-bench && copilot login
export MODEL_A=...  MODEL_B=...  BUDGET_PREMIUM_REQUESTS=...
DRY_RUN=1 bench/phase0/run_step4_copilot.sh           # 計画の確認のみ(モデルを呼ばない)
CONFIRM_SPEND=yes bench/phase0/run_step4_copilot.sh   # プローブ → プリフライト → 本番(最大 12 trial)
```

結果は [summarize_job.py](../bench/phase0/summarize_job.py) で trial ごとの reward・トークン・プレミアムリクエスト・時間にまとめ、`reports/phase0-step4-copilot-<日時>.txt` に保存される。`EXTRA_ARGS` で `harbor run` の引数(例: `--agent-timeout-multiplier`)を足せる。

### 5.2 B. API キー経路(mini-swe-agent。モックで確認済み。実モデルは未実施)

実行は [run_step4.sh](../bench/phase0/run_step4.sh) だけで済む。**課金が発生するので、次の 3 つをユーザーが決める**。

1. 比較する 2 つのモデルの ID(snapshot 付き。alias は使わない)。litellm の価格表に載っていること。
2. 専用の失効可能な API キー(プロバイダ側で利用上限を設定。§8.3)。
3. Step 4 の総額上限 `BUDGET_USD`。難しいタスクでは 1 trial が約 $14 になりうる(§8.2)。

```bash
source bench/env.sh
export MODEL_A=...  MODEL_B=...  BUDGET_USD=...     # と API キー
DRY_RUN=1 bench/phase0/run_step4.sh                  # 計画・価格・上限の確認のみ(何も実行しない)
CONFIRM_SPEND=yes bench/phase0/run_step4.sh          # プリフライト → 本番(最大 12 trial)
```

スクリプトは、未設定の入力・12 trial 超過・価格未登録・`CONFIRM_SPEND` 無しを拒否する(終了コード 2・3・4 を確認済み)。上限は 1 trial あたり `BUDGET_USD / trial 数` の `cost_limit` と、step・時間の上限で掛かる。実行経路(プリフライト → 本番 → 集計)はモックで通しで確認した(§3.3)。Gateway(G3)経由で行う場合は `EXTRA_ARGS="--base-url <URL> --api-format openai_chat_completions"` を付ける。結果は [summarize_job.py](../bench/phase0/summarize_job.py) で trial ごとの reward・トークン・コスト・時間にまとめ、`reports/phase0-step4-<日時>.txt` に保存される。

その後、`harbor view` で G7 を目視確認し(F4 に注意)、実 usage の取得可否と合わせて §3.2 に反映する。

---

## 6. 成果物

| パス | 内容 |
|---|---|
| [tasks/dev/smoke-fix-calc](../tasks/dev/smoke-fix-calc) | 自作の動作確認タスク(§5.1 の規約。canary GUID 入り) |
| [configs/phase0-mini-swe-agent.yaml](../configs/phase0-mini-swe-agent.yaml) | 共通の step・時間上限 |
| [configs/phase0-mock-rehearsal.yaml](../configs/phase0-mock-rehearsal.yaml) | モックでの予行演習用 job config(`--api-format`・価格表・回避策入り) |
| [bench/env.sh](../bench/env.sh) | テレメトリ off などの共通環境 |
| [bench/phase0/run_step4.sh](../bench/phase0/run_step4.sh) | 実モデル Step 4 のガード付きスクリプト(API キー経路) |
| [bench/phase0/run_step4_copilot.sh](../bench/phase0/run_step4_copilot.sh) | 実モデル Step 4 のガード付きスクリプト(Copilot 経路。API キー不要) |
| [bench/agents/copilot_host.py](../bench/agents/copilot_host.py)、[sandbox_mcp.py](../bench/agents/sandbox_mcp.py) | ホスト側の Copilot CLI を agent にする Harbor 外部型 agent と、サンドボックスへの橋渡し(MCP の `bash` 1 つ)。`python -m bench.agents.copilot_host probe <model>` で倍率を測れる |
| [bench/phase0/summarize_job.py](../bench/phase0/summarize_job.py) | job の集計と、harness 失敗候補の検出 |
| [bench/phase0/mock_llm_server.py](../bench/phase0/mock_llm_server.py)、[mock-loop-limits.yaml](../bench/phase0/mock-loop-limits.yaml) | 配線確認用のモック LLM と上限テスト用設定 |
| [bench/phase0/collect_env.sh](../bench/phase0/collect_env.sh)、[phase0-env.txt](./phase0-env.txt) | 環境・版の記録 |
| `jobs/p0-*`、`jobs/p0c-*` | 今回の全 job(git 管理外)。`p0-mock-*` はモックの結果、`p0-copilot-host-*`・`p0c-*` は Copilot 経路の動作確認(短縮 timeout や hard タスクの結果を含み、評価値ではない) |

## 7. 後始末と残したもの

- 停止済み: モックサーバー、Viewer。`/tmp` の一時ファイル(scratch の venv、プローブ用タスク、変種タスク、Copilot のプローブ用スクリプトとログ)は削除した。
- **Copilot のセッション**: 検証で作った 19 件のセッション(`list_sessions` で、作業ディレクトリが私の一時ディレクトリ `/tmp/cbh-*`・`/tmp/copilot-probe.*` のものを特定。ユーザー自身のセッションは 0 件)を `delete_session` で削除し、失敗した呼び出しが残した空の殻 4 件(イベントなし)も削除した。各 trial のログ・trajectory は `jobs/` 配下に保存済み。今後の実行でもセッションは増える(F12)。
- ホストに残るもの: ユーザー領域の `~/.docker/cli-plugins/`(compose・buildx)、`~/.local/share/uv/tools/harbor`、`~/.cache/harbor`、Docker の image(`python:3.12-slim`、`ubuntu:24.04`、terminal-bench の 3 image、gost の sidecar)。共有 Docker デーモンの他ユーザーの container・image には触れていない。Copilot の認証情報(`COPILOT_HOME` 内)は読み取りも複製もしていない。

## 8. 異なる2モデルでの Step 4 実行(2026-11-05)

ユーザーの続行指示に基づき、Copilot 経路で公開1タスクを2モデル各2回実行した。
API キー経路、Claude Code、Codex は実行していない。

### 実行条件

- CLI: GitHub Copilot CLI 1.0.96、Harbor 0.25.0。
- モデル: `claude-haiku-4.5` / `claude-sonnet-5.5`。
- タスク: terminal-bench@2.0 の `gpt2-codegolf`。全4試行の task checksum は同一。
- `N_TASKS=1 K=2 N_CONCURRENT=1`、`EXTRA_ARGS='--max-retries 0'`。短縮タイムアウトは指定していない。
- 両モデルで smoke タスクを1回ずつ事前実行し、いずれも reward=1.0。
- モデル名固定は指定したが、提供元の不変な snapshot を固定したという意味ではない。

### 結果

| モデル | 公開タスク成功数 | 平均試行時間(準備・採点込み) | 本試行の利用点数合計 | 例外 |
|---|---|---|---|---|
| Haiku 4.5 | 0/2 | 約330秒 | 0.66 | なし |
| Sonnet 5.5 | 1/2 | 約137秒 | 2.00 | なし |

全4試行に result、ATIF trajectory、入力・出力・キャッシュトークン、コンテナ操作回数、
CLI が報告したプレミアムリクエスト値が保存され、既存の集計スクリプトが警告なしで終了した。
両モデルの smoke も同じ項目を確認した。成功数は1問での観測であり、順位の根拠にはしない。

保存先:

- [Haiku 本試行](../jobs/p0c-step4-20261105-1758-a/)
- [Sonnet 本試行](../jobs/p0c-step4-20261105-1758-b/)
- [Haiku smoke](../jobs/p0c-preflight-20261105-1758-1/)
- [Sonnet smoke](../jobs/p0c-preflight-20261105-1758-2/)
- [本試行の集計](phase0-step4-copilot-20261105-1758.txt)

### 消費量とモデル確認

最初に試した Sonnet 4.5 / 4.6 は利用不可で、本試行前に停止した。
その後、ACP の `initialize` → `session/new` でモデル一覧を取得し、
利用可能な Sonnet 5.5(表示1倍)を選んだ。この一覧取得では AI への問題送信はしていない。

| 内訳 | CLI の報告に基づくプレミアムリクエスト値 |
|---|---|
| 最初の2回の起動試行に含まれる Haiku プローブ | 0.66 |
| 最終実行の2モデルのプローブ | 1.33 |
| 2モデルの smoke | 1.33 |
| 公開タスク4試行 | 2.66 |
| 合計 | **5.98** |

利用不可モデルのプローブは使用量を取得できていないため、この合計には加算していない。
上記は取得できた CLI の報告値の合計で、提供元の請求明細との照合は未実施。
実行スクリプトの予算判定はプローブ後の見積もりであり、厳密な課金上限の保証ではない。

### 比較表示と後始末

Viewer を `127.0.0.1:18080` のみで一時起動し、
`GET /api/compare?job=p0c-step4-20261105-1758-a&job=p0c-step4-20261105-1758-b` を確認した。
同一タスクの2モデルのセルが返り、それぞれ `n_trials=2`、`n_completed=2`、
平均 reward=0 / 0.5 と集計結果に一致した。UI の目視確認はしていない。
確認後に Viewer を停止し、対象 smoke / gpt2-codegolf の稼働中コンテナがないことを確認した。
今回作成された Copilot セッションは削除せず、試行ログも保持した。

### Phase 0 の判断

Copilot 経路の G2′ は、異なる2モデル・同じ公開タスク・k=2 まで確認できた。
G7 も今回の2モデルの実結果で API 比較を確認した。
一方、mini-swe-agent の実 API による G2、必要時の実 Gateway による G3、
G4 の運用方針の承認は残る。**Phase 0 全体の無条件完了や Path A の正式確定とはしない。**
Copilot 経路で先行する場合の次の技術作業は、Phase 1 のタスク規約と初期タスクの整備。
