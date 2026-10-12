# 設計検証 lt-Y2ZEDKRFVZR3S6T34PNQZ28Z97 — 共有の Hy の bytecode の保存先の鍵 1 つに macro の環境ごとの entry を並べる(card ki-6828e9af04b4)

この文書の目的: doeff-hy の保存先の入口(code_store.py)が鍵 1 つに macro の環境ごとの entry を並べる変更が、どの責務に閉じているか、何で守られているかを、盲検 A・B と実測の後の形で示す。読み手は、上限・除く順・記録の形・掃除の決まりを変える時に、どこを変え、どの検が守るかを決められる状態になる。

盲検の前の主張は design-before-blind.md(sha256 f3c6df9d6e7670984b607a18e4bb5e75b73e435c513051b5135064f1de1c3a96)に固定してあり、この文書は主張を上書きしない。主張と実測の差は 4 節に書く。

## 全体の形(今)

```
 作業木の import(doeff の版は作業木ごと)            実行環境の準備(worker の道具・root の venv の python)
 loader_hooks._from_shared_store / _to_shared_store     bytecode_pool.hy bake-one
   照らし = 作業木の版の records(M2)                     照らし = root の版の bytecode-is-current(M2)
            │ current_code(store, key, 照らし)                     │ current_code / add_code
            │ add_code(store, key, code)                           │ (worker の版の code_store.py を path で読む)
            ▼                                                      ▼
   ┌──────────────── code_store.py(M1・保存先の入口 1 つ)────────────────┐
   │ <store>/<鍵の頭 2 字>/<鍵の残り>/<記録の指紋>.code                     │
   │  ・照らす順 = 時刻の新しい順(使った entry が前・足しただけの entry は後ろ)│
   │  ・使った印 = 照らして使った entry だけ時刻を今に                        │
   │  ・足した entry = 鍵の他の entry より 1 秒前(1 日より前にはしない)       │
   │  ・上限 16 = 今足した entry を残して時刻の古い物から除く                 │
   └──────────────────────────────────────────────────────────────────┘
            ▲ StatTree / RemoveTree(effect)
 worker の掃除 env_store.unused-store-names(M5): 7 日動いていない file と、自分も下も全部 7 日動いていない dir
```

## 1. 完了の範囲

| 項目 | 状態 |
|---|---|
| 鍵 1 つに環境ごとの entry を並べる形(code_store・loader_hooks・bytecode_pool・掃除・ADR R2b・R5b) | 実施済み — doeff main 27f35483(L2170) |
| 盲検 A を受けた直し: 足した entry を鍵の他の entry より後ろに置く(`add_code`・`_place_after`・`NEW_ENTRY_FLOOR_SECONDS`)と単体の検 3 件 | 実施済み — この記録と同じ branch(2377ad0cf)で main へ |
| 盲検 B を受けた検: 記録の世代の違う書き手が交互に読む検(`test_two_record_generations_…`) | 実施済み — 同上 |
| 失敗例 N4 を作る中で見つけた検の弱さの直し: 掃除の検の「使った entry の在る鍵の dir」の時刻を 7 日前に置く | 実施済み — 同上(c1a73eeda) |
| 本番での効き(card の受入 c05aec7db — worker の入れ替えの後の 1 時間の焼き直し) | この検証の範囲の外。準備の道具は worker の版の code_store.py で走るので、worker の次の版上げの後に測る(card ki-4c2f5878e524 と同じ扱い) |

## 2. 責務と公開契約(盲検の後)

盲検の前の表(M1〜M5)はそのまま有効。M1 の不変条件と公開契約の説明を、盲検 A を受けて書き足す。

| id | 実体 | 持つ判断 | 公開契約 | 盲検の後に足した事 |
|---|---|---|---|---|
| M1 code-store-entry | `doeff_hy_bytecode_guard/code_store.py` | 並び・指紋・照らす順・使った印・足した entry の置き場所・上限と除く順 | `current_code(store, key, is_current)`・`add_code(store, key, code)`・`code_entries(store, key)` | 足した entry は使われるまで鍵の他の entry より後ろ。上限は「使い続けている entry を何個まで同時に守るか」の数で、一度きりの環境の数に依らない。使い続ける環境が上限を越えると、越えた分(+1)だけが外れる |
| M2 record-check | `records.py`・`macro_use.py`・`loader_hooks.record_is_current_here`・`bytecode_is_current` | 記録の欄の意味と、今の環境に合うか | `record_of`・`record_is_current_here`・`bytecode_is_current` | 「今の版で読めない記録」は他の版の書き手の entry で、読み手は除かない(ADR R2b に書いた) |
| M3 import-hook | `loader_hooks._from_shared_store`・`_to_shared_store` | いつ保存先を引き・足すか | import の get_code の包み | なし |
| M4 bake-tool | `bytecode_pool.hy` の `bake-one` | 焼く物 1 つの用意 | 標準入力・標準出力の行 | なし |
| M5 store-sweep | `env_store.hy` の `unused-store-names`(と閾 `env_upkeep.CODE-STORE-UNUSED-SECONDS`) | 何を消すか | `unused-store-names(entries, now-ms)` | なし(足した entry の時刻は今から 1 日より前にはならないので、7 日の閾と食い違わない) |

## 3. 強制方法(盲検の後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 保存先の並びを綴るのは M1 だけ(S1) | ADR の法 one-bytecode-store-entry(入口の外で `".code"`・`".imports"` を綴ると赤)— 失敗例 N1 で拒む事を確かめた | `docs/adr/defadr_doeff_build_002_one_bytecode_store.hy` の `test-adr-doe-build-002-one-bytecode-store-entry` | pytest(ADR の冊) | 文字列の綴りだけを見る。M1 の公開の名(`entry_path`・`CODE_SUFFIX`・`write_entry`)で 1 鍵 1 entry を組む呼び手は捕まえない |
| 環境の違う書き手が上書きし合わない(S2) | macro の違う 2 つの書き手が交互に読む検(Hy の import と準備の道具)・単体の検 — 失敗例 N2 で 3 本とも拒む事を確かめた | `test_bytecode_guard.py::test_two_macro_versions_…`・`test_code_prepare_content_store.py::test_e_…`・`test_code_store_entries.py::test_writers_with_different_records_…` | pytest | — |
| 一度きりの環境が使い続けている entry を押し出さない・上限を越えても崖にならない(S2・盲検 A) | 単体の検 3 件(足した entry は使うまで後ろ・一度きりの環境 48 個の間も準備の代役が当たる・上限 + 1 の環境で 2 巡目から外れは 2 以下)— 着地した版では 3 件とも赤 | `test_code_store_entries.py` の `test_a_new_entry_waits_behind_…`・`test_one_off_environments_…`・`test_more_environments_than_the_limit_…` | pytest | 使い続ける環境が上限を越えた時の外れは残る(越えた分 + 1)— 本番の鍵ごとの環境の数は R4 の測りで読む |
| 記録の世代の違う書き手が互いの entry を除かない(S3・盲検 B) | 包みの写しで RECORD_TAG だけを次の世代へ上げた書き手と交互に読む検 — 盲検 B の差分で赤(着地した版でも直した版でも)・差分なしで緑 | `test_bytecode_guard.py::test_two_record_generations_taking_turns_on_one_store_keep_each_others_entries` | pytest | 世代の違う書き手は印の文字列だけを変えた代役(欄や digest の変化は写さない) |
| 記録の欄を足しても指紋が潰れない(S3) | 単体の検(bytes・浮動小数の欄) | `test_code_store_entries.py::test_records_differing_only_in_a_bytes_or_float_field_…` | pytest | 形の外の値(frozenset など)を足すと 1 つの名に潰れる — 静的には止めていない(code_store の註で書き手に頼む) |
| 掃除は使った entry の在る dir を消さない(S4) | 掃除の検(使った entry の在る鍵の dir の時刻も 7 日前)— 失敗例 N4 で拒む事を確かめた。強める前の検は N4 を拒まなかった | `test_env_store.hy` の `test-the-sweep-removes-unused-code-entries-in-key-dirs-and-the-dirs-left-unused` | pytest | 保存先の dir を誤って別の dir に向けた時の守りは無い(前から) |

## 4. シナリオごとの予想と実測

| id | 軸 | 主張(盲検の前) | 実測 | 差と理由 | 直し |
|---|---|---|---|---|---|
| S1 | storage | 上限・除く順・指紋を変えるのは code_store.py と M1 の検だけ | 盲検 A を受けて除く順(足した entry の置き場所)を実際に変えた。変わった file は `code_store.py` と検の 2 file(`git diff --stat 27f354830 2377ad0cf -- packages/` — evidence/scenarios/positive.txt)と ADR R2b の本文。M2〜M5 と公開契約は変わらない。失敗例 N1(準備の道具が並びを自分で綴る)は ADR の法が拒んだ(evidence/scenarios/N1-negative.txt) | 予想どおり。ただし ADR R2b の本文が上限の値(16)と除く順を文で持つので、値を変えると ADR の本文も変える(盲検 A の「主の例にしなかった物」— 検は無い) | 不要(ADR の本文は決まりの記録で、知識の漏れではない) |
| S2 | concurrency | 書き手が 3 つ以上でも、新しい種類の書き手が加わっても M1・M2 は変わらない | 盲検 A: 鍵 1 つに同時に使われる環境が上限 16 を越えると全部が外れる崖になり、一度きりの環境(macro を直している作業木の検)14 個で準備の entry が押し出された。着地した版で再現した(evidence/a/A-repro-before-fix.txt — 17 の環境で毎巡 17 本・一度きり 14 個で準備の代役が 3/3 外れる) | 主張は外れ。主張は「使い続ける環境の数 ≤ 上限」を黙って前提にしていた。足した entry を頭に置く除く順が、一度きりの環境の数まで上限に数えさせていた(M3 の足し方の性質が M1 の除く順に響く — 盲検 A の 3 節) | M1 の中だけで直した: 足した entry を使われるまで後ろに置く。直した後: 17 の環境で毎巡 2 本・20 で 5 本・32 で 17 本(越えた分 + 1)・一度きり 40 個でも準備の代役は外れない(evidence/a/A-repro-after-fix.txt)。単体の検 3 件は着地した版で赤・直した版で緑(evidence/a/A-new-tests-on-the-landed-version.txt・evidence/scenarios/positive.txt)。失敗例 N2(指紋を 1 つの名に)は 3 本の検が拒んだ(evidence/scenarios/N2-negative.txt) |
| S3 | storage | 記録の形を変えても(RECORD_TAG を上げても)M1 は変わらず、版が違っても効く | 盲検 B: import の口が「今の版で読めない記録の entry」を除く差分は、提示した検 22 件を全部通るのに、印の違う書き手どうしが互いの entry を消し合う。着地した版で再現した(evidence/b/B-presented-checks-with-patch.txt — 22 passed) | 主張そのもの(M1 の作り)は成り立つ — 差分を外した版では印の違う書き手が 2 巡目から展開し直さない(evidence/b/B-new-check-without-patch.txt)。足りなかったのは、主張を守る検(書き手の doeff-hy が 1 つの検しか無かった) | 検を足した: 包みの写しで RECORD_TAG だけを上げた書き手と交互に読む。B の差分で赤('this', 2)・なしで緑(evidence/b/B-new-check-with-patch.txt・evidence/scenarios/N3-negative.txt)。ADR R2b に「読めない記録の entry も除かない」を書いた |
| S4 | effects | 掃除の日数は 1 行、M5 は並びを知らないので M1 の並びを変えても M5 は変わらない | S1 の除く順の変更で M5 は変わらなかった(足した entry の時刻は今から 1 日より前にならないので 7 日の閾と食い違わない)。失敗例 N4(dir を自分の時刻だけで判じる)を作ると、前の掃除の検は拒まなかった(evidence/scenarios/N4-against-the-earlier-test.txt)。盲検 A は「7 日 → 3 日」も ADR の検 `(val CODE-STORE-UNUSED-SECONDS WHEEL-UNUSED-SECONDS)` に当たると指摘した | 日数の変更は R5 の決まり(wheel と同じ 7 日)の意図した変更で、ADR の本文と検も変わる — 予想の範囲が狭かった(漏れではない)。掃除の検は「使った entry の在る dir の時刻は古い」本番の形を持っていなかった | 掃除の検を強めた(c1a73eeda)。N4 を拒む事を確かめた(evidence/scenarios/N4-negative.txt) |
| S5 | hardware | 除外 | — | — | — |
| S6 | distribution | 除外 | — | — | — |
| S7 | simulation | 除外 | — | — | — |

範囲外として残す事(この依頼の要件の外・未解決の項目ではない):
- 本番の準備での効き(card の受入 c05aec7db)。worker の次の版上げの後に、`/work/state/roots/*/.doeff-env-ready.json` の時ごとの `bytecode.rebuilt` と bytecode の段の秒で測る(card ki-4c2f5878e524 の発言の script)。鍵ごとの entry の数の分布(盲検 A の 4(c) の find の命令)も同じ時に読む。
- 使い続ける環境が鍵 1 つで 16 を越えた時の外れ(越えた分 + 1)。上限を上げると、どの entry にも合わない新しい環境の読みが照らす数が増える。本番の分布を読んでから決める。

## 5. 盲検の記録

- 起動: blind/blind-meta.md。第 1 候補 gpt-6-astra は、この Pod の codex-cli で試して 401(資格なし)。第 2 候補 opus・effort xhigh で起こした。観測した model と effort は未確認。1 回目の A は API の認証の誤り(OAuth token revoked)で返答が無く、同じ候補・同じ入力で 1 回だけ起こし直した(安全の行を prompt に 1 行足した — blind-meta.md)。
- 入力: blind/blind-common.md・blind/blind-a-input.md・blind/blind-b-input.md・design-before-blind.md。返答: blind/blind-a-return.md・blind/blind-b-return.md(加工なし)。
- 盲検 A の試しの script は置き場に残っていなかったので、返答の 4(a) の要点に沿って設計者が書いた(evidence/a/probe_cap.py.txt)。
- 盲検 B の差分と試しの driver は B の置き場から写した(evidence/b/blind-b.patch.txt・evidence/b/blind-b-driver.py.txt)。

## 6. 設計の前に頼んだ見直し(盲検ではない)

実装の着地の前に、新しい文脈の Fable 5.1 に見直しを頼んだ(盲検ではない — 設計者の説明を渡した)。採った物: 上限を 8 → 16・指紋の対象の形に bytes と浮動小数を足す。採らなかった物: 他の環境の記録を照らす時の import の失敗の扱い(前からある露出・`except Exception` は禁じ手)・掃除の保存先の dir の形の確かめ(前から file を消す — 増える危うさは小さい)。この見直しの返答は盲検 A・B に渡していない。
