提示された検査をすべて通るのに責務を侵す実装を 1 つ作り、一時の複製の中で実際に確かめました。中身は、待ちの子から分かれる道でも uv が置く `UV` を task に見せるという機能(主張 S2)を、foundation の `replace-environ` に足す実装です。共有の作業木は書き換えておらず(`git status` は空)、commit・push・他の会話への連絡もしていません。

## 1. 機能要求と差分

**要求(S2)**: 冷えた道では `uv run` が子へ `UV`(uv 本体の絶対 path)と `UV_RUN_RECURSION_DEPTH` を置きます。待ちの子から分かれる task でも同じ値を見せ、task の script が `"$UV" …` で uv を呼べるようにします。

**実装者がこの場所を選ぶ理由**(いずれも普通に通りそうな理屈です):
- 純粋な判断 `job-launch` に渡る `uv` は命令の名で、`main.hy` の既定は `"uv"` です。絶対 path に解くには探し道を引く I/O が要り、判断の側には書けません。
- 待ちの子は `warm-child-argv` によって `uv run … --project <root>` の下で起きるので、自分の環境に正しい値をすでに持っています。
- 環境変数に触ってよい層は foundation だけです(DOEFF106)。だから `foundation/child_environ.hy` に置くことになります。

**差分**(`/tmp/blind-b.5Kq7/change.diff`):
```diff
--- packages/doeff-cluster/src/doeff_cluster/foundation/child_environ.hy
@@ (defk replace-environ [env]
-  "この process の環境変数を、名と値の組(#(名 値))の列で置き換えるため。答え = 置いた環境変数の数。"
+  "…(同じ文)… 待ちの子は worker が `uv run --project <root>` で起こすので、uv が子へ置く UV と UV_RUN_RECURSION_DEPTH を
+   自分の環境に持つ。…分かれた子 A にも、頼みの env に無ければこの 2 つを継ぐ(task が $UV で uv を呼べるように…)。"
+  (val kept (dfor name #("UV" "UV_RUN_RECURSION_DEPTH") :if (in name os.environ) name (get os.environ name)))
   (.clear os.environ)
-  (.update os.environ (dict env))
+  (.update os.environ (| kept (dict env)))
   (len os.environ))
```
関数の型は変わらず、`doeff_hy.static_stub` で作り直した `.pyi` も今の物と同じでした(実測)。

## 2. 本来の持ち主と、侵している契約

**本来の持ち主**: 「uv run が子へ何を足すか」の知識と「分かれる task が何を見るか」の判断は M-judge が持ちます。具体的には `uv-run-environ` の 1 か所と、それを載せた `entry-env` です。

この実装で、その知識と判断が M-forked(foundation)へ移ります。値の出どころも、判断への入力から「待ちの子が起動した時の process の状態」へ移ります。

侵している契約は次のとおりです。
- **M-forked の契約**「頼みの env を A の環境としてそのまま置く(足さない・引かない)」: A が頼みに無い 2 つの名を足します。判断が頼みに入れなかった名も復活するので、判断の側から「引く」ことができなくなります。
- **JobLaunch.entry-env の意味**「入口が走る環境」: 入口が実際に見る環境と一致しなくなります。
- **不変条件**「待ちの子そのものは env の値を持たない(頼みの env は A の中でだけ置く)」: 待ちの子の起動時の環境が、task の環境の 2 つ目の出どころになります。
- **S2 の主張**「uv の足し方の写しは uv-run-environ 1 か所」: 写しが 2 か所に割れます。PATH と VIRTUAL_ENV は M-judge、UV と UV_RUN_RECURSION_DEPTH は M-forked です。S2 で「変わらない」とされた M-forked が変わります。
- **S4 の主張**: 模擬の答え手は頼みの env しか見ません。task の env を再現するには「A は待ちの子の UV を継ぐ」という規則を持たせる必要が出て、主張が崩れます(コードからの推測)。
- **M-rules と warm_host**: 待ちの子の起こし方(`warm-child-argv` が uv run か、直接 python か)が、task の `UV` を黙って左右するようになります。env の面では「変わらない」とされていた所です(コードからの推測)。

## 3. 各検査が拒否しない理由(すべて実測)

変更後に、検査を 1 file ずつ走らせました。

| 検査 | 結果 | 通る理由 |
|---|---|---|
| `test_job_launch.hy` | 5 passed | `launch.hy` に触れておらず、`entry-env` は変わらない |
| `test_warm_host.hy`(名指しの test を含む) | 5 passed | 台本の答え手は A を走らせない。記録するのは頼みの env で、そこは変わらない |
| `test_task_venv_on_both_paths.hy` | 1 passed | 待ちの子は本物の uv で起き、変更した code も実際に通る(下の probe で確認)。比べているのは PATH(全部)と VIRTUAL_ENV だけで、足した 2 つの名は比べない |
| `test_warm_child.py` | 11 passed | 待ちの子を `env={"PYTHONPATH": …}`(uv なし)で起こすので、継ぐ名が無く振る舞いが変わらない。env の検査も「含む」かどうかで、等しいかは見ない |
| semgrep(hook `semgrep-hy` と同じ命令をこの file に) | 11 rules・0 findings・exit 0 | os.environ を見る規則は doeff-ml-nexus と doeff-agents の path に限られる。Python の semgrep 項と semgrep-other-files は `.hy` を受けない |
| doeff-linter(`lint-doeff-cluster.sh` と同じ形) | 所見なし・exit 0。warning の基点の比べも exit 0 | DOEFF106 は foundation を許している。DOEFF172 は `:spells "env"` を持つ core の外の定義を数えない |

doeff-linter の測り方には注意が要ります。
- hook が使う固定版の binary は、この機体の置き場にありませんでした(`doeff_linter_locked.py which` が exit 3)。この環境で hook を走らせると「測れない」と出て通ります。
- そこで、同じ source から debug 版を組んで測りました。版の名乗りは f8356ec98 で、d6ebc3db9 との差は docs だけです。
- 同じ binary が `worker/core` に置いた env の読みを DOEFF106 で止めることも確かめ、linter が `.hy` を実際に見ていることを確認しました。

## 4. 検査通過と責務違反の両方を確かめる手順

1. 作業木を `cp -a` で `/tmp/blind-b.5Kq7/doeff` へ複製します。
2. 複製の `.venv` の中で、絶対 path を複製先へ書き換えます(`*.pth`・`__editable__*.py`・`bin/*` の shebang の `/work/toolset/worktrees/doeff-wt-ki-b884-design-check`)。しないと元の木の source が読まれます。
3. 自作の probe `/tmp/blind-b.5Kq7/test_blind_b_probe.hy` を `packages/doeff-cluster/tests/` に置きます。1 つの job-launch の答えで本物の冷えた道と分かれる道を起こし、次の 2 つを別々に判じます。
   - (a) 入口が見る `UV` が 2 つの道で同じか(S2 の機能が満たされたか)
   - (b) 分かれた子の環境が `plan.entry-env` と完全に同じか(M-forked の契約)
4. `uv run --no-sync pytest -v -s packages/doeff-cluster/tests/test_blind_b_probe.hy` を、変更の前と後で走らせます。
5. 変更後に、提示された 4 file・semgrep・linter を上の表のとおり走らせます。

probe の結果(実測):

| | (a) S2 の機能 | (b) M-forked の契約 |
|---|---|---|
| 変更前 | FAIL(cold `UV='/usr/local/bin/uv'`・warm `UV=None`) | PASS(足された名なし) |
| 変更後 | PASS(どちらも `/usr/local/bin/uv`・深さ 1) | FAIL(頼みに無い名 `['UV', 'UV_RUN_RECURSION_DEPTH']`) |

つまり変更後は、機能は動き、提示された検査は全部緑で、契約だけが破れています。

## 5. 確かめていない前提と足りない情報

- **推測**: 本番の worker に渡る `--uv` の値。`main.hy` の既定は `"uv"` ですが、配備の引数はこの木にありません。絶対 path が渡されているなら、「判断の側では引けない」という置き場の理由は弱まります。
- **この機体での実測のみ**: uv 0.12.19 が `UV` と `UV_RUN_RECURSION_DEPTH=1` を置くこと。本番の worker では確かめていません。
- **推測**: S4(模擬との食い違い)と M-rules への影響は、コードを読んでの推論で、走らせていません。
- **走らせていない物**: 固定版の linter binary、pre-commit そのもの、提示されていない検査(`test_warm_child_uv_reap.hy`・doeff-core-effects の契約の検・日次の全体の検)、macOS。これらが拒否するかどうかは分かりません。
- probe は自作の検で、提示された検査ではありません。

一時の dir `/tmp/blind-b.5Kq7/` に、変更済みの複製・差分・probe・各 log が残っています(linter の組み立て dir は消しました)。

WAIT: work — 呼び手の次の依頼
