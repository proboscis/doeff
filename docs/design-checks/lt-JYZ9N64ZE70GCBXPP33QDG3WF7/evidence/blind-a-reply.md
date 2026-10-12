## 失敗例: Hy の版上げで module の macro の表の名が替わる(S1 の後半の形)

主張 S1 は「直すのは guard-recording だけで、guard-check は変えない」と言っています。代役の Hy で測ると、これが成り立ちませんでした。guard-recording を正しく直した後も、guard-check の file(`test_bytecode_guard.py`)に赤が 2 本残ります。

### 1. 変更要求と、それが要る理由

- **要求**: uv.lock の Hy を次の版へ上げる便。その版は module の macro の表の名を `_hy_macros` から別の名へ替えている(代役の名は `_hy_macro_table`)。`defmacro`・`defreader`・`require :readers` はそのまま通る。
- **理由**: doeff は Hy を PyPI の版で使い、上げる便は R6 の手順で必ず来ます。Hy は以前にも表の名を替えています(`__macros__` から `_hy_macros` へ)。ただしこれは記憶で、確かめていません。

### 2. 関係する主張・前提と、予想に反して変わる所

S1 の前提が今の code で守られているかを、1 つずつ確かめました。

| S1 の前提 | 結果 |
|---|---|
| 新しい版でも見本の書き方が通る | 守られている(実測。代役の Hy で probe が値 11 を返し、手順 C で 22 本が緑) |
| 版は uv.lock から読む | 半分だけ。使い捨ての環境で走る検は守る。同じ file の中で同じ process で走る検は、venv の Hy を使う(実測) |
| 検は名の写しを持たない | 守られていない(`test_bytecode_guard.py:319-320` に `("_hy_macros", "pkg.macros", "answer")` の字がある) |

変更要求そのものは、前提も利用の状況(`make test-changed` は `--no-sync`)も変えていません。範囲内の失敗例です。

予想に反して変わる所は次のとおりです。

- **(a) guard-check の名の写し**: `test_the_record_lives_inside_a_standard_pyc` は、guard-recording を直した後も赤のままです。
- **(b) 同じ file の中で同じ process で走る検**: `test_a_file_loaded_again_by_path_under_another_name_records_the_macros_its_expansion_used` は venv の旧い Hy で走り、新しい名に合わせた直しで赤になります。便は次のどれかを強いられます。
  - guard-recording に新旧両方の名を持たせる
  - 登記の前に venv を sync する(利用の状況に反する)
  - guard-check の検を分ける、または外す
- **(c) 赤にならずに古くなる所**:
  - ADR-DOE-HY-008 の R7 の規則の文と事実(633・641 行・621 行)が `_hy_macros` を名で書いています。adr-record は古い事実を言い続けます。
  - `test_macro_use_record.py:860-861` にも `"_hy_macros"` の写しがあります。sync の後の日次の回で赤になる見込みです(未実測)。

### 3. どの知識がどこへ漏れるか

**(a) 隠すはずの知識の漏れ**
- Hy の表の名は、`records.MACRO_TABLE`(`records.py:62`)の 1 か所に閉じているはずの知識です。guard-check はそれを字で写しています。
- 記録の公開の形を検が確かめる事自体は、公開の契約の正当な使い方です。期待値を `records.MACRO_TABLE` でなく字で書いた所が漏れです。
- もう一段の原因があります。公開の形 `UsedMacro.table`(`records.py:85-95`・`190`・`265`)は、包み自身の区別(普通の macro か reader macro か)でなく、Hy の内部の名を値として持ちます。照らしの時は、その字で provider の表を引きます(`macro_use.py:305`→`_macro_now` の `vars(module).get(table)`)。
- そのため .pyc・共有の置き場・記録の指紋に Hy の内部の名が載ります。今は `record_is_current` が先に `hy_version` を比べるので、害は出ません(code を読んだ結論)。
- 公開の契約を広げたのでも、配線を変えたのでもありません。

**(b) 1 つの file が 2 つの責務を持っている**
- 「この検はどの版の Hy で測るか」は、guard-check の判断のはずです。
- ところが `test_bytecode_guard.py` には次の 2 種類の検が同居しています。
  - uv.lock の Hy を使い捨ての環境で測る検
  - 同じ process で venv の Hy を測る、guard-hooks・置き場・guard-recording の検
- 選び方の表(`pyproject.toml:248-255`)は file 全体を uv.lock の行で選びます。runner は `--no-sync` で走ります(`run_changed_tests.py:1102-1103`)。
- その結果、Hy を上げる便は包みを新旧 2 つの版で同時に測られます。「包みは走っている Hy に合う」という共同の不変条件が、S1 の表のどこにも書かれていません。
- 名や形を替える版上げの直しは、新旧両方に合う形(旧い版の知識を guard-recording に残す)か、guard-check の変更を強いられます。
- 両方の名に合わせる場合、`loader_hooks.py:275`(guard-hooks)も変わります。ここは表を 1 つの定数で引いて `RecordingTable` を探すからです(code を読んだ推測で、実装して測っていません)。

**付随して見つけた事(code を読んだ推論)**
- **S3 の前提の破れ**: `loader_hooks.py:606-626` の `_module_being_loaded` は、Hy の compile の口(`hy.importer.loader_module_obj`)が module を引く仕方を写した、Hy の内部への依りです。
  - その検は `test_bytecode_guard.py` に置かれていて、S3 の前提の字面は満たします。
  - ただし同じ process で走る検なので、uv.lock の Hy では測られません。
  - S3 の「足した検は同じ使い捨ての環境で測られる」は、file に置くだけでは成り立ちません。
- **列挙に無い依り**: `macro_use.py:384` の `_hy_export_macros` は、Hy の `macros.py:218-222` の require の読み方を写した依りです。要件の列挙にも R7 にも、どの検にもありません(grep で確かめただけで、測っていません)。

### 4. 再現の手順と、観測すべき結果

**代役の Hy を作る**
- `blind-a/scripts/build_wheels.py` で、venv の Hy 1.3.1 の .py と .hy の中の `_hy_macros` を `_hy_macro_table` へ替えます(19 か所)。
- その Hy の wheel と funcparserlib の wheel を `wheels-renamed/` に作ります。
- 版は 1.3.1 のまま残します。uv.lock を変えずに、使い捨ての環境へ入れるためです。

**検の走らせ方**
- `UV_FIND_LINKS=<wheel の dir> UV_NO_INDEX=1 UV_NO_CACHE=1 uv run --no-sync pytest packages/doeff-hy/tests/test_bytecode_guard.py -q -m 'not e2e'`

| 手順 | 作業木の変更 | 結果 |
|---|---|---|
| A | なし | 9 failed / 14 passed。期待どおりの赤で、ADR の事実の 9 本と合う |
| B | `records.py:62` を `MACRO_TABLE = "_hy_macro_table"` にするだけ(S1 の言う直し) | **2 failed / 21 passed**。`('_hy_macro_table', …) != ('_hy_macros', …)` と、`MacroRecord(hy_version='1.3.1', macros=(), absent=(), …)` |
| C | B に加えて `test_bytecode_guard.py:320` の期待値を `records.MACRO_TABLE` にする | **1 failed / 22 passed**。残るのは同じ process で走る検だけ |
| 対照 | 名を替えずに同じ作り方をした wheel(`wheels-orig/`)と、元の包み | `-k` で絞った 6 本が全部緑 |

- **選び方の確認**: `select_targets` に {uv.lock, packages/doeff-hy/pyproject.toml, records.py} を渡すと、`test_bytecode_guard.py` が file 全体で「接頭辞 uv.lock」の理由で選ばれます。

### 5. 未確認の前提と、足りない情報

- 本物の将来の Hy はありません。代役は字の置き換えだけで、Hy の他の振る舞いの変化は写しません。
- 手順 B・C で同じ process で走る検が赤になる理由は、対照との差からの推論です。venv の Hy を新しい名へ替えれば緑になる事は測っていません(venv を書き換えないため)。
- 両方の名に合わせる直しが guard-hooks に及ぶ事は推測です。
- `test_macro_use_record.py` が sync の後に赤になる事は測っていません。逆依存の選び方は測れませんでした(doeff-linter が PATH にありません)。
- `make test-changed` そのものは走らせていません。同じ `--no-sync` の venv で pytest を直に走らせました。
- 便を書く人が、実際には登記の前に sync するかどうかは分かりません。

### 後片付け

- 作業木の source は、2 か所の一時の変更を sed で手で戻しました。`git diff` は空です。`git checkout -- .` は破壊を止める hook に断られました。
- pytest が集めの段で書いた git に入らない物(`.pytest_cache` と、checkout の下の .pyc 63 個)は残っています。`rm` は worktree を守る hook に断られました。`records.py` から作った古い .pyc は、source の大きさと時刻が違うので Python は使いません。
- venv の python を 1 度だけ `uv run` を通さずに直に呼びました(cache の印を見るため)。

### 実際に走らせたコマンドと結末

1. `cat BLIND_INPUT.md` → rc 0
2. `uv run --no-project --python 3.14 python -I scripts/build_wheels.py <site-packages> wheels-renamed _hy_macros=_hy_macro_table` → rc 0、「replaced 19 occurrence(s)」
3. 代役の Hy の probe(`uv run --no-project --isolated --with hy==1.3.1 …`)→ rc 0、`11 ['_hy_macro_table', '_hy_reader_macros']`
4. 手順 A の pytest → `9 failed, 14 passed, 1 warning in 43.32s`(pipe を通したので rc は取っていません)
5. 手順 B の pytest → `2 failed, 21 passed, 1 warning in 59.50s`(rc は同じく取っていません)
6. `git checkout -- records.py` → hook に断られ、未実行
7. sed で戻した後、`build_wheels.py … wheels-orig` → rc 0
8. 対照の pytest(`-k "standard_pyc or loaded_again_by_path or macro_change_recompiles"`)→ `6 passed, 17 deselected in 17.53s`
9. 手順 C の pytest → `1 failed, 22 passed, 1 warning in 46.50s`
10. `uv run --script scripts/select.py …` → rc 0、選ばれた検 7 本
11. `.venv/bin/python -c "…cache_tag…"` → rc 0、`cpython-314`
12. sed で戻した後、`git diff --stat` → 空
13. .pyc と `.pytest_cache` の削除 → hook に断られ、未実行

作った物と log は `/work/agent-workspaces/a-PX52E1C685CV4A7JJJC2F3A6GD/blind-a/` の下にあります(`scripts/`・`wheels-renamed/`・`wheels-orig/`・`probe1/`・`logs/`)。

WAIT: work — 依頼した側の次の指示
