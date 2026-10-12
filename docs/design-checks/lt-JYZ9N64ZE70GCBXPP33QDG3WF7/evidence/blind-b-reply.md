## 結論

検査を通るのに責務を侵す実装を、1つ具体的に作りました。題材は「関数の中の `require` で入れた macro も記録する」という修正です。

この修正を入れた状態で、提示された検査はどれも緑でした。

- `test_bytecode_guard.py`: uv.lock の版の Hy で 24 passed、Hy の内部だけを替えた代役でも 24 passed
- 選び方の検: 15 passed

一方、代役の Hy の下では、包みが局所の require の macro を黙って記録しなくなります。macro を変えても、使う側は古い展開のまま動きます。

作業木は自分の差分を逆に当てて元に戻しました。`git status` で残っているのは `?? BLIND_INPUT.md` だけです。

## 1. 機能要求と差分

**要求(現実の不具合)【実測】:** 今の包みは、関数の中で require した macro を記録しません。Hy 1.3.1 の `macroexpand` は、局所の表(`compiler.local_state_stack[i]['macros']`)を module の表より先に引くためです。
- 確かめ方: `(defn f [] (require pkg.macros [answer]) (answer))` で macro を 11 から 12 に変える
- 結果: uv.lock の版の Hy で、変更前 11 → 変更後 11 → もう一度 11(古い展開のまま)

**差分:** 全文は `/work/agent-workspaces/a-PX52E1C685CV4A7JJJC2F3A6GD/blind-b/failure_example.diff`(3 file・64 行)。要点は次の 3 つです。

- **`loader_hooks.py`**: `macro_recording()` の頭で、次の `_remember_local_macro_tables()` を 1 度だけ呼ぶ。
  ```python
  previous = HyASTCompiler.new_local_state
  def new_local_state(compiler):
      previous(compiler)
      module = getattr(compiler, "module", None)   # 一番下の状態は module を置く前に開く
      table = None if module is None else vars(module).get(records.MACRO_TABLE)
      if isinstance(table, macro_use.RecordingTable):
          table.local_tables = (*table.local_tables, compiler.local_state_stack[-1]["macros"])
  HyASTCompiler.new_local_state = new_local_state
  ```
  docstring には「包みは process の共有物を書き換えるので、持ち主はこの module」と書いています。これは file の頭の注記と同じ理屈です。
- **`macro_use.py`**: `RecordingTable.local_tables: tuple[dict, ...] = ()` を足す。`record()` は、局所の表にある他の module の macro を `found` に入れる。
- **`test_bytecode_guard.py`**: 検を 1 本足す(`test_a_macro_required_inside_a_function_is_recorded`)。隣にある alias の検と同じく、process の中で動く形です。
  - `import hy` の後、`source_to_code_as_import` で compile する。
  - 記録が `("localpkg.macros", "answer")` を名指すことを確かめる。

抑制・cast・ignore・baseline の変更・対象の除外は使っていません。

## 2. 本来の所有者と、侵す契約

| 何が | 本来の所有者 | この実装で移った先 |
|---|---|---|
| Hy の内部への新しい依り(局所の状態は `new_local_state` を通って開き、`local_state_stack[-1]["macros"]` に入る) | guard-recording(S3「guard-recording か guard-canonicalization」) | guard-hooks(`loader_hooks.py`)。guard-hooks の知識は「口の包み方・compile の口」だけのはず |
| その依りが uv.lock の版の Hy に在るか、という判断 | guard-check(使い捨ての環境 `--with hy==<locked_version>`)と version-source | pytest の process の `hy`、つまり venv の状態(`make sync` の持ち物)。`make test-changed` は `--no-sync` なので、Hy を上げる便では古い Hy を測る |

- **侵す契約:** S3 の前提「足した検は … 同じ使い捨ての環境で測られる」と、受入の条「依りを、Hy を上げる便が登記の前に測る形にする」です。
  - 検を `test_bytecode_guard.py` に置くという形は満たしています。
  - しかし、どの環境で測るかという条件は満たしていません。
- **起きること:** 局所の状態の開き方を変えた Hy へ上げる便が、登記の前は緑で通ります。sync の後、局所の require の macro は記録から黙って漏れます(#1003 の形)。
- **既存の木の前例【読み取り】:** `loader_hooks._module_being_loaded` は Hy の `loader_module_obj` を写しています。その検(alias の検)も process の中で動き、venv の Hy で測っています。同じ置き方の前例が、すでに木の中にあります。

## 3. 提示された各検査が拒否しない理由

1. **`test_bytecode_guard.py`【実測】**
   - 使い捨ての環境で動く検は、どれも module の頭で require する見本しか使いません(`pkg.user`、reader の検、gensym の検)。局所の require の道を通らないので、代役の Hy でも結果が変わりません。
   - 足した検は pytest の process の `hy` を使います。そのため uv.lock の版ではなく venv の Hy を測ります。
2. **`prefix = "uv.lock"` の行と `select_targets`【実測】**
   - `{uv.lock}` でも `{uv.lock, packages/doeff-hy/pyproject.toml}` でも、file 全体 `packages/doeff-hy/tests/test_bytecode_guard.py` が選ばれます。足した検も選ばれます。
   - 選び方は path で決まるので、file の中の検がどの Hy で測るかは見ません。
3. **`tests/test_contract_table_follows_packages.py`【実測】**
   - 確かめる中身は 2 つです。source に `locked_version(… "hy")` を持つ file が選ばれるか、file 単位で選べば足りるか。
   - `test_bytecode_guard.py` には今も `reader.locked_version(ROOT, "hy")` があるので、緑のままです。検 1 本ごとに、どの Hy で測るかを確かめる所はありません。

## 4. 最小の入力・手順・結果

**入力:**
- `pkg/macros.hy`: `(defmacro answer [] 11)`。後で 12 に変える
- `pkg/user.hy`: `(defn f [] (require pkg.macros [answer]) (answer))` と `(setv value (f))`

**代役の Hy:** venv の hy 1.3.1 から組み直した wheel です。`HyASTCompiler.local_state` の `self.new_local_state()` を、`self.local_state_stack.append(dict(macros = {}))` に替えただけです。Hy から見た振る舞いは同じで、`new_local_state` の名も残ります(`__init__` が呼ぶ)。

| 手順 | 結果【実測】 |
|---|---|
| 差分なしで検の file | 23 passed |
| 差分なしで局所の require の確かめ | 11 → 11 → 11(不具合を再現) |
| 差分を当てて検の file(PyPI の Hy) | 24 passed。確かめは 11 → 12 → 12 |
| 足した検だけを、包みの直しを戻して走らせる | 1 failed(`macros=()`)。検に意味があることの確かめ |
| `UV_FIND_LINKS=<代役> UV_NO_INDEX=1 UV_NO_CACHE=1` で、使い捨ての環境に代役が入ることを確かめる | `variant-local-state` |
| 同じ設定で検の file | **24 passed(検査を通過)** |
| 同じ設定で局所の require の確かめ | **11 → 11 → 11(責務違反の結果: 古い展開のまま)** |
| sync の後の日次の代わりに `PYTHONPATH=<代役>` で足した検 | 1 failed。合併の後にしか見つからない |
| 選び方の確かめと選び方の検 | 上の §3 のとおり。15 passed |

## 5. 未確認の前提と不足する情報

- **`make test-changed` そのものは走らせていません。** 選び方の関数と、選ばれた file を `uv run --no-sync` で直に走らせただけです。60 秒の枠と「未測」の扱いは再現していません。
- **代役は版の名が 1.3.1 のままです。** 本物の版上げでは、最初の読み込みで全部の module が compile し直されます。漏れが出るのはその後の macro の変更からで、この確かめはその順で測りました。将来の Hy が実際にこの変更をするかは分かりません【推測】。
- **提示外の検(参考):** 同じ行の ADR の R7 と R4 の検を、代役の設定で 1 度走らせて 2 passed でした。ただし代役が実際に入ったかは確かめておらず、検の中身も評価していません。`test_daily_test_population.py`・doeff-linter の逆依存・pyright・ruff・semgrep は走らせていません。
- **日次は真似で代えました。** 日次の実際の走らせ方は確かめていません。free-threaded の Python で、包みの書き換えが thread に対して安全かも見ていません。
- **読み取りだけで測っていない気づき:**
  - 使い捨ての環境の検には、表に無かった名を覚える経路(`名 in 表` が偽の時)を通る見本がありません。Hy が `in` をやめて `try/except KeyError` で引くようになると、この経路だけが壊れて検は緑のままかもしれません【推測・未実測】。
  - `macro_use._exported` は `"_hy_export_macros"` を、`doeff_effect_analyzer/result_cache.py:395` は表の名を、文字列で直に持っています。

## 走らせたコマンドと結末

作業 dir は `B=/work/agent-workspaces/a-PX52E1C685CV4A7JJJC2F3A6GD/blind-b`、cwd は作業木です。

1. `uv run --no-sync python $B/scripts/probe_local_require.py …`(差分なし): rc 0、`local-require 11 11 11 hy 1.3.1`
2. `uv run --no-sync python $B/scripts/build_wheels.py … local-state` と `… plain`: rc 0 / rc 0(plain は後で使っていない)
3. `uv run --no-sync pytest …/test_bytecode_guard.py -q -m 'not e2e' -p no:cacheprovider`(差分なし): rc 0、`23 passed in 20.09s`
   - この 1 回だけ `-p no:cacheprovider` を付けました。以降は提示どおりのコマンドです。
4. 同じ検を `-k inside_a_function` で(差分あり): rc 0、`1 passed`
5. `git apply -R src_only.diff` の後に同じ検: rc 1、`1 failed`。その後 `git apply` で戻した
6. 検の file を通しで(差分あり・PyPI の Hy): rc 0、`24 passed in 28.16s`。確かめは rc 0、`11 12 12`
7. `probe_which_hy.py`: 設定なしで `pypi-local-state`、代役の設定で `variant-local-state`(どちらも rc 0)
8. 代役の設定で検の file を通しで: rc 0、`24 passed in 52.38s`。確かめは rc 0、`11 11 11`
9. `PYTHONPATH=$B/variant-site`: hy の置き場が代役であることを確かめた。`-k inside_a_function` は rc 1、`1 failed`
10. `probe_selection.py`: rc 0。どちらの組でも file 全体が選ばれた
11. `uv run --no-sync pytest tests/test_contract_table_follows_packages.py -q -m 'not e2e'`: rc 0、`15 passed in 11.26s`
12. (参考・提示外)代役の設定で ADR の R7 と R4 の検: rc 0、`2 passed in 30.57s`
13. `test_macro_use_record.py` と `test_gensym_canonical.py`(差分あり): rc 0、`92 passed in 395.64s`
14. 後片付けの `git checkout -- …` は環境の hook が止めたので、走っていません。代わりに `git apply -R failure_example.diff` で戻し、`git status` に残るのは `?? BLIND_INPUT.md` だけです。
15. **未実行:** `make test-changed`、repo 全体の検、e2e、R6 の上限の検、`test_hy_stubs.py`、`test_daily_test_population.py`、doeff-linter の逆依存、型と lint の検査。

差分・script・log・代役の wheel は `$B` の下(`failure_example.diff`・`scripts/`・`logs/`・`wheels/`・`variant-site/`)にあります。

WAIT: work — 呼び出し元の次の指示
