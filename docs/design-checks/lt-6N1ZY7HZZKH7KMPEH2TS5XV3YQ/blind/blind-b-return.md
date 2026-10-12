盲検 B の失敗例を 1 つ作りました。何も実行しておらず、file と git を読んだだけです。「検査を通る」と書いた所はすべて読みからの推測で、実測ではありません。盲検 A の file(blind-a-input.md)は読んでいません。

**要旨**: 先読みの準備が、走っている準備の横で起きる時だけ、焼きの並べる数を天井の分け前に絞る、という変更です。これを M4 の側(env_rules・env_store)に書くと、「いつ既定を外すか」(M3 の判断)と「並べる数をいくつにするか」(M1 の判断)が M4 へ移ります。その結果、atlas では並列数が下がり(R3 の破り)、上限 2〜3 の pod では pod の上限を越えます。

---

## 1. 機能要求と最小差分

**要求(S3 の形)**: zeus の worker では、job の準備と先読み(温める表の root)の準備が同時に走ると、焼きの process が 8 + 8 = 16 本並びます。先読みは急がないので、走っている準備の横で起きる先読みは、天井を分けた数で焼いて job の準備に CPU を譲りたい。job の準備の速さは変えない。

**この場所に書きそうな理由**
- `launch-order` には既に「先読みは同時の枠の 1 つを job に残す」という方針があります。
- 起こす瞬間に「ほかの準備が走っているか」を知っているのは env-host(M4)だけです。M3 の `plan` は、1 拍の中で job と先読みの `PrepareEnv` をどちらも `None` で返すだけです(test_env_warm.hy 486 行の検が確かめている並び)。
- 天井は設計の求めどおり `BAKE-JOBS-CEILING` の名で引き、数 8 は書きません。

```diff
--- a/packages/doeff-cluster/src/doeff_cluster/worker/core/env_rules.hy
+++ b/packages/doeff-cluster/src/doeff_cluster/worker/core/env_rules.hy
@@ -8,4 +8,5 @@
 (import json)
 (import doeff_cluster.shared.intent.runtime_env_model [EnvFailure EnvFailureKind])
 (import doeff_cluster.worker.core.env_upkeep [PrepareLimits])
+(import doeff_cluster.worker.core.bake_plan [BAKE-JOBS-CEILING])
 
@@ -36,4 +37,16 @@
   launched)
 
 
+(defk launch-compile-jobs [requested warm running]
+  {:pre [(: requested (| int None)) (: warm bool) (: running int)] :post [(: % (| int None))]
+   :tags {:context "worker" :role "judgment"}}
+  "起こす準備 1 本の焼きの並べる数を決めるため(requested = 頼みの数 — None = 道具の既定・running = 今走っている準備の数)。先読みを
+   走っている準備の横で起こす時は、天井 BAKE-JOBS-CEILING を走っている本数と自分とで分けた数で焼く — 先読みは job の準備に CPU を
+   譲る(launch-order が同時の枠の 1 つを job に残すのと同じ向き・zeus で job の準備と先読みが並ぶと焼きの process が 8 + 8 本並ぶ)。
+   頼みが数を持つ時・job の準備・ひとりで走る先読みは頼みのまま。"
+  (if (and warm (is requested None) (> running 0))
+      (max 1 (// BAKE-JOBS-CEILING (+ running 1)))
+      requested))
+
+
 (defk prepare-request [declared name platform root known min-free-bytes compile-jobs]
--- a/packages/doeff-cluster/src/doeff_cluster/worker/protocol/env_store.hy
+++ b/packages/doeff-cluster/src/doeff_cluster/worker/protocol/env_store.hy
@@ -56,3 +56,3 @@
 (import doeff_cluster.worker.core.env_rules [ReadyAnswer launch-order prepare-request prepare-argv answer-of-text prepare-outcome
-                                             overdue-failure root-project declared-project])
+                                             overdue-failure root-project declared-project launch-compile-jobs])
 (import doeff_cluster.worker.protocol.heartbeat [env-report])
@@ -645,5 +645,7 @@
   (for [key order]
     (val entry (get left key))
     (:= left (dfor #(k v) (.items left) :if (!= k key) k v))
-    (<- started PendingEnv (launch-prepare settings key entry.runtime-env entry.compile-jobs entry.warm))
+    ;; 走っている準備の横で起こす先読みは、焼きの並べる数を絞る(launch-compile-jobs)。
+    (<- jobs (| int None) (launch-compile-jobs entry.compile-jobs entry.warm (len running)))
+    (<- started PendingEnv (launch-prepare settings key entry.runtime-env jobs entry.warm))
     (:= running (| running {key started})))
--- a/packages/doeff-cluster/tests/test_env_warm.hy
+++ b/packages/doeff-cluster/tests/test_env_warm.hy
@@ -49,2 +49,3 @@
 (import doeff_cluster.worker.core.bake_plan [BAKE-JOBS-CEILING cpu-limit-of])
+(import doeff_cluster.worker.core.env_rules [launch-compile-jobs])
 (import doeff_cluster.shared.core.detached_rules [submit-detached-task])
@@ -538,4 +539,12 @@
   (assert (= (! (cpu-limit-of "800000 100000\n" 16)) 8) "atlas の上限 8 は今の並列数のまま"))
 
 
+(deftest test-a-warm-preparation-beside-a-running-one-bakes-with-a-share-of-the-ceiling
+  ;; zeus で job の準備と先読みが並ぶと、焼きの process が 8 + 8 本並ぶ。走っている準備の横の先読みは天井を分ける。
+  (assert (= (! (launch-compile-jobs None True 1)) (// BAKE-JOBS-CEILING 2)) "走っている準備 1 本の横の先読みは天井を 2 本で分ける")
+  (assert (is (! (launch-compile-jobs None True 0)) None) "ひとりで走る先読みは道具の既定")
+  (assert (is (! (launch-compile-jobs None False 1)) None) "job の準備は道具の既定")
+  (assert (= (! (launch-compile-jobs 2 True 1)) 2) "方策が数を載せた頼みはそのまま"))
+
+
 (deftest test-bytecode-entries-travel-in-the-declaration-but-not-in-the-key
```

## 2. 本来の持ち主と、破れる契約

**「いつ既定を使わずに数を渡すか」が M3 から M4 へ移る**
- M3 の公開契約は「`PrepareEnv.compile-jobs` が None なら道具の既定」です。
- 変更の後も、M3 の `plan` は先読みに `None` を出し続けます(M3 の検は緑のまま)。
- ところが env-host が起こす時に、その None を 4 に書き換えます。端から端まで見ると「None = 道具の既定」が成り立ちません。

**「並べる数をいくつにするか」が M1 の外に出る**
- M1 は「天井の値と、既定の決め方」を持つ所です。
- 変更後は、`BAKE-JOBS-CEILING` を使った別の決め方が M4 に置かれます。この決め方は cgroup も affinity も見ません。
- M2 の契約では「渡された `--jobs` が既定より優先」です(code_prepare.hy 352〜355・378 行 → bytecode_pool.hy の `ProcessPoolExecutor :max-workers jobs`)。
- そのため M1 の不変条件「答え ≤ min(天井, available, ceil(quota/period))」は、先読みの実際の並列数をもう縛りません。

| pod | M1 の答え | 走っている準備の横の先読み | 結果 |
|---|---|---|---|
| atlas(cpu.max 8) | 8 | 4 | 下がる(R3 の破り) |
| eos・k3s-*(cpu.max 2〜3) | 2〜3 | 4 | pod の上限を越える(`cpu-limit-of` が防ぐために作られた症状の型) |
| zeus(上限なし) | 8 | 4 | 狙いどおり |

**設計の主張とのずれ**
- **S1**(天井を変えるのは 1 行と M1 の検だけ・Pod の上限の変化は M1 の式が吸う): 天井を 6 にすると、M4 を通って先読みの数が 3 に変わります。先読みについては Pod の上限の変化を吸いません。
- **S3**(M3 の契約は変わらない): None の意味が変わるので崩れます。
- **M4 の受け持ちが増える**: これまでは「同時に走らせる準備の本数」だけでしたが、準備ごとの焼きの並列数まで決めるようになります。

**波及(読みからの推測)**: env-host の `PrepareEnv` の答え手は、走っている先読みを job の準備へ上げる時に「走っている焼きの並べる数は変えない」(env_store.hy 712 行付近の註)。そのため、先読み中の root を求めて来た job は、atlas でも 4 並列の焼きを待つことになります。要求の「job の準備の速さは変えない」も破れます。

## 3. 提示された各検査が拒否しない理由(読みによる推測・未実行)

**(1) pytest `test_env_warm.hy`**
- M1 の検は `cpu-limit-of` だけを呼び、`cpu-limit-of` は変えていません。
- sim の筋書きの検では、`PrepareEnv` に答えるのは sim/local.hy の宿です(2094 行)。env_store の `launch-waiting` は通りません。
- `test-the-worker-warms-after-its-jobs-...` は M3 の `plan` が None を出すことを確かめるだけで、今も None です。
- 足した検は、作りから通ります。

**(2) pre-commit の semgrep(semgrep-hy・.semgrep.yaml)**
- この 3 file に当たる規則は次のとおりです: `hy\.mangle`、`observations.(readiness|metrics)`、業務の語(agora・kanban・budget・acp ほか)、defhandler の位置、defprogram、defk の引数名 effect/eff/k、comprehension や素の fn の中の `!`、WithHandler、`claude -p`。
- 足した文はどれにも当たりません。並列数や天井の参照を見る規則はありません。設計の表も「静的な検査は無い」としています。
- Python の 2 項(semgrep・root の doeff-linter)は `types: [python]` なので、.hy は渡りません。

**(3) doeff-linter(package の設定)**
- 層の向き: core の env_rules → core の bake_plan は `:imports [core intent]` の内です。protocol の env_store → core の env_rules は今も import しています。
- 生の副作用・defn は使っていません。:tags を書き、`:role judgment` は core で許されます。defk は `<-` と `!` で呼んでいます。
- 写像を新しく組んでいないので、DOEFF172 の基点(env_store 2)は変わりません。
- architecture.hy には :single-point-vocabulary・:confined-spellings・:call-sites・:counted-spellings の宣言が無く、environment_names の設定もありません。DOEFF146・148・159・161・108 には判じる物がありません。
- 注意: hook は linter の binary が置き場に無い時「測れない」と出して 0 で通ります。測ったかどうかの確認が要ります。

**(4) code-quality(fast の段)**
- 中身の定義が repo に無く、読めていません。通るかどうかは書けません。

## 4. 最小の確かめ方(設計者が実行する)

```sh
git -C /home/ubuntu/.worktrees/doeff-wt-ki-4c2f-dc worktree add --detach /tmp/blind-b-exp a40add2d9
cd /tmp/blind-b-exp && git apply /path/to/blind-b.patch        # 上の差分
# 提示の検査(期待: 全部通る)
uv run pytest packages/doeff-cluster/tests/test_env_warm.hy
git add packages/doeff-cluster/src/doeff_cluster/worker/core/env_rules.hy \
        packages/doeff-cluster/src/doeff_cluster/worker/protocol/env_store.hy packages/doeff-cluster/tests/test_env_warm.hy
uv run --no-project --python 3.12 python scripts/precommit_locked.py run --files \
  packages/doeff-cluster/src/doeff_cluster/worker/core/env_rules.hy \
  packages/doeff-cluster/src/doeff_cluster/worker/protocol/env_store.hy packages/doeff-cluster/tests/test_env_warm.hy
# 期待: semgrep-hy と doeff-linter-doeff-cluster が Passed(「測れない」の行が出ていないこと)
```

**違反の実測**: 下の検を、pre-commit の後に `packages/doeff-cluster/tests/test_env_host_judgments.hy` の末尾へ足します(記録用・commit しない)。

```hy
(import doeff_cluster.worker.core.bake_plan [cpu-limit-of])
(import doeff_cluster.worker.protocol.env_translation [compile-argv])
(import doeff_cluster.worker.intent.env_prepare_model [BytecodeTree])

(defk job-then-warm-with-policy-none [key text other-key other-text]
  {:pre [(: key str) (: text str) (: other-key str) (: other-text str)] :post [(: % None)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "plan が 1 拍で返す並び(job の準備と先読み・どちらも並べる数 None)を env-host へ頼むため。"
  (<- (PrepareEnv key text None))
  (<- (PrepareEnv other-key other-text None :warm True))
  None)

(deftest test-probe-blind-b-policy-none-becomes-a-number-beside-a-running-preparation [tmp-path]
  (val runs (ToolRuns))
  (<- settings EnvSettings (settings-at tmp-path))
  (<- env RuntimeEnv (declared 1))
  (<- other RuntimeEnv (declared 2))
  (<- key str (env-key-of env))
  (<- text str (declared-text env))
  (<- other-key str (env-key-of other))
  (<- other-text str (declared-text other))
  (<- (scheduled (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (inline-env-tool runs)
                                 (env-host settings)]
                   (job-then-warm-with-policy-none key text other-key other-text))))
  (assert (= (len runs.launches) 2) runs.launches)
  (val jobs (lfor path runs.launches (get (json.loads (.read-text (Path path))) "compileJobs")))
  (assert (= jobs [None 4]) jobs)
  (val trees #((BytecodeTree :tree "/s/roots/k/app" :roots #(".") :declared True)))
  (<- argv tuple (compile-argv "uv" "/w/code_prepare.hy" "/s/roots/k/app" trees #() (get jobs 1)))
  (assert (in #("--jobs" "4") (tuple (zip (cut argv 0 -1) (cut argv 1 None)))) argv)
  (assert (= (! (cpu-limit-of "800000 100000\n" 16)) 8) "atlas: M1 は 8 — 先読みの 4 はこれより低い")
  (assert (= (! (cpu-limit-of "200000 100000\n" 16)) 2) "上限 2 の pod: M1 は 2 — 先読みの 4 はこれを越える"))
```

```sh
uv run pytest packages/doeff-cluster/tests/test_env_host_judgments.hy -k probe_blind_b
```

**期待する結果**
- 差分を当てた木: 通る。M3 が None を渡した先読みの頼みに `compileJobs` 4 が載り、命令は `--jobs 4` になります。
- 対照(a40add2d9 に、この検だけを足した木): `jobs` の assert が `[None, None]` で赤になります。4 がこの変更から来ていることの確かめです。

**任意**: 設計の表が挙げる既存の検も緑のままのはず(推測)です。これは全数検査ではなく 4 file です。
```sh
uv run pytest packages/doeff-cluster/tests/test_policy.hy packages/doeff-cluster/tests/test_env_rules.hy \
  packages/doeff-cluster/tests/test_code_rules.hy packages/doeff-cluster/tests/test_env_host_judgments.hy
```
`test-the-prepare-request-carries-the-compile-jobs` は 2 本とも warm=False なので、この変更の外です。

## 5. 未確認の前提と、足りない情報

- **実測は 0 件です。** 上の「通る」「赤」はすべて読みからの推測です。
- 本番で、atlas や eos・k3s の worker が job の準備と先読みを同時に走らせる頻度は分かりません。読みで分かっているのは次の 2 点だけです: `plan` が 1 拍で両方を返すこと、max-parallel 2 で job 1 本と先読み 1 本が並べること。
- 上限 2 の pod で 4 並列にした時に、どれだけ絞られるかは測っていません。根拠は `cpu-limit-of` の docstring の過去の測り(上限 4 で 16 並列 → 97%)からの類推です。
- 次の 3 つは、既存の code の書き方からの推定です: Hy の `//`、`(<- x (| int None) …)` の形、probe の `scheduled` が要るかどうか。
- hook が使う doeff-linter の binary の版と規則は、この commit の source から読んだだけです。置き場の binary の実体は見ていません。
- code-quality(fast)の規則は repo に無く、判じられません。

WAIT: work — 設計者による実験の結果と次の依頼
