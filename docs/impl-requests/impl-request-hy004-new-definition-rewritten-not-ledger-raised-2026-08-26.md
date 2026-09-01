# 実装依頼書 — 新しく増えた関数定義は台帳の追認ではなく defk への書き直しで直す(ADR-DOE-HY-004 の赤)

発注元 task: route-4b012e30cd(doeff の日次基底検証が赤・6 日連続。精査段としてこの書を起草)
方向宣言: `hy004-new-definition-rewritten-not-ledger-raised`(開発ボードへ seat-proposal で登記)
対象: repo `~/repos/doeff` / 基底 `main` の断面 `5dc6c3ded5a6`
検証 log(全文): `/Users/s22625/.local/state/ai/land-runs/doeff-verify-20260826-065004.log`(該当の失敗は 54〜85 行)

## この赤は何か(前提知識ゼロ向けの導入)

doeff の Hy コードには、契約(`:pre` / `:post`)つきの関数を定義するマクロが 2 つある。

- `deff` — ふつうの関数。呼ぶと値がそのまま返る。
- `defk` — doeff の効果系のうえで動く関数(program)。呼ぶと「まだ実行していない program」が返り、
  program の中では `<-` で束ね、program の外では `run` で実行して初めて動く。

2026-08-21 の設計記録 ADR-DOE-HY-004(実体 = `docs/adr/defadr_doeff_hy_004_defk_only.hy`)が、
オーナー指示「we need defadr to disallow all deff. only use defk」を受けて **`deff` の新設を全面禁止**し、
既存の 227 定義を「file ごとの上限表」(ADR 内の `DEFF-ROSTER`。以下 **上限表**)で凍結した。
上限表は単調に減るだけで、増やすのはオーナー裁定のみ(R5)。この上限を機械が検査するのが
`test_adr_doe_hy_004_deff_ratchet` で、いま赤いのはこれである。

```
AssertionError: 新しい deff の定義は禁止(ADR-DOE-HY-004 R1 — defk で書く。既存の変換は R3 の一括出荷で台帳を同便で削る):
  ['packages/doeff-agents/src/doeff_agents/sessionhost/host.hy: 36 > 台帳 35']
```

原因は特定済み。断面が `c994a86de718` から 1 commit 進んだその 1 つ、

```
5dc6c3de sessionhost: launch_attribution — 発注者申告の帰属 metadata を行に verbatim 保存(ACP 帰属便 2)
```

が `host.hy` に **新しい `deff` を 1 つ**(`admit-launch-attribution`、host.hy:622)追加し、
上限表 35 を据え置いた。増えた定義はこの 1 つだけで、同じ commit が触った
`effects.hy` / `launch.hy` / `store.hy` の数は上限表と一致している(実測)。

## 結合核

**非該当。** 突合の根拠(印象ではなく watchlist との照合):

- doeff の結合核 watchlist = `docs/crystallization/constraint-graph.md`。載っているのは VM の K1/K2
  (継続・handler 意味論)で、`sessionhost` は頂点にない。本変更は VM に触らない。
- `sessionhost` 側で結合核を名指しているのは ADR-DOE-AGENTS-011 冒頭
  (「結合核: ACP core-04(liveness closure)の入口側閉包 + core-13(invocation execution model)の
  attempt 境界」)。本変更が触るのは受付検査 1 個の**書き方**であって、生死判定・attempt 境界・
  拒否の位置・拒否の文言はいずれも不変(受入条件 ② が既存テストで固定する)。
- ⚠ ただし **後続の burn-down は結合核に触る**: `host.hy` に残る 35 定義を `defk` へ移す作業は
  RPC の受付から実行までの筋(`dispatch-method` → `run-hosted`)を program 化する話で、
  interpreter 境界の再配置を伴う。あれは最上位モデル + 人間審査の車線で、法・反例・テスト・実装を
  1 まとめに出す便として別に起こす(この依頼書の範囲外。下の「未確定事項」参照)。

## 確定した決定

### 1. 上限表を 36 へ上げる形は採らない(条文の帰結・議論の余地なし)

- R1: 「新しい deff 定義は禁止(repo 全域)。新しい関数は defk で書く」。`admit-launch-attribution` は
  2026-08-25 に**新設**された関数であり、R1 の直撃対象。
- R5: 「台帳の増額・除外の新設は operator 裁定のみ」。この便にオーナー裁定は無い。
- R3 の「台帳を同便で削る」は既存定義を `defk` へ**変換する**便の話で、新設の追認には使えない。

したがって「上限表を上げれば緑になる」は**この便では実行不能**であり、実装席は選んではならない。
(もし実装中に「どうしても上げる以外に無い」と判断したら、上げずに止めて発注元へ差し戻すこと。)

### 2. 直し方 = 新設された 1 定義を `defk` へ書き直す

`packages/doeff-agents/src/doeff_agents/sessionhost/host.hy:622` の

```hy
(deff admit-launch-attribution [params method] ...)
```

を `defk` にする。これで `host.hy` の実数は 36 → 35 となり上限表と一致する。
**他の定義には触らない**(上限表も編集しない)。上限表より実数が少なくなると
「削り忘れ検知」の側が赤くなるので、35 ちょうどに着地させる。

### 3. 呼び出しは `run` で実行する(2 箇所)

`defk` は呼んだだけでは動かない。呼び出し元は 2 箇所ともふつうの関数(`deff`)なので、
`<-` は使えず `run` で実行する:

- `host.hy:745`(`build-launch-program-params` の中)→ `(run (admit-launch-attribution params "session.launch"))`
- `host.hy:1088`(`dispatch-method` の resume 分岐)→ `(run (admit-launch-attribution p "session.resume"))`

`run` を落とすと **program が作られるだけで検査が走らない**(静かに無検査になる)。これは ADR 本文が
反例として名指している失敗形そのもの。ただし既存テスト 2 本(受入条件 ②)が不正な値を撃って
拒否を確認しているので、`run` 落ちは自動で赤くなる — 新しいテストを足す必要はない。

実測で確認済み(この席が実射):

- `defk` + `run` で `RuntimeError` は**型も文面も**そのまま呼び出し元へ伝わる。
- program の走行中に、ふつうの関数を経由した内側の `run` が入れ子になっても伝わる
  (deftest の本体は program なので、テストはこの入れ子経路を通る)。
- ADR 自身の 2 本目の検査 `test_adr_doe_hy_004_pure_logic_lives_in_defk` が、まさに
  「純粋な検証を `defk` で書き `run` で回す」形を出荷済みの手本として持っている(現在緑)。

### 4. 認めて記録する副作用 2 点(隠さない)

- **標準エラー出力に doeff のトレースが出る**: `doeff/run.py:33` が例外時に無条件で
  doeff traceback を stderr へ書く(抑止の口は無い)。よって不正な `launch_attribution` を
  受けた時、常駐 host のログに数行のトレースが増える。診断として有用な情報であり受容する。
  (抑止したいなら `run` 境界の設計変更 = doeff 側の別件。この便で細工しない。)
- **1 回の受付につき約 21 µs 増える**: 実測 `deff` 直接呼び 0.2 µs/call、`defk` + `run` 21.6 µs/call
  (1000 回平均)。launch は tmux 起動を伴う処理で 100 ms 級なので影響しない。

### 5. 採らなかった案(実装席が再発明しないための記録)

| 案 | なぜ採らないか |
|---|---|
| 上限表を 36 へ上げる | R1 違反の追認 + R5(増額はオーナー裁定のみ)違反。上の 1 |
| 素の `defn` で書く | R1 の `defn` 除外は「Python 相互運用の境界」限定。受付検査は該当せず、別の扉から法を抜ける形 |
| 検査本体を呼び出し元 2 箇所へ直接書く(関数を作らない) | 同一規則の定義点が 2 つになる。兄弟の `admit-context-file` が共有関数になっている理由と衝突 |
| `run` を `dispatch-method` 側へ集める(param builder から追い出す) | 兄弟 3 つ(`expected_result` / `context_file` / `workspace_seed`)と受付位置が非対称になり、出荷済みテストの検査点も移る。非対称は次の書き手が「直す」ので侵食源 |
| `host.hy` の 35 定義もまとめて `defk` 化 | 正しい終着点だが、稼働中の常駐 host の RPC の筋と interpreter 境界の再配置を伴う。赤の修理便に載せる規模ではない(後続便・結合核の車線) |

## 未確定事項

1. **`run` が受付検査の中に入ることの扱い**(実装は上の決定どおりで進めてよい・後続で見直す)。
   下流の姉妹 repo(agent-control-plane)の同種の法 ADR-0056 R3 は「library 関数の中で `run` を
   綴るのは defect(interpreter を固定し合成を壊す)」と書いている。doeff 側の ADR-DOE-HY-004 に
   その条文は無く、`host.hy` の受付〜dispatch は socket の受付ループとテストからしか呼ばれない
   「殻」に近い層なので、この便では許容する。恒久解は決定 5 の最終行(35 定義の移行便)。
   → **後続便の起票が要るかは発注元/routing の判断**。この席は起票していない。
2. **失敗 A(この依頼書の範囲外)**: 同じ日次検証で
   `tests/test_enforcement_ledger.py::test_enforcement_inventory_matches_ledger` も赤
   (`docs/adr/enforcement-ledger.json` の 3 欄が実数と不一致: defadr_files 23/実 24、
   adr_deftest_enforcements 39/実 41、adr_laws 88/実 89)。この席の実測では、差分は
   **ADR-DOE-HY-004 を新設した commit `f47f0a4b`(2026-08-21)自身**が
   台帳を同時に更新しなかった分とちょうど一致する(defadr file +1・deftest +2・law +1)。
   先行の担い手席は `fix-doeff-enforce-ledger-drift`(s-68eaab4ac2)で、状態は `done` なのに
   台帳の値は 08-21 から動いておらず、`docs/adr/enforcement-ledger.json` の最終更新も
   `78495e20`(2026-08-18)のまま = 直しが本線に入っていない。**この便では直さない**が、
   放置なら別途起票が必要(発注元へ報告済み)。
3. **この便は `docs/adr` の法・検査・semgrep 規則を 1 つも増減させてはならない**。増減すると
   上の 3 欄が動き、失敗 A の直しと数が衝突する(どちらが正しい値か機械には決められなくなる)。

## 手順

0. 着工前に `ai direction ls` で開発ボードを照会し、方向宣言
   `hy004-new-definition-rewritten-not-ledger-raised` に `ai direction update <id> --working-by <自分の会話 id>` で担当を記帳する。
1. 共有 checkout を占有しないため、専用の worktree を切る(この repo に自動供給の宣言は無いので手で作る)。
   branch 名は直近の慣行に合わせて `wt/hy004-admit-launch-attribution-defk`。
2. **赤の確認(TDD の赤は既に出荷済み — 新規に書かない)**:
   ```
   uv run --no-sync pytest -q "docs/adr/defadr_doeff_hy_004_defk_only.hy::test_adr_doe_hy_004_deff_ratchet"
   ```
   この席の実測ではこの機体で 10 秒・`1 failed`(赤)。文面に `host.hy: 36 > 台帳 35` が出ることを確認する。
3. `host.hy:622` の `deff` を `defk` へ変える。
   ⚠ **上限表の数え方は字面**である(`\(deff\s` の正規表現で、コメントや文字列の中も数える)。
   「もとは deff だった」等のコメントに `(deff ` の字面を書くと数が減らず赤が残る。
4. 呼び出し 2 箇所(`host.hy:745` と `host.hy:1088`)を `run` 経由に変える(決定 3)。
5. 上限表(`DEFF-ROSTER`)と `docs/adr/enforcement-ledger.json` は**触らない**。
6. 検証(受入条件の順に実射し、出力を PR 本文へ貼る)。
7. commit / push / PR は個人プロファイルの席で行う(この repo は編集は任意席・歴史の著述は個人系。
   会社プロファイルの席は `delegate-commit` skill の手順で委譲する)。
8. `ai land request --title "<題>" --branch wt/hy004-admit-launch-attribution-defk --repo ~/repos/doeff` で
   land queue へ登記する。着地の門は現在 focus mode(`.agents/land-queue.toml`)で全量検査を走らせないため、
   失敗 A が赤のままでもこの便の登記・着地は妨げられない。

## 受入条件

検証は 1 対 1 で対応づけ、PR 本文に下の表をそのまま載せる(この repo の検証契約 —
表に載らない条件は「済んでいない」と扱われる)。

| # | 条件 | 実射する検査 |
|---|---|---|
| ① | 語彙の検査が緑 = `host.hy` の実数 35 で上限表と一致 | `docs/adr/defadr_doeff_hy_004_defk_only.hy::test_adr_doe_hy_004_deff_ratchet` |
| ② | 受付検査の挙動が不変(launch 面: 合格は素通し・省略は None・非 object は拒否) | `packages/doeff-agents/tests/test_sessionhost_host.py::test_launch_program_params_attribution_admission` |
| ③ | 受付検査の挙動が不変(resume 面: 非 object は store に触る前に拒否) | `packages/doeff-agents/tests/test_sessionhost_host.py::test_dispatch_resume_admits_launch_attribution_shape` |
| ④ | fork への持ち込みが拒否のまま | `packages/doeff-agents/tests/test_sessionhost_host.py::test_dispatch_fork_rejects_launch_attribution` |
| ⑤ | host の受付一式に回帰なし(この席の基線 = 28 passed) | `uv run --no-sync pytest -q packages/doeff-agents/tests/test_sessionhost_host.py` |
| ⑥ | 設計記録側の検査に回帰なし | `uv run --no-sync pytest -q docs/adr/defadr_doeff_hy_004_defk_only.hy`(2 本とも緑) |
| ⑦ | lint が通る | `make lint`(`lint-doeff` / `lint-semgrep` を含む) |
| ⑧ | 上限表と強制力台帳を触っていない | `git diff --stat` に `docs/adr/defadr_doeff_hy_004_defk_only.hy` と `docs/adr/enforcement-ledger.json` が現れない |
| ⑨ | land queue へ登記済み | `ai land list --repo ~/repos/doeff` に当該 branch の行がある |

条件を弱める・別の検査で代える場合は、PR 本文に `## Verification deviations` を立てて
「何を何に替えたか・なぜか」を書く(黙って弱めた PR は無審査で差し戻される)。

## 著者席

`doeff-hy004-ratchet-request`(会話 `s-0c4fc8c029` / doeff)— 精査段としてこの書を起草。実装はしていない。

## 著者モデル(authorModel)

`claude-opus-5`

---

## 実装席の解決(3 例目 = policy.hy 便 / 2026-09-01)

この書は 2 例目(host.hy / land 台帳 L47)の処方として書かれたが、**同じ処方を 3 例目に
そのまま適用した**記録をここに残す(この書が「同型の赤の共通処方」として再利用されているため)。

- 患部: `packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy: 30 > 上限表 29`
- 原因 commit: `7c55afa6`(従量課金 credential を launch 関所で締め出す・land 台帳 L51)が
  `metered-credential-env-offenders` を 1 つ新設しながら `DEFF-ROSTER` を据え置いた。
- 直し: 新設された 1 定義を `defk` へ書き直し、実数を 29 へ戻した。上限表と
  `docs/adr/enforcement-ledger.json` はどちらも触っていない(決定 1 / 手順 5 のとおり)。
- 着地: commit `157d989a`(基底 `7c55afa6`)/ branch `wt/hy004-metered-credential-env-offenders-defk`
  / land 台帳 **L52**(landed 2026-09-01 16:44)。
- 実装席: 会話 `dd58b81e-4689-42e0-93fd-ceff913dd34d`(発注 `route-291b1c3d65`)。

### 決定 3 の枝(この書の「`run` で実行する」は今回は当てはまらない)

この書の決定 3 は「呼び出し元がふつうの関数なので `run` で実行する」と書いている。これは
host.hy の 2 呼び出し元がどちらも素関数だったことに依存した**その便固有の枝**であって、
規則そのものではない。3 例目の呼び出し元 `launch-session` は `defk`(program)なので、
`<-` bind が正規形(ADR-DOE-HY-004 R3 が名指す「全呼び出し site の `<-` bind 化」)であり、
そちらを採った。**規則の一般形は「呼び出し元が program なら `<-`・素関数なら `run`」**である。
次の書き手はこの枝を先に判定すること(呼び出し元の定義マクロを見れば決まる)。

### この site では「静かに no-op 化する」失敗形は起こらない(反証の実測)

ADR は反例として「直接呼びの site が Program 値を受け取るだけで実行されず、静かに
no-op 化する」を名指している。今回の site では起こらないことを実測で確認した:
`<-` を `setv` へ落とすと `metered-offenders` が Program 値(常に truthy)になり、
**全 launch が無条件に raise する** — `66 failed / 19 passed` の loud red。
すなわち bind 落ちは沈黙ではなく即死で、追加のテストは要らない。

### 検証の実射(3 例目)

| # | 条件 | 実射した検査 | 結果 |
|---|---|---|---|
| ① | 語彙の検査が緑(policy.hy 実数 29 = 上限表) | `docs/adr/defadr_doeff_hy_004_defk_only.hy::test_adr_doe_hy_004_deff_ratchet` | 緑(修正前は `30 > 台帳 29` で赤を確認済み) |
| ② | 受付検査の挙動が不変(launch 面) | `packages/doeff-agents/tests/test_sessionhost_launch.py::test_launch_rejects_metered_credential_in_overlay` | 緑 |
| ③ | 受付検査の挙動が不変(resume 面) | `packages/doeff-agents/tests/test_sessionhost_resume.py::test_resume_rejects_metered_credential_in_overlay` | 緑 |
| ④ | 受付一式に回帰なし(基線 171 passed) | policy / launch / resume / resume_cross_binding の 4 file | 171 passed(基線と同数) |
| ⑤ | 設計記録側の検査に回帰なし | `docs/adr/defadr_doeff_hy_004_defk_only.hy` 全 3 本 | 3 passed |
| ⑥ | lint | semgrep 0 findings(201 rules / 903 files)・pyright 0 errors・doeff-linter 6265(**基底と A/B 実射して同数**)・ruff 3(基底赤・触っていない 2 file 由来) | 基線と同値 |
| ⑦ | 上限表と強制力台帳を触っていない | `git diff --name-only` = `policy.hy` と `launch.hy` の 2 file のみ | 満たす |
| ⑧ | land queue へ登記済み | `ai land list --repo ~/repos/doeff` | L52 landed |

④ の host.hy 便固有の条件(fork への持ち込み拒否)は、3 例目には対応する検査が存在しない
(`metered-credential` は fork 面に固有の受付点を持たない)ため、代わりに binding overlay の
回帰面 `test_sessionhost_resume_cross_binding.py` を同じ 1 回の実射に含めた。

### 未着手のまま残っている隣接事項(この便では直していない)

- **未確定事項 2(失敗 A)は 2026-09-01 時点でも赤のまま**:
  `tests/test_enforcement_ledger.py::test_enforcement_inventory_matches_ledger`。
  着地後の main(`157d989a`)で実射して確認した。この便は `docs/adr` を 1 file も触って
  いないので、この赤の数は本便の前後で不変。
- **この依頼書そのものが repo に登記されていない**: `docs/impl-requests/` 配下でこの file だけが
  git の管理下に無い(共有 checkout の untracked file)。commit message の `IMPL-REQUEST:` 行が
  指す先が本線に存在しないため、書の耐久性は共有 checkout の生存に依存している。
  登記するかは著者会話 / operator の判断。
