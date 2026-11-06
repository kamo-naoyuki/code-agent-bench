# Code Agent ベンチマーク計画書

| 項目 | 内容 |
|---|---|
| 版 | v0.6 (Draft) |
| 作成日 | 2026-11-05 |
| 目的 | LLM の code agent としての性能を、再現可能・公平・低コストに比較できるベンチマーク基盤を作る |
| 状態 | Phase 0 を実機で実施済み(2026-11-05。結果は [reports/phase0.md](reports/phase0.md))。G0・G1 は達成。**Copilot 経路の Step 4 は Haiku 4.5 / Sonnet 5.5 × 公開1タスク × k=2 の4試行を完了**。結果・trajectory・usage と Viewer の2モデル比較 API を確認した。API キー経路(mini-swe-agent)の G2・G3 は実モデル未確認。G4 の運用方針の承認が残るため、Harbor は暫定採用 |
| 後続の進捗 | Phase 1 は初期10問の Q1〜Q5 と独立AIレビュー後の修正を検証済み(人手承認・`harbor check` は未)。Phase 2 は1コマンド実行・manifest・中断再開を oracle/nop で実機確認済み。GitHub CI は設定のみ。[Phase 1](reports/phase1.md) / [Phase 2](reports/phase2.md) |

> **注記**
> - Harbor に関する記述は、公式ドキュメントで確認した内容に基づく(確認日 2026-11-05)。
> - 付録 B のコマンドのうち Step 0〜3 は実機で確認した。Step 4 は Copilot 経路を smoke タスクと公開タスクで確認済みで、API キー経路は実モデル未実施。差分と発見は [reports/phase0.md](reports/phase0.md)(特に F1〜F12)。
> - 作業ホストは Docker が使える環境に移った(§1.4)。当初のホストでは Docker が権限不足で使えなかった。
> - 見積り・配分・閾値は初期の目安であり、最短ルートの実測値で置き換える。
> - 読む順序の目安: **まず §0 だけ読む**(用語集・Harbor との分担・最短ルート)。§1〜§8 と Phase 3 以降は、最短ルートの結果を見てから読めばよい。

---

## 0. 要約と最短ルート

- **作るもの**: 複数のモデル(と agent)を、隔離した sandbox 上で実タスクに挑戦させ、hidden tests で自動採点し、精度・コスト・時間・安定性を**信頼区間付き**で比較できるベンチマーク基盤。
- **設計の核**
  1. (agent, model) を独立した軸にし、`task × agent × model × 試行` を 1 つの trial として扱う。
  2. 工数は実行ハーネスよりも**タスクと採点器の品質**に割く。結果の信頼性はここで決まる。
  3. 実行(高コスト)と採点・分析(低コスト)を分離し、生ログを全て残す。
  4. 失敗を原因別に分類し、インフラ起因の失敗を agent の失敗に混ぜない。除外数は必ず開示する。
  5. 数字は必ず信頼区間付きで出し、差が検出できない規模では順位を作らない。
- **実現方針(推奨)**: [Harbor](https://github.com/harbor-framework/harbor) を土台にする。タスク形式・sandbox 実行・並列/リトライ・agent 統合・trajectory・採点分離・不正対策ツールは既に揃っている(§3)。自作は「タスク品質保証の自動化 / private タスクの生成 / 統計とレポート / 運用自動化」に絞り、計測の統一(Gateway)は必要になってから導入する。Viewer で足りる部分は自作しない。採用は **Phase 0 の判断ゲート**で確定する。
- **進め方**: 最初の一歩は §0.3 の最短ルート(= Phase 0)。実行環境を確保し、Harbor で少数の trial を動かして、1 trial の単価と、Viewer でタスク別に比較できるかを確認する。Phase 3 以降は、その結果を見て必要なものから着手する。全体は Phase 0〜7 と 3 つのマイルストーン(M1 動く最小ベンチ → M2 対象スイート内で信頼できる単一モデル評価 → M3 複数モデル比較の運用)に分けてある(§9)。
- **実行環境の注意**: 現在の作業ホストは `docker` グループで Docker が使える(2026-11-05 確認)。ただし compose / buildx プラグインが無く、ユーザー領域に導入した(§1.4)。当初のホストは権限不足で使えず、選択肢は §0.3 の Step 0 に残してある。

### 0.1 用語集

| 用語 | 意味 |
|---|---|
| model | コードを考えて書く AI 本体(例: Claude、GPT 系)。API などで呼び出す |
| agent | model に作業をさせる道具・進行役(例: Claude Code、Codex CLI、mini-swe-agent)。指示を model に送り、返答に応じてファイル編集やコマンド実行を行う。同じ model でも agent が違えば結果が変わる |
| task | 1 つの課題。指示文(`instruction.md`)、実行環境(`environment/`)、採点テスト(`tests/`)、模範解(`solution/`)のセット |
| trial | 1 つの task に対する、ある agent × model の 1 回の挑戦。`task × agent × model × 試行番号` で 1 つ |
| job | trial の集まり。`harbor run` を 1 回実行すると 1 つできる |
| sandbox | trial を隔離して動かす場所。手元の Docker か、クラウドの実行環境(Harbor の CLI では `environment` と呼び、`-e` で選ぶ) |
| verifier / reward | 採点役(`tests/test.sh`)と、それが出す点数(0〜1) |
| oracle / nop | Harbor 付属の確認用 agent。oracle は模範解を実行する(満点になるはず)。nop は何もしない(0 点になるはず)。タスクが壊れていないかの検査に使う |
| trajectory | agent の作業ログ(会話・コマンド・結果)。Harbor は ATIF という共通形式で保存する(対応する agent のみ) |
| resolve rate / pass@k / pass^k | 成功率。k 回試行して「1 回でも成功」した割合が pass@k、「k 回すべて成功」した割合が pass^k |
| Gateway | モデル API の呼び出しを中継し、トークン・コスト・ログを一箇所で記録する中継サーバー(任意。§4.3) |
| regrade | agent を再実行せず、採点だけをやり直すこと |
| heldout / canary | 調整に使わず正式評価にだけ使う問題群 / 学習データへの混入を検知するための目印文字列 |

### 0.2 Harbor がやること / 自分でやること

```text
自分で用意:        タスク(指示 + 採点テスト) / agent と model の選択 / API キー / 実行環境
       │
       ▼
Harbor がやる:      sandbox を起動 → agent が作業 → 採点 → 結果・ログ・コストを保存 → Viewer で閲覧
       │
       ▼
自分で見る・決める:  タスクごとの成否、モデル間の差、続けるかどうか(必要なら集計・レポートを自作)
```

| 項目 | Harbor がやる | 自分でやる |
|---|---|---|
| タスク | 形式の定義、公開データセット(Terminal-Bench など)の配布 | 評価したい自社タスクと採点テストの作成 |
| 実行 | sandbox の起動、agent の実行、並列・リトライ、採点の起動 | 実行環境の確保(Docker の権限など)、API キーと利用契約 |
| agent | 多数の agent の統合(Claude Code、Codex、Copilot CLI、mini-swe-agent など) | どの agent・model を比べるかを決める |
| 記録・閲覧 | 結果(`result.json`)、trajectory、トークン・コスト(対応する agent のみ。要確認)、閲覧用の Viewer | 版情報の記録(manifest)、統計・レポート。必要になってから |
| 品質保証 | `lint` / `check` / `analyze`、oracle / nop | 採点の妥当性の確認、解答漏洩対策(§5・§7) |

### 0.3 最短ルート(まずここだけ)

実行環境の確保から「続けるかどうかの判断」までを、同じ手順で一通り通す。Phase 0(§9.2)と付録 B はこの手順に従う。Phase 0 は基盤の動作確認であり、モデルの優劣を判断する評価ではない。

| Step | やること | 完了の目安 | 記録すること |
|---|---|---|---|
| 0 | 実行環境を確保する(下の 4 択)。外部環境では公開タスクだけを使う | `harbor run` を動かせる環境が 1 つある | 選んだ環境と理由、データ区分 |
| 1 | `uv tool install harbor`(Python 3.12 以上が必要) | `harbor run --help` が表示される | Harbor の版 |
| 2 | hello-world を `-a oracle`、次に `-a nop` で実行 | oracle が満点、nop が 0 点 | 結果の置き場所、所要時間 |
| 3 | 自作タスク 1 件を `harbor lint` に通し、oracle / nop で採点。公開データセットでも oracle / nop を各 3 タスク実行 | 自作タスクで oracle=1.0 / nop=0.0。公開タスクでも両方が完走 | タスク、結果の置き場所 |
| 4 | 参照 agent は `mini-swe-agent` 1 種に固定し、2 model × 同じ公開タスク最大 3 件 × k=2 を実行(任意の G3 を含めて最大 12 agent trial)。**API キーが無い場合は、ホストの Copilot CLI を agent にする Copilot 経路で同じ手順を実行できる**(モード B。mini-swe-agent の結果とは混ぜない。§3.2 の G2′、付録 B) | 全 trial に結果区分が付き、結果・trajectory・取得可能な usage が保存される | **1 trial あたりの実コストと時間**(Copilot 経路はプレミアムリクエスト) |
| 5 | `harbor view` で2モデルの結果を見る | 成否・コスト・時間・作業ログを確認し、G7 の判定を記録 | タスク別比較ができるか |
| 6 | G0〜G4 を判定し、G7 の結果を記録 | Harbor 継続 / 代替基盤を再検討 / 環境待ち、のいずれかを決める | 判断メモと自作範囲 |

Step 4 と条件付き G3 を合わせて最大 12 agent trial とする。実行前に総額の上限を決め、見積りに応じてタスク数を 1〜3 件にする。G3 を実施する場合は Step 4 の task 数を減らして上限内に収める。難しいタスクでは 1 trial が約 $14 になりうる(§8.2)。コマンド例は付録 B。ここでは agent の種類やモデルの順位を決めない。

**Step 0 の選択肢**(作業ホストの確認結果は §1.4)

| 選択肢 | 概要 | 注意 |
|---|---|---|
| A. 管理者に依頼 | `docker` グループへの追加、または rootless Docker の設定(`/etc/subuid` など) | `docker` グループは実質 root 相当の権限になるため、共有サーバーでは断られる可能性がある |
| B. singularity を使う | このホストに導入済み(`/usr/bin/singularity`)。Harbor の対応一覧に `singularity` がある(`-e singularity`) | Dockerfile ベースのタスクで動くかは**未確認**。ネットワーク制限(no-network / allowlist)の対応一覧に載っていないため、§7 の外向き通信の制限は使えない見込み |
| C. クラウド sandbox | Daytona・Modal・E2B など(`-e daytona` など。`uv tool install "harbor[daytona]"` のように extra を入れる) | sandbox 提供元の API キーが必要。タスクと成果物が外部サービスに出る(§8.3) |
| D. 別マシン | Docker が使えるマシン(個人 PC など) | 結果の持ち出し・同期が必要 |

推奨: 公開タスクでの試走は D か C で行う。外部環境には社内タスクや社内コードを送らない。社内タスクを扱う運用環境は、A か B の可否とデータ取り扱いを確認してから決める。

---

## 1. 目的・スコープ

### 1.1 目的

複数の LLM を同一条件の code agent で実タスクに挑戦させ、精度・コスト・時間・安定性を比較する。用途は次の 3 つ。

1. モデル選定(どのモデルをどの用途に使うか)
2. 新モデル公開時の回帰評価
3. agent 設定(prompt / tools / 予算)の改善効果の測定

### 1.2 成功基準

| ID | 基準 | 確認方法 |
|---|---|---|
| S1 | 全タスクで oracle が満点、nop(何もしない agent)が 0 点 | 品質ゲート(§5.3)を CI で実行 |
| S2 | 固定した同一スイート・同一設定で独立した全タスク実行を2回行う(各タスク k≥3)。2回の pass@1 差の 95% CI が、事前に定めた同等性幅 ±10pt に完全に収まれば合格。区間が幅の境界をまたぐ場合は判定保留、区間全体が幅の外なら再現性不合格とする | 対応ありのタスク単位 bootstrap で比較(Phase 5) |
| S3 | 公開ベンチと比較可能な条件・不確実性情報がある場合、ローカル値と公表値の差の 95% CI が ±10pt に完全に収まる。タスク版・採点・agent/model・実行条件が一致しない、または差の区間を計算できない場合は「比較不能」と記録し、外部校正済みとは扱わない | 校正可能性を確認し、差と区間または比較不能理由を記録(Phase 5) |
| S4 | 1 コマンドで評価を起動でき、中断しても再開でき、レポートまで自動生成される | E2E テスト |
| S5 | 全 trial に再現に必要な版情報(task 版・image digest・agent 版・model snapshot・harness 版)が付く | manifest の検査 |

### 1.3 スコープ外(当面)

- モデルの学習・RL(rollout 生成は将来拡張として残す)
- 公開リーダーボードの運営
- GUI・ブラウザ操作など、コード実行以外の agent 能力の評価
- 知識・推論など汎用の LLM ベンチ

### 1.4 前提(仮置き)

| 項目 | 仮置き | 変わった場合の影響 |
|---|---|---|
| 主目的 | モデル比較(agent は固定)。副次的に agent 製品の比較 | 製品比較が主なら Phase 6(外部 CLI)を前倒し |
| 規模 | 数十〜数百タスク × 数モデル × 3 試行 | 数千 trial を超えるならクラウド sandbox(Phase 7)を前倒し |
| 実行環境 | **確定(暫定)**: `docker` グループで Docker が使える Linux 共有サーバー(下の現ホストの表)。Phase 0 はここで実施した。直接の外向き通信は遮断され、外部へは社内プロキシ経由のみ | private タスクの egress 制限は Gateway が前提になる(§4.3)。社内タスクの運用環境は、データ取り扱いの確認後に決める |
| タスク | 自社リポジトリ由来(private)+ 手書き + 公開ベンチ(校正用) | |
| モデル接続 | 各社 API と OpenAI 互換エンドポイント(ローカル推論を含む) | ゲートウェイの対応 API 形式を再確認(§4.3) |
| 実装言語 | Python 3.12 + uv(Harbor が Python 製) | |
| 体制 | 1〜2 名 | §9 の見積りの前提 |
| 機密性 | private タスクや社内コードを扱う。外部サービスへの送信は既定で禁止 | Hub・hosted の利用可否(§8.3) |

**作業ホストの環境確認結果(現ホスト `ttgh4`、2026-11-05。Phase 0 を実施)**

| 項目 | 結果 |
|---|---|
| Docker | 29.1.3(Ubuntu の `docker.io`)。実行ユーザーは `docker` グループに属し、`docker info` が通る(実質 root 相当の権限。`sudo` はパスワード要求で使えない) |
| compose / buildx | **どちらも無かった**。Harbor は `docker compose` と、egress 制限用の `docker buildx build` を使うため、公式バイナリを SHA256 検証のうえ `~/.docker/cli-plugins/` に導入した(Compose v5.6.1、buildx v0.38.1) |
| 資源 | 12 CPU、62 GiB RAM、`/` の空き約 1.6 TB |
| Python / uv / Harbor | uv 0.13.4、Python 3.12.15(uv 管理)、Harbor **0.25.0**(`uv tool install "harbor==0.25.0" --python 3.12`) |
| ネットワーク | 直接の外向き通信は github.com 以外で遮断。外部へは社内プロキシ経由のみ。シェルは `proxy-k`、Docker の設定(`~/.docker/config.json`)は `proxy-u`。`proxy-u` は LLM API 宛の TLS を傍受する(F3) |
| Podman / Singularity | なし |

**当初のホストの環境確認結果(2026-11-05。Docker が使えなかったホスト)**

| 項目 | 結果 |
|---|---|
| Docker | CLI あり(29.9.1)。`/var/run/docker.sock` は `root:docker` の 660 で、実行ユーザーは `docker` グループに入っておらず、`permission denied` になる |
| rootless Docker | `rootlesskit` と `newuidmap` はあるが、`/etc/subuid` に実行ユーザーの設定が見当たらない。管理者の設定が必要とみられる |
| Podman / Apptainer | なし |
| Singularity | あり(`/usr/bin/singularity`) |
| Python / uv | Python 3.10.12(Harbor は 3.12 以上が必要)、uv 0.13.4 |
| Harbor | PyPI の最新は 0.25.0、`requires-python >=3.12`、ライセンスは Apache-2.0(PyPI の表記) |
| ネットワーク | PyPI と Harbor の API(`api.harborframework.com`)に到達できた(`curl` で確認) |

---

## 2. 設計方針(決定事項)

| ID | 決定 | 理由 |
|---|---|---|
| D1 | (agent, model) を独立軸にし、trial = task × agent × model × attempt を最小単位にする | モデル比較・製品比較・agent 改善を同じ基盤で扱える。モデルの順位は scaffold で入れ替わりうるため、必ず併記する |
| D2 | 基盤は Harbor を第一候補とし、Phase 0 で確定する | 必要な部品が既にあり、自作は差分に限定できる(§3) |
| D3 | タスクは Harbor 形式(`instruction.md` / `task.toml` / `environment/` / `tests/` / `solution/`) | タスクを実装から切り離せる。公開ベンチや他の基盤と相互運用できる |
| D4 | 採点は実行ベース(hidden tests)で、agent とは別の sandbox で行う | 改ざんを防ぎ、採点だけの再実行(regrade)ができる。LLM judge は失敗分析などの補助に限る |
| D5 | 正式評価は 1 (task, agent, model) あたり k ≥ 3 回試行する。Phase 0 の動作確認だけ k=2 とする | 非決定性を吸収し、pass^k で安定性を測る。Phase 0 は順位判断に使わない |
| D6 | 予算(steps / tokens / $)は全モデル共通の上限にする。wall-clock は安全弁にとどめる | プロバイダの遅延が順位を歪めないようにする |
| D7 | 失敗は原因別に分類し、infra/provider 起因は agent の失敗に混ぜない。除外数は開示し、感度分析を併記する | 公平性の確保 |
| D8 | 集計はタスク単位のクラスタ bootstrap。モデル間比較は対応あり(paired) | 試行は独立ではなく、タスクが標本単位のため |
| D9 | 実行と採点・分析を分離し、生ログ(trajectory・LLM の入出力)を全て保存する | 採点の修正のたびに agent を回し直さない |
| D10 | 再現性メタデータを全 trial に付ける | 後から比較・再現できる |
| D11 | LLM 呼び出しの計測は、まず Harbor と agent の記録(`result.json`・ATIF)で足りるかを確認する。足りない場合に Gateway(任意)を導入し、経由に統一する(可能な範囲で) | 計測・予算・鍵管理・レート制限を一箇所に集約できる反面、運用の手間が増えるため、必要性を実測してから決める |

---

## 3. 基盤選定(Build vs Buy)

### 3.1 候補

| 候補 | 概要 | 確認できた機能 | 位置づけ |
|---|---|---|---|
| **[Harbor](https://github.com/harbor-framework/harbor)** | Terminal-Bench の作者による agent/model 評価フレームワーク。Terminal-Bench 2.0 の公式ハーネス | タスク形式、並列・試行・リトライ(`-n` `-k` `-r`)、sandbox 切替、40 以上の事前統合 agent(`claude-code` `codex` `copilot-cli` `gemini-cli` `openhands` `mini-swe-agent` `terminus-2` `oracle` `nop` など)、ATIF trajectory、失敗の原因分類、採点専用 sandbox と regrade、`lint` / `check` / `analyze` / `view`、SWE-bench・Aider Polyglot などの公開ベンチ | **第一候補(Path A)** |
| [Inspect AI](https://inspect.aisi.org.uk/) | UK AISI らの評価フレームワーク。Dataset + Solver + Scorer で評価を Python で記述する。200 以上の既成評価、Docker/K8s/Modal などの sandbox、外部 agent(Claude Code / Codex CLI / Gemini CLI)の実行に対応 | | 代替(Path B)。コード以外の評価も同じ枠で扱いたい場合に有力 |
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | 約 100 行の最小 agent。SWE-bench Verified で 74% 超。多数のモデルを litellm などで利用でき、local / docker / podman などで動く | | **参照 agent(基準線)の候補**。Harbor に統合済み |
| 自作ランナー | 付録 A のインターフェースで一から作る | | 最後の手段(Path C) |

### 3.2 推奨と判断ゲート

推奨は **Path A: Harbor を基盤にし、差分のみ自作**する。次の項目を Phase 0 で実測して確定する。

| ID | 確認する内容 | 区分 |
|---|---|---|
| G0 | 実行環境が確保でき、`harbor run` で hello-world の oracle / nop が動く | 必須(他のゲートの前提) |
| G1 | 自作タスク 1 件が lint を通り oracle=1.0 / nop=0.0。公開データセット 3 件でも oracle / nop が完走する | 必須 |
| G2 | `mini-swe-agent` 1 種を2モデルで同じ公開タスク 1〜3 件 `-k 2` 実行し、結果区分・result・trajectory・取得可能な usage を確認できる | 必須 |
| G2′ | (API キーが無い場合の代替)ホストの Copilot CLI を agent にした Copilot 経路で、同じ確認ができる。agent が mini-swe-agent ではなくなるため、G2 の完全な代替ではなく、Harbor の実行・採点・記録経路を実モデルで確認するものとして扱う | G2 の代替(API キーが無い場合) |
| G3 | 使いたい接続先(ゲートウェイ、ローカル推論など)を `--base-url` で指定できる | 条件付き(Gateway またはローカル推論を使う場合は必須) |
| G4 | 運用方針上の問題がない(ライセンス、外部送信、テレメトリ、社内ネットワーク要件) | 必須 |
| G5 | `reward_valid=false` や欠損 reward を集計から正しく分離できる | 正式な集計を作る前に確認(Phase 5) |
| G6 | Docker の `allowlist`(agent の外向き通信の制限)がホストで動く。Linux コンテナと nftables が必要 | private タスクを実行する前に確認 |
| G7 | `harbor view` で2モデルの結果をタスク単位で確認できる | Phase 0 で確認。不可なら簡単な集計を自作する |

判定は次のとおり。

- G0〜G2 と G4 を満たせば Path A に確定する。G3 は Gateway やローカル推論を使う場合のみ必須。
- 必須ゲートを満たせない場合は、原因を環境・データ方針・Harbor の機能に分けて記録する。環境の問題なら先に解消し、Harbor の要件不足なら Path B を検討する。Path C は既存基盤で要件を満たせない場合に限る。
- G5 は正式集計を作る Phase 5 で確認する。問題があれば独自の `metric.py` で吸収する。
- G6 は private タスクを実行する前に確認する。利用できない場合はクラウド sandbox やホストのファイアウォールなど、§7 の対策を決める。
- G7 は Phase 0 で確認する。NG なら Phase 5 でタスク別集計・レポートを自作する。これは Harbor 採用の可否を左右しない。

**Phase 0 の判定結果(2026-11-05。詳細は [reports/phase0.md](reports/phase0.md))**

| ID | 判定 | 要点 |
|---|---|---|
| G0 | OK | hello-world の oracle = 1.0、nop = 0.0 |
| G1 | OK | 自作タスクは lint 違反 0・oracle = 1.0・nop = 0.0。terminal-bench@2.0 先頭 3 件も oracle 3/3 = 1.0、nop 0/3 |
| G2 | 部分 | モック LLM で配線を確認(result・ATIF・usage・cost が揃い、モック側の記録と完全一致)。**API キー経路の実モデルでの usage は未確認** |
| G2′ | OK(異なる2モデル × k=2 も完了) | Copilot 経路: Haiku 4.5 / Sonnet 5.5 で同じ公開タスク `gpt2-codegolf` を各2回実行。成功は0/2・1/2、全4試行で例外なし、result・ATIF・トークン・利用点数を確認。両モデルの smoke は成功。詳細と消費量は [レポート §8](reports/phase0.md#8-異なる2モデルでの-step-4-実行2026-11-05)。先行検証では timeout 時の usage 保存と、不正モデルの `model_provider_failed` 分類も確認済み |
| G3 | 部分 | モックで `--base-url` と `allowlist` の自動許可を確認。`config_file` 併用時は `--api-format` が必須 |
| G4 | 条件付き OK | テレメトリは `HARBOR_TELEMETRY=off` で停止。Viewer の無認証 API(§8.3)と LLM API 宛 TLS を傍受するプロキシ(F3)の扱いに承認が必要 |
| G5 | 未 | Phase 5 で確認。`failure_cause` / `reward_valid` は保存されず読み出し時に計算される |
| G6 | 機構は OK | このサイトでは外向きがプロキシ経由のみで、許可した外部 API にも届かない。private タスクは Gateway が前提(§4.3) |
| G7 | OK(API で確認) | `GET /api/compare` がタスク × (agent, model) の格子を返す。CI は無い。UI の目視は未実施 |

**Path の判断**: G0〜G2 と G4 を満たせば Path A に確定する、という基準に対し、G2 の実モデル確認(API キー経路。API キーが無い場合は Copilot 経路の G2′ で代替し、その代替が許容されるかを決める)と、G4 の方針承認が残る。両方が済むまでは Path A は**暫定採用**とする。Harbor を捨てる理由は見つかっていない。

### 3.3 本計画の要素と Harbor の対応・自作範囲

| 本計画の要素 | Harbor の対応物 | 自作の要否 |
|---|---|---|
| Task | タスクディレクトリ(`instruction.md` `task.toml` `environment/` `tests/` `solution/`) | タスクの作成のみ。形式は流用 |
| Job / Trial・並列・試行・リトライ | `harbor run`(`-n` 並列、`-k` 試行数、`-r` リトライ)、job config(`n_attempts` など) | 不要。job config を版管理する |
| Sandbox | `BaseEnvironment`、`-e` で Docker などを切替 | 不要 |
| 参照 agent | Phase 0 は `mini-swe-agent` に固定。要件を満たせない場合のみ `terminus-2` を代替候補にする | Phase 0 で動作確認。複数 agent の比較は Phase 6 |
| 外部 CLI agent | 事前統合 agent。未対応のものは `BaseInstalledAgent` で追加 | 未対応の CLI のみ |
| oracle / null agent | `oracle` / `nop` | 不要 |
| 採点 | `tests/test.sh` が `reward.txt` / `reward.json` を出力。採点専用 sandbox(separate verifier)、`rewardkit` | 共通の採点ヘルパー(§5.2) |
| trajectory | ATIF(`agent/trajectory.json`) | 不要。非対応 agent は変換 |
| 失敗の分類 | error classification(`failure_cause` `reward_valid`) | 集計側で利用 |
| 採点だけの再実行 | `harbor job regrade`(separate verifier が前提) | 不要 |
| タスク更新時の差分実行 | task の semver + `harbor run --diff` | 運用ルールのみ(§5.5) |
| タスク検査 | `harbor lint`(静的)、`harbor check`(rubric 審査) | スイート全体を回す CI を薄く自作 |
| 不正の検出 | `harbor analyze`(agent による判定) | rubric の整備。必要なら規則ベース検出を追加 |
| 結果の閲覧 | `harbor view` | 不要 |
| 集計 | `metric.py`(既定は平均。欠損は 0 扱い) | **自作**: CI・pass@k・pass^k・paired 比較 |
| 比較レポート | `harbor view` は閲覧が中心。モデル間のタスク別比較や統計付きの比較は未確認(G7) | **自作**(G7 の結果次第で範囲を縮める) |
| 計測の統一 | `--base-url`(job config では `agents[].base_url`)で接続先を差し替え。トークン・コストは agent と Harbor の記録(対応する agent のみ) | 記録で足りなければ **導入・運用**: LiteLLM Proxy など(任意。§4.3) |
| private タスクの生成 | 未確認 | **自作** |
| 再現性 manifest | job の `config.json` | **自作**: 版情報を追記 |
| 新モデルの自動回帰 | 未確認 | **自作**: cron / CI |

---

## 4. アーキテクチャ

### 4.1 全体像

```text
Task Suite ─┐
Agent spec ─┼─▶ Job config (+ manifest) ─▶ Scheduler ─▶ Trial × N (並列)
Model spec ─┘

Trial の流れ
  1. Sandbox 起動 (image は digest 固定)
  2. Agent 実行: instruction → 作業 → trajectory
       └─ LLM 呼び出しは(任意)LLM Gateway 経由 ─▶ Providers
          (トークン / コスト / レイテンシ / 生の request・response を記録)
       └─ Gateway を使う場合は、agent 実行中の外向き通信を Gateway のみ許可
  3. 成果物の回収 (artifacts)
  4. Verifier (別 sandbox): 成果物を新しい環境へ → hidden tests → reward
  5. 後片付け

保存: job ディレクトリ (result.json / trajectory / artifacts) + manifest.json
  │
  ├─▶ ingest ─▶ Analytics DB (DuckDB) ─▶ 集計・統計 ─▶ レポート (HTML / Markdown)
  └─▶ harbor view / harbor analyze (人手と自動による trajectory のレビュー)
```

### 4.2 評価モード(比較軸)

| モード | 目的 | 固定するもの | 変えるもの | 報告時の必須記載 |
|---|---|---|---|---|
| A. モデル比較(主) | モデルの code agent 性能 | 参照 agent(版・prompt・tools・予算) | model(snapshot ID・reasoning effort) | 参照 agent の名前と版 |
| B. 製品比較 | 各社 CLI 製品の実力 | なし(各 CLI を既定設定のまま) | agent × model | 各 CLI の版・設定 |
| C. agent 改善 | prompt / tools / 設定の A/B | model | agent の設定 | paired 比較の結果 |

モデルの順位は scaffold によって入れ替わりうる。参照 agent は最小構成(シェル実行 + ファイル編集)のものを版固定で使い、Phase 6 で 2 種類以上の参照 agent による順位の頑健性を確認する。

**Copilot 経路はモード B の 1 例**: ホストの Copilot CLI を agent にして(`bench/agents/copilot_host.py`)API キー無しで実行できるが、scaffold が Copilot CLI になる。mini-swe-agent を固定した参照 agent の結果とは同じ順位表に混ぜず、報告には「agent = copilot-host(Copilot CLI の版、ラッパープロンプトの版)」を併記する。

### 4.3 LLM Gateway(任意)

Phase 3 で、Harbor と agent の記録(`result.json`・ATIF の `final_metrics`)だけで計測と予算管理が足りるかを確認する。足りない場合に導入する。以下は導入する場合の要件。

- **目的**
  1. トークン・コスト・レイテンシ・生の request/response を、全 agent で同じ方法で記録する。
  2. 予算(ジョブ単位・trial 単位)を強制する。
  3. API キーを sandbox に置かない。期限と予算付きの仮想キーを払い出す。
  4. レート制限や 429/5xx のバックオフ・リトライを一元化する。
  5. agent 実行中の外向き通信を Gateway のみに絞る。
- **要件**: 対象 agent が使う API 形式(OpenAI Chat / Responses、Anthropic Messages、Gemini など)を受けられること。ストリーミングに対応すること。usage に cached / reasoning トークンが含まれること。
- **実装候補**: LiteLLM Proxy など。Harbor もモデル名の解釈に LiteLLM を使う。
- **Harbor との接続**
  - `--base-url`(job config では `agents[].base_url`。Phase 0 で確認した実際のキー名)で接続先を指定する。mini-swe-agent で `config_file` や `litellm_model_registry` を併用する場合は `--api-format`(job config では `model_api_format`)も明示する。指定しないと `--base-url` が無効になる。
  - `task.toml` の `[agent]` を `network_mode = "allowlist"` にする。接続先ホストは Harbor が自動で許可する(条件あり。`--allow-agent-host` で明示もできる)。Phase 0 のモックで、`allowlist` 下でもホスト上の `172.17.0.1` に自動許可で届くことを確認した。
  - agent が接続先を自前で決める場合があるため、agent ごとに `--base-url` が効くかを確認する。
- **このサイトでは private タスクに必須(Phase 0 の実測)**: 外向きは社内プロキシ経由のみで、`allowlist` の sidecar は許可ホストへプロキシを迂回して直接接続するため、許可した外部 LLM API にも届かない。ホスト上の Gateway(docker0 の IP など)だけを許可し、Gateway から社内プロキシ経由でプロバイダに出る構成にする。公開タスクなら `public` モードでプロキシ経由の API 呼び出しが使える(`proxy-u` は LLM API 宛の TLS を傍受するため、HTTPS 用のプロキシだけを傍受しないものに `--ae` で上書きする。HTTP も上書きすると apt が止まる)。詳細は [reports/phase0.md](reports/phase0.md) の F3。
- **Gateway を使わずに済む経路(Phase 0 で確認)**: モデルをホストで動かし、サンドボックスにはツール呼び出しだけを橋渡しする外部型 agent なら、サンドボックスの外向き通信は不要で、`allowlist`(許可ホスト 0)で完全に遮断できる。Copilot 経路(`bench/agents/copilot_host.py`)がこの形で、API キーもサンドボックスに入らない(F8)。ただし計測は Gateway ではなく Copilot CLI の使用量ファイルに頼る(プレミアムリクエスト・トークン・nano-AIU)。
- **フォールバック**: Gateway を通せない agent は ATIF の `final_metrics` で代替し、計測精度が異なる旨をレポートに注記する。
- **検証(Phase 3)**: Gateway の集計と trajectory のトークン合計が、説明可能な範囲(目安 ±5%)で一致すること。

### 4.4 リポジトリ構成案と想定 CLI

```text
code_agent_test/
├── PLAN.md
├── tasks/            # Harbor 形式のタスク。dev/ と heldout/ を分ける(heldout は private)
├── suites/           # 評価セット定義(タスク一覧 + 版 + 除外基準)
├── configs/          # Harbor job config(版管理)
├── bench/            # 自作コード(薄いラッパー・検証・集計・レポート)
├── gateway/          # LLM Gateway の設定(導入する場合)
├── jobs/             # 実行結果(git 管理外)
└── reports/
```

想定する CLI は次のとおり(薄いラッパー)。まず job config の版管理とスクリプト数本から始め、足りなくなってから CLI 化する。

| コマンド | 役割 |
|---|---|
| `bench validate-tasks <dir>` | 品質ゲート Q1〜Q5(§5.3) |
| `bench run <suite>` | job config の生成、manifest の書き出し、`harbor run` の起動 |
| `bench ingest <job>` | job ディレクトリを DuckDB に取り込む |
| `bench report <job...>` | 統計とレポートの生成 |
| `bench canary` | 固定サブセットの定期実行 |

---

## 5. タスク設計と品質保証

### 5.1 タスクの構成

```text
tasks/<name>/
├── instruction.md        # agent に渡す自己完結した指示
├── task.toml             # 設定とメタデータ
├── environment/
│   └── Dockerfile        # agent 用。FROM は digest 固定、依存は image に焼き込む
├── tests/
│   ├── Dockerfile        # 採点用 image(hidden tests を焼き込む)
│   ├── test.sh           # 成果物の検疫 → テスト実行 → reward 出力
│   └── ...
└── solution/
    └── solve.sh          # oracle 解(複数解を推奨)
```

`task.toml` の例(フィールドは Harbor の schema 1.4 に沿う。`[metadata]` は自由記述で、以下は本計画の規約)。

```toml
schema_version = "1.4"
artifacts = [
  { source = "/app", destination = "app", exclude = [".git", "__pycache__", "node_modules"] },
]

[task]
name = "myorg/fix-parser-off-by-one"
description = "パーサの off-by-one を修正する"
keywords = ["python", "bugfix"]

[metadata]
category = "bugfix"        # bugfix / feature / refactor / test / setup / multilang
language = "python"
difficulty = "medium"      # 初期値は著者の主観。運用後は実測の通過率で見直す
source = "internal"        # internal / public-bench / handwritten
split = "heldout"          # dev / heldout
canary = "<canary-guid>"

[agent]
timeout_sec = 1800
user = "agent"             # 非 root で実行
network_mode = "allowlist" # 推論先(モデル API または Gateway)は Harbor が自動で許可。他に必要なホストは allowed_hosts へ

[verifier]
timeout_sec = 600
environment_mode = "separate"  # 採点は別 sandbox(regrade 可)
network_mode = "no-network"

[environment]
cpus = 2
memory_mb = 4096
storage_mb = 10240
network_mode = "public"    # 起動と agent セットアップ用の基準。agent 実行中は [agent] が上書きする
```

### 5.2 採点設計(コード修正タスク)

1. 採点は agent と別の sandbox で行う(`environment_mode = "separate"`)。採点用 image に hidden tests を焼き込み(`tests/Dockerfile`)、agent の image には tests も solution も入れない(`harbor lint` の `agent-image-test-leak` で検査する)。
2. agent の成果物は `artifacts` で明示的に受け渡す。**成果物は信頼できない入力**として扱い、共通ヘルパー(`test.sh` から呼ぶ)が次を行う。
   1. 許可パス(ソースコード)以外の変更を破棄する。
   2. テスト本体・`conftest.py`・テスト/ビルド設定を、採点側の信頼できるコピーで上書きする。
   3. 提出コードの実行は低権限ユーザーで行う(`rewardkit` の `Worker` など)。
   4. `reward.json` は採点側だけが書く。
3. reward は resolved(fail-to-pass が全て通り、pass-to-pass が壊れていない)を 0/1 で出す。部分点(fail-to-pass の通過率など)は `reward.json` の別キーに併記する。
4. **検証済み(Phase 0)**: ディレクトリを成果物にすると、Harbor は採点側の復元先を空にしてから展開するため、削除・リネームは反映される(verifier image に元コードを焼き込んでも反映されることを実験で確認)。注意点は 2 つ。(a) 単一ファイルを成果物にすると、削除されたファイルは収集できず採点側の元ファイルが残る。ディレクトリ単位で渡す。(b) `exclude` したもの(`node_modules` など)は採点側の `/app` から消える。依存は `/app` の外に置くか、除外しない。
5. 採点に必要な成果物と `exclude` は初回から決める。regrade は収集済みの成果物が前提で、`exclude` の変更を許さない。
6. artifact の収集失敗は trial を失敗にせず manifest に記録されるだけである。集計前に `artifacts/manifest.json` の status を検査し、`failed` / `empty` を `harness_error` として分離する。放置すると agent の 0 点に紛れる。
7. 制約: 採点 sandbox は実行中プロセスやメモリ状態を引き継がない。サービスの起動を要するタスクは、成果物をファイルとして具現化するか shared verifier を使う。後者はリスクをタスクごとに評価する。

### 5.3 品質ゲート

Q1〜Q5 は CI で自動化する(`bench validate-tasks`)。Q6 は `harbor check` と人手のレビュー、Q7・Q8 は後続の Phase で実施する。

| ID | チェック | 方法 | 合格条件 | Phase |
|---|---|---|---|---|
| Q1 | 静的検査 | `harbor lint`(`--extend-select root-agent --extend-select unpinned-image` を追加) | 違反なし。例外は理由付きで ignore | 1 |
| Q2 | oracle | `-a oracle` | reward = 1.0 | 1 |
| Q3 | null | `-a nop` | reward = 0.0(fail-to-pass が実際に落ちている) | 1 |
| Q4 | 複数解 | 解が複数ある場合、各解で oracle(例: `--ae SOLUTION=N`) | 全て 1.0(採点が一つの解に過適合していない) | 1 |
| Q5 | flaky | oracle を 10 回以上、別コンテナで実行 | 全回 1.0 | 1 |
| Q6 | 仕様とテストの整合 | `harbor check`(rubric 審査)+ 人手レビュー | instruction に書かれていない要件をテストが要求していない | 1 |
| Q7 | 改ざん耐性 | verifier の単体テスト(`.harborignore` で sandbox 外に置く)と、既知の改ざんを行う疑似 agent | 疑似 agent が全て 0 点、または検出される | 6 |
| Q8 | 実測難度 | 参照 agent で試走 | 全モデルで 0% が続くタスクは仕様・環境の不備を疑い、Q6 をやり直す | 3 |

### 5.4 タスクの出どころ

| 出どころ | 用途 | 作り方 | 注意 |
|---|---|---|---|
| A. 自社リポジトリの過去 commit / issue | private heldout の主力 | テスト付き commit のソース側の変更を巻き戻し、fail-to-pass / pass-to-pass をテスト実行で確定する。instruction は issue や commit message から起こし、人手で「テストが要求する情報が全て書かれているか」を確認する | git 履歴のサニタイズが必須(§7)。問題文の曖昧さが最大のリスク |
| B. 公開ベンチ | ハーネスの校正、外部との比較 | Harbor の dataset(`terminal-bench@2.0`、SWE-bench、Aider Polyglot など)を実行する | 学習データへの混入がありうるため、絶対値の比較には使わない |
| C. 手書き | 環境構築・CLI 操作・多言語・長い作業など、穴になりやすい領域 | 著者が instruction と tests を書き、oracle で検証する | 作成コストが大きい。数を絞る |

初期の開発用ポートフォリオ案(30〜50 タスク。配分は目安で、参照 agent の試走後に実測で再配分する)。M2 の正式評価スイートはこの数に固定せず、必要な CI 幅を満たすまで拡張する(§6.4・§9.3)。

- 種別: bugfix 40% / feature 25% / refactor 10% / テスト作成 10% / 環境構築・CLI 10% / 多言語 5%
- 難度: easy : medium : hard = 3 : 5 : 2
- 分割: `dev`(開発・調整用)と `heldout`(正式評価用。調整に使わない)

汚染対策として、heldout は非公開にし、モデルの学習 cutoff 以降の commit を優先し、canary 文字列を入れ、tests と solution は private リポジトリで管理する。

### 5.5 版管理

- タスクとスイートは semver で管理する。Harbor の運用に合わせ、patch は既存結果を再利用、minor は新しい verifier で regrade、major は再実行とする(`harbor run --diff` を使う)。
- スイートの版は、含まれるタスク版の組で定義する。評価結果は必ずスイート版と紐づける。
- タスクの除外・修正は理由付きで CHANGELOG に残す。除外基準は事前に決め、結果を見てから恣意的に外さない。

---

## 6. 評価プロトコル

### 6.1 固定する条件・記録する条件

| 区分 | 項目 |
|---|---|
| 全モデル共通で固定 | instruction、参照 agent(版・prompt・tools)、予算上限(steps / $ / agent の timeout)、sandbox 資源(cpus / memory / storage)、network policy、試行回数 k、採点方法 |
| モデルごとに設定(記録必須) | model の snapshot ID(alias は使わない)、reasoning effort / thinking budget、最大出力トークン、temperature(指定できるモデルのみ) |
| 実行時に記録 | task 版、image digest、agent 版、Harbor 版、Gateway 設定、価格表の版、実行日時、本リポジトリの git SHA |

steps / $ の上限は、agent 側の設定でかける(Gateway を導入した場合は、その予算と二重にする)。**Phase 0 の実測**: Harbor 経由の mini-swe-agent は既定で `cost_limit` / `step_limit` / 時間がすべて無制限(`cost_limit="0"`)なので、必ず `--ak cost_limit=<USD>` と `--ak config_file=<step・時間の上限を書いた YAML>`(例: `configs/phase0-mini-swe-agent.yaml`)を渡す。`cost_limit` は呼び出しの前に判定するため最大 1 call 分超過し、価格が未登録のモデルでは cost が 0 になって発動しない。他の agent の対応可否は Phase 6 で確認する。

**Copilot 経路の例外(Phase 0 で確認)**: モデル ID は Copilot カタログの alias(`claude-haiku-4.5` など)で、snapshot を固定できない。上の「snapshot ID(alias は使わない)」の例外として扱い、実行日と Copilot CLI の版(`--no-auto-update` を付けるが、ホストの CLI 版の管理は別)、ラッパープロンプトの版を記録する。予算は USD ではなく**プレミアムリクエスト**で管理する。課金は 1 プロンプト = モデルの倍率 × 1 で、ツール呼び出しやモデル呼び出しの数に依存しないため、`trial 数 × 倍率` が正確な上限になる(`run_step4_copilot.sh` が倍率を測って予算超過を拒否する)。

### 6.2 trial の結果区分と集計上の扱い

| 区分 | Harbor の `failure_cause` | reward | 集計上の扱い |
|---|---|---|---|
| 成功・失敗(採点まで完了) | なし | 有効 | 分母に含める |
| 予算超過(タイムアウト、コンテキスト/出力の上限) | `agent_exhausted_budget` | 有効 | 失敗として分母に含める |
| 安全上の拒否 | `agent_refused_task` | 有効 | 失敗として分母に含める。件数は別枠で開示する |
| sandbox 障害 | `sandbox_failed` | 無効 | 自動リトライ。解消しなければ分母から除外し件数を開示する |
| モデル API 障害(レート制限・5xx・接続断など) | `model_provider_failed` | 無効 | 自動リトライ(上限あり)。解消しなければ除外し、0 点扱いの感度分析を併記する |
| 採点失敗 | `verifier_failed` | 無効 | `harbor job regrade` で再採点。解消しなければ除外する |
| ハーネス不具合・未分類 | `harbor_failed` / `unclassified` | 無効 | 原因調査が済むまで集計に含めない。件数を開示する |
| 成果物の収集失敗 | なし(manifest の status が `failed` / `empty`) | 要判定 | `harness_error` として分離する(§5.2) |
| キャンセル | `cancelled` | 無効 | 再実行する |

除外が特定のモデルに偏ると、信頼性の低いモデルほど有利になり比較が歪む。除外率はモデル別に必ず開示し、偏りが大きい場合は 0 点扱いの結果を併記する。自動リトライは infra / provider / verifier の障害に限り、agent の失敗にはかけない。

**Phase 0 で判明した補足(区分の判定に Harbor の `failure_cause` だけを使わない)**

- agent 内部の失敗が Harbor には別の形で見える。Harbor 0.25.0 + mini-swe-agent は、litellm の不具合で LLM を一度も呼べないまま、バックオフ再試行を続けて `AgentTimeoutError`(= `agent_exhausted_budget`、reward 有効)として記録した。そのまま集計すると agent の失敗に混ざる。**trajectory も入力トークンも無い trial は `harness_error` 候補として分離する**(`bench/phase0/summarize_job.py`)。
- agent の step / cost 上限による停止は、例外なしの通常終了として記録される(`failure_cause` は付かない)。「予算超過」は trajectory の `info.exit_status == "LimitsExceeded"` で判定する。
- `failure_cause` と `reward_valid` は `result.json` に保存されず、`TrialResult` の property として `exception_type` から計算される。取り込み側で Harbor の Python モデルを使うか、対応表を持つ。

### 6.3 指標

- **主指標**: resolve rate(= pass@1。タスクごとの成功率の平均)
- **安定性**: pass@k(k 回のうち 1 回でも成功)と pass^k(k 回すべて成功)。実運用では pass^k が効く
- **コスト**: $/trial、$/解決(総コスト ÷ 解決数)、トークン(入力・出力・cached・reasoning)
- **時間**: trial あたりの wall-clock、LLM 待ち時間の比率
- **挙動**: steps 数、tool error 率、予算超過率、拒否率
- **補助**: 部分点(fail-to-pass の通過率)、diff の大きさ
- **内訳**: カテゴリ別・言語別・難度別の resolve rate。N が小さい層は CI 幅を併記する

参考: Terminal-Bench 4.0 の公式 leaderboard(Harbor Hub)も、accuracy ± 95% CI、pass@k(k=2〜5)、トークン(cached / uncached / output)、総コスト、平均 trial 時間、reasoning effort を載せている。公開ベンチ校正(S3)の候補になる。ただし確認時点ではタスク別結果を参照できなかったため、タスクごとの paired 差を計算できない場合は、報告されている区間から差の区間を作れるかを確認し、作れなければ比較不能とする。

### 6.4 統計

タスクが標本単位で、同一タスクの試行はクラスタを成す。タスクごとに n 回試行して c_t 回成功したとき、次の推定量を使う(出典: [Chen et al. 2021](https://arxiv.org/abs/2107.03374)、[τ-bench](https://arxiv.org/abs/2406.12045))。

```text
pass@1 = mean_t( c_t / n )
pass@k = mean_t( 1 - C(n - c_t, k) / C(n, k) )   # 1 回でも成功(k <= n)
pass^k = mean_t( C(c_t, k) / C(n, k) )           # k 回すべて成功(k <= n)
```

- 信頼区間は**タスク単位のクラスタ bootstrap**(タスクを復元抽出)で求める。
- モデル A/B の比較は**同一タスクの paired**(タスクごとの差の平均を bootstrap)で行う。
- S2 の再現性確認では、各タスクの2回分の pass@1 差を対応づけてからタスクを復元抽出する。試行ノイズも含めるため、各選択タスク内では各 run の k 試行をそれぞれ復元抽出する。95% CI 全体が ±10pt 内に入った場合のみ S2 合格とし、0 を含むだけでは合格にしない。
- S3 はタスク別公表値があれば paired 差を使う。集計値しかない場合、ローカルと公表値の不確実性から差の 95% CI を計算できるときに限って判定する。比較条件が揃わない場合や区間を作れない場合は「比較不能」とする。
- 多数のペアを比較するときは、p 値の羅列ではなく、差の CI と順位の bootstrap 分布を示す。

**サンプルサイズの目安**(k=1・二値の場合。表の値は 95% CI の半幅で、単位は pt)。差の列は「真の差 5pt」を仮定し、不一致率は「一方だけが解けるタスクの割合」を指す。

| タスク数 N | 単一モデル(正答率 50%) | 差(不一致率 10%) | 差(不一致率 20%) | 差(不一致率 30%) |
|---|---|---|---|---|
| 50 | ±13.9 | ±8.7 | ±12.3 | ±15.1 |
| 100 | ±9.8 | ±6.1 | ±8.7 | ±10.7 |
| 200 | ±6.9 | ±4.3 | ±6.2 | ±7.6 |
| 300 | ±5.7 | ±3.5 | ±5.0 | ±6.2 |
| 500 | ±4.4 | ±2.7 | ±3.9 | ±4.8 |
| 1000 | ±3.1 | ±1.9 | ±2.8 | ±3.4 |

- 5pt の差を検出力 80% で検出するのに必要なタスク数は、不一致率 10% / 20% / 30% でそれぞれ約 306 / 620 / 934。検出力 50%(CI がちょうど 0 を外れる水準)なら約 150 / 303 / 457。
- k を増やすと試行ノイズは減るが、タスク抽出に由来する分散は減らない。N が小さいと k を増やしても CI はあまり縮まない。
- N が足りないときは「差は有意とは言えない」と明記し、順位表を作らない。
- 統計コードは、既知の分布で生成したデータで CI の被覆率(約 95%)を確認する単体テストを付ける。

### 6.5 レポートの必須項目

- スイート名・版・タスク数(split 別)
- agent(名前・版)、model snapshot、reasoning effort、k、予算上限
- 除外した trial 数(区分別・モデル別)と、0 点扱いの感度分析
- CI の手法と信頼水準、Harbor 版、実行期間、既知の問題
- 可視化: 精度 × コスト / 時間の散布図(誤差棒つき)、タスク × モデルのヒートマップ、結果区分の内訳、pass@1 と pass^k の並置
- 単一スコアのランキング表だけにしない

---

## 7. 妥当性と不正対策

Harbor の分類(Tampering / Cheating / Loopholes)に、本計画で必要な項目を加えた。

| 脅威 | 例 | 対策 | 手段 |
|---|---|---|---|
| Tampering(採点の改ざん) | テストの削除・改変、`conftest.py` などで常に pass、reward ファイルの直接書き込み、symlink で参照解を指す、setuid バイナリ | 別 sandbox で採点。成果物を検疫する(Harbor が symlink・特殊ファイル・setuid を除去)。テストと設定は信頼できるコピーで上書き。agent は非 root。提出コードは低権限で実行。採点の開始時に reward ファイルを削除する(Harbor の既定) | Harbor + 採点ヘルパー(自作) |
| Cheating(答えの入手) | `git log --all` で未来の修正を見る([SWE-bench #465](https://github.com/SWE-bench/SWE-bench/issues/465))、Web で解答を検索、tests や solution が image に混入 | git 履歴のサニタイズ(下記)。agent 実行中は allowlist(推論先のみ)にし、制限時は組込みの Web 検索も無効化される(Harbor)。`agent-image-test-leak` で検査 | Harbor + スクリプト(自作) |
| Loopholes(解かずに通す) | 期待出力のハードコード、特定の実装への過適合 | 振る舞いを検証する hidden tests、入力のバリエーション、複数解での検証(Q4)、`harbor analyze` + 人手確認 | タスク設計規約 + Harbor |
| 汚染 | 問題や解が学習データに含まれる | private heldout、学習 cutoff 以降の commit、canary、公開ベンチは校正用に限定 | 運用 |
| flaky | 時刻・乱数・ネットワーク・並列への依存 | 依存と image の固定(`unpinned-image` `unpinned-python-dependency`)、oracle の複数回検証(Q5)。採点の自動リトライはしない | CI |
| 計測のずれ | 価格表やキャッシュ/推論トークンの扱いの誤り、収集失敗が 0 点に化ける | 価格表の版管理、Gateway と trajectory の相互検証、manifest の検査 | 自作 |
| scaffold 依存 | 参照 agent との相性でモデルの順位が変わる | 2 種類以上の参照 agent で順位の頑健性を確認。常に scaffold を併記 | Phase 6 |
| バージョンドリフト | alias が指すモデルの変更、CLI の自動更新、image の更新 | snapshot ID、agent 版の固定(image への焼き込みと `skip_install`)、digest 固定、定期 canary | 運用 |
| 評価環境の認識 | agent が評価と気づいて挙動を変える | Harbor が中立的な名前・パスを使う。完全には防げない前提で、報告に注記する | Harbor |

**git 履歴のサニタイズ**(動作確認済みの手順。問題の base commit に切り替えたあと、agent に渡す前に実行する)。SWE-bench の #465 では、未来の commit・branch・origin・reflog が修正の漏洩経路になっていた。

```bash
git checkout --detach "$BASE_COMMIT"
git for-each-ref --format='%(refname)' | xargs -r -n1 git update-ref -d   # branch / tag / remote-tracking を全削除
git remote | xargs -r -n1 git remote remove
git reflog expire --expire=now --all
git gc -q --prune=now                                                      # 未来の commit の実体も削除
```

使い捨てのリポジトリで検証し、実行前は `git log --all` に未来の commit が見え、実行後は base のみになり、未来の commit のオブジェクトも消えることを確認した(pack 済みの場合も同様)。

---

## 8. データ・運用

### 8.1 結果の保存と再現性

- 一次データは Harbor が生成する job ディレクトリ(`config.json`、trial ごとの `result.json`・`agent/trajectory.json`・`artifacts/manifest.json` など)とし、加工しない。
- 自作で追加するもの
  - `manifest.json`: suite 名と版、task 一覧と版、image digest、agent の名前と版、model の snapshot と各パラメータ、Harbor 版と本リポジトリの git SHA、価格表の版(Gateway を使う場合はその設定も)、予算上限、ホスト情報(OS / カーネル / Docker 版)、開始・終了時刻
  - (Gateway を使う場合)Gateway の生ログ(request/response と usage)
  - `analytics.duckdb`: 集計用のテーブル(trials・steps・llm_calls)。`bench ingest` で job ディレクトリから作る
- Harbor は 0.x で、minor 版(`0.x.0`)に破壊的変更がありうる(stable は概ね隔週、nightly は毎日)。stable 版を固定し、アップグレードは oracle/nop と校正サブセットの回帰を通してから行う。

### 8.2 コストと所要時間の見積り

```text
総 trial 数 = タスク数 × (agent, model) の組数 × k
総コスト   ≈ Σ_(agent, model) [ タスク数 × k × 1 trial あたりの平均コスト ] + Gateway / sandbox の費用
所要時間   ≈ 総 trial 数 × 1 trial あたりの平均時間 ÷ 並列度
```

1 trial の単価はタスクとモデルで桁が変わるため、固定値を置かず、§0.3 の Step 4 で実測する。参考値として、Terminal-Bench 4.0 の公式 leaderboard(2026-11-05 時点、先頭行: Opus 5.5 / Claude Code)は、330 trial で $4,718.65、平均 4,790 秒(約 80 分)、つまり約 $14/trial・約 80 分/trial だった。タスクの性質が違うため直接は比べられないが、上限側の目安になる。

例(幅で見る): 100 タスク × 4 モデル × k=3 = 1,200 trial。1 trial が $1〜$14 なら総額は約 $1,200〜$17,000。並列度 20 で 1 trial が 15〜80 分なら、所要時間は約 15〜80 時間(1,200 × 分 ÷ 20 ÷ 60)。

M2 の S2 確認は、100 タスク × 1 モデル × k=3 × 独立 2 run なら 600 trial(概算 $600〜$8,400)。Phase 3 の 10 タスク pilot で実単価を確かめてから、正式評価の予算を決める。S3 の公開ベンチ実行費用は別途見積もる。

- 日常の確認は dev サブセット(例: 20 タスク × k=1〜2)で行い、フル実行は節目だけにする。
- タスクの更新時は `harbor run --diff` で変更分だけ再実行・再採点する。
- trial 単位の上限(agent の設定)をかける。Gateway を導入した場合は、ジョブ単位の予算(仮想キー)も併用する。
- 少数の trial で実測単価を出してから、全体を見積もる(§0.3 の Step 4)。

### 8.3 データガバナンス

- API キー: sandbox に長期のキーを置かない。専用の(失効できる)キーを使い、プロバイダ側で利用上限を設定する。Gateway を導入した場合は、期限と予算付きの仮想キーに切り替える。
- 外部送信: Harbor Hub のアップロードや hosted jobs は、private が既定でも外部サービスへの送信にあたる。private タスクや社内コードを含む結果は、方針が決まるまで使わず、ローカルの `harbor view` で閲覧する。**ただし `harbor view` の API は無認証**で、ジョブ起動・削除・Hub 送信・LLM 解析(`harbor analyze`)を含む(Phase 0 で確認)。共有サーバーでは同一ホストの他ユーザーも `127.0.0.1` に接続できるため、常時起動せず、必要なときだけ起動して即停止するか、`jobs/` を手元の PC に同期して閲覧する。`--host 0.0.0.0` は使わない。
- `harbor check` / `harbor analyze` は、タスクのソースと trial のログ・trajectory を LLM に渡す。既定は agent = claude-code、モデルは check = claude-sonnet-4-6、analyze = claude-haiku-4-5(`--help` で確認。どちらも未実行)。使用するモデルと送信範囲を方針に沿って選ぶ。
- テレメトリ: Harbor の usage stats(PostHog。agent・モデル・トークン・コストなどを送る)は既定で有効。**`HARBOR_TELEMETRY=off` で 3 つの送信経路すべてが止まる**ことをコードで確認した(Phase 0)。全実行で off にする(`bench/env.sh`)。
- 保管: trajectory や生ログには社内コードや秘密情報が含まれうる。保存先のアクセス制御と保持期間を決める。
- ライセンス: 公開ベンチと各 CLI の利用条件を確認する。

---

## 9. ロードマップ

### 9.1 フェーズ一覧

見積りは 1 名の工数の目安(S = 1〜2 人日、M = 3〜5 人日、L = 6〜10 人日)で、タスク作成の工数は含まない。Path C は、Harbor 相当の機能(sandbox 実行・並列・リトライ・失敗分類・採点分離)を自作する場合。

| Phase | 名称 | 目的 | 見積り Path A | 見積り Path C | 依存 | 着手の条件 |
|---|---|---|---|---|---|---|
| 0 | 基盤検証と判断ゲート(= §0.3 / 付録 B) | 1つの環境・参照 agent・2モデルで最小経路を通し、Harbor を採用するか決める | S〜M | S | なし | 最初に着手 |
| 1 | タスク基盤 | タスク規約、初期 10 タスク、品質ゲート Q1〜Q6 | M | M | 0 | G0〜G4 を満たし、G7 の扱いを記録した後 |
| 2 | 実行パイプライン | 1 コマンド実行、manifest、oracle/nop の E2E | S | L | 1 | Phase 1 の後 |
| 3 | 参照 agent・コスト計測(Gateway は必要なら) | 1 モデルで通し、計測を統一する | M | M | 2 | Phase 2 の後。Gateway は記録が不足する場合のみ |
| 4 | スケールと堅牢化 | 並列・リトライ・予算・障害注入 | S | L | 3 | 数百 trial 以上に増やすとき |
| 5 | 集計・統計・レポート・校正 | CI、pass^k、paired 比較、公開ベンチでの校正 | M〜L | M〜L | 3 | 結果を比較・共有する段階。G7 が OK なら縮小 |
| 6 | 外部 CLI 比較・分析強化 | CLI の追加、scaffold の頑健性、改ざん耐性 Q7 | M | L | 5 | 製品比較が目的になったとき |
| 7 | 運用化 | クラウド sandbox、heldout の運用、新モデルの自動回帰 | M〜L | M〜L | 4, 5 | 定期運用が必要になったとき |

**着手の順序**: Phase 0〜2 を先に行う(Path A で約 5〜12 人日)。Phase 3 以降は、Phase 0〜2 の結果(実行環境、1 trial の単価、Viewer で足りるか)を見て、必要なものから着手する。以下の見積りと完了条件は、全部やる場合の目安である。

全部やる場合の合計の目安は Path A が約 18〜44 人日、Path C が約 31〜62 人日。タスク作成は別枠で、仮に 1 件 0.5〜1 人日(作成と検証)とすると、50 件で 25〜50 人日になり、ハーネスより大きくなりうる。タスクの拡充は Phase 1 以降、全フェーズと並行して続ける。

### 9.2 各フェーズの作業と完了条件

**Phase 0: 基盤検証と判断ゲート(= §0.3 / 付録 B)**
- 作業: §0.3 の手順を順に行う。参照 agent は `mini-swe-agent` 1 種、比較する model は 2 種、同じ公開タスクを 1〜3 件、各 `k=2`で実行する。条件付き G3 を含む agent trial は最大 12 件とする。開始前に総額上限を決める。外部送信・テレメトリ方針を確認し、Harbor の stable 版を固定する。
- 成果物: G0〜G4 の OK/NG 表、G7 の Viewer 判定、1 trial あたりの実コストと時間、Path の決定、版固定の記録。
- 完了条件: G0〜G4 が判定済み(G3 は条件該当時)、G7 の結果と後続作業への影響が記録され、決定が §3.2 に反映されている。G5 は Phase 5、G6 は private タスクの実行前に確認する。

**Phase 1: タスク基盤**
- 作業: タスク規約(`[metadata]`・命名・split)、共通 base image(digest 固定)、git サニタイズのスクリプト、採点ヘルパー(§5.2)、初期 10 タスク(種別を混ぜる)、`bench validate-tasks`(Q1〜Q5)の CI 化、Q6 のレビュー手順。
- 完了条件: 10 タスク全てで Q1〜Q6 に合格。削除・リネームの反映(§5.2 の 4)を検証済み。
- 実装入口: [初期10問の規約・検証手順](tasks/README.md)、[スイート登録](suites/dev-v1.json)、`bash bench/validate-tasks`。共有 grader とビルドテンプレート、履歴なしソース export、GitHub Actions 設定を追加。Q6 は独立レビューと承認後の `harbor check` を要し、自動テスト合格だけで完了とはしない。G4 等の残件を正式承認したという意味ではなく、開発タスク整備を先行している。
- 2026-11-05 の実機結果: **Q1〜Q5 は初期10問で合格**。nop 10/10 が0点、別解10/10が1点、各問の正解10回(計100回)が全て1点。固定スナップショットと全試行の checksum 一致を確認。[Phase 1 レポート](reports/phase1.md) に記録。Q6 と GitHub 上での CI 実行は未完了。
- 独立AIレビューで5問の仕様・テストを修正し、suite 1.1.0 に更新。修正5問は nop 5回・別解5回・正解50回を再検証して全件期待どおり。変更なしの5問は旧スナップショットとの一致を確認。人手承認と `harbor check` は別の残件として維持。

**Phase 2: 実行パイプライン**
- 作業: job config の版管理、`manifest.json` の生成、結果の置き場と規約、oracle/nop の E2E を CI に載せる。
- 完了条件: 1 コマンドで実行から結果・manifest の生成まで進み、中断しても再開できる(S4 の前半)。
- 実装・実機結果: `bash bench/run --config configs/dev-oracle.json` と `--resume <名前>` を追加。固定入力・設定・manifest・summary を1か所に保存し、実コンテナで中断→再開→完了済み再開の無操作、および nop の0点を検証した。[手順](bench/PIPELINE.md)、[結果](reports/phase2.md)。CI は設定済みだが GitHub 上の実行は未確認。新ラッパーの有料経路は実行しておらず、API 直接経路は従来の入口を維持する。

**Phase 3: 参照 agent・コスト計測(Gateway は必要なら)**
- 作業: 参照 agent の確定と版固定、価格表の版管理、1 モデルで 10 タスク × k=3 の pilot を通す、Q8(実測難度)。Harbor と agent の記録でトークン・コストが足りない場合は、Gateway の導入、接続先の切替、外向き通信の制限も行う。
- 完了条件: pilot が完走し、1 trial の平均コストと時間を実測して §8.2 を更新する。これは運用・費用見積り用で、S2 や M2 の達成判定には使わない。Gateway を導入した場合は、その集計と trajectory のトークン合計が説明可能な範囲(目安 ±5%)で一致する。

**Phase 4: スケールと堅牢化**
- 作業: 並列度とリトライ方針(`-n` `-r`、`retry` の include/exclude)、レート制限対策、ジョブの予算上限、障害の注入(コンテナの強制停止、429、不正なキー、artifact 収集の失敗)。
- 完了条件: 障害を注入しても job が完走し、各失敗が §6.2 の区分に正しく入る。

**Phase 5: 集計・統計・レポート・校正**
- 作業: `bench ingest`(job → DuckDB)、`metric.py`(`reward_valid` を考慮)、統計(bootstrap・pass@k・pass^k・paired)、レポート(§6.5)。S2 を固定した評価スイートで判定し、S3 の外部校正可能性を確認する。
- 完了条件: S2 に合格する。S3 は「校正済み」「不一致/判定保留」「比較不能」のいずれかと根拠を記録する。S3 が校正済みでない場合は外部校正済みと主張しない。CI 付きレポートが 1 コマンドで生成され、統計コードの被覆率テストが通る。

**Phase 6: 外部 CLI 比較・分析強化**
- 作業: 比較対象 CLI の追加(事前統合 agent を使い、未対応のものだけ `BaseInstalledAgent` で実装)。参照 agent を 2 種に増やして順位の頑健性を確認する。`harbor analyze` の rubric(reward hacking / 仕様不備)を整備する。Q7 の疑似 agent を作る。必要なら規則ベースの監査(`git log --all`・`git reflog`・`/tests` の参照などの検出)を追加する。
- 完了条件: 対象 CLI の全てで ATIF の trajectory が得られる。Q7 の疑似 agent が全て 0 点または検出される。

**Phase 7: 運用化**
- 作業: クラウド sandbox(`-e`)、heldout の運用(アクセス制御・非公開 tests)、新モデル公開時の自動回帰(cron / CI)、定期 canary(固定サブセットを同一設定で定期実行し、プロバイダ側の変化を検知する)、スイートの更新計画(飽和対策)、ドキュメント整備。
- 完了条件: 新モデルの追加から評価・レポートの公開までが、1 操作で完了する(S4 の後半)。

### 9.3 マイルストーン

| MS | 到達点 | 含むフェーズ | 完了条件 |
|---|---|---|---|
| M1 | 動く最小ベンチ | 0〜2 | 10 タスクが oracle/nop で E2E 動作し、結果と manifest が揃う |
| M2 | 対象スイート内で信頼できる単一モデル評価 | 3〜5 | 1 モデルで固定スイートを k≥3、独立2回実行する。pass@1 の 95% CI 半幅が ±10pt 以下で、run 間差の 95% CI が ±10pt 内に完全に入り(S2 合格)、S3 の状態も明記する。正答率50%付近なら約100タスクが目安で、実際の件数は pilot の分散で決める。S3 が校正済みでなければ外部校正なしと報告する |
| M3 | 複数モデル比較の運用 | 6〜7 | 新モデルの追加から自動評価・レポートまでが 1 操作で完了する |

---

## 10. リスクと対策

評価の妥当性に関わるリスクは §7 に記載した。ここではプロジェクト上のリスクを扱う。

| リスク | 影響 | 対策 |
|---|---|---|
| 実行環境(Docker の権限)が確保できない | 運用環境の準備が遅れる。Phase 0 の環境は確保済み(2026-11-05) | §0.3 の Step 0 の 4 択。社内タスクの運用環境は管理者と並行して調整する。現ホストは compose / buildx が無く、root なしでユーザー領域に導入した(§1.4) |
| タスク作成が律速になる | 規模・多様性の不足 | 半自動生成と公開ベンチの併用。品質ゲートの自動化で手戻りを減らす。初期 30〜50 件は開発用とし、M2 は必要な CI 幅を満たす評価スイートを別途用意する |
| 自作範囲の肥大化 | 保守負担の増加 | 「Harbor にあれば使う」を原則にし、自作は §3.3 の範囲に限る。追加は各 Phase の終わりにレビューする |
| Harbor の仕様変更・破壊的変更 | 再現性の低下、CI の破損 | stable 版の固定。アップグレードは回帰テストを通してから。アダプタ層を薄く保つ |
| 実行コストの超過 | 予算の逸脱 | 少数の trial で実測してから規模を決める(難しいタスクでは約 $14/trial の例がある。§8.2)。ジョブ予算の上限。dev サブセットでの日常運用 |
| ベンチの飽和・識別力の低下 | モデル差が見えなくなる | 難度分布の監視、難問の定期追加(スイート版の更新)、新しい heldout の継続作成 |
| プロバイダのレート制限・障害 | 実行の遅延・欠損 | Harbor のリトライ設定(Gateway を導入した場合はその一元的なバックオフ)、限定リトライ、除外数の開示 |
| 結果の過信 | 誤った意思決定 | CI を必須にし、N が不足なら順位を出さない。scaffold と条件を併記する |

---

## 11. 未決事項(要確認)

| # | 項目 | 影響する箇所 |
|---|---|---|
| 1 | **最優先**: Step 4 の実行入力。Copilot 経路(API キー不要): 比較する 2 モデルの ID(`copilot --model` の値)と、予算上限 `BUDGET_PREMIUM_REQUESTS`。API キー経路: 2 モデルの ID(snapshot 付き)、専用 API キー、総額上限 `BUDGET_USD`。対象モデル・プロバイダと月額の予算上限も合わせて決める | Phase 0 の完了(G2・G2′)、§8.2、Phase 3・7 |
| 2 | 対象の言語とタスクの種類(自社リポジトリの言語構成) | §5.4、Phase 1 |
| 3 | 結果の共有範囲(社内限定か公開か)、Hub の利用可否 | §8.3 |
| 4 | ~~実行環境~~ **解決(2026-11-05)**: `docker` グループで Docker が使えるホストで Phase 0 を実施した。残る論点は、社内タスクを扱う運用環境(Gateway 前提。§4.3)と、外部へ送るデータの区分 | Phase 4・7 |
| 5 | 比較したい agent 製品(CLI)の一覧 | Phase 6 |
| 6 | 基準線とする参照 agent | Phase 0 は `mini-swe-agent` を使う。動作しない場合だけ `terminus-2` を代替候補にする。Phase 0 では mini-swe-agent が動作した(litellm の不具合には日付固定の回避策が必要。F1) |
| 7 | 体制と期限 | §9.1 の見積り |
| 8 | G4 の方針承認: (a) LLM API 宛の TLS を傍受するプロキシの扱いと、コンテナに使わせるプロキシ、(b) Viewer の運用方法(無認証 API)、(c) `harbor check` / `analyze` の送信範囲 | G4 の確定、§8.3、Path A の確定 |
| 9 | Harbor の stable 更新の方針: `litellm[mcp]` の修正は 0.25.1 の nightly にのみある。stable に入るまで日付固定の回避策を使うか、nightly を使うか | §8.1、F1 |
| 10 | Copilot 経路の運用方針: (a) ベンチ専用の Copilot プロファイル(`COPILOT_HOME`。device code でログインするだけで、キーの入力は不要)を作るか(F12。推奨)、(b) Copilot 経路の結果を mini-swe-agent の参照 agent の順位とどう分けて報告するか(モード B)、(c) 組織の Copilot CLI ポリシーと、自動実行でのクォータ消費の承認 | Phase 0 の完了、§4.2、§6.1 |

---

## 付録 A. 自作する場合(Path C)のインターフェース

Path C を選ぶ場合も、タスク形式は Harbor 互換にしておく(後から Harbor へ移行できる)。自作ランナーが最低限持つべき機能は次のとおり。

1. trial 単位の隔離実行
2. 失敗の原因分類と、infra / provider / verifier に限ったリトライ
3. 採点の分離と再採点
4. 全 trial への版情報の付与
5. 中断と再開(`trial_id = hash(task 版, agent, model 設定, attempt)` で冪等にする)

```python
from typing import Protocol


class Environment(Protocol):
    async def start(self, task: "Task") -> None: ...
    async def exec(self, cmd: str, timeout: int) -> "ExecResult": ...
    async def upload(self, src: str, dst: str) -> None: ...
    async def download(self, src: str, dst: str) -> None: ...
    async def stop(self) -> None: ...


class Agent(Protocol):
    async def run(
        self, instruction: str, env: Environment, llm: "LLMEndpoint", limits: "Limits"
    ) -> "Trajectory": ...


class Verifier(Protocol):
    async def verify(self, task: "Task", artifacts: str) -> "Reward": ...
```

---

## 付録 B. Phase 0 検証チェックリスト

```bash
# Step 0: 実行環境の確認(Docker を使う場合)
id                                # docker グループに入っているか
docker info                       # permission denied なら §0.3 の Step 0 を先に解決する

# Step 1: 版を固定してインストールする(stable を使い、nightly は使わない。Python 3.12 以上が必要)
uv tool install "harbor==0.25.0" --python 3.12
uv tool list                      # 版を記録する
source bench/env.sh               # HARBOR_TELEMETRY=off を設定する(以降のコマンドの前に必ず)
# Docker に compose / buildx が無い場合は ~/.docker/cli-plugins/ に導入する(reports/phase0.md §2)

# Step 2 / G0: 最小の動作確認(期待値: oracle=1.0, nop=0.0)
harbor run -t hello-world/hello-world -a oracle
harbor run -t hello-world/hello-world -a nop

# Step 3 / G1: Harbor 形式の自作タスク 1 件を lint し、oracle / nop で確認する。
# 同じく公開データセットの先頭 3 件で oracle / nop を実行する。
TASK_PATH="tasks/my-smoke-task"
harbor lint "$TASK_PATH"
harbor run -t "$TASK_PATH" -a oracle
harbor run -t "$TASK_PATH" -a nop
harbor run --dataset terminal-bench@2.0 -a oracle -l 3
harbor run --dataset terminal-bench@2.0 -a nop -l 3

# Step 4 / G2: mini-swe-agent 1 種を 2 モデル、同じタスク 1〜3 件、k=2 で実行(最大 12 trial)
# 下の素のコマンドは予算上限が無効で、Harbor 0.25.0 では litellm の不具合にも当たる(reports/phase0.md の F1・F2)。
# 実行は上限・回避策・プリフライトを組み込んだスクリプトを使う:
#   DRY_RUN=1 bench/phase0/run_step4.sh                  # 計画の確認のみ
#   CONFIRM_SPEND=yes bench/phase0/run_step4.sh          # MODEL_A / MODEL_B / BUDGET_USD が必須
export MODEL_A="<provider/model-snapshot-id>"
export MODEL_B="<provider/model-snapshot-id>"
harbor run --dataset terminal-bench@2.0 -a mini-swe-agent -m "$MODEL_A" -l 3 -k 2
harbor run --dataset terminal-bench@2.0 -a mini-swe-agent -m "$MODEL_B" -l 3 -k 2

# Step 4 / G2′(API キーが無い場合): ホストの Copilot CLI を agent にする Copilot 経路(実機で確認済み。reports/phase0.md の §3.4・F8〜F12)
# 認証は Copilot CLI の既存ログインで、キーの入力もサンドボックスへの受け渡しも無い。課金の単位はプレミアムリクエスト。
#   export MODEL_A=...  MODEL_B=...  BUDGET_PREMIUM_REQUESTS=...    # MODEL は `copilot --model` の値(例: claude-haiku-4.5)
#   DRY_RUN=1 bench/phase0/run_step4_copilot.sh                      # 計画の確認のみ(モデルを呼ばない)
#   CONFIRM_SPEND=yes bench/phase0/run_step4_copilot.sh              # プローブ → プリフライト → 本番(最大 12 trial)
# 素のコマンド(1 タスク):
PYTHONPATH="$PWD" harbor run -p tasks/dev/smoke-fix-calc -a bench.agents.copilot_host:CopilotHostAgent -m "$MODEL_A"

# G3(Gateway やローカル推論を使う場合のみ): 接続先を差し替える(config_file 併用時は --api-format も必要)
harbor run --dataset terminal-bench@2.0 -a mini-swe-agent -m "$MODEL_A" -l 1 -k 1 --base-url "$GATEWAY_URL" --api-format openai_chat_completions

# Step 5 / G7: 結果の閲覧(モデル間をタスク単位で見比べられるか)
harbor run --dataset terminal-bench@2.0 -a mini-swe-agent -m "$MODEL_A" -l 1 --print-config
harbor view "$JOBS_DIR"

# Docker が使えない場合の代替(未検証)
harbor run -t hello-world/hello-world -a oracle -e singularity
harbor run -t hello-world/hello-world -a oracle -e daytona   # 事前に uv tool install "harbor[daytona]" と DAYTONA_API_KEY が必要
```

コマンドで確認する以外に、次の項目を確認する。

- G0: hello-world の oracle / nop が完走し、期待 reward になる。
- G1: 自作タスク 1 件が lint を通り oracle=1.0 / nop=0.0。公開データセットの 3 件でも oracle / nop が完走する。
- G2: 同じ公開タスク 1〜3 件を2モデルで実行し、`result.json`・trajectory・結果区分が保存される。usage の取得可否と、参照 agent の steps / $ 上限対応(`--ak` など)を記録する。
- G2′(Copilot 経路): 同様に確認し、加えて (a) モデルに見えるツールがサンドボックスの `bash` 1 つだけであること、(b) 権限の拒否が 0 件であること(`n_permission_denied`)、(c) プレミアムリクエストが `trial 数 × 倍率` に一致することを確認する。
- G3(該当時のみ): Gateway またはローカル推論へ `--base-url` で接続できる。
- G4: Harbor の usage stats の扱い、Hub / hosted を使わない運用、外部環境に送るデータの区分を決める。
- G7: `harbor view` で、2モデルの成否・コスト・時間・作業ログをタスク単位で確認できるか記録する。できなければ Phase 5 で必要最小限の集計を作る。

G5 と G6 は Phase 0 の完了条件ではない。G5 は正式集計を作る前に、G6 は private タスクを実行する前に確認する。
G3 を実施する場合は G2 の `-l` を下げ、G2 と G3 の agent trial 合計を 12 以下にする。
- 追加: 削除・リネームを伴う変更が採点環境に反映されるか(§5.2 の 4)は**検証済み**(反映される)。`harbor check` / `harbor analyze` が使う既定のモデルは §8.3。費用は未測定(未実行)。

---

## 付録 C. 参考資料

- Harbor: [GitHub](https://github.com/harbor-framework/harbor) / [Docs](https://docs.harborframework.com/)
  - [Task overview](https://docs.harborframework.com/tasks/overview.md) / [Configuration](https://docs.harborframework.com/tasks/configuration.md) / [Separate verifier](https://docs.harborframework.com/tasks/separate-verifier.md) / [Network policies](https://docs.harborframework.com/tasks/network-policies.md) / [Lint](https://docs.harborframework.com/tasks/lint.md)
  - [Run a job](https://docs.harborframework.com/jobs/run-a-job.md) / [Error classification](https://docs.harborframework.com/jobs/error-classification.md) / [Regrade](https://docs.harborframework.com/jobs/regrade.md) / [Artifact collection](https://docs.harborframework.com/jobs/artifact-collection.md)
  - [Pre-integrated agents](https://docs.harborframework.com/agents/pre-integrated-agents.md) / [Custom agents](https://docs.harborframework.com/agents/custom-agents.md) / [ATIF](https://docs.harborframework.com/agents/atif.md) / [Base URL management](https://docs.harborframework.com/agents/base-url-management.md)
  - [Metrics](https://docs.harborframework.com/datasets/metrics.md) / [Release policy](https://docs.harborframework.com/contributing/release-policy.md) / [Cracking down on reward hacking](https://docs.harborframework.com/news/reward-hacking.md)
  - [Installation](https://docs.harborframework.com/getting-started/installation.md) / [Pre-integrated sandboxes](https://docs.harborframework.com/sandboxes/pre-integrated-sandboxes.md) / [View job results](https://docs.harborframework.com/results/view-job-results.md) / [Leaderboards](https://docs.harborframework.com/harbor-hub/leaderboards.md)
- [Terminal-Bench](https://www.tbench.ai) / [Terminal-Bench 4.0 の公式 leaderboard(Harbor Hub)](https://hub.harborframework.com/datasets/terminal-bench/terminal-bench/latest?tab=leaderboard&leaderboard=4-0-0)
- [Inspect AI](https://inspect.aisi.org.uk/)
- [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)
- [SWE-bench #465: Repo State Loopholes During Agentic Evaluation](https://github.com/SWE-bench/SWE-bench/issues/465)
- [Evaluating Large Language Models Trained on Code (pass@k)](https://arxiv.org/abs/2107.03374)
- [τ-bench (pass^k)](https://arxiv.org/abs/2406.12045)

---

## 付録 D. 改訂履歴

| 版 | 日付 | 内容 |
|---|---|---|
| v0.1 | 2026-11-05 | 初版 |
| v0.2 | 2026-11-05 | 用語集、Harbor との分担、最短ルート(§0)を追加。作業ホストの環境確認結果(Docker の権限なし)と Step 0 の選択肢を反映。判断ゲートに G0(実行環境)・G7(タスク別比較)を追加し、G3 を条件付きに変更。Gateway を必須から任意に変更。Phase 3 以降に着手の条件を追加。コスト見積りを、公式 leaderboard の実績を踏まえた幅に変更。付録 B の最初の題材を hello-world に変更 |
| v0.3 | 2026-11-05 | Phase 0 を1つの手順に統一。参照 agent を mini-swe-agent に固定し、最大3公開タスク・2モデル・k=2(条件付き G3 を含め最大12 agent trial)で実機確認する。G0〜G4をPhase 0必須、G7を記録事項、G5/G6を後続フェーズの確認事項に整理。付録 B とロードマップを同期 |
| v0.4 | 2026-11-05 | S2 を ±10pt の同等性幅による再現性判定に変更。S3 に比較可能性と区間条件を定義。M2 を対象スイート内の CI 精度・独立反復に合わせ、Phase 3 の小規模 pilot と区別 |
| v0.5 | 2026-11-05 | Phase 0 を実機で実施([reports/phase0.md](reports/phase0.md))。実行環境を Docker が使えるホストに更新(compose / buildx はユーザー領域に導入)。ゲート結果を §3.2 に反映(G0・G1 OK、G2・G3 はモックで配線のみ、G7 OK、Path A は暫定採用)。実測で判明した事項を §4.3・§5.2・§6.1・§6.2・§8.3・付録 B・未決事項に反映: Harbor 0.25.0 + mini-swe-agent は litellm の不具合で LLM を呼べず agent の失敗に見える、予算上限が既定で無効、プロキシ環境では private タスクに Gateway が必須、Viewer の API は無認証、削除・リネームは採点側に反映される。自作スモークタスクと Step 4 のガード付きスクリプトを追加 |
| v0.6 | 2026-11-05 | API キー無しの Copilot 経路を追加(要望: キーを入力せず、いま使っている code agent から実行したい)。ホストの Copilot CLI を Harbor の外部型 agent にし、サンドボックスには MCP の `bash` 1 つだけを橋渡しする(`bench/agents/`)。実モデル(`claude-haiku-4.5`)で smoke と公開タスクを通し、G2′ を追加(§3.2)。反映: §0.3 Step 4、§4.2(モード B)、§4.3(Gateway 不要の経路)、§6.1(alias とプレミアムリクエストの予算)、未決事項 #1・#10、付録 B。発見は reports/phase0.md の F8〜F12(隔離は `--available-tools` が要、課金はプロンプト単位、ベンチ専用プロファイルの推奨など) |
