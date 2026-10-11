# 盲検 B の入力(lt-DMXJNAWWMCFNV7F1QX3GJZ574H)

```text
あなたは静的検査を通過する責務違反の失敗例を生成してください。
合理的な機能追加・修正として実装されそうで、提示した検査をそのまま実行しても通るのに、
知識・判断・状態・effectsの所有者や公開契約を侵す実装を1つ具体化してください。
一般的な設計レビューや承認判定は求めていません。

検査の無効化、baselineの改変、対象除外、as any、ignoreやlint抑制、根拠のないcast、
動的importによる回避は使わないでください。小さい既存例に対する抜け道だけでなく、
通常の実装者が機能を実現しようとして書きそうなコードで示してください。

返答には次を含めてください。
1. 現実的な機能要求と、提案する具体的なコードまたは最小差分。
2. 本来の責務所有者と、違反する契約。何の判断・知識がどこへ移るか。
3. 提示された各検査がそのコードを拒否しない理由。未提示の検査は推測しないこと。
4. 同じ設定で検査通過と責務違反の両方を確かめる最小の入力・手順・期待結果。
5. 未確認の前提、不足する情報。推測と実測を明確に区別すること。

共有ソースを変更せず、全数検査や他会話への連絡を行わず、日本語で返してください。
実行していない検査を通ったと断定しないでください。失敗例が見つからない場合は探索した範囲と
不足を述べ、合格・安全とは結論しないでください。
```

## 材料(固定済み)

### 対象
- repo: doeff(Python と Hy の effect の枠組み)の package `doeff-effect-analyzer`。版: c1e7731d67cf0b6bbc60102a72fa4113c23b8dc9 の中身。
- 読む木(読むだけ・変えない): `/home/ubuntu/.worktrees/doeff-wt-ki81-design-check`(本線 bca4c2f9a。下の 3 file は c1e7731d と同じ中身)。
- 実コード:
  - `packages/doeff-effect-analyzer/python/doeff_effect_analyzer/hy_expansion.py`(全体)
  - `packages/doeff-effect-analyzer/python/doeff_effect_analyzer/program_effects.py`(置き場は 440〜850 行あたり: `_module_source`・`_compile_hy`・`_expanded`・観測・`_TreeKey`・`_CachePlaces`・`_hy_cache_dir`・`_tree_key`・`_hy_cache_places`・`_reader_digest`・`_CachedTree`・`_ChunkedTree`・`_find_cached_tree`・`_read_cached_tree`・`_derived`・`_write_cached_tree`。読み手の残りは同じ file の他の所)
  - `packages/doeff-effect-analyzer/python/doeff_effect_analyzer/result_cache.py`(答えの置き場・同じ dir を使う)
  - `packages/doeff-effect-analyzer/python/doeff_effect_analyzer/env_places.py`
  - `packages/doeff-hy-pytest/src/doeff_hy_pytest/budget.py`(観測の使い手・855 行あたり)
  - 展開の記録: `packages/doeff-hy/src/doeff_hy_bytecode_guard/`(`macro_recording`・`record_is_current_here`・`record_fingerprint`・`records.py` の `RECORD_TAG`・`MacroRecord`)

### 背景と要件
- analyzer は doeff の Program の source を実行せずに読み、Hy の module は Hy の compiler で macro を展開して Python の AST にしてから読む。
  展開は 1 module 0.5〜1 秒で、閉じの検 1 本で 100〜224 module を展開する。展開の木は disk の置き場(`DOEFF_EFFECT_ANALYZER_CACHE`・
  既定 `$XDG_CACHE_HOME/doeff-effect-analyzer/hy-trees`)に置く。日次の検は 4 時間ごとに走り、置き場は機体の `/work/state` の下で回をまたいで残る。
- 前の形: 置き場の 1 項に、展開の木と、読み手が木から作る派生の値(定義の索引・塊ごとの本体の節と本体の事実)が一緒に入り、鍵に読み手
  (program_effects.py)の source の digest が入っていた。program_effects.py が 1 字変わるだけで全部の module を展開し直した。
- 要件:
  1. Hy の展開の木(と macro の記録)を、読み手(program_effects.py)の版に依らない鍵で置く。
  2. program_effects.py だけが変わった後の 1 回目の解析で、Hy の展開(`_expand_hy`)が 0 回になり、答えが変える前と同じ。
  3. 派生の値の形が変わった時に古い項を読まない守り(#2973 — 鍵に読み手の digest を入れる)は派生の値の側に残す。
  4. 触らない: 置き場を無くす・閉じの検を外す・時間の予算で隠す・RECORD_TAG が変わった時に展開を捨てる事。

### 責務(module)と公開の口(package の中の口)
| id | 責務 | 持つ知識 | 隠す知識 | 口 |
| --- | --- | --- | --- | --- |
| X | `hy_expansion.py` — Hy の module の展開と、展開の木の置き場 | 展開のやり方(scratch の module・`hy_compile`・`macro_recording`)・展開の鍵 `_ExpansionKey`(python・hy・自分の source の digest `_expander_digest`・RECORD_TAG・module の名・source の sha256)・項の形(macro の記録 → 木の pickle)・項の名(macro の記録の指紋) | 項の中の pickle の並び・鍵の綴り・`expansions/` の dir の並び | `_expand_hy`・`_expansion_key`・`_ExpansionKey.text()/.place(dir)`・`_stored_expansion(place)`・`_store_expansion(place, expanded)`・`_entry_name(macros)` |
| R | program_effects.py の派生の値の置き場 | 派生の値の形・その鍵(展開の鍵 + 読み手の digest + 形の印 `_TREE_KEY_FORMAT`)・読む順(派生の項 → 展開の項 → 展開) | 派生の値の pickle の並び・塊の切り方 | `_compile_hy(source, filename, module_name) -> _WholeTree \| _ChunkedTree` |
| A | program_effects.py の読み手(`_Reader`・`analyze_program`・`_body_facts` ほか) | 木から effect の答えを読む規則 | 木がどこから来たか | `analyze_program`・`module_ast` ほか |
| O | 外れの観測(`observe_expansions`・使い手 = doeff-hy-pytest の予算) | 外れ(展開・展開の項の読み・派生の値の作りと書き)の間を観測者に知らせる | 観測者が誰か | `observe_expansions(observer) -> stop` |
| C | result_cache.py — 答えの置き場 | 答えが依る module の組を辿る(source を読んだ module・解析の module・名前空間の値の定義元 …) | 置き場の形 | `cached_result(identity, compute)` |
| P | env_places.py | 環境変数の素の値 | 判断は呼び手 | `tree_cache_setting()` |

effects: X = Hy の compile(scratch の module に macro を登録)・置き場の dir の作成・項の書き(一時 file → `os.replace`)と読み。R = 派生の項の書きと読み。
項は disk に残り、誰も消さない。

### 将来の変更についての主張(変更シナリオ)
- S1(storage): 読み手の変更 — 派生の値の形や読む規則など program_effects.py の何かを変える。主張: 変えるのは program_effects.py(R・A)だけで X は変えない。
  形の印を手で上げなくても、変えた後の 1 回目の解析は展開 0 回で、派生の値は今の読み手が作り直す(古い読み手の派生の項は読まない)。
  前提: 展開の木の中身を決める code は全部 X に在り、R・A は木を読むだけ。
- S2(storage): 展開のやり方や展開の項の形を変える。主張: 変えるのは hy_expansion.py だけ。X の digest が両方の鍵に入るので、前の版が作った木を読まない。
- S3(storage): 使った macro の本体・Hy / Python の版・RECORD_TAG が変わる。主張: code を変えずに展開し直す。使わない macro の変更では外れない。
- S4(effects): 読み手が変わった後の「展開の項を読んで派生の値を作る」分も、展開と同じく観測の中に在る。O は変えない。当たりは観測しない。
- S5(concurrency): 並べた pytest の process が同じ source を同時に外す。主張: 書き手は一時 file から `os.replace` で置くので書きかけの項は読まれない。
  読めない項は外れとして展開し直して書き直す。鍵は要らない。
- S6(distribution)・S7(hardware)・S8(simulation): 適用外(置き場は機体ごと・木は python の版で決まり版は鍵に在る・analyzer は Program の外の道具で時刻や乱数を持たない)。

### 検査(実体と範囲)
- 契約の検: `packages/doeff-effect-analyzer/tests/python/test_hy_tree_cache.py`(全部・20 本)。読み手の写しに 1 行足して `pe.__file__` に読ませる検、
  hy_expansion.py の写しに 1 行足す検、古い読み手の派生の項を読まない検、macro の変更の検、観測の検、読めない項の検を含む。
- 答えの置き場の検: `packages/doeff-effect-analyzer/tests/python/test_result_cache.py`・`docs/adr/defadr_doeff_analyzer_001_result_key.hy`。
- 予算の検: `packages/doeff-hy-pytest/tests/test_budget.py`(観測)。
- 静的: ruff(repo 根の pyproject.toml の設定)・pyright(pyrightconfig.json)・doeff-linter(package の `architecture.hy` — 今は env_places の層 1 つだけを宣言し、DOEFF004 を外す)・`.semgrep.yaml`(analyzer に向けた規則は無い)・code-quality の fast の段(変更範囲)。
- 実行の経路: 日次の全体の pytest(root の testpaths に analyzer の tests/python が在る)と、変更時の名指しの実行。commit の hook は semgrep の規則と台帳の突合。
- 命令: `uv run --no-sync pytest packages/doeff-effect-analyzer/tests/python/test_hy_tree_cache.py -q -p no:cacheprovider`

### 制約
- 上の木の file は読むだけで、変えない・新しい file を置かない。検査を走らせない(再現は依頼した側が行う)。全数の検査・他の会話への連絡をしない。
