## 失敗例: 局所の表を覚える包みを「展開の `with` の間だけ」に絞る

### 1. 機能要求と差分

**要求(もっともらしい後片付けと性能の修正として)**: 今の作りでは、関数の中の require を拾うために `HyASTCompiler.new_local_state` を process の間ずっと包んだままにしています。そのため展開の外の compile(REPL・`hy.eval`・実行時の再 compile)でも、`compiler.module` の名前空間を毎回引く費用が残ります。そこで `unittest.mock.patch` と同じ形にして、展開の `with` の間だけ包み、抜けたら元に戻します。記録の表を閉包で持たせれば、`compiler.module` から表を探す処理も、process で 1 度だけの印と lock も要らなくなります。

差分の全文は `/tmp/blind-b-lt07hr/proposal.diff` にあります。要点は次のとおりです(`import threading`・`_LOCAL_WATCH`・`_LOCAL_WATCH_LOCK` は消します)。

```python
def _watch_local_tables(table: RecordingTable) -> Callable[[], None]:
    from hy.compiler import HyASTCompiler
    opened = HyASTCompiler.new_local_state
    def new_local_state(compiler: HyASTCompiler) -> None:
        opened(compiler)
        table.local_tables = (*table.local_tables, compiler.local_state_stack[-1][LOCAL_MACRO_TABLE])
    HyASTCompiler.new_local_state = new_local_state
    def unwatch() -> None:
        HyASTCompiler.new_local_state = opened
    return unwatch
# MacroRecording: __init__ は _watch_local_tables() を呼ばず self._unwatch = None
#   __enter__(owner の時): self._unwatch = _watch_local_tables(self.table)
#   __exit__: if self._unwatch is not None: self._unwatch(); self._unwatch = None
```

### 2. 本来の持ち主と、破られる契約

- **持ち主は guard-recording(`macro_use.py`)です。** 隠している知識は「局所の表の覚え方」で、S3 の契約は「口の包みは process で 1 度だけ lock の下で入れ、局所の表は展開している module の記録の表にだけ覚える」です。
- **判断の移り先**: 「この局所の状態はどの module の展開のものか」は、元は compile している compiler 自身の `compiler.module` で決まっていました。変更後は「その時 process で開いている MacroRecording はどれか、包みがどの順で重なったか」で決まります。他の展開が始まり終わる時間の順に移り、誰もその判断を持っていません。
- **状態と副作用**: process で共有している `HyASTCompiler.new_local_state` を、持ち主の MacroRecording がそれぞれ入れ替えて戻すようになります。持ち主が 1 つではなくなり、lock もありません。thread をまたぐと戻す順が後入れ先出しにならず、次の 2 つが起きます。
  - 別の記録の包みを外してしまう。
  - 終わった記録の包みを入れ直してしまう。
- **公開の契約**: `record(source)` は「この module の展開が使った macro」を返すはずですが、変更後は他の module の展開に左右されます。
- **S4 も破られます**: 関数の中の require を持たない module の記録が、import の順と cache の状態によって変わります。RECORD_TAG は変わらないままです。
- S1 で直す場所(guard-recording と records-names)は変わらないので、今回の対象外です。

### 3. 提示された検査が拒否しない理由(実測)

- **`test_bytecode_guard.py`**: 変更後も **37 passed** でした(変更前も 37 passed)。関数の中の require を測る検は `test_a_macro_required_inside_a_function_recompiles_the_user_on_pypi_hy` の 3 本だけです。これらは 1 process に使う側が 1 つ、thread も 1 つで動きます。
  - 中で compile される `pkg.macros` は使う側の囲みの中で入れ子に開くので、後入れ先出しが保たれます。
  - `pkg.macros` 自身は関数の中の require を持たないので、混ざる局所の表は空で、差が表に出ません。
  - 2 つの展開を thread で重ねる検も、入れ子の module の局所の macro が外の記録に混ざらない事を確かめる検もありません。
- **`test_macro_use_record.py -k local`**: **3 passed** でした。選ばれる 3 本はどれも thread 1 つです。入れ子に compile される macros.hy・consts.hy などは関数の中の require を持たないので混ざる表は空で、`record.macros == ["used"]` も成り立ったままです。
- **Hy の代役**: 走らせていません(手で使う道具で、S1 向けのため)。
- 提示されていない検査(lint・型検査など)は走らせておらず、結果を推測もしていません。

### 4. 最小の再現の手順と結果(作業木の根で)

1. 差分を当て、2 つの検を書かれたとおりに走らせます。結果は上の 37 passed と 3 passed です。
2. `uv run --no-sync python /tmp/blind-b-lt07hr/demo.py nested|threads|pool` を走らせます。環境は Python 3.14.3 の free-threaded 版(GIL なし)、Hy 1.3.1 で、`DOEFF_HY_CODE_STORE=off`・.pyc を書かない設定です。

入力は次のとおりです。
- `m.hy`: `(defmacro answer [] 11)`
- `b.hy`: `(defmacro wrap [x] x)` と `(defn f [] (require <pkg>.m [answer]) (answer))`
- `a.hy`: `(require <pkg>.b [wrap]) (setv value (wrap 1))`
- threads 用の local: `(defn f [] (require <pkg>.m [answer]) (answer)) (setv value (f))`
- threads 用の quiet: `(setv value 1)`

| 確かめ | 元の code(実測) | 変更後(実測) |
|---|---|---|
| nested: thread 1 つで、A の展開の中で B が compile される(`source_to_code_as_import`) | A の記録は `[(b, wrap)]`。answer を替えても A は今に合う(True) | A の記録は `[(b, wrap), (m, answer)]`、全部 require の提供元に m が加わる。answer を替えると A が古いと判じられる(False)。A の記録に B の局所の macro が混ざっている |
| threads: Event で順を固定(quiet が入る → local が入る → quiet が出る → local を compile)。形は effect analyzer の `_expand_hy` と同じ | local の記録は `[(m, answer)]`。answer を替えると古いと判じる(False) | local の記録は `[]`。answer を替えても **今に合う(True)**。古い展開を黙って使う。両方が終わった後も `new_local_state` に、quiet の RecordingTable を抱えた包みが残っている |
| pool: 順を決めず 8 thread で 80 本 | 漏れ 0・混入 0(1 回) | local 40 本中 6 本で漏れ、quiet 40 本中 11 本に混入(1 回) |
| 実物の `_expand_hy` を thread pool で(`/tmp/blind-b-lt07hr/analyzer_pool.py`) | 0/0、0/0(2 回) | 漏れ 0・混入 10、漏れ 2・混入 8(2 回) |

記録は `/tmp/blind-b-lt07hr/` の下の `demo-orig.log`・`demo-after.log`・`analyzer-*.log`・`baseline-guard.log`・`after-guard.log`・`after-local.log` にあります。

### 5. 確かめていない前提と足りない情報

- **推測**: 今の本番で、別々の module の展開を thread で並べる利用者がいるかは確かめていません。effect analyzer がいま逐次で動くかも読んでいません。S3 はこの並びを前提の場面として挙げています。
- **推測**: nested の混ざりは thread なしで起きます。測ったのは `source_to_code_as_import` の道だけです。普通の import(get_code が .pyc や置き場に書く道)でも同じ `macro_recording` を通るので同じ事が起きると見ていますが、端から端までは測っていません。
- **推測**: thread で漏れた記録が、実際に .pyc や置き場の entry として残って古い値のまま動くところまでは測っていません。測ったのは「記録の照らしが合うと判じる」所までです。
- **頻度について**: pool の頻度は 1〜2 回の標本です。元の code で 0 だった事は、起きないという証明にはなりません。
- **判断について**: この機能要求が実際の残り仕事にあるかは分かりません。もっともらしさは私の判断です。指示どおり git の歴史と `docs/design-checks/` は読んでいないので、過去にこの形が検討されたかも分かりません。
- **後片付け**: `macro_use.py` は退避した写しから戻しました。sha256 が元と同じ `83de7a97…` である事を確かめています。`git status` で残っているのは、元からあった `docs/design-checks/` の削除と `BLIND_INPUT.md` だけです。作業用の file は `/tmp/blind-b-lt07hr/` の下だけに置きました。

WAIT: work — 依頼元の次の依頼
