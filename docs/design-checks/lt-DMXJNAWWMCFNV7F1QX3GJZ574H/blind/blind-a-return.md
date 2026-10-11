## 結論

失敗例が 1 つ見つかりました。program_effects.py を分けて、派生の値を作る code の一部を別 file へ移すとします。その後に移した側の file だけを変えると、要件 3 と S1 の「古い読み手の派生の項は読まない」が、何の知らせもなく破れます。

原因は、派生の値の鍵 `_reader_digest` が program_effects.py という 1 つの file の digest で、「派生の値を作る code」の digest ではない事です。正しく直すには、R(派生の値の置き場)の鍵が A(読み手)の file の分け方を知らなければなりません。

この例は S1 の明示された前提(木の中身を決める code は全部 X)を守ります。一方で、S1 の字面「program_effects.py の何かを変える」の外へ出ます。ですから S1 への反例ではなく、書かれていない前提「読み手 = 1 file」が表に出た例として報告します。検査は一切実行していません。以下、【読んで確認】はコードを読んで確かめた事、【推測】は読みから予測した事です。

---

## 1. 変更の要求と、その理由

**要求:**
1. program_effects.py から、体の事実を作る AST だけの関数 `_reads_itself`・`_wraps`・`_rewrapped_seeds`(続いて `_read_body_facts`・`_BodyFacts` など)を `body_facts.py` へ分けます。
2. その後、包み直しの規則を広げ、`prog = retry(prog, times=3)` のように keyword の付いた包みも包みとして読みます。

**理由:**
- program_effects.py は 2956 行あり、handler_effects.py は既にそこから分かれています。handler_effects.py は `_body_nodes` をはじめ 20 を超える私的な名を import しています【読んで確認】。
- `_wraps` は今、keyword の無い呼びしか包みと認めません(1819 行 `keywords=[]`)【読んで確認】。そのため keyword 付きの wrapper の後の `prog` は追えません。この規則を広げるのは、読み手の規則の自然な拡張です。#2973 も同じ `rewraps` の変更でした。
- 同じ種類の別の形として、handler_effects の節の索引 `_CLAUSE_FUNCTIONS`(340〜357 行。今は process の中の memo)を派生の値に入れる案があります。#1590 で `_body_facts` を disk に載せたのと同じ動機です【推測】。

## 2. 関係する主張・前提と、予想に反して変わる物

- **主張:** 要件 3 と S1「形の印を手で上げなくても、変えた後の 1 回目の解析は展開 0 回で、派生の値は今の読み手が作り直す(古い読み手の派生の項は読まない)」。
- **守る前提:** S1 の前提「展開の木の中身を決める code は全部 X」は守ります。木には触らず、hy_expansion.py も変えません。
- **適用範囲:**
  - 2 つ目の commit は body_facts.py だけを変えるので、S1 の字面の外です。
  - ただし「派生の値を作る code は program_effects.py に在る」という条件は、どこにも前提として書かれていません。暗に在るのは次の 2 か所だけです【読んで確認】。
    - 表が R・A を「program_effects.py の…」と書いている事
    - `_reader_digest` の docstring の「built by the code here」(644〜650 行)
  - 検査でも静的な検査でも守られていません(3 に書きます)。そこで、この例は「契約の適用範囲外。ただし適用範囲そのものが書かれていない」例として扱います。
- **予想に反して変わる物:**
  - R の鍵の範囲です。A の内部を分けるたびに、R の `_reader_digest` が新しい file を数え上げる必要が出ます。
  - C(result_cache.py)は自分の code は変わりません。ただし、古い派生の値から作った答えを新しい内容の鍵で書く事になります【推測。3 を参照】。

## 3. どの知識がどこへ漏れるか(区別)

**漏れる知識:** 読み手の code をどの file に置くか(A の内部の分け方)。
- `_reader_digest()` は `sha256(Path(__file__).read_bytes())` です。
- `_TreeKey.place` は `_TREE_KEY_FORMAT`・展開の鍵・reader digest だけで派生の置き場を決めます(603〜606 行)【読んで確認】。
- つまり、R と A が同じ file に同居している間だけ正しい近似です。

**区別:**
- **公開契約の拡張ではありません。** `_compile_hy`・`_Expanded`・X の口はどれも変わりません。
- **配線の変更そのものは無害です。** commit 1 は program_effects.py を変えるので digest も変わり、その時点では正しく外れます。
- **共同の不変条件が在ります。** 「派生の値を作る code は、`_reader_digest` が hash する file の中に在る」という R と A の条件です。これを守る物は在りません【読んで確認】。
  - 検 `changed_file` は `pe` と `hy_expansion` の file だけを変えます(docstring「Nothing else of the analyzer changes」・568〜595 行)。
  - architecture.hy は env_places の層だけを宣言しています。
  - semgrep には analyzer に向けた規則がありません(材料の記述による)。
- **漏れの本体:** 不変条件を保ったまま A を分けるには、R が A の file の並びを知る必要があります。A が「木がどこから来たか」を隠すのと対になる形で、R は「誰が派生の値を作るか」を file 単位で知ってしまっています。
- **C への波及【推測】:**
  - body_facts.py の関数は program_effects の名前空間に入ります。`_dependencies` → `_function_modules` の `__module__` によって、body_facts は答えの依存の module に入るはずです。
  - そのため C は外れて計算し直しますが、R が古い facts を返すので、古い答えを新しい内容の鍵で保存します。
  - 次に program_effects.py が無関係に変わった時(10 日で 9 回)に初めて答えが変わり、原因を辿りにくくなります。
- **X にも同じ形が在ります。** `_expander_digest` も hy_expansion.py 1 file の digest です(122〜125 行)。ただし X は docstring で「Only the expansion … are here」と宣言しています。また、後処理の閉包の digest を記録に入れる前例 `record(..., also=(value_reference(__name__, "POST_PROCESSING"),))`(doeff_hy/static_check.py)があり、X の中だけで直せます。
- **直す方向(分類のため・推測):**
  - (a) 不変条件を宣言し、検で守る(例: `_derived` から呼ばれる関数の module が program_effects だけかを確かめる)。
  - (b) 鍵を file ではなく `_derived` の code の閉包の digest にする。(b) なら A の分け方が R に漏れません。

## 4. 最小の入力・変更と再現の手順、観測すべき結果

**commit 1(振る舞いを変えない分割):**
- `python/doeff_effect_analyzer/body_facts.py` を作り、`_reads_itself`(1444〜1450 行)・`_rewrapped_seeds`(1801〜1812 行)・`_wraps`(1815〜1824 行)をそのまま移します。どれも `ast` と `Sequence` にだけ依ります【読んで確認】。
- program_effects.py では 3 つの定義を消し、`from doeff_effect_analyzer.body_facts import _reads_itself, _rewrapped_seeds, _wraps` を足します。

**commit 2(body_facts.py だけを変える):** `_wraps` の最初の case を次のようにします。
```python
case ast.Call(args=[ast.Name(id=argument)], keywords=keywords, func=func):
    return (argument == name and not _reads_itself(func, name)
            and not any(_reads_itself(k.value, name) for k in keywords))
```

**入力:** 検の `REWRAPPED`(472 行)の 1 か所だけを変えた物です。
```hy
(defn clause [effect wrap]
  (setv prog effect.program)
  (setv prog (wrap prog :times 3))
  prog)
```

**手順:**
1. commit 1 の木で `DOEFF_EFFECT_ANALYZER_CACHE=<D>` を設定し、process 1 で `pe._compile_hy(KW, "/src/k.hy", "k")` を呼びます。
2. commit 2 を当てます。program_effects.py は 1 字も変わりません。
3. 同じ D のまま、新しい process 2 で同じ呼びをします。D の派生の項を読み、`pickle.loads(chunk).body_facts` の `rewraps` を見ます。読み方は `test_an_entry_written_by_another_reader_is_not_read` と同じです。
4. 比べるために、空の D' で同じ呼びをします。

**期待(要件 3 の通りなら):**
- 派生の項が今の規則で作り直され、`rewraps == {"prog": (<effect.program>,)}` になる。
- 派生の項が 2 つになる(`test_a_changed_reader_expands_nothing_again_and_answers_the_same` の `len(entries)==2` と同じ)。

**予測される実際【推測】:**
- `pe._hy_cache_places(KW, "k").derived` が手順 1 と同じ path になる。
- 派生の項は 1 つのまま。
- 返る木は `_ChunkedTree`(当たりなので観測の外)。
- `rewraps == {}`、つまり古い規則のまま。
- D' では `prog` が入る。同じ code でも、置き場の状態によって答えが変わります。
- Python の module は毎回 `_WholeTree` から新しい規則で読まれるので、Hy と Python で規則が食い違います。
- 検は `cache_dir` を tmp に置くので緑のままで、4 時間ごとの日次の検(置き場は残る)だけが古い規則で読みます。

**別の形:** `_BodyFacts` と `_scope_of` も移した後、body_facts.py の中だけで #2973 と同じ形の変更をした場合です。
- 古い塊を読んで、TypeError か AttributeError で解析が落ちます【推測】。
- 外れとしては扱われません。塊の unpickle は `_ChunkedTree._definitions_of`(728〜742 行)で、`_read_cached_tree` の try の外にあります。`_locate` も `find` を try の外で呼びます(2724〜2728 行)【読んで確認】。

## 5. 確かめていない前提と、足りない情報

- 検査は何も実行していません。2〜4 の振る舞いの予測は、すべて読みからの推測です。
- program_effects.py で読んだのは 1〜80・400〜900・1440〜1560・1585〜1960・2215〜2232・2700〜2870 行です。
  - `_derived` が呼ぶ関数の連鎖は program_effects.py の中で閉じている事を確かめました【読んで確認】。つまり今の版には穴がありません。
  - `_Unpacked`(1166 行)と `_Assignment` の定義は読んでいません。
- program_effects.py を分ける計画が実際に在るかは知りません。現実的だという判断は、file の大きさと、handler_effects が既に分かれている事からの推測です。
- 古い `rewraps` が `analyze_program` の最終の答え(effects や unresolved)をどう変えるかは追っていません。`_scope_of` が `local_rewraps` に入れる所(1640 行)までしか読んでいません。
- C の依存に body_facts が入るのは、compute が program_effects に届く場合に限ります【推測】。
- 新しい field を足した frozen dataclass の古い pickle を読むと AttributeError になる、というのは pickle の一般の動きからの推測です。
- 方向 (b) の閉包の digest(`macro_digest`・`value_digest`)が、別の module の helper の code を本当に辿るかは、docstring でしか確かめていません。
- **深掘りしていない別の候補:**
  - 【読んで確認】doeff_hy_bytecode_guard/expansion.py の `TYPE_CHECK_EXPANSION` が立っている間、doeff-hy の macro は別の形の木を出します(macros.hy・handle.hy などが `static-view-enabled` を読む)。そして `_ExpansionKey` にはこの印がありません。
  - 【推測】analyzer の展開が `static_view()` の中で走ると、型検査の形の木(`x: T = _doeff_perform(e)`、yield が無い)が普通の鍵で X に残り、以後の普通の解析が effect を見落とします。
  - そう呼ぶ箇所が今在るかは調べていません。直すのは X の鍵だけなので、責務をまたいで波及する例ではありません。

**関係する file:**
- `/home/ubuntu/.worktrees/doeff-wt-ki81-design-check/packages/doeff-effect-analyzer/python/doeff_effect_analyzer/program_effects.py`(603〜606・644〜650・728〜771・774〜821・1801〜1824・2724〜2728 行)
- `/home/ubuntu/.worktrees/doeff-wt-ki81-design-check/packages/doeff-effect-analyzer/python/doeff_effect_analyzer/hy_expansion.py`(122〜125 行)
- `/home/ubuntu/.worktrees/doeff-wt-ki81-design-check/packages/doeff-effect-analyzer/python/doeff_effect_analyzer/result_cache.py`(151〜176・367〜443 行)
- `/home/ubuntu/.worktrees/doeff-wt-ki81-design-check/packages/doeff-effect-analyzer/python/doeff_effect_analyzer/handler_effects.py`(52〜67・340〜357 行)
- `/home/ubuntu/.worktrees/doeff-wt-ki81-design-check/packages/doeff-effect-analyzer/tests/python/test_hy_tree_cache.py`(472〜622 行)

WAIT: work — 依頼した側の次の指示
