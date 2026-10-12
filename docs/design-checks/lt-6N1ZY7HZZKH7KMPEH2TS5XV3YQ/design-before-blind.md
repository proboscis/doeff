# 設計検証 lt-6N1ZY7HZZKH7KMPEH2TS5XV3YQ — 盲検の前の主張(card ki-4c2f5878e524)

この文書の目的: bytecode の焼きの並列数の既定に天井を置いた変更(doeff a40add2d9)について、盲検 A・B の前に、責務の持ち主・公開契約・将来の変更が収まる範囲の主張を固定する。読み手は、この主張を盲検の返答と実測で照らせる状態になる。

- 完了の範囲: 実装完了まで(main に a40add2d9 が在る)。本番で効くのは worker の Pod の入れ替えの後(worker の版 `WORKER_DOEFF_COMMIT` で走るため)で、入れ替えはこの依頼の範囲の外。
- 基準の版: da660e83ed7b8c50a5a4c201e14d501b750bd8c3(変更の前)・実装の版: a40add2d9d4ea5202c3ccc5addead4b11ea9599d。
- 書いた時: 実装と着地の後・盲検の前(主張は実装を見て書いた。盲検の返答を見て書き換えない)。

## 1. 要件(固定)

| id | 要件 | 出所 |
|---|---|---|
| R1 | 実行環境の準備の bytecode の焼きの、zeus での並べる数か回数を下げた形が main に在る | card ki-4c2f5878e524 の受入 cf9780f1c の前半 |
| R2 | 値の変更で worker の Pod を作り直さない | card 本文・ki-b52157bbc19d |
| R3 | 上限(cpu.max)の在る worker の並列数を下げない(atlas 8・eos と k3s-* 2〜3) | 調べの判断(他の機体の準備を遅くしない) |

背景の事実: zeus の worker の Pod は cpu.max を持たず、焼きの並列数の既定(cgroup の上限か使える CPU の数)が 36 になり、準備 1 回で 14 core(nice 10)・load 1 分 170(2026-10-12 06:50 JST)。

## 2. 責務(module)

| id | 実体 | 持つ判断・知識 | 隠す知識 | 公開契約 | effects | 寿命・解放 | 守る不変条件 |
|---|---|---|---|---|---|---|---|
| M1 bake-parallelism | `worker/core/bake_plan.hy` の `cpu-limit-of` と `BAKE-JOBS-CEILING` | cpu.max の文(`"<quota> <period>"` / `"max <period>"`)と使える CPU の数から、焼きの並列数の既定を決める。天井の値 | 天井の値・cpu.max の文の読み方 | `cpu-limit-of(cpu-max: str\|None, available: int) -> int`(defk・純粋) | なし(純粋な判断 — 入力は呼び手が読む) | なし(状態を持たない) | 1 ≤ 答え ≤ min(天井, available, ceil(quota/period)) |
| M2 code-prepare-entry | `worker/entry/code_prepare.hy` の `usable-cpus` と `--jobs` の既定 | どの file(`/sys/fs/cgroup/cpu.max`)と affinity から使える CPU を読むか | file の path・読めない時の扱い(上限なし) | 命令 `code_prepare.hy --jobs N`(N を渡せば既定を使わない) | `ReadText`(os-file-handler が答える) | 1 回の起動の間 | 渡された `--jobs` を既定より優先 |
| M3 override-policy | `worker/core/policy.hy` の `replace-step`・`prepare-actions` と `WorkerPolicy.compile-jobs-while-replacing` | いつ既定を使わずに並べる数を渡すか(recreate の job の旧い process が同じ Pod で動いている間 = 2) | Pod の memory の分け合いの事情 | `PrepareEnv`/`PrepareCode` の `compile-jobs: int\|None`(None = 道具の既定) | なし(action を返す判断) | なし | None の時だけ道具の既定 |
| M4 prepare-concurrency | `worker/protocol/env_store.hy` の `max-parallel`(2)と `worker/core/env_rules.hy` の `launch-order` | 同時に走らせる準備の本数 | 待ちの列・先読みの枠 | `EnvSettings.max-parallel` | 準備の process を起こす(env-host) | 準備ごと | 同時の準備 ≤ max-parallel |
| M5 bake-pool | `foundation/bytecode_pool.hy` の `bake` | 渡された jobs 本の process の pool で焼く | pool の起こし方(fork)・chunksize 1 | 命令 `bytecode_pool.hy --jobs N` | 子 process・file の書き | `with` の終わりで pool の process を全部回収 | jobs ≤ 1 か焼く物 ≤ 1 なら pool を作らない |

## 3. 変更シナリオと主張(6 軸)

| id | 軸 | 適用 | 変える要求 | 主張 | 予想する範囲 | 変わる module | 変わらない module |
|---|---|---|---|---|---|---|---|
| S1 | hardware | 適用 | node の CPU の数や Pod の上限が変わる(例: zeus に cpu.max 16 を足す・64 CPU の node が加わる)、または運用が天井を 6 に下げたい | 天井の値と既定の決め方は M1 だけが持つので、値を変えるのは `BAKE-JOBS-CEILING` の 1 行と M1 の検だけ。M2〜M5 と公開契約(`--jobs`・`compile-jobs`)は変わらない。Pod の上限の変化は M1 の既存の式が吸う(コードの変更なし) | `bake_plan.hy` の 1 行・`test_env_warm.hy` の M1 の検 | M1 | M2・M3・M4・M5 |
| S2 | effects | 適用 | cgroup v1 の node(`cpu.cfs_quota_us`・`cpu.cfs_period_us`)でも上限を読みたい | file を読む effect は M2、文の読み方は M1。v1 は 2 つの file の値なので、M2 の読む file と、M1 へ渡す形(今は v2 の 1 行の文)の両方が変わる。これは M1 と M2 の間の契約(cpu.max の文)の拡張で、隠すはずの知識の漏れではない。M3〜M5 は変わらない | `code_prepare.hy` の `usable-cpus`・`bake_plan.hy` の `cpu-limit-of` の入力の形・それぞれの検 | M1・M2 | M3・M4・M5 |
| S3 | concurrency | 適用 | 同時に走る準備が何本でも、node の上の焼きの process の総数を 8 以下にしたい | M1 の天井は準備 1 本ごとの値で、総数は M4 の同時の本数(2)× M1 の天井(8)= 16 で決まる(共同の不変条件)。総数を 8 にするには、M4 の同時の本数を下げるか、準備をまたぐ枠(node の共有の lock など)を新しく置く必要があり、M1 だけでは収まらない。M3 と M5 の契約は変わらない | `env_store.hy` の `max-parallel` か新しい枠の部品 | M4(か新しい部品) | M1・M2・M3・M5 |
| S4 | storage | 除外 | — | 並列数の判断は保存先(bytecode の保存先・root の置き場)に依らない。保存先の上書きの取り合いは別の card ki-6828e9af04b4 で扱う | — | — | — |
| S5 | distribution | 除外 | — | 並列数は worker の Pod ごと・焼きの process ごとに、その Pod の cgroup と affinity だけから決める。機体をまたぐ調整は無く、単一機体から cluster への移行で判断の持ち主は変わらない(既に worker ごとに決めている) | — | — | — |
| S6 | simulation | 適用 | 本物の 36 CPU の機体や cgroup の file 無しで、既定の並列数を決定的に確かめたい | M1 は純粋な判断なので、入力の文と数を渡すだけで確かめられる。M2 の読みは `ReadText` の effect なので、答え手を差し替えれば本物の file を読まずに確かめられる。どちらも module を変えずにできる | 検の file だけ | なし | M1〜M5 |

主張の前提:
- 焼きの道具は M2 の既定か、M3 が渡す `--jobs` のどちらかで並べる数が決まる(ほかの経路で並べる数を決める呼び手は無い — boot.sh の自己起動の焼きも M2 の既定を使う)。
- M1 の検(`test_env_warm.hy` の `test-compile-parallelism-*`)は doeff の検の一式で走る。
- 本番での効きは worker の版上げの後(R2 のため、この変更のために Pod を作り直さない)。

## 4. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 既定の並列数は天井を越えない(M1) | 純粋な判断の検(上限なし 36 → 8・file なし 36 → 8・上限 16 → 8・atlas の上限 8 → 8) | `packages/doeff-cluster/tests/test_env_warm.hy` の `test-compile-parallelism-stays-under-the-ceiling-without-a-pod-limit` | pytest(doeff の検の一式・着地の門) | 天井の値そのものが妥当か(8 か 6 か)は検で決められない — 測りで決める |
| 上限の在る pod の並列数は下げない(R3) | 同じ検の `"800000 100000\n" 16 → 8`・既存の `"400000 100000\n" 16 → 4` | 同上と `test-compile-parallelism-follows-the-cgroup-cpu-limit` | 同上 | 天井より大きい上限(16 など)の pod は 8 に下がる(今の cluster には無い) |
| 既定の決め方を M1 の外へ複製しない | 他の module は `cpu-limit-of` を呼ぶだけで、天井の数を持たない(`BAKE-JOBS-CEILING` の名で引く・註も名で書く) | `code_prepare.hy` の `usable-cpus` | 静的な検査は無い(数の複製を止める規則は置いていない) | 複製を機械で止めていない。盲検 B で確かめる |
| None の時だけ既定(M3) | 既存の検(`test_code_rules.hy`・`test_env_rules.hy`・`test_policy.hy` の compile-jobs の検) | 各 file | pytest | — |
| 同時の準備 ≤ max-parallel(M4) | 既存の `launch-order` の検 | `tests/test_env_warm.hy` ほか | pytest | node の総数(S3)は強制していない |
