# doeff-conductor 制約グラフ(constraint-graph.md の拡張)

作成: 2026-06-12。ADR-0001・spec-workflow-orchestration.md・検証キャンペーン(validation-2026-06-11.md)・live probe(run `doeff-review-20260612-1`)に基づく。
**用途**: 本体グラフと同じ — モデルルーティングの基準。結合核に触れる変更はフロンティア+人間+law/SPEC改訂を1セットで。核番号は本体の K1/K2 から連番で **K3/K4/K5**(グラフは1つの生きた成果物)。分岐番号は conductor 空間として C1〜C10。

意図性の判定区分は本体と同じ: `spec` / `test` / `code` / `owner` / `成り行き`。

経緯: conductor は「k8s 風 control plane」を標榜しながら **k8s の意味論を束縛として輸入していなかった**。本書の核 K4/K5 はいずれも、輸入していれば最初から決まっていた事項が「選択の不在」のまま走った結果である(検死の教訓と同型)。

---

## 1. 分岐表 — 何を選ぶと何が排除されるか

| # | 分岐 | 現実装の選択 | 根拠 | 排除されるもの | 意図性 |
|---|---|---|---|---|---|
| C1 | ワーカー経路 直接subprocess / agentd監督セッション | **agentdのみ**(到達不能ならfail loud、fallback禁止) | ADR D1/D6、semgrep `adr0001-d1-agentd-only-worker-route`、`1b86b4df`(bypass削除) | 不可視ワーカー、親と心中するプロセス | spec+test |
| C2 | 結果の真実 画面テキスト/transcript / schema検証済みartifact | **artifact**(セッション内result channelをagentd `await_result`が検証) | ADR D1、ADR condemned §B/C、semgrep `doeff-agents-no-terminal-text-success-status` | capture sniffing、画面文字列での成否判定 | spec+test |
| C3 | authoring面 開いたPython / 閉じたHy DSL | **Hy-only閉語彙**+loaderがAST走査で非決定性を禁止(診断が置換名を指示) | ADR D2/D10、`8e0089b5` | workflow内のask/Local/handler露出、生のdatetime/random/open/network | spec+loader強制 |
| C4 | replay/cache識別 評価サイト基準 / 結果分布基準 | **結果分布基準**: prompt+schema+resolved identity fp(adapter/model/identity/effort)。substrateは明示除外。識別は「式」に属し評価サイトに属さない | replay_keying.py:12-105、ADR D2/D7、`47141e24` `3d765cac` `4294f0e8` | 場所・時刻・評価回数依存のキー、未解決profile名でのキー | code+test。**law未明文** — 5/25以降7コミットのchurnで実地収束した軸(`233b8184` `0ad2c7b4` `875df298` 含む) |
| C5 | 実行記録 in-memoryミラー / journal SSOT | **journal SSOT**: longest_valid_prefix でreplay、エントリ不一致はfail | journal.py:161,176,228-231,250 | journalと並ぶ第二の真実、蓄積状態ミラー | code |
| C6 | 並行性 セッションhandle/futureの露出 / 構造的(Spawn/Gather) | **構造的**: parallel→Spawn+Gather、workflowはhandleを一切持たない | ADR D1「concurrency is structural」、workflow_runtime.py:201-223 | DSL内のfuture/handle/セッション操作 | spec。**ただしハンドラ橋が前提を破っている**(→K4) |
| C7 | 失敗経路 terminal error / closure law(gate park) | **closure**: 全ノード・全失敗経路は artifact/verdict/escalation/gate で終端。展開時検査+retry枯渇はlive parkへ | ADR D2検証規則5、D9、validate retry-exhaustion→open gates×6(stub確認 2026-06-12) | 黙殺、wedge、「何を待っているか分からないrun」 | spec+stub検証。**裁定の書き側が未実装**(→K5) |
| C8 | workspace識別 評価サイト / 式座標+ノード毎commit | 式座標に帰属(`47141e24`)、agentノード毎にcommit(`1ee28e85`)、mergeは全source再適用(`875df298`) | test_workspace_resume_identity.py | 評価回数依存のworktree、merge時のsource取りこぼし | code+test。**ただし生成イベントがjournal外**(既知欠陥→K3) |
| C9 | 監視権威 L2ローカルstore / agentd sqlite | **agentd単一権威**(PR #449) | semgrep `doeff-agents-cli-monitoring-uses-agentd` | 二重bookkeeping、ローカルstoreの幽霊 | spec+test |
| C10 | 監督policy グローバル恒久設定 / run-scoped | **run-scoped**(autonomous / phase-checkpoints)、plan承認アーティファクトに刻印 | cli.py `plan --supervision`、spec §8.1 | 全runを常時gateする運用、無監督の初回run | spec |

## 2. 結合核のマーキング

### 核K3: 識別・replay核 — C4 ⇔ C5 ⇔ C8(+ L2セッション命名)

相互拘束の構造(7コミットchurnの正体):

```
cache key判定基準(C4) ──決める──> journal prefix無効化のsemantics(C5)
       │                                  │
       └──導出──> session_id(effects/agent.py:46,64        └──前提──> resumeの決定論
                  session_node_key: 識別修飾済みキー)
                        │
workspace識別(C8) <──同型── 「識別は式座標の純関数」 ── だがworkspaceの生成はjournal外(欠陥)
```

- fingerprint に何を入れるかを動かすと、journal の prefix 無効化・セッション再採用(冪等launch)・workspace 再生成が連動して変わる。1つだけ動かした修正が `3d765cac`→`233b8184`(digest がL2に届かずjournal汚染)の二段failを生んだ — 部分修正の失敗例として本体グラフのPR #371に対応
- **law候補(未明文 → 昇格対象)**:
  - **L-K3-1(宣言的命名)**: `session_id = f(run_id, node_path, loop_iter, attempt, resolved_identity_fp)` — 生成時刻・生成回数・生成サイトに非依存(k8sのname-from-spec)
  - **L-K3-2(replay決定論)**: same(snapshot, params, 環境fp) ⇒ 同一のcache-key列(longest_valid_prefix = 全長)
  - **L-K3-3(資源被覆)**: agentノードが消費する**全ての**状態的資源(session・workspace・…)はreplay識別系に被覆される。被覆漏れの実例 = workspace! 非journal欠陥(resume時にsessionとworkspaceが別の世界を指し、gateが空workspaceで偽greenを返す)
- 固定状況: ADR△(D7にfp基準の文章のみ)/ law✗ / runtime△(journal不一致failのみ)/ static✗

### 核K4: await×並行性核 — C6 ⇔ C1 ⇔ 本体B10(スケジューラ=ハンドラ)

- D1が「並行性は構造的」と宣言し(C6)、D1/D6がワーカー待ちをagentd RPCにし(C1)、スケジューラは協調的ハンドラである(本体B10)。**3つの選択は個々に正しいが、「RPC待ちは協調スケジューラとどう合成するか」だけが決められなかった**: `DaemonAgentHandler.handle_await_result`(doeff-agents handlers/daemon.py:298)→ `AgentdClient.await_result`(agentd_client.py:135)が同期ブロックし、`run(scheduled(conductor_handler(program)))`(api.py:152)のループごと止める
- 帰結: **parallelは形だけで実行は直列** — live実証 2026-06-12、run `doeff-review-20260612-1`(6-branch parallel、起動5分後もセッション1個。兄弟branchはlaunchすら起きない=スケジューラ飢餓)。conductor srcに`Await`/スレッド退避は0箇所(唯一の言及はutils.py:51の深層皮肉「use Await effects in programs」)
- **law候補**:
  - **L-K4-1(非ブロックハンドラ)**: エフェクトハンドラは無制限ブロッキングI/Oを同期実行しない。無制限待ちはスケジューラのAwait/external completion経路(scheduler.pyのexternal_queueが既に受け皿)からのみ入る
  - **L-K4-2(重なり観測)**: pendingな並列agentノード a,b のセッション生存区間は交差する。`wall_clock(parallel(a,b)) < wall_clock(a) + wall_clock(b)`(agentd stubのsleepで機械検証可能)
- 参照意味論の輸入: k8s controller = level-triggered・非ブロックreconcile(「子の完了をスレッドを塞いで待つcontroller」は存在しない)。doeff語では「blockingはeffectとしてyieldせよ」 — VM側で確立済みの規律のL3への適用
- 固定状況: **全層✗**(ADRは並行性を構造的と言うのみで合成則に触れない)

### 核K5: closure・裁定核 — C7 ⇔ C10 ⇔ C5(journal経由でK3に隣接)

- closure law は「全経路はgateで終端しうる」と言う。gateは決定点であり、**決定が第一級の記録(journal write)として存在しresumeがそれを消費して初めて、閉包は操作的になる**。現状はGateOption(proceed/redirect/abort)のメタデータと`gate list`(読み側、cli.py:672-708、overseer.py:203)のみ — 「answer」という語はsrcに0出現。読みverbだけ作って書きverbを忘れる、まさに「選択の不在」
- 裁定がjournalに乗らなければL-K3-2(replay決定論)も破れる — answerはreplay入力だから。これがK3への辺
- **law候補**:
  - **L-K5-1(操作的閉包)**: ∀ open gate g, ∃ answer(g, o), o ∈ options(g): journalに記録され、resumeが消費し、runはparkを離脱する。「読みverbには書きverbを」
  - **L-K5-2(裁定の決定論)**: answerはreplay識別の一部 — answer後の再resumeは同じ決定を再生する(裁定のやり直しはanswerの上書きでなく新answerの追記)
- 参照意味論の輸入: k8s では裁定=リソースへの書き込みで、controllerがそれを観測する(生きたプロセスへのRPCではない)。conductor語では「answerはjournal entry、resumeはjournal reader」 — 新機構ではなくC5の延長で実装できる(し、すべき)
- 固定状況: ADR✓(D2/D9+L-K5-1/L-K5-2)/ law✓(等式明文化)/ runtime✓(gate-answer-journal.jsonl + E2Eテスト: park→answer→離脱、redirect→resume、replay決定論)/ static△(D1のsemgrepのみ)

### 独立領域(下位モデルへ委譲可能)

`conductor wait` verb(終端/park/失敗をexit codeで返す)、CLI出力整形、docs整合、blocked表示の改善(agentd分類器)、template整理、issue verb群。これらは核への辺が0〜1本で、churnが常に局所diffで済んでいる。

## 3. ルーティング規則(本体グラフ §3 を継承、conductor分の追加)

1. **K3/K4/K5の頂点に触れる変更 = フロンティア+人間+law/SPEC改訂を1セット**。具体的に現時点で該当: parallel非ブロック化(K4)、gate answer機構(K5)、workspaceのjournal被覆(K3)。**この3つを独立issueとしてcodexへ出すことを禁止する**
2. 独立領域は検証条件付きissueでcodexへ(本体規則2と同じ)
3. 判定に迷えば本グラフに頂点を足し、K3/K4/K5への辺を数える(本体規則3と同じ)
4. K4/K5は「選択の不在」で生まれた核である。修正は欠陥の局所fixでなく、laws L-K4-*/L-K5-*を等式のままSPECへ昇格し、stub機械検証(L-K4-2の重なり、L-K5-1のpark離脱)を同PRに含めること

## 4. 欠陥 → 核 対応表(2026-06-12時点)

| 欠陥 | 核 | 破れているlaw | 状態 |
|---|---|---|---|
| workspace! がresume非安定(偽green) | K3 | L-K3-3 | **fixed** — `workspace-journal.jsonl` + `JournaledWorkspaceHandler` records workspace materializations; pre-coverage runs fail loudly |
| parallel直列化(ハンドラ内同期RPC) | K4 | L-K4-1/2 | **fixed** — `make_offloaded_scheduled_handler` bridges the blocking RPC through ExternalPromise+daemon thread (law ratified, integration test + semgrep rule shipped) |
| gate answerの書き側不在 | K5 | L-K5-1 | **解決済み** gate-answer-journal.jsonl + `conductor gate answer` CLI |
| await budget所有軸が未決(検証台帳 §11-7) | K4の縁 | L-K4-1の系(budget更新は誰の決定か) | open |
| `conductor wait` verb不在 | 独立 | — | **fixed** — `conductor wait <run-id>` 実装済み(issue conductor-wait-verb、codex run merge済み) |
| blocked表示のcosmetic誤読(§11-8) | 独立 | — | issue候補(codex可) |

## 5. 固定計画(4層、核ごとの次の成果物)

| 核 | 1 ADR | 2 law(等式) | 3 runtime invariant | 4 static |
|---|---|---|---|---|
| K3 | ADR-0001にD12追記(workspace journal被覆) **✓済** | L-K3-1/2/3をspec §に等式で | workspace-journal.jsonl + JournaledWorkspaceHandler + E2E 3テスト **✓済** | semgrep: ハンドラ内でのsession名の手組み禁止(replay_keying経由を強制) |
| K4 | D1に合成則を追記(handler非ブロック) | L-K4-1/2 | stub二並列で生存区間交差をassertするテスト(CI) | semgrep: handle_*内の`await_result`直呼び禁止(Awaitブリッジ強制) |
| K5 | D9に裁定の記録義務を追記 **✓済** | L-K5-1/2 **✓等式明文化** | gate-answer-journal.jsonl + E2E 6テスト **✓済** | semgrep: GateOption追加時にanswer consumerの存在を要求(または閉包テスト) |
