S2 そのものに当たる変更で、責務分離の失敗例が 1 つ見つかりました。作業木と同じ版で試作し、実測しています。S2 の前提を守って実装すると、scope の主張(外の関数に局所の名を作らない)は保たれます。崩れたのは「変えるのは handler-imports と handler-expr だけ」という予想の範囲です。これとは別に、R3 の反例も実測で 1 つ出ました。

## 1. 変更要求と理由

- **要求**: handle・defhandler の節に、effect を出した所へ例外を投げ返す終わり方 `(throw exc)` を足す(OCaml 5 の `discontinue` に当たる)。
- **理由**:
  - 今の終わり方では、本文の側の try・finally が effect を出した所で例外を受けて後始末できません。`raise` は handler の中で上がり、`finish` はスコープごと打ち切るからです。
  - 例: 時間切れの handler が Sleep の続きへ TimeoutError を投げ、本文が資源を返す。
  - VM には `ResumeThrow`・`TransferThrow` が既に在ります(実測: doeff・doeff_vm の公開の名)。handle.hy には、それを書く口がありません(実測: 参照 0 件)。
- S2 の文面「`_vm-imports` に node の名を 1 つ足し、`_rewrite-ops` がそれを読む」にそのまま当たります。

## 2. 主張・前提と、予想に反して変わるもの

- **主張 S2**: 変えるのは handler-imports と handler-expr だけ。S1 の予想の範囲でも、clause-endings は「変えない」側にあります。
- **前提は守っている**: `TransferThrow` は `_vm-imports` の実行時の枝で束ねます。S1 の前提にも触れません。
- **保たれた部分(実測)**:
  - defk の本体で traverse の後に throw の節を持つ handle を書いても走ります(答え -1)。外の関数の局所の名に `TransferThrow` は現れません。
  - handle だけを書いた関数の局所は `[]`、defhandler だけなら `['h']` です。
  - この変更を入れた状態で、`tests/test_handle_scope.py` は 29 passed です。
- **予想の外で変わる所**:
  - a. `handle.hy` L274 の `_terminates`(展開の時の終わり方の検め)。表のどの行にも入っていません(handler-expr の行は L432〜L737)。足さないと展開の時に SyntaxError になります(実測)。
  - b. `clause_endings.hy` L26 の `ENDING-OPS` と L28 の `RESUMING-OPS`。足さないと、検めが**黙って**抜けます(実測、下の 4.)。
  - c. ADR 003 の law `handler-clauses-end-explicitly`(`docs/adr/defadr_doeff_core_effects_003_absent_and_raise.hy` L542)が持つ操作の名の一覧。読んで確かめただけです。
  - d. 型検査のための展開。`_vm-imports` は Pass と WithHandler だけを出すので、`TransferThrow` が import の無いまま参照されます(展開の文字列で実測)。直し方はどれも static view の規則の知識を要します。

## 3. 何が漏れて波及するか(区別)

- **意図した公開契約の拡張**: 節の言語に throw を足す事。clause-endings が throw を「再開する操作」に数えるか(Raise・Absent で断るか)を決める事。clause-endings が変わる事自体は正当で、欠陥は S2 の予想がそれを数えていない事です。
- **配線**: `_vm-imports`・`_rewrite-ops`・検の `_HANDLE_IMPORT_NAMES` に足す所。S2 が予告した通りです。
- **共同の不変条件**: 次の 3 つが同じ操作の名の一覧で揃っている必要があります。
  - `_terminates`
  - `ENDING-OPS`(`clause-ops` → 検めの記述 → `check-clause-endings`)
  - `_rewrite-ops`・`_tail-resume-to-transfer`

  非末尾版(ResumeThrow)では `_doeff_clause_resumed` の立て方も加わります(推測)。
- **隠すはずの知識の漏洩**: 「どの操作が節を終え、どれが続きを再開するか」は clause-endings の知識のはずです。ところが handle.hy L282 が同じ集合を literal で別に持っています。clause_endings.hy の頭の註(L4)も、半分を「handle.hy の `_terminates`」に置いています。
  - 一覧が 2 つの module に写しで在るので、S2 の地図どおりに進むと、音を立てて落ちる `_terminates` だけが直ります。
  - 黙る clause-endings は残ります。その時、展開・定義・実行の 3 点の検めがどれも止めません。

## 4. 再現手順と観測すべき結果

補助の script と patch は `/tmp/blind-a-d4d7/` にあります。
1. `git -C /home/ubuntu/.worktrees/doeff-wt-blind-a-d4d7 apply /tmp/blind-a-d4d7/s2-step1-map-only.patch` を当てます(S2 の地図どおりの 2 か所)。`uv run python /tmp/blind-a-d4d7/t5.py` を走らせると、throw を書いた 2 例とも展開の時に「missing resume/transfer/finish/reperform」で落ちます。
2. step1 を外し、代わりに `s2-step2-plus-terminates.patch`(step1 に加えて `_terminates` に "throw")を当てて t5.py を走らせます。
   - **raise-clause-throws が 42 を返します。** これが観測すべき結果です。
     - 節: `(Raise [reason] (throw (ValueError "into-k")))`
     - 本文: `(try (do (<- (Raise "x")) 0) (except [e ValueError] 42))`
     - どの検めも止めず、Raise の続きが走ります。
   - 対照の `(Raise [reason] (resume 0))` は、展開の時に「Raise の節は resume できない」で断られます。
   - `t6.py` で見ると、検めの記述は `[(Raise, (), False)]` で throw が落ちています。型検査の展開では `TransferThrow` に import がありません。
3. さらに `ENDING-OPS` と `RESUMING-OPS` に "throw" を足すと、「Raise の節は throw できない」で断られます(実測)。直す場所は clause-endings だと分かります。

試作の変更は元に戻しました。作業木の `git status` に出るのは `BLIND_INPUT.md` だけです。

## 5. 別の実測: R3 の反例

これは責務分離の失敗ではなく、handle-expansion の中の欠陥です。
- **主張**: R3・commit の文「検めと組み立ては本文の式より前に走る(前と同じ順)」。
- **反例**: 関数の中で `(handle (do (.append LOG "body-statement") (bump-n 1)) (Halt [] (resume 0)))` を書きます(Halt は Resumption.NEVER)。
  - 直した後(2bf226a96): LOG が `['body-statement']` になってから ClauseEndingError。
  - 直す前(a2254d66c): LOG が `[]` のまま ClauseEndingError。
  - 本文が `(let [x (! (bump-n 5))] …)` の時、直した後は外の Bump が実際に行われてから落ちます。直す前は行われません。
- **原因**(展開を読んで確認): Hy は本文の文の部分を、式 `_hy_anon_7()(…)` より前へ出します。既存の検 `test_handle_checks_clause_endings_before_the_body` は本文が素の式なので、これを捉えません。
- 直す所は handle-expansion の中だけで済む見込みです(推測)。
- 再現は `/tmp/blind-a-d4d7/t3.py`・`t4.py` です。`PYTHONPATH=/tmp/blind-a-d4d7/old` を付けて走らせると、直す前の handle.hy・macros.hy で動きます。

## 6. 未確認の前提・不足する情報

- **推測**: throw を Raise・Absent で断るべきかは ADR の判断です。私は R15 の反例「失敗したのに成功したかのように続く」に当たると見ましたが、方針は決まっていません。断らない方針なら b は要りませんが、操作の名の一覧の写しは残ります。
- **未実測**:
  - pyright・doeff-hy-check は走らせていません(型検査は全体テストの段だけの決まり)。2. の d は展開の文字列からの推測です。
  - 非末尾の `resume-throw`(ResumeThrow)に要る変更は試していません。
- **数え方の幅**: 表の責務の行は handle.hy L85〜L431 を覆っていません。この範囲を handler-expr に含めるかで a の数え方は変わりますが、b は変わりません。
- **探索した範囲**: handle.hy・macros.hy・clause_endings.hy・static_view.py・tests/test_handle_scope.py・bytecode guard の概略・ADR 003 だけです。pytest は `tests/test_handle_scope.py` だけを走らせ、他の branch・他の作業木は見ていません。
- **補足(変更の前からの事で、R4 で「変えない」範囲)**: class の本体だけに defhandler を書いた module では、その handler を使うと NameError `_doeff_do` になります(直す前後とも実測)。
  - `_scoped-definition` は呼び手に「class の本体では、名で読む補助を `_install-guard-globals` で入れる」事を求めています。今回新たに呼び手になった defhandler は、それを満たしていません。
  - `test_module_and_class_level_definitions_are_not_wrapped` は、同じ module の他の form が globals を埋めるので、これを捉えません。
