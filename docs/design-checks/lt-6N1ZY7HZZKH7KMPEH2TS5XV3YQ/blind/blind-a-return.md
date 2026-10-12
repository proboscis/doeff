# 盲検 A の返答(lt-6N1ZY7HZZKH7KMPEH2TS5XV3YQ)— 責務分離の失敗例

使ったのは file の読みと git の読み(grep・show・diff)だけです。下では次の 3 つを区別して書きます。
- **読みで確かめた**: コードを読んで確かめた事。
- **記録の値**: repo の註や材料に書かれた測りの値。
- **推測**: 確かめていない事。

実験のコードは書いただけで、走らせていません。

## 0. 選んだ変更と、前提を守っているか

- **選んだ変更**: S3 の要求「同時に走る準備が何本でも、node の上の焼きの process の総数を 8 以下にしたい」を、a40add2d9 の上で実装します。
- **前提**: 焼きの道具の呼び手は増やしません。並べる数は、M2 の既定か M3 の `--jobs` の 2 つの経路のまま決まります。S5 の「機体をまたぐ調整は無い」も守り、数えるのは 1 つの機体(zeus)の中だけです。
- **範囲の注意**: 同じ機体に同じ名の worker の 2 つの世代の Pod が並ぶ分(AL-D2)は、Pod をまたぎます。S3 を「Pod ごと」と読むなら、この分は契約の適用範囲の外です。ただし、この分を除いても、下の (a)〜(d) は 1 つの Pod の中で起きます。

## 1. 変更要求と、それが要る理由

a40add2d9 は、焼き 1 回の並列数を天井 8 で止めました。けれども zeus の load を上げるのは、node の上で同時に CPU を取る焼きの process の数です。焼きが重なれば「8 × 重なった数」になります。

今のコードで、1 つの worker の世代が同時に起こしうる焼きは次のとおりです(読みで確かめた)。

| 焼きを起こす者 | 同時の数の上限 | nice | 焼き 1 回の並列数 |
|---|---|---|---|
| env-host(`env_store.launch-waiting` → `env_tool` → `CompileTrees`) | `max-parallel` 2 | 10(`env_rules.prepare-argv`) | M1 の既定(zeus では 8) |
| code-host(`code_store` の `PrepareCode` → `code_rules.prepare-script`) | 無い(版ごとに 1 本。重ねないのは同じ版だけ — `code_store.hy:98-131`) | 足さない | 8。recreate の旧い process が動いている間は 2 |
| `boot.sh` の ROLE=prepare(版上げの前に、走っている Pod の中で焼く — `boot.sh:18-20`) | 外の版上げの Program 次第 | 不明 | 8(`--jobs` を渡さない — `boot.sh:258-262`) |
| 同じ機体の新しい世代の Pod の env-host(`env_store.hy:97-100`) | 世代ごとに別の 2 | 10 | 8 |

- 版上げの時に、env の準備 2 本・版の木 1 本・ROLE=prepare 1 本が重なると 32 本になります。新しい世代も準備すれば 48 本で、36 CPU を越えます。06:50 JST と同じ形がもう一度出ます。これはコードが許す重なり方から導いた数で、測ってはいません。
- code-host の焼きは nice 0 で走ります。そのため、同じ node の nice 0 の service と同じ優先度で CPU を取り合います。

## 2. 関係する主張・前提と、予想に反して変わる物

**主張 S3**: 「総数は M4(2)× M1(8)= 16 で決まる。M4 を下げるか、新しい枠を置く。変わる module は M4(か新しい部品)、変わらない module は M1・M2・M3・M5。M3 と M5 の契約は変わらない」。

**(a) 「総数 = 2 × 8」は、今の時点で成り立っていません**(読みで確かめた)。
- code-host と ROLE=prepare は、M4 の列を通りません。
- `launch-waiting` が数えるのは、env-host 自身の `pending` だけです(`env_store.hy:638-650`)。code-host は別の handler で、準備中の記録を別の session の値で持ちます(`code_store.hy:94-97`)。
- 「準備は nice 10 で起こす」という判断も、env 側の `prepare-argv` だけが持っています。
- つまり、この共同の不変条件に加わる者は主張より多いです。M1〜M5 の表に無い `worker/protocol/code_store.hy` と `deploy/boot.sh` も加わっています。

**(b) 「M4 を下げる」案では、要求を満たせません。** `max-parallel` を 1 にしても、code-host の焼きは数の上限なく並びます。版の木 2 本と env の準備 1 本が重なれば 24 本です。

**(c) 「新しい枠」案は、枠をどこで取っても「変わらない module」を破ります。**

起こす者の側(env-host・code-host・boot.sh)で枠を取る場合:
- 何本分の枠を取るかは、焼き 1 回の並列数で決まります。ところがその値は、起こされた後の子 process の中で M2+M1 が決めます(cgroup の file・affinity・天井)。起こす者はこの値を知りません。
- 取れる道は 2 つです。
  - (i) M2 の読みと M1 の天井を、起こす者へ写す。
  - (ii) 起こす者がいつも `--jobs` を決めて渡す。この場合、M3 の「None = 道具の既定」が使われなくなり、既定の決め方が M1 の外へ移ります。強制方法の表の「M1 の外へ複製しない」に反します。
- 枠の数えを分け合う新しい効果を、env-host と code-host の両方に通す必要もあります。
- code-host は今でも多くの事を 1 つで持っています(木の置き場と rename での公開・壊れた木の作り直し・検めの答えの使い回し・子の起こし・秒の記録・起こす物の集め)。そこに「node の焼きの枠」が加わります。

道具の中で枠を取る場合:
- 3 つの経路が共通に通るのは、`code_prepare.hy` の `main` → `BakeSources` → `bytecode_pool.hy` だけです。
- M2 は、枠の置き場(機体で分け合う dir)という新しい知識を持つことになります。
- M5 の寿命の約束(`with` の終わりで pool の process を全部回収)が、「落ちた時も枠を返す」まで広がります。
- 並列数は min(`cpu-limit-of(...)`, 取れた枠) になります。M1 の純粋な判断に node の状態が入るか、M2 に 2 つ目の決め方が生まれます。
- M3 の `--jobs 2`(`policy.hy:297`)も枠に従わせるには、M2 の公開契約を変える必要があります。今の「N 本で焼く」を「N 本まで」か「枠が空くまで待つ」へ変えることになり、「M3・M5 の契約は変わらない」に反します。

**(d) 主張に無い共同の不変条件が、新しく現れます。**
- env の準備の進みの印は、処理ステージの始まりにだけ書かれます(`env_translation.hy:637-641`)。焼きの間は動きません。
- 停滞の期限は 600 秒です(`env_upkeep.hy:55-59`)。
- 枠を待つ焼きや、枠を 1 本しか取れない焼きは、空の保存先から焼くと 600 秒を越えうります。越えると PREPARE-TIMEOUT で止められます。
- 記録の値: 並べる数 2 で 414 秒(`worker_model.hy:290`)。並べる数 1 の秒は記録に無く、414 秒より長くなると推測しています。

## 3. どの知識がどこへ漏れるか(区別)

| 波及 | 種類 |
|---|---|
| 総数に code-host・ROLE=prepare・他の世代が加わる | 共同の不変条件(主張は加わる者を見落としている)。漏洩ではない |
| 枠の効果を env-host・code-host・boot.sh へ通す | 配線 |
| 起こす者が焼き 1 回の並列数を求める(cpu.max の path・affinity・読めない時は上限なし・天井 8) | 隠すはずの知識の漏洩。M1 の「天井の値・文の読み方」と M2 の「path・読めない時の扱い」が、worker の process と shell へ出る。複製を止める静的な検査は無い |
| 起こす者がいつも `--jobs` を渡す | 判断の持ち主が M1 から起こす者へ移る(漏洩) |
| `--jobs N` の意味を「N 本で」から「まで」か「待つ」へ変える | 公開契約の変更(足すのではなく、意味を変える) |
| 枠の大きさと停滞の期限 600 秒 | 新しく現れる共同の不変条件 |

**根の原因**: 総数を守る者は「誰が何本焼くか」を、起こす前に知る必要があります。けれども M1 の入力を読むのは道具の入口(M2)で、その値は子 process の中でしか決まりません。並列数の判断を「焼き 1 回」に閉じた今の境界は、S1・S6 には効きます。けれども総数の判断(S3)には、境界の位置そのものが合いません。

## 4. 最小の入力・変更例と再現手順

### 4.1 読みだけで確かめる

```sh
R=/home/ubuntu/.worktrees/doeff-wt-ki-4c2f-dc
git -C "$R" grep -n -e 'PREPARE-TOOL' -e 'code-prepare' -e 'code_prepare.hy' a40add2d9 -- packages/doeff-cluster/src packages/doeff-cluster/deploy/boot.sh   # 起こす者が 3 つ出る
git -C "$R" grep -n 'max-parallel' a40add2d9 -- packages/doeff-cluster/src      # env_store/env_rules だけ
git -C "$R" grep -n '"nice"' a40add2d9 -- packages/doeff-cluster/src packages/doeff-cluster/deploy   # env_rules だけ
git -C "$R" show a40add2d9:packages/doeff-cluster/src/doeff_cluster/worker/protocol/code_store.hy | sed -n '94,131p'
git -C "$R" show a40add2d9:packages/doeff-cluster/src/doeff_cluster/worker/protocol/env_translation.hy | sed -n '637,642p'
```

### 4.2 主張どおりに変えても、総数が守られない事を見る

主張どおりの変更は、`env_store.hy:113` の `max-parallel 2` を 1 にする事です。この変更は code-host に効きません。

下の試しは、code-host が 2 つの版の焼きを、数の上限も nice も無く同じ拍に起こす事を見ます。置き場は、試しの worktree の `packages/doeff-cluster/tests/test_probe_bake_launchers.hy` です(commit しない)。

```hy
;; 試し(盲検 A): code-host は版ごとの木の準備を数の上限なく同時に起こし、焼く道具に nice を足さない。
(require doeff-hy.macros [defk deftest <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import os)
(import subprocess)
(import time)
(import doeff [Program with-handlers])
(import doeff_core_effects.handlers [slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [sync-time-handler])
(import doeff_cluster.worker.intent.worker_model [CodeLayout PrepareCode])
(import doeff_cluster.worker.protocol.observations [ObserveCode])
(import doeff_cluster.worker.protocol.code_store [CodeSettings PREPARE-TOOL code-host])


(defk commit-all [repo message]
  {:pre [(: message str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "検の repo の変更を全部 commit し、その sha を返すため。"
  (subprocess.run ["git" "-C" (str repo) "add" "."] :check True)
  (subprocess.run ["git" "-C" (str repo) "-c" "user.name=t" "-c" "user.email=t@t" "commit" "-q" "-m" message] :check True)
  (.strip (. (subprocess.run ["git" "-C" (str repo) "rev-parse" "HEAD"] :check True :capture-output True :text True) stdout)))


(defk start-both [rev-a rev-b]
  {:pre [(: rev-a str) (: rev-b str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "2 つの版の準備を同じ拍に求め、1 秒後の観測を返すため。"
  (<- (PrepareCode rev-a None))
  (<- (PrepareCode rev-b None))
  (time.sleep 1.0)
  (<- views tuple (ObserveCode))
  views)


(defk run-codes [settings program]
  {:pre [(: settings CodeSettings) (: program Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "筋書きを code-host と本物の答え手の下で回すため(test_code_store の run-codes と同じ並び)。"
  (<- answer (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (code-host settings)] program))
  answer)


(deftest test-probe-code-host-launches-every-revision-without-a-cap-or-nice [tmp-path]
  (setv repo (/ tmp-path "repo"))
  (.mkdir (/ repo "pkg") :parents True)
  (.write-text (/ repo "pkg" "__init__.py") "")
  (subprocess.run ["git" "init" "-q" (str repo)] :check True)
  (setv rev1 (! (commit-all repo "c1")))
  (.write-text (/ repo "pkg" "m.py") "X = 1\n")
  (setv rev2 (! (commit-all repo "c2")))
  ;; 焼く道具を起こす hy の代わり: 起きた刻と nice の値を 1 行書き、2 秒眠って断る(数えたいのは同時に起きた数)。
  (setv log (/ tmp-path "starts.log"))
  (setv fake (/ tmp-path "fake-hy.sh"))
  (.write-text fake (+ "#!/bin/sh\n" f"echo \"$(date +%s.%N) nice=$(nice)\" >> '{log}'\n" "sleep 2\nexit 3\n"))
  (os.chmod fake 0o755)
  (setv settings (CodeSettings :repo (str repo) :cache (str (/ tmp-path "cache")) :hy-command (str fake) :tool PREPARE-TOOL
                               :layout (CodeLayout)))
  (setv views (! (run-codes settings (start-both rev1 rev2))))
  (setv lines (.splitlines (.read-text log)))
  (print "\n" (.join "\n" lines) "\n" views)
  (assert (= (len lines) 2) #("2 本が同じ拍に起きていない" lines))
  (setv own (os.nice 0))
  (assert (all (gfor l lines (.endswith l f"nice={own}"))) #("焼く道具に nice が足されている" own lines)))
```

**命令**: `uv run pytest packages/doeff-cluster/tests/test_probe_bake_launchers.hy -q -s`(この 1 file だけ)

**観測すべき結果**:
- `starts.log` に 2 行が出て、2 つの刻の差が 1 秒未満である事。本物の道具なら、それぞれが既定の 8 本の pool を作ります。
- 2 行とも、nice の値が試しの process と同じである事。env 側は nice 10 で起こします。
- 試しが赤なら、(a) の読みは外れで、S3 の主張の側が当たっています。

### 4.3 任意: 本番で読むだけ

版上げの時間に、zeus の worker の Pod で 1 秒ごとに次を数えます。

```sh
ps -eo ni,args | grep '[b]ytecode_pool.hy' | awk '{print $1}' | sort | uniq -c
```

8 本を越えるか、ni=0 の行(code-host の焼き)が在れば、総数 16 の主張が本番でも外れている事の実測になります。

### 4.4 任意: 停滞の期限との連動

保存先を使わず、並べる数 1 で焼いた秒を測ります。道具は木に `.pyc` と完成の印を書くので、木は写しを使います。

```sh
DOEFF_HY_CODE_STORE=off PYTHONDONTWRITEBYTECODE=1 <root>/.venv/bin/hy <worker のコード>/doeff_cluster/worker/entry/code_prepare.hy \
  --revision probe --jobs 1 --entries <宣言の bytecode-entries> --tree <写した木> --roots <根> 2>&1 | grep -o 'compile_s=[0-9.]*'
```

`compile_s` が 600 を越えれば、(d) の実測になります。

## 5. 未確認の前提・不足する情報

- zeus の worker が版の木の job を受けるかは、確かめていません(`CODE_REPO_URL` — `boot.sh:11`・`:477`)。受けないなら、zeus では code-host の分は起きません。その場合も、ROLE=prepare と他の世代の分は残ります。
- 版上げの Program が ROLE=prepare をいつ、どの nice で撃つかは分かりません。撃つ側はこの repo の外にあり、読んでいません。
- 2 つの世代が zeus で同時に準備する頻度は分かりません。
- 06:50 の「準備 1 回で 14 core・load 170」が、どの焼きから来たかの内訳は、材料にありません。
- 並べる数 1 で焼いた時の秒は推測です。記録は、2 本で 414 秒、4 本で 359 秒の 2 つだけです。
- 4.2 の試しは走らせていません。doeff-hy の macro の細部で、直しが要るかもしれません。
- 32 本・48 本は、コードが許す重なり方から導いた数で、測っていません。

## 参考: 詳しくしなかった別の候補

実行環境の準備には、もう 1 つの焼きがあります。`uv sync --compile-bytecode` です(`env_translation.hy:707`・`boot.sh:177`)。
- 並べる数は uv が自分で決めます。commit 807baf47a の記録では、24 スレッドで 0.9 秒、1 CPU で 6.5 秒かかっていて、並列で焼いています。数を決める変数は `UV_CONCURRENT_INSTALLS` だと推測しています。
- ここに天井を効かせようとすると、天井と cgroup の読みの置き場がどこも難しくなります。
  - `native_wheel.py` は標準ライブラリだけで書く決まりなので、Hy の `bake_plan` を import できません。
  - `env_translation` に置くと、「uv の子の変数の定義点は 1 つ」の決まりを破ります。
  - `boot.sh` は venv ができる前に走るので、天井 8 を shell に写すしかありません。
- `UV_DROP` が `UV_*` を落とすので、Pod の env から渡す道もありません。
- 漏洩の形ははっきりしています。ただ、load への効きは秒の単位で小さいと推測したので、主な例にはしませんでした。

**読んだ主な file**(どれも `/home/ubuntu/.worktrees/doeff-wt-ki-4c2f-dc/packages/doeff-cluster/` の下):
- `src/doeff_cluster/worker/protocol/code_store.hy`
- `src/doeff_cluster/worker/protocol/env_store.hy`
- `src/doeff_cluster/worker/protocol/env_translation.hy`
- `src/doeff_cluster/worker/core/code_rules.hy`
- `src/doeff_cluster/worker/core/env_rules.hy`
- `src/doeff_cluster/worker/core/env_upkeep.hy`
- `src/doeff_cluster/worker/entry/code_prepare.hy`
- `src/doeff_cluster/foundation/bytecode_pool.hy`
- `src/doeff_cluster/shared/core/native_wheel.py`
- `deploy/boot.sh`
