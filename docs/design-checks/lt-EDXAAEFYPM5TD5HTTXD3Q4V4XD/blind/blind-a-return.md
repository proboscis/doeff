## 失敗例: traverse-lib の「件の失敗の方針」を変えると failing-child を変える必要が出て、変えないと検が何も言わずに弱まる

### 1. 変更の要求と、それが要る理由

**要求**: traverse-lib の Traverse handler(sequential・parallel)が件の失敗として記録する例外を、Fail を通った失敗だけにする。Fail を通る失敗とは、try-call や backend が出す Fail のような業務上の失敗です。それ以外の例外(f の bug・macro の展開の不具合・defk の :pre/:post の違反)は件の失敗として記録せず、Traverse の外へ上げます。

**理由**:
- test_examples_run.py の頭の docstring(10〜12 行)に実例があります。When の展開が件の中で壊れても、ex09 は件を failed と印字して 0 で終わりました。bug が「失敗した件」という data に紛れて見えなくなる問題で、lib の側で直したくなるのは自然です。
- lib はすでに「件の失敗ではない例外」を区別し始めています。ItemOutcomeNotPassedError と TaskCancelledError は記録せずに上げます。また Fail は「low-level failure notification」で、try_call は例外を Fail に変える道具です。失敗を伝える正規の経路は Fail だ、という考え方がコードにすでにあります。
- 変更の規模は handlers.py の 13 行と、ex08 の 1 か所(raise を Fail にする)だけです。

### 2. 関係する主張・前提と、予想に反して変わる物

**前提は守っています。** §4 の末にある前提は「Python 3.12 以上」と「例は `hy <例>` で走る script」の 2 つで、どちらも変えていません(3.14t・hy のまま)。件の失敗の方針は §2 で traverse-lib の持ち分とされていて、前提として固定されてはいません。したがってこの例は契約の適用範囲の中です。

**反証される主張**:
- **S1**: 「traverse-lib が変わり…例が件の結果を印字しなくなる → example-check を変えずに赤」「変えない物: example-check・failing-child」。実測では、方針を変えた後に例が件の結果を印字しなくなっても緑のままでした。赤に戻すには failing-child を変える必要がありました。
- **§3-3**: 「子が 0 以外で終われば失敗は終了コードに出たと数える」。方針を変えると、件を持つ例すべてで子が 0 以外で終わります。その結果、出力を比べる検が実質的に働かなくなります。
- **S3 の趣旨**: 「依る物が変わった時は黙って緑にならない」。failing-child が依る物の表(Traverse.__init__ の形・doeff の run の在りか)に traverse-lib の失敗の方針が無く、方針が変わっても何も言わずに緑になります。

**変わる module**:
- traverse-lib: 変更の原因です。
- examples: ex08 の追従が要ります。これは正当な追従です。
- failing-child: 知識の漏れのために変える必要があります。
- example-check: 方針の変化を見逃さないようにするなら変える必要があります(共同の不変条件)。

### 3. どの知識がどこへ漏れているか

**漏れている所**:
- **failing-child の docstring(6〜7 行)**: 「Traverse の handler は件の中の例外を件の失敗として記録して先へ進むので…出力の比べ方だけが失敗を捕まえる」。traverse-lib の方針を、自分の前提として書き写しています。注入の形 `raise InjectedCaseError` が「記録される件の失敗」になるのは、この方針の下でだけです。
- **example-check の 441 行** `result.returncode != 0 or not _matches(...)`: 「子が 0 以外で終わった = 例が注入された失敗を出力に出した」と読んでいます。これは方針が「記録して先へ進む」の時にだけ正しい読み方です。新しい方針では「lib が注入された例外を上げた」場合も 0 以外になり、検は 2 つを区別できません。

**4 つの種類の区別**:
- **公開契約の変更**: traverse-lib から examples への影響(ex08 の raise → Fail)です。正当な追従で、欠陥ではありません。
- **配線の変更**: failing-child が Fail を import するのは配線ですが、import が必要になる理由そのものは知識の漏れです。
- **共同の不変条件(宣言されず、強制もされていない)**: 「failing-child が注入する失敗は、traverse-lib が件の失敗として記録する種類である」「子の終了コードが何を意味するか」の 2 つです。設計の §2〜§5 のどこにも無く、守らせる検もありません。
- **隠すはずの知識の漏洩**: failing-child の追従の仕方は 2 通りありますが、どちらも漏れます。
  - (a) traverse-lib の私的な印(実験では `__doeff_traverse_item_failure__`)を、注入する例外に付ける。traverse-lib が隠すはずの実装を failing-child が知ることになります(この案は試しておらず、推測です)。
  - (b') 注入を `return (yield Fail(InjectedCaseError(...)))` にする。traverse-lib の公開の Fail を使う形ですが、注入した失敗がどう扱われるかは、各例の Fail handler の組(examples の持ち分)で決まります。replace-with-zero の組では失敗にならず、0 を代わりの値とする成功に変わりました。その結果、ex02 と ex04_traverse_macro の case-calls が 25 から 26 に変わりました(実測)。
  - 素朴な案 (b)(Fail の後に AssertionError を上げる)も試しました。上の 2 例で子が自分の AssertionError のために 1 で終わり、441 行の逃げ道で緑のまま通りました(実測)。failing-child 自身の壊れも、検は何も言わずに通してしまいます。

### 4. 最小の変更例・再現手順・観測すべき結果

**実験の木について**: `/tmp` に worktree を作ろうとしましたが、hook(worktree-placement-guard)に止められました。そこで `/work/toolset/worktrees/doeff-wt-ki-f8a5-blind-a`(86ecd1344 の detached)に作りました。木は変更の無い状態に戻してあります。script と差分は `/tmp/ki-f8a5-blind-a/` に置きました。

**置いた物**:
- `/tmp/ki-f8a5-blind-a/policy.diff`: 方針の変更(handlers.py に `_is_item_failure` を足し、fail_handler が印を付ける)と ex08 の追従。
- `/tmp/ki-f8a5-blind-a/mutation-M.diff`: S1 後半の変異。ex04_simple の印字を `(print "done")` だけにし、期待の出力を `done` にする。
- `/tmp/ki-f8a5-blind-a/child-via-fail-b2.diff`: failing-child の追従の案 (b')。
- `/tmp/ki-f8a5-blind-a/check-exit0.diff`: example-check の終了コードの逃げ道を外す案。
- `/tmp/ki-f8a5-blind-a/scripts/repro.sh <木>`: 上の差分を順に当てて `-k ex04_simple` を走らせ、最後に元へ戻す。
- `/tmp/ki-f8a5-blind-a/scripts/probe_children.sh <木>`: 例ごとに、失敗を注入した子の終了コードと実行の印を並べる。

**観測した結果(すべて実測)**:

| 状態 | 結果 |
|---|---|
| 86ecd1344 のまま | 48 passed・1 skipped。子は 10 例すべて exit 0 |
| 方針の変更だけ | 48 passed・1 skipped(緑)。件を持つ 9 例の子はすべて exit 1(InjectedCaseError が上がる)。case-calls は 1(ex08 だけ 11) |
| 今の方針 + 変異 M | test_case_failure_shows_in_compared_output[ex04_simple] が赤(S1 の主張どおり) |
| **新しい方針 + 変異 M** | **4 passed。何も言わずに緑** |
| 新しい方針 + 変異 M + 案 (b') | 赤に戻る。変異 M を外すと 48 passed。子はすべて exit 0 |
| 新しい方針 + 元の子 + check-exit0 | 9 failed(方針の変化で検が赤になる)。今の方針 + check-exit0 は 48 passed |

### 5. 未確認の前提・不足する情報

**実測したこと**: 上の表の内容すべてです。86ecd1344 の木で、木の uv の環境を使い、`test_examples_run.py` だけを走らせました。

**推測にとどまること**:
- 方針の変更が doeff-traverse の計画に実際にあるかは知りません。読む範囲の制限で、card や ADR は読んでいません。現実味の根拠は、test file の docstring にある事故の記録と、lib がすでに一部の例外を件の失敗から外していることだけです。
- 方針の実装の形(例外に印を付ける)は私が選んだものです。ほかの形(cause を包む型、handler の引数で選ぶ)でも、failing-child の `raise` が記録されない側に入る限り結論は同じだと考えますが、実測したのは 1 つの形だけです。
- 方針を handler ごとに選べる形(例 `(sequential :strict True)`)にした場合は、それを選んだ例だけで同じ素通りが起きると推測します(未実測)。
- 案 (a) は試していません。
- fail_handler を入れない例は今は 1 つもありません。そうした例があれば、案 (b') の注入は UnhandledEffect になり、新しい方針では上へ上がって、441 行の逃げ道で緑になると推測します(未実測)。
- contract-tests の経路と日次の経路では走らせていません。走らせたのは file 1 本と `-k` で絞った分だけです。

**欠陥ではない部分**: ex08 の追従(traverse-lib の公開契約の変更に従っただけ)と、変わる file の数そのものは欠陥ではありません。欠陥は、宣言されていない共同の不変条件(注入の種類・終了コードの意味)が failing-child と example-check に分かれて書き写され、それが崩れても検が何も言わない点です。

WAIT: work — 依頼元の会話の次の指示
