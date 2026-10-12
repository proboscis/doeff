# 設計検証 lt-6N1ZY7HZZKH7KMPEH2TS5XV3YQ — 焼きの並列数の天井(card ki-4c2f5878e524)

この文書の目的: bytecode の焼きの並列数の既定に天井を置いた変更(doeff a40add2d9)が、どの責務に閉じているかと、何で守られているかを、盲検 A・B と実測の後の形で示す。読み手は、天井の値を変える時・同時の準備の扱いを変える時に、どこを変え、どの検が守るかを決められる状態になる。

盲検の前の主張は design-before-blind.md(sha256 887a8724555d5ef8e428c89293cf62836a4d1d512ed5d52720edc2836a99eb60)に固定してあり、この文書は主張を上書きしない。主張と実測の差は 4 節に書く。

## 1. 完了の範囲

| 項目 | 状態 |
|---|---|
| 天井の実装(`bake_plan.hy` の `BAKE-JOBS-CEILING` 8・`cpu-limit-of`) | 実施済み — doeff main a40add2d9(L2155) |
| 天井の検(`test_env_warm.hy` の `test-compile-parallelism-stays-under-the-ceiling-without-a-pod-limit`) | 実施済み — a40add2d9 |
| 盲検 B を受けた契約の検(`test_env_host_judgments.hy` の `test-the-host-carries-the-compile-jobs-of-a-warm-preparation-unchanged`) | 実施済み — この記録と同じ branch で main へ |
| 本番での効き | worker の Pod の入れ替えの後(焼きの道具は worker の版 `WORKER_DOEFF_COMMIT` で走る)。入れ替えはこの依頼の範囲の外で、依頼者の決め(10-12 11:11 JST)で次の版上げの後に測る |

## 2. 責務と公開契約(盲検の後)

盲検の前の表(M1〜M5)はそのまま有効。盲検 A を受けて、焼きを起こす者に 2 つを書き足す(M6・M7 — 主張の表に無かった者)。

| id | 実体 | 持つ判断 | 公開契約 | 並列数の決まり方 |
|---|---|---|---|---|
| M1 bake-parallelism | `worker/core/bake_plan.hy` の `cpu-limit-of`・`BAKE-JOBS-CEILING` | 既定の並列数(cpu.max の文と使える CPU の数と天井) | `cpu-limit-of(cpu-max, available) -> int`(純粋) | 1 ≤ 答え ≤ min(天井, available, ceil(quota/period)) |
| M2 code-prepare-entry | `worker/entry/code_prepare.hy` の `usable-cpus`・`--jobs` | どの file を読むか | `--jobs N`(無ければ M1 の既定) | — |
| M3 override-policy | `worker/core/policy.hy`・`WorkerPolicy.compile-jobs-while-replacing` | いつ既定を使わずに数を渡すか | `PrepareEnv`/`PrepareCode` の `compile-jobs`(None = 道具の既定) | recreate の旧い process が動いている間だけ 2 |
| M4 prepare-concurrency | `worker/protocol/env_store.hy` の `max-parallel`・`env_rules.launch-order` | 同時の env の準備の本数 | `EnvSettings.max-parallel` | 頼みの `compile-jobs` を書き換えずに運ぶ(盲検 B を受けて検で守る) |
| M5 bake-pool | `foundation/bytecode_pool.hy` | 渡された数の pool で焼く | `--jobs N` | — |
| M6 code-host(足した) | `worker/protocol/code_store.hy` の `PrepareCode` | 版ごとの木の準備を起こす | `PrepareCode` | 同時の本数の上限なし・nice を足さない(盲検 A の試しで確かめた)。zeus の worker は版の木の job を受けない(`CODE_REPO_URL` 無し) |
| M7 boot-prepare(足した) | `deploy/boot.sh` の自己起動の焼き・ROLE=prepare | worker 自身の root の焼き | `code_prepare.hy`(`--jobs` を渡さない) | M1 の既定 |

## 3. 強制方法(盲検の後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 既定の並列数は天井を越えない(M1) | 純粋な判断の検 | `tests/test_env_warm.hy` の `test-compile-parallelism-stays-under-the-ceiling-without-a-pod-limit` | pytest(doeff の検の一式) | 天井の値そのものの妥当さは測りで決める |
| 上限の在る pod の並列数を下げない(R3) | 同じ検の atlas の assert(天井を 6 にすると赤になる事を変異で確かめた) | 同上 | 同上 | 天井より大きい上限(16 など)の pod は 8 に下がる(今の cluster には無い) |
| 「None = 道具の既定」を env-host が書き換えない(M3 の契約・M1 の判断の持ち主) | 契約の検: job の準備と先読みの準備がどちらも並べる数 None の時、準備の process への頼みの `compileJobs` が 2 本とも null | `tests/test_env_host_judgments.hy` の `test-the-host-carries-the-compile-jobs-of-a-warm-preparation-unchanged`(盲検 B の差分を当てると `compileJobs` 4 で赤) | pytest | env-host の外(code-host・boot.sh)で数を決める差分は、この検では捕まえない。code-host の数の運びは既存の `test_code_rules.hy` の compile-jobs の検(None なら `--jobs` を足さない)が守る |
| 既定の決め方を M1 の外へ複製しない | 静的な規則は置かない。代わりに上の契約の検で、複製が env-host の頼みに現れる形を止める | — | — | 数を直に書く複製(`4` など)は静的に止めていない |

## 4. シナリオごとの予想と実測

| id | 軸 | 主張(盲検の前) | 実測 | 差と理由 | 直し |
|---|---|---|---|---|---|
| S1 | hardware | 天井の値を変えるのは M1 の 1 行と M1 の検だけ | 天井を 6 にする変異で、落ちたのは `test_env_warm.hy` の atlas の assert 1 つ(R3 を守る検)・ほかの 43 本は緑。天井の名を引くのは M1 の式と検だけで、ほかの file は註だけ(evidence/s1/S1-mutation-ceiling-6.txt)。盲検 B は、天井を使った別の決め方を env-host に書くと S1 の主張が崩れる差分を示した(evidence/b/) | 予想どおり。ただし主張を守る検が無かった(盲検 B の差分は検の一式と commit の時の検を全部通った) | 契約の検を足した(3 節) |
| S2 | effects | cgroup v1 を読むには M2(読む file)と M1(入力の形)の両方を変える — 契約の拡張 | 偽の file の答え手で、cpu.max の無い node(v1)では既定が 8(v1 の pod の上限は読まれない)、上限 2 の pod では 2(evidence/s2/S2-positive.txt) | 予想どおり。v1 を読むなら 2 module の契約の拡張になる | 不要(v1 の node は今の cluster に無い) |
| S3 | concurrency | node の総数 = M4 の同時の本数 2 × 天井 8 = 16。総数を 8 にするには M4 か新しい枠を変え、M1・M2・M3・M5 は変わらない | 盲検 A: 総数に加わる者は env-host だけでなく code-host(M6 — 同時の上限なし・nice なし)と boot.sh の ROLE=prepare(M7)も在る。試しで、code-host は 2 つの版の焼きを 7 ms の差で起こし、nice を足さなかった(evidence/a/A-repro-after-probe-fix.txt)。zeus は `CODE_REPO_URL` が無く、code-host の焼きは起きない(evidence/s3/S3-zeus-code-repo-url.txt) | 主張は外れ。共同の不変条件(node の総数)に加わる者を見落としていた。総数を守る変更は M4 だけでは収まらず、M6・M7 と M2 の契約(`--jobs N` の意味)まで広がる(盲検 A の 2 節(c)) | 設計の表に M6・M7 を足した。node の総数の上限はこの依頼の要件ではないので実装しない(4 行下の「範囲外」) |
| S4 | storage | 除外 | — | — | — |
| S5 | distribution | 除外 | — | — | — |
| S6 | simulation | M1 は純粋で、M2 の読みは `ReadText` の答え手の差し替えで、本物の 36 CPU の機体や cgroup の file 無しで確かめられる | 偽の答え手で 5 通りの答えを回し、どれも 1 ≤ 既定 ≤ 8(evidence/s2/S2-positive.txt)。直す前の振る舞いへの変異は純粋な検が拒んだ(evidence/s1/S1-mutation-no-ceiling.txt) | 予想どおり | 不要 |

範囲外として残す事(この依頼の要件の外・未解決の項目ではない):
- node の上の焼きの総数の上限(S3)。zeus では env-host の同時の準備 2 本 × 8 = 16 本が上限で、前の 72 本から下がる。code-host の焼きは zeus では起きない。
- 共有の bytecode の保存先の上書きの取り合い(1 回に焼き直す本数の主因)— card ki-6828e9af04b4。

## 5. 盲検の記録

- 起動: blind/blind-meta.md(第 1 候補 gpt-6-astra は起こせず、opus・effort xhigh で代用。観測した model と effort は未確認)。
- 入力: blind/blind-common.md・blind/blind-a-input.md・blind/blind-b-input.md。返答: blind/blind-a-return.md・blind/blind-b-return.md(加工なし)。
- 盲検 A の試しは `defk` の `:pre` に引数 `repo` の型が無く展開で落ちたので、型を 1 つ足して走らせた(evidence/a/probe-fix.diff.txt・evidence/a/A-repro.txt が落ちた記録)。
- 盲検 B の差分は、提示の検査をそのまま通った(pytest 21 本・pre-commit の semgrep-hy と doeff-linter が Passed — evidence/b/B-tests-with-patch.txt・B-precommit-with-patch.txt)。違反は B の試しの検で実測した(差分ありで `compileJobs` が [None, 4]・差分なしの対照で [None, None] — B-probe-with-patch.txt・B-probe-control-without-patch.txt)。
