## 盲検 A の答え: 失敗例が 1 つ見つかりました(実測済み)

**要点**: S1(a) が「変わるのは macros だけ」と見込んだ別名の改名そのもので、examples の 7 本を書き換えないと緑に戻りません。隠すはずの別名が、例の source に漏れているためです。

### 1. 変更要求と、それが要る理由

- **要求**: doeff-hy が、traverse・for/do の展開が引く隠す別名を改める。設計の S1(a) が挙げた `_doeff_traverse_Traverse` → `_doeff_tv_Traverse` をそのまま使います。
- **理由**: 責務の表では、`_doeff_…` の別名は macros が隠す物です。名の付け方の整理や展開の作り替えで、doeff-hy が自由に変えられる前提になっています。設計自身も S1(a) で、これを「答えを変えない展開の変更・macros だけ」と見込んでいます。
- 同じ仕組みの近い例として、for/do の When が引く `_doeff_traverse_Skip` を改める(または When が出す effect を替える)場合も確かめました。

### 2. 関係する主張・前提と、予想に反して変わる所

- **関係する主張**
  - R2: 答えを変えない macro の変更で、例と例の検は変わらない。
  - R3: 例 9 本(file は 10 本)と期待の出力は、書き換えずに緑のまま。
  - S1(a): 変わるのは macros だけ。
  - 表: macros の口は `(require doeff-hy.macros [名 …])` だけで、隠す物に「隠す別名 `_doeff_…`」が入っている。
- **前提は変えていません。** S1 が自分で挙げた変更型(改名)そのものなので、契約の適用範囲の内です。
- **実際に変わる所**
  - macros の他に、examples の 7 本(ex04_simple・ex04_traverse_macro・ex05・ex06・ex07・ex08・ex09)の import の行も直す必要があります。期待の出力は変えずに済みます。
  - ex09 は Skip の別名も import しています。

### 3. 何が漏れているか(4 つの区別)

- **漏れている知識**
  - traverse・for/do の展開は、`_doeff_traverse_Traverse`・`_doeff_traverse_Skip` を、使う側の module の名から読みます。
  - macro はこの名を自分では束ねません。docstring の「Requires: (import doeff_traverse [Traverse :as _doeff_traverse_Traverse])」の通り、使う側が import します。
  - `_doeff_do` の場合は事情が違います。defk の `_helper-imports` が module の頭で import し、fnk は `__import__` で引くので、使う側は要りません。
  - つまり別名の綴りという隠すはずの知識が、例の source(と、それを真似る利用者の module)に写っています。
  - doeff-hy 自身の検 `packages/doeff-hy/tests/test_static_check_for_do.py` の頭にも、「書き手がその名を import する(for/do の docstring)」と書かれています。
- **他の 3 つには当たりません**
  - 意図した契約の拡張ではありません。今の表は別名を隠す物に入れ、口に書いていません。
    - 前の設計検証 lt-C53PGBQD7WWPYAA1WWSW41BHDP では、盲検 A の付記を受けてこの名を macros の口に書き足していました。今回の表からは消えています。
  - 配線の変更ではありません。`[[tool.doeff.contract-tests]]` は macros.hy の変更でこの検を正しく選ぶので、マージの前に赤になります。見逃しではなく、変更が波及するという失敗です。
  - 共同の不変条件(C1・C2 の印)とも別の話です。
- **どちらに決めても S1(a) の主張は成り立ちません**
  - 別名を口とみなすなら、改名は公開契約を壊す変更です。「答えを変えない展開の変更・macros だけ」には入りません。
  - 隠す物とみなすなら、上の通り漏れです。

### 4. 再現手順と観測すべき結果(作業木で実測し、元に戻し済み)

1. `sed -i 's/_doeff_traverse_Traverse/_doeff_tv_Traverse/g' packages/doeff-hy/src/doeff_hy/macros.hy` を当てます(変わるのは 7 行)。
2. `uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q` の結果は **15 failed, 45 passed, 1 skipped** でした。変える前は 60 passed, 1 skipped です。
   - `test_example_runs` が 7 本落ちます。
   - `test_case_failure_shows_in_compared_output` も同じ 7 本が落ちます。
   - `test_printed_expansion_copy_is_named` も落ちます。
3. `env TZ=UTC0 uv run python -m hy packages/doeff-traverse/examples/ex04_simple.hy` は `NameError: name '_doeff_tv_Traverse' is not defined` で、終了コード 1 でした。
4. 7 本の例の `_doeff_traverse_Traverse` も同じく置き換えると(期待の出力には触れずに)、60 passed に戻ります。8 file、15 行の差分です。
5. **Skip の別名だけを改めた場合**
   - ex09 は終了コード 0 で終わります。
   - 出力では、When で落ちるはずの件が `[1] SKIP: name '_doeff_tv_Skip' is not defined` と `process: failed` になります。
   - 赤にするのは出力の比べ方の検 `test_example_runs[ex09_for_do_when.hy]` だけです(1 failed, 1 passed)。

### 5. 未確認の前提と、足りない情報

- **推測**
  - 改名が実際にどれほど求められるかは確かめていません。設計が S1(a) で見込んでいる事に頼っています。
  - 例の外にも同じ別名を import する file がありますが、変更が要るかは走らせていません。grep で見つけたのは次の所です。
    - `tests/test_for_do_runtime.py`
    - `packages/doeff-traverse/tests/test_traverse_answer_types.py`
    - `packages/doeff-hy/tests/test_static_check_for_do.py`
    - publications の例
  - repo の外の利用者の module(test の docstring が挙げる agora の book_rows.hy など)も、読めていません。
  - 型検査のための展開(doeff-hy-check)でも、使う側の import に頼っているように見えますが、走らせていません。
- **macros だけで閉じる道**: traverse が `_do-reference` と同じく `__import__` で Traverse を引くなどの道は在り得ますが、試していません。
  - これは改名ではなく、名の束ね方の変更になります。
  - その場合、例に残る別名の import は使われないまま「古い展開の知識」を教え続けるはずです。例の source の隠す名は今の検のどれも見ないので、気づかれないと推測します。
- **補足の候補(実測済み・主の例とは別)**
  - 変更: `<-` を module の直下に書いた時、展開の時点で案内つきの SyntaxError にする。val・var の `_module-level` が同じ ScopeGlobal の確かめをしており、前例があります。今は Python の `'yield' outside function` になります。
  - この変更を試しに入れると、例は全部走ります(49 passed)。
  - 一方で、`test_example_expansions_show_their_marks` が 10 本全部と `test_printed_expansion_copy_is_named` が、`HyMacroExpansionError` で落ちます(11 failed)。C2 が言う「`<行>: <macro>` を名指す」形ではありません。
  - 原因: 例の検の `_doeff_hy_expansions` は、使い所を 1 つずつ文脈から切り離して、新しい module の scope で展開します。これは「macro の展開が書かれた所に依らない」という macros の中身の知識を前提にしています。
  - 実際、ex04_simple の 23 行目の `<-` について検が見る展開は `__import__` 版です。走る code は、defk が `_doeff_outcomes` で書き換えた別物です(実測)。

作業木には何も残していません(`git diff --stat` が空)。測った時の記録は `/tmp/blindA/` に在ります(`probe_scope.log`・`skip_variant.log`・`ex09_skip.out`)。

WAIT: work — 盲検の依頼元の次の依頼
