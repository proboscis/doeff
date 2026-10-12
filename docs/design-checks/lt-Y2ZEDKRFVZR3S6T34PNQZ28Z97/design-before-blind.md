# 盲検の前の主張 — 共有の Hy の bytecode の保存先の鍵 1 つに macro の環境ごとの entry を並べる(card ki-6828e9af04b4・依頼 lt-Y2ZEDKRFVZR3S6T34PNQZ28Z97)

この文書の目的: 盲検 A・B を起こす前に、設計者の主張(変更シナリオごとに何が変わり、何が変わらないか)と強制方法を固定する。後の設計検証の本文(design.md)は、この主張を上書きせず、実測との差を別に書く。

完了の範囲: 実装完了まで。実装は doeff main 27f35483076413668aa7b2ca32949b170a0987b4(L2170・2026-10-12 12:55 JST 着地)、変更の前の版は 94287ee5646ae0ffbd790dcc47c9760f0de24a23。この主張は実装の後・盲検の前に書いた(実装の前に書いた主張ではない)。

## 1. 要件(固定)

| id | 要件 | 出所 |
|---|---|---|
| R1 | 保存先の鍵 1 つを、macro の環境(doeff の版・記録の形の印)の違う書き手が交互に上書きしない形が doeff の main に在る | card の受入 c39df0971 |
| R2 | 版の違う 2 つの書き手が同じ source を交互に読み書きしても、焼き直しが 2 回目から起きない事を確かめる検が在る | 同上 |
| R3 | worker の掃除(env_store の sweep-leftovers)が新しい並びの file も消せる | card 本文の案 2 |
| R4 | worker の入れ替えの後の 1 時間の焼き直しの本数と bytecode の段の秒を card に書く | card の受入 c05aec7db(worker の版上げの後 — この検証の範囲の外) |

背景の事実: 書き手は 2 種 — 実行環境の準備(doeff-cluster の焼きの道具 bytecode_pool.hy。準備する root の venv の python で走り、root の版の doeff-hy の照らし bytecode-is-current と、worker の版の code_store.py を path で読む)と、作業木の import(doeff-hy の loader_hooks。doeff の版は作業木ごとに違う)。直す前は鍵 1 つに entry 1 つで、互いの entry を古いと見て焼き直し、上書きし合った(2026-10-12 zeus — 準備 1 回 100〜600 本)。

## 2. 責務(module)

| id | 実体 | 持つ判断・知識 | 隠す知識 | 公開契約 | effects | 寿命・解放 | 守る不変条件 |
|---|---|---|---|---|---|---|---|
| M1 code-store-entry | `packages/doeff-hy/src/doeff_hy_bytecode_guard/code_store.py` | 保存先の dir の解き方・鍵の作り方(code_key)・source の code の entry の並び(`<鍵の頭 2 字>/<鍵の残り>/<指紋>.code`)・指紋の作り方(code_fingerprint — code の定数の末尾の値の repr の sha256)・照らす順(使った時刻の新しい順)・使った印・数の上限(CODE_ENTRIES_PER_KEY 16)と除く順・一時の file からの置き換え・壊れた entry の名指しと除き | 並び・指紋・上限・除く順・時刻の扱い | `code_key(...) -> str`・`current_code(store, key, is_current) -> CodeType\|None`・`add_code(store, key, code) -> str\|None`(書けない理由)・`code_entries(store, key)`(使った時刻の新しい順の path)・`store_dir()`・`store_is_named()` ほか(dataclass・.imports・.binding・.facts 用の entry_path・read_entry・write_entry・stored_code・discard_entry) | 保存先の file の読み・書き・時刻・除き(標準 library の os を直に — worker の道具が root の python へ path で読むので標準 library だけ・ADR-DOE-BUILD-002) | 状態を持たない。entry は上限か掃除で消える | 足した後、鍵 1 つの entry ≤ 上限。記録の同じ code は同じ名。照らして使った entry だけ時刻が進む。書きかけを読ませない |
| M2 record-check | `packages/doeff-hy/src/doeff_hy_bytecode_guard/records.py`(記録の形・RECORD_TAG・record_of)と `macro_use.py`(digest)・`loader_hooks.record_is_current_here` | macro の記録の欄と、記録が今の環境に合うかの判断 | 記録の欄の意味・digest の作り方 | `records.record_of(code)`・`record_is_current_here(record)`・`bytecode_is_current(path, pyc, source)` | 記録が名指す module の import・file の sha256 の読み | process の中の覚え | M1 は記録の欄の意味を読まない(末尾の定数の値を丸ごと指紋にするだけ) |
| M3 import-hook | `packages/doeff-hy/src/doeff_hy_bytecode_guard/loader_hooks.py` の `_from_shared_store`・`_to_shared_store`・`_store_key` | いつ保存先を引くか(作業木の .pyc が使えない時)・compile した code を足すか(記録が在る時) | — | Python の import の get_code の包み | source の読み・作業木の .pyc の書き・M1 の呼び | import 1 回 | 鍵は compile した bytes から作る(#2799) |
| M4 bake-tool | `packages/doeff-cluster/src/doeff_cluster/foundation/bytecode_pool.hy` の `bake-one` | 焼く物 1 つを、木の .pyc → 保存先 → 焼く の順に用意する | — | 標準入力の行・標準出力の `stored`/`failed`/`reused`/`unstored` の行 | source の読み・.pyc の書き・pool の子 process・M1 の呼び | 道具の 1 回の起動 | 照らしは root の版の M2(bytecode-is-current)で、引きと足しは worker の版の M1 |
| M5 store-sweep | `packages/doeff-cluster/src/doeff_cluster/worker/protocol/env_store.hy` の `unused-store-names`(純粋な判断)・`unused-store-entries`・`sweep-leftovers`、閾 `worker/core/env_upkeep.hy` の `CODE-STORE-UNUSED-SECONDS` | 保存先の何を消すか: 7 日動いていない file と、自分も下の物も全部 7 日動いていない dir(いちばん上の dir だけを名指す) | 7 日・dir ごと消す決め | `unused-store-names(entries, now-ms) -> tuple[str]`(StatTree の答え → 消す相対の名) | StatTree・RemoveTree(effect — 答え手は os-file-handler と offloaded-tree-handler) | 掃除 1 回 | 保存先の並びを知らない(木の時刻だけで決める)。使った entry の在る dir と新しい dir は消さない |

## 3. 変更シナリオと主張(6 軸)

| id | 軸 | 適用 | 変える要求 | 主張 | 予想する範囲 | 変わる module | 変わらない module |
|---|---|---|---|---|---|---|---|
| S1 | storage | 適用 | 鍵 1 つの entry の数の上限や、除く順・指紋の作り方を変えたい(例: 上限を 32 に・使った時刻でなく足した時刻で除く) | 並び・指紋・上限・除く順は M1 だけが持つ。M3・M4 は `current_code`/`add_code` を呼ぶだけで、M5 は並びを知らない。変えるのは code_store.py と M1 の検だけで、公開契約(current_code・add_code)は変わらない | `code_store.py`・`tests/test_code_store_entries.py` | M1 | M2・M3・M4・M5 |
| S2 | concurrency | 適用 | macro の環境の違う書き手が 3 つ以上(準備・doeff の版の違う作業木が幾つも)同時に同じ鍵を読み書きする。あるいは新しい書き手の種類が加わる(例: image の組み立ての道具が保存先へ足す) | 書き手は自分の照らし(is_current)を渡して `current_code` を呼び、焼いたら `add_code` を呼ぶだけで、他の環境の entry を上書きしない(名 = 自分の記録の指紋)。新しい書き手はその module と配線だけが変わり、M1・M2 は変わらない。同時の書きは一時の file からの置き換えで混ざらない | 新しい書き手の module だけ | (新しい書き手) | M1・M2・M3・M4・M5 |
| S3 | storage | 適用 | 記録の形を変える(RECORD_TAG を 7 に上げ、欄を足す) | 記録の欄の意味は M2 だけが持つ。M1 は末尾の定数を丸ごと指紋にするだけなので、足す欄が文字列・bytes・整数・浮動小数・None とその組の入れ子なら M1 は変わらない。M4 は worker の版の M1 で root の版の記録を名にするので、root と worker の版が違っても効く | `records.py`・`macro_use.py` と M2 の検 | M2 | M1・M3・M4・M5 |
| S4 | effects | 適用 | 掃除の決まりを変える(例: 7 日を 3 日へ・保存先に別の種類の entry の並びを足す) | 日数は `CODE-STORE-UNUSED-SECONDS` の 1 行(M5)。M5 は並びを知らず木の時刻だけで決めるので、M1 の並びを変えても M5 は変わらず、M5 の日数を変えても M1 は変わらない | `env_upkeep.hy` の 1 行(日数)・新しい並びは `code_store.py` だけ | M5(日数)か M1(並び) | 他方と M2・M3・M4 |
| S5 | hardware | 除外 | — | 保存先は worker の機体の disk の dir(env DOEFF_HY_CODE_STORE)で、判断は CPU の数・機体の形に依らない | — | — | — |
| S6 | distribution | 除外 | — | 保存先は機体ごと(worker の state の下)で、機体をまたいで分け合わない。機体をまたぐ保存先は要件に無く、作るなら M1 の effect(os の直の読み書き)を替える別の設計になる | — | — | — |
| S7 | simulation | 除外 | — | 保存先は速さのための cache で、doeff-cluster の模擬の世界(sim/env_world.hy)は保存先を中身の印の file の代役で持ち、entry の並びを写さない。この変更は模擬の世界の判断を変えない(M5 の判断は純粋で、StatTree の答えの代役で確かめられる — 検 test_env_store.hy は tmp の dir で確かめている) | — | — | — |

主張の前提:
- source の code の entry を読み書きする呼び手は M3 と M4 だけで、どちらも M1 の `current_code`・`add_code` を通る(dataclass の関数の code・.imports・.binding・.facts は別の鍵の印で 1 鍵 1 entry のまま)。
- 照らし(is_current)は呼び手が渡し、M1 は記録の意味を読まない。
- 本番の準備での効きは worker の版上げの後(R4 はこの検証の範囲の外)。

## 4. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 保存先の並びを綴るのは M1 だけ(S1) | ADR の法 one-bytecode-store-entry: 入口の外の Python と Hy の file が種類の末尾 `".code"`・`".imports"` の文字列を綴ったら赤 | `docs/adr/defadr_doeff_build_002_one_bytecode_store.hy` の `test-adr-doe-build-002-one-bytecode-store-entry`・`spells-store-layout` | pytest(ADR の冊) | 文字列の綴りだけを見る。M1 の公開の名(`entry_path`・`CODE_SUFFIX`・`write_entry`)で 1 鍵 1 entry の書きを組む呼び手は捕まえない |
| 環境の違う書き手が上書きし合わない(S2・R1・R2) | 振る舞いの検: macro の違う 2 つの書き手が同じ置き場で新しい木を 3 巡交互に読み、2 巡目から展開(焼き)が 0 | `packages/doeff-hy/tests/test_bytecode_guard.py::test_two_macro_versions_taking_turns_on_one_store_stop_expanding_from_the_second_round`・`packages/doeff-cluster/tests/test_code_prepare_content_store.py::test_e_two_macro_versions_taking_turns_do_not_rebake_each_other` | pytest | 書き手 2 つ・鍵 1 つの形だけ。上限を越える数の環境は単体の検だけ |
| M1 の並び・上限・使った印(S1・S2) | 単体の検 8 件 | `packages/doeff-hy/tests/test_code_store_entries.py` | pytest | 同時の書きの競りは検で再現していない(置き換えの原子性に頼る) |
| 記録の欄を足しても指紋が潰れない(S3) | 単体の検 `test_records_differing_only_in_a_bytes_or_float_field_keep_separate_entries` | 同上 | pytest | 形の外の値(frozenset など)を足すと 1 つの名に潰れる — 静的には止めていない(註で書き手に頼む) |
| 掃除は並びを知らず、使った物を消さない(S4・R3) | 振る舞いの検: 鍵の dir の下の 7 日使われない entry・中が全部 7 日動いていない dir は消え、使った entry の在る dir と新しい空の dir は残る | `packages/doeff-cluster/tests/test_env_store.hy` の `test-the-sweep-removes-unused-code-entries-in-key-dirs-and-the-dirs-left-unused`・既存の `test-the-sweep-removes-store-entries-unused-for-seven-days` | pytest | 保存先の dir を誤って別の dir に向けた時の守りは無い(前から) |
