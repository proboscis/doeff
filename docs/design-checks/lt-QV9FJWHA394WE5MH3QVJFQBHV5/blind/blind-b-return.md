## 結論

検査を通るのに責務を侵す実装を 1 つ作り、自分の実験の木で実際に検査を走らせて確かめました。例 ex04_traverse_macro を直す時に、doeff-hy が意図して消した `fold` macro を例の中にもう一度定義する、という実装です。`test_examples_run.py` は 12 passed で、どの行も拒否しませんでした。macro の持ち主が規則を締めた時の違いも、同じ設定で実測しました。

## 1. 機能の要求とコード

**要求**: ex04_traverse_macro を今の macro で走るように直し、BROKEN の表から外す(design §3 の S2(b) が想定している変更です)。

**最小差分**(3 か所。中心は ①):

```diff
-(require doeff-hy.macros [defk <- traverse fnk fold])
+(require doeff-hy.macros [defk defhandler <- traverse fnk])
 (import doeff [do :as _doeff-do])
+;; fold: doeff-hy の fold は fa37eaaf1 で消えた。この例の説明(implicit `acc` / `it`)を保つため、
+;; 消える前の doeff-hy の定義と同じ展開(Reduce への書き換え)をここに置く
+(defmacro fold [collection #* args]
+  ...(:init と本体を取り出す。fa37eaaf1^ の定義と同じ)...
+  `(_doeff_traverse_Reduce
+     (fn [acc it] ((_doeff_do (fn [] (do ~@body-forms)))))
+     ~init ~collection))
```

- ② `defk pipeline` に `{:pre [(: items list)] :post [(: % dict)]}` を足し、`replace-with-zero` を `(defhandler replace-with-zero (Fail [cause] (resume 0.0)))` に替えます。この 2 つは正当な直しです。
- ③ `test_examples_run.py` の BROKEN の行 `"ex04_traverse_macro.hy": _FOLD,` を 1 行消します。行を消す前に検査を走らせると、`test_broken_example_is_still_broken[ex04_traverse_macro.hy]` が「BROKEN の行…を外す」で赤になりました(実測)。つまりこの削除は検査そのものが求めた操作で、除外を足したものではありません。

**実際に書かれそうな理由**:
- BROKEN の理由の文が「Reduce で書き直す」と言い、この shim の展開の中身は Reduce です。
- 素直に `(_doeff_traverse_Reduce (fnk [acc it] …) …)` を `defk pipeline` の中に書くと、実行時に `UnboundLocalError: cannot access local variable '_doeff_do'` で落ちました(実測)。fnk が関数の中に入れる import と、traverse の展開が使う module の `_doeff_do` がぶつかるためです。この摩擦が、実装者を shim の方へ押します。

## 2. 本来の持ち主と、侵す契約

- **知識が移る**: 「在る macro の名(`fold`)と展開の規則(暗黙の `acc`/`it` → Reduce)」の持ち主は macros(`doeff_hy/macros.hy`)です。持ち主が fa37eaaf1 で意図して消した規則が、例(examples)の中に 2 つ目の定義元として戻ります。例の持ち分は「data・handler の組・印字」だけです。
- **判断が移る**: kleisli の書き方の規則(契約の必須・bang の展開・handler の形の拒否)は fnk / defk が決めます。shim は素の `fn` + `_doeff_do` で kleisli を作るので、これらの規則をすべて迂回します。bang を展開しない点は code を読んでの判断で、実行はしていません。
- **侵す契約**:
  - I3「例は、今の macro … の公開の口だけで最後まで走る」。同じ環境で `(require doeff-hy.macros [fold])` は HyRequireError で落ちます(実測)。例は、利用者が使えない API を教える読み物になります。
  - S1「macro の規則を締めると、壊れた例が赤になる」。下の手順で、この例には持ち主の変更が届かない事を実測しました。
  - §4 の I3 の強制「例を実際に走らせる(macro の名は展開の時に解ける)」。名は解けますが、その macro が doeff-hy の物か例の中の物かは見分けられません。

## 3. 各検査が拒否しない理由

- **例が走る**: 終了コードだけを見ます。shim のままでも ex04 は 0 で終わり、Mean 45.0 / 36.0 などコメント通りの値を印字しました。`test_example_runs[ex04_traverse_macro.hy]` は PASSED です(実測)。
- **表の例は今も落ちる**: 残る 9 行はどれも今も落ちるので PASSED です(実測)。
- **変更箇所の品質検査(fast の段)**: 実行できず、通るとは断定できません。
  - この環境の `agentcli` には `code-quality` がありません(invalid choice、実測)。`ai code-quality` もありません。
  - 代わりに ruff 0.15.21 で一部だけ確かめました。変更した `.py` は "All checks passed!" でした(実測)。
  - ruff に dir を渡すと `.hy` は対象に入りませんでした(実測)。出た 4 件は、変えていない別の file の既存の物です。
  - `.hy` を名指しで渡すと Python として読まれ、元の file でも 1430 件の構文エラーが出ます(実測)。これは意味のある検査になりません。
  - fast の段の「速い規則」に Hy 向けの規則があるかどうかは分かりません。
- **マージの列(`ai land`)**: 走らせていません。走らせると実際にマージの列へ載るためです。差分に衝突の印は無いので、示された範囲(衝突の印の走査だけ)では止まらないと推測しますが、確かめてはいません。

## 4. 確かめる最小の手順と結果(すべて実測)

1. 差分を当てて `uv run --no-sync pytest packages/doeff-traverse/tests/test_examples_run.py -m "not e2e" -q` を走らせる → **12 passed**。
2. `uv run --no-sync hy -c '(require doeff-hy.macros [fold])'` → **HyRequireError**(持ち主は `fold` を出していない)。
3. `grep -n '(defmacro fold' examples/*.hy doeff_hy/*.hy` → **例の中の 1 件だけ**が当たる。
4. S1 の模擬(実験の木の macros.hy だけを書き換え):fnk に「先頭に契約の map が要る」という規則を足す。defk の時の締め方と同じ形です。この上で 2 つの書き方を比べました。

| macros の状態 | X(本件の shim) | Y2(module の直下の fnk + Reduce:持ち主に従う書き方) |
|---|---|---|
| 元のまま | 12 passed | 12 passed |
| 規則を締めた | **12 passed(持ち主の変更が届かない)** | ex04 が赤:`SyntaxError: fnk: …required` |

## 5. 確かめていない事・推測

- 推測:
  - 同じ shim を ex07・ex08 にも写すと、定義元が 3 つ増えると見ています(未実行)。
  - shim が bang(`!`)を展開しない点は、code を読んでの判断です(未実行)。
  - fast の段の品質検査と `ai land` の結果は未確認です。
- 実験の木の場所:指定の `/tmp/blind-b-ex09/tree` は、木の置き場を守る hook が拒否しました。代わりに `/work/toolset/worktrees/doeff-wt-blind-b-ex09` に同じ版(2cff14d6a)を detached で切りました。
- 実験の木の片づけ:
  - 自分の変更は `git apply -R` で戻し、差分 0 の状態です。
  - ただし `worktree remove`(`--force` の有無どちらも)は hook が拒否しました。案内された `ai worktree reap` もこの環境にありません。
  - そのため **木そのものは残っています**。回収は `ai worktree reap` のある所で行う必要があります。
  - 自分の作業用の file(`/tmp/blind-b-ex09-scratch`)は消しました。
- 共有の置き場への影響:検査の子 process と、模擬の macros.hy の compile で、共有の `DOEFF_HY_CODE_STORE` に項目が足されています。置き場は中身を鍵に持つので、他の木を壊すものではありません。
- 読める木と `~/repos/doeff` は変えていません。commit・push、他の会話への連絡もしていません。
