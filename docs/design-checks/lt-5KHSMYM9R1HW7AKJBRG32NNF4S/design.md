# 設計検証 lt-5KHSMYM9R1HW7AKJBRG32NNF4S — doeff-hy-check の 1 枠の中の並走を、機体の検の枠の宣言に従わせる(card ki-1bfe2c42dce6)

## この文書の目的

読み手が次の 4 つを、この文書だけで辿れるようにする。

1. doeff-hy-check の 1 枠の中の並走を宣言に従わせるか、その決めと訳。
2. 従わせる時の、正本(dotfiles の `machine_check_slots.py`)の公開の口と、doeff 側の受け口(M-slot)の形。
3. M-slot の守りが、何を断ち何を通すか(盲検 B の実装を断ち、盲検 A の形を通す)。
4. 盲検 A・B・構成レビューの指摘と、直した所・直さなかった所。

## 結論

- **従わせる**(D1)。展開の子の数と pyright の thread の上限を、枠の正本が答える数(宣言 `processesPerSlot`)にする。ただし**日次の機体で冷えた回を測るのを、agora-controllers の doeff の pin を進める前の門にする**(構成レビューの条件を採った)。
- 正本に公開の口 `process_width(policy, holding, environ) -> int` を足し、旧い `width_for_child` を替える。M-slot はこの口に方策と take の答えをそのまま渡して数を受ける。
- M-slot の守りは「開かない物の使い道の文法」へ作り直す。方策を不透明に渡す形は通り、take の答えや方策から欄を読み戻す形は断つ。
- 試作は両 repo の作業 branch にある(doeff 8b81009f9・dotfiles 4b9b243b9)。main へは入れていない。

## 全体の構成(今 ⇒ 目標)

箱の中は実物の名(file・関数・環境変数)。矢印の上の文字は渡す物。

```
【今】                                                                  【目標】
会話の shell / gate / pytest                                           会話の shell / gate / pytest
   │ doeff-hy-check                                                       │ doeff-hy-check
   ▼                                                                      ▼
doeff_hy/static_check.py  main                                         doeff_hy/static_check.py  main
   │ take_slot(home,label,out)        ┌ 展開の子の数 =                   │ take_slot(home,label,out)        ┌ 展開の子の数 =
   │                                  │  expander_limit(env DOEFF_HY_    │                                  │  expander_limit(slot.width)
   │                                  │  CHECK_EXPANDERS | 4)            │                                  │  (宣言が届かない機体は 4)
   │                                  └ pyright の thread =              │                                  └ pyright の thread =
   │                                    env DOEFF_HY_CHECK_PYRIGHT_         │                                    min(env, slot.width)
   │                                    THREADS (既定 1)                  │
   ▼                                                                      ▼
doeff_hy/check_slot.py (M-slot)                                        doeff_hy/check_slot.py (M-slot)
   │ module.take(module.read_policy(),…)                                  │ policy = module.read_policy()
   │ → SlotHeld(holding)  持ち物ごと預かる                                  │ holding = module.take(policy,…)
   │ 幅の口は無い                                                          │ width = module.process_width(policy, holding, environ)
   ▼                                                                      │ → SlotHeld(let_go, width)  解く口と数だけ預かる
dotfiles agent/tests/machine_check_slots.py (M-canon)                     ▼
   read_policy / take / run_held                                       dotfiles agent/tests/machine_check_slots.py (M-canon)
   width_for_child(policy,nested,inherited)->str                          read_policy / take / run_held
     └ run_held が子の env AI_CHECK_SLOT_PROCESSES へ置く                  process_width(policy,holding,environ)->int   ← 規則の座は 1 つ
   宣言 machine_check_slots.json                                            └ run_held も同じ関数で子の env を決める
     slots=8 processesPerSlot=2 …                                        宣言 machine_check_slots.json(持ち主は変わらない)

守り: test_the_canon_policy_goes_straight_into_take                     守り: tests/test_check_slot_opacity.py
      (read_policy の答えが take の第 1 引数にだけ現れる)                    (開かない物の使い道の文法 + 読んでよい名の組
                                                                            + 正本が持つ名が code に無い + 既定の 4 を読む所は 1 つ)
```

今の問題は 3 つ: (1) 展開の子の数の持ち主が doeff の定数と環境変数にあり、宣言と 2 つになっている。(2) 守りが「方策が take の引数にだけ現れる」を見るので、take の答えから欄を読む実装は通り(入る向きしか見ない)、方策を正本の別の口へ渡す正しい形は断られる。(3) 正本に口を足すと、口が無い正本の機体では枠そのものを失う(口の揃いの判定が全か無か)。目標はこの 3 つを、上の構成で同時に直す。

## 決めと訳

| id | 決め | 訳(根拠) |
|---|---|---|
| D1 | 従わせる。展開の子の数 = 宣言の数。pyright の thread は環境変数の値を宣言の数で頭打ちにする | 機体の検の合計(枠の数 K × 1 枠の並走)が宣言の関数になる。今は 2 か所が doeff の定数 4 を前提に容量を数え(`controllers/agent_env/entry/system.hy` 84 行「6 本 × 4 = 24 < 36 core」・`.agents/land-queue.toml` 345 行)、1 か所が値の持ち主を doeff と指している(`scripts/hy_type_check.hy` 59 行 — evidence/F-capacity-notes-agora-controllers.txt)。直の入口(会話が撃つ doeff-hy-check)は命令の包みの外なので、env だけを読む入口(agora の `scripts/node-test.mjs` の形)では幅が届かない |
| D2 | 環境変数 `DOEFF_HY_CHECK_EXPANDERS` を外す。定数 `EXPANDER_LIMIT = 4` は「宣言が届かない機体の既定」として残す | 使い手 0 件(11 repo の origin の先端・文の言及が 1 行 — evidence/F-users-of-the-changed-names-11-repos.txt)。口が在ると「値を替えた会話の検だけが別の数で数え、枠が割れる」(正本の頭の註) |
| D3 | pyright の thread は `min(環境変数の値・既定 1, 宣言の数)`。日次の 2 は宣言 2 の内で変わらない | thread は pyright が起こす子の process(agora-controllers の `.agents/land-queue.toml` 354〜362 行の実測: 674 本で 1 本 = 310 秒・CPU 329 秒・RSS 3.6 GB / 2 本 = 206 秒・CPU 410 秒・RSS 合計 5.3 GB — evidence/F-capacity-notes-agora-controllers.txt)。環境変数は数を下げる口で、宣言を越えて上げる口にしない |
| D4 | 正本の `width_for_child`(env の文字列を返す)を、公開の口 `process_width(policy, holding, environ) -> int` に替える。規則は同じ。`run_held` も同じ関数で子の env を決める | 規則の座を 1 つにする。使い手は dotfiles の中だけ(evidence/F-users…)。入口は方策も持ち物も開かず、take の前に得た物をそのまま渡す |
| D5 | M-slot は `SlotHeld(let_go, width)` を返す。持ち物は預からず、解く口だけを預ける。口は必須(read_policy・take)と任意(process_width)に分け、任意が無くても枠は取る。幅の口が落ちても取った枠は持ち続ける | 盲検 A: 口の揃いが全か無かで、正本に無い口を足すと枠を失う。付随の危険: 幅の口の失敗を take の囲いに入れると、取った枠の fd を持ったまま「取らずに走る」と名乗る |
| D6 | 守りを 3 つに替える: 開かない物の使い道の文法 / 読んでよい名の組(固定) / 正本が持つ名が code に無い事。加えて、既定の数 `EXPANDER_LIMIT` を読んでよい場所を `expander_limit` の中だけにする | 旧い守りは入る向きしか見ず、正しい形を断った。盲検 B(2 回目)は、別の道具が既定の数で pool を使う実装を示した |

却下した案: 従わせない(doeff-hy-check を例外の利用者にする)は、容量の数えが doeff の定数に結ばれたまま残る。take の答えから欄を読む(盲検 B の 1 回目の形)は、正本の中身が doeff へ移る。

## 事前の主張と実測

事前の主張は design-pre-blind.md(sha256 e3f92dd3…)。盲検の後に直した所は右端に書き、事前の文は書き換えていない。

| シナリオ | 事前の主張(要約) | 実際 | 正常例 | 失敗例 | 直し |
|---|---|---|---|---|---|
| S1 hardware(宣言の数を替える) | JSON の 1 行だけが変わり、展開の子と thread が従う | 直の入口は宣言の数 1・2・3 のとおり、包みの中は正本の口の答え(宣言を越えない)。展開の子の数は `expander_limit(width)`、thread は `min` | A-S2S3-after-fix(H0: 4 passed)・P-final-affected(63 passed) | N1(幅を捨てる変異)で赤 | 構成レビューの指摘: 「別の機体の種類で」は成り立たない(宣言は dotfiles に 1 つ)ので「追随する全機体で一斉に」と読み替え、thread は展開の子と違い `min` と明記 |
| S2 storage(正本の内部) | Policy の欄の改名・入れ子の規則の変更は正本の中だけで終わる | **src は変わらなかった**。ただし doeff の実物の正本の検 2 本が、入れ子の規則の答え(2・1)を写していて赤になった(H2) | A-S2S3-after-fix(H2: 4 passed) | A-S2S3-before-fix(H2: 赤・期待 2 が 1) | 盲検 A: 期待の数を正本の `process_width` に問う形へ直した。構成レビュー: JSON の鍵の改名は公開の契約(使い手の縁の検が写す)なので別扱いと明記 |
| S3 effects(env の名・運び方) | env の名を替えても doeff は変わらない | src は変わらず。検の中継が env の名を文字列で書いていて赤になった(H1) | A-S2S3-after-fix(H1: 4 passed) | A-S2S3-before-fix(H1: 赤・期待 1 が 2) | 盲検 A: 名を正本の公開の定数 `PROCESSES_ENV` から読む形へ直した |
| S4 concurrency(外側が小さい幅を配った包みの中) | 内側は配られた数に従い、宣言へ戻して倍にしない | 検で確かめた(配った 1 → 内側の答えは正本の口の答えと同じで 1 以下) | P-final-affected | N2 / N2b(持ち物を None として問う変異)で赤 | 外側が内側を N 本並べる時は幅/N を配るのは外側の仕事で、検は強いない(構成レビュー: 範囲外と明記) |
| S5 distribution(版のずれ) | 口が無い正本でも枠は取れ、幅は届かず 1 行名乗って既定の 4 | 検で確かめた。着地の順は dotfiles → doeff でも逆でも安全(構成レビューも読みで確認) | P-final-affected | N3(幅の口を必須にする変異)で赤 = 先行の盲検 A の E2b の再現 | — |
| S6 simulation | 除外 | — | — | — | — |
| S7 concurrency(新しい道具が展開の子の pool を使う) | (事前の主張なし — 盲検 B が示したので後から足した) 新しい道具は `expander_limit(宣言の数)` を通した数でなければ pool を作れない | 盲検 B の実装(型の宣言の一致の検が既定の数 4 で pool を使う)は、直す前の全検査を通り(34 passed)、宣言 2・1 でも子 4 本(子孫の最大 6)を起こした | 守りの検(30 passed) | B-repro-stub-pool(宣言 2 でも pool [4])→ B-patch-on-fixed-tree(直した後は赤) | `project_closure` の `limit` の既定を外し、`EXPANDER_LIMIT` を読んでよい場所を 1 つにした |

## 盲検・構成レビューの指摘と扱い

| 出所 | 指摘 | 再現 | 成立 | 扱い |
|---|---|---|---|---|
| 盲検 A | 検が env の名と入れ子の規則の答えを写し、dotfiles だけの変更(env の改名・規則の変更)で doeff の検が赤になる | 設計者が再現(正本の 3 変種 H0・H1・H2 で、2 本が H1・H2 で赤) | 成立(src は主張どおり、検の層が崩した) | 直した。検は env の名を正本の定数から読み、期待の数を正本の口に問う |
| 盲検 B | 型の宣言の一致の検(doeff-hy-stub)が展開の子の pool を既定の数で使う実装が、守りを全部通る | 設計者が盲検の観測 script で再現(宣言 2 でも pool [4]・子孫 6)。直す前の検 34 本は通る | 成立 | 直した。`limit` の既定を外し、既定の数を読める場所を 1 つにする守りを足した。直した後は同じ差分を守りが拒む |
| 構成レビュー D1 | 日次の測りを「実装の受入(事後)」でなく pin を進める前の門にする | 日次が変わるのは agora-controllers の pin を進めた時(読み) | 採る | 「次の仕事」の順に書いた |
| 構成レビュー D3 | 要件 R1 の文言と D3 が食い違う | — | 採る | 下の要件の言い直し |
| 構成レビュー D6 | 守りの母集団は src だけ。検の層に宣言の鍵と env の名が残る | 検の層の env の名は上の A の直しで消えた。宣言の鍵は縁の検の写し(公開の契約)として残す | 採る(一部は A と同じ) | 下の不変条件の言い直し |
| 構成レビュー(責務) | 外側の入口が内側を N 本並べる時の約束・`hold_phase` だけを使う入口は方策を持たない | 読み | 採る(範囲外として明記) | 下の範囲と限界 |
| 構成レビュー(事実) | 「9 repo」は 11 | `~/repos` の 11 dir を引いた(自分で fetch したのは 5 つ) | 採る | 本文を 11 に直した |

### 言い直した要件・不変条件(事前の文は design-pre-blind.md のまま)

- R1(言い直し): 展開の子の数の持ち主は宣言だけ。pyright の thread は、宣言が上限・値は入口が選ぶ(既定 1・環境変数で下げも上げも宣言の内で)。
- I1(言い直し): 1 枠の process 木の並走(worker の数)≤ 宣言の数。親の待機は数えない(node-test も同じ数え方)。
- I2(言い直し): 宣言の鍵・方策の欄・env の名・入れ子の規則は、doeff の src に現れない。doeff の検の層では、宣言の鍵は縁の検が書く写し(使い手が知る公開の契約・agora の `scripts/check-slot.test.mjs` と同じ扱い)、env の名と規則の答えは正本の公開の口から取る。

## 強制方法と実装の証拠

| 守る責務 | 強制 | 実装箇所(試作 doeff 8b81009f9) | 実行経路 | 正常例 / 失敗例の証拠 | 限界 |
|---|---|---|---|---|---|
| 開かない物を開かない | AST の文法の検 | `tests/test_check_slot_opacity.py::opened_uses` | doeff の pytest(変更時・日次) | G-guard-on-history(試作 0 件)・13 の変異(pytest 内)/ 先行の盲検 B の差分・先行の盲検 A の形・基準の旧い形は拒否 | check_slot.py の中だけ。`slot.let_go` を static_check 側で開く道と、`vars(module)["a"+"b"]` の動的な名は見ない |
| 読んでよい正本の名は口と例外だけ | 同上 + 口の組の固定の検 | `_name_findings`・`test_the_mouths_this_module_declares…` | 同上 | 同上 | 組を書き換えれば通る(組は検の固定) |
| 正本が持つ名が code に現れない | AST の名・文字列の走査(docstring・コメントを除く) | `canon_owned_names` | 同上 | 試作 0 件 / import の別名・文字列・属性・識別子 | 名の組は検の写し。別の綴りは見ない |
| 既定の数を読む場所は 1 つ | 名の走査 | `default_limit_uses`・`test_the_default_child_count_is_read_only…` | 同上 | 試作で `expander_limit` だけ / B-patch-on-fixed-tree(赤) | 新しい道具が枠を取り忘れる事は見ない(数を得られず書き手が気づく) |
| 版のずれでも枠を失わない・幅の口の失敗で枠を失わない | 口だけの正本の検 | `tests/test_static_check_slot.py` | 同上 | P-final-affected / N3・N4・N4b | |
| 幅が宣言に従う | 実物の正本の写しの検(宣言 1・2・3・直の入口・前の値が残る入口・包みの中・外側が配った値) | 同上 | 同上(幅の口が無い機体は skip) | A-S2S3-after-fix / N1・N2・N2b | 正本が公開の口を失えば skip になる |
| 幅を使う側の配線 | main を偽の `take_slot` で撃つ検 | `test_static_check_parallel.py`・`test_static_check_pyright_threads.py` | 同上 | P-final-affected / N5・N6 | 展開の速さの実測は検でなく、pin の前の門 |
| 正本の規則 | 正本の検 | dotfiles `agent/tests/test_machine_check_slots.py`(34 passed) | dotfiles の pytest | P-canon-proto-tests | |

変更時の品質検査(fast): doeff の試作 `code-quality: passed (scope=changed)`(P-final-code-quality)・dotfiles の試作も passed(P-canon-code-quality-commit-hook — commit の hook の出力の転記)。型検査は含まない(日次)。

## 日次・移行への影響と、測っていない範囲

- **日次(agora-controllers の hy-type-trend)**: 2,523.6 秒 / 上限 3,300 秒(10-12 07:02 UTC の回・evidence/F-daily-log-excerpt.txt)。日次の job の HOME に正本があり(同じ回の doeff-lint-whole が `$HOME/dotfiles/agent/tests/machine_check_slots.py run` を通る)、`scripts/hy_type_trend.hy` の `measure` は doeff-hy-check を `RunProcess` で直に撃ち、枠の呼びを持たない(設計者が読んだ — origin/main の同 file に `check_slot`・`machine_check`・`hold_phase` の語が無い)ので、doeff-hy-check は枠を自分で取る入口で、正本があれば宣言の数 2 が効く(日次の機体の doeff の pin が check_slot を持つかは未確認)。**冷えた展開(約 750 秒・子 4 本)が子 2 本で伸びる分は推測で +350〜750 秒**(展開だけの実測 上限 1 = 55.8 秒・上限 8 = 17.6〜18.4 秒 — evidence/F-expander-speed-commit-f7869baa6.txt — から、2 → 4 を 1.5 倍前後と見た)。上限 3,300 秒に接する。**実測は無い**。
- **日次が変わる時点**: dotfiles と doeff の着地では日次は変わらない。agora-controllers の doeff の pin を進めた時に初めて変わる。だから冷えた回の測りを pin を進める前に置く(下の次の仕事)。余りは 4,800 − (1,048 + 3,300) = 約 450 秒(`.agents/land-queue.toml` 359 行の算術)。
- **測っていない範囲**: 日次の機体での展開の秒(上)・本物の枠で複数の doeff-hy-check を同時に撃った時の load・doeff-hy-check の本物の起動(試作の検は pyright を偽にした入口と、main を偽の枠で撃つ検)・型検査(日次)・doeff 全体の pytest(4 時間ごとの回)・dotfiles origin/master の最新(17eb56835)への rebase の衝突の有無。
- **範囲外と明記する物**: (1) 外側の入口が内側の doeff-hy-check を N 本並べる時、幅/N を配るのは外側の仕事(I1 は共同の約束で、検は強いない)。(2) `hold_phase` だけを引く入口(agora-controllers の `scripts/check_slot.hy`)は方策を手に持たないので `process_width` を呼べない。今回は要らない。呼ぶ入口が来た時の論点として残す。(3) node と doeff のように入口ごとに違う幅は、整数 1 つの口では表せない(宣言の拡張が要る)。(4) 型の宣言の道具 `doeff-hy-stub` など、枠を取らずに pool を使う入口は、使う前に `take_slot` と `expander_limit` を通す必要がある(守りが既定の数の直読みを断つので、通さずには書けない)。

## 完了の範囲と次の仕事

完了の範囲: **設計 + 最小実験まで**。本実装は着地していない(試作は branch に置いた)。環境変数 `DOEFF_HY_CHECK_EXPANDERS` を外すのは機能の削除に当たるので、実装の前に依頼側の確認を取る(使い手 0 件は実測)。

次の仕事(この順。card は依頼側が切る):
1. dotfiles: 正本に `process_width` を足し `width_for_child` を替える(試作 4b9b243b9・master の最新へ rebase)。
2. doeff: M-slot・static_check・env_places・守りの検(試作 8b81009f9)を着地する。dotfiles と doeff の着地の順は問わない(口が任意)。
3. 日次の機体で、宣言 2 の冷えた回を測る(pin を進める前の門)。上限に収まらなければ、`--timeout-s` を上げる余地(約 450 秒)を使うか、日次の機体の取り方を別の card で決める — 宣言の数を上げるのは会話の機体にも効くので最後の手。
4. agora-controllers の註(`system.hy` 84 行・`hy_type_check.hy` 59 行・`land-queue.toml` 345 行)を、数を書き写さない形(「processesPerSlot × 6」)に直す。
5. 他の入口(`doeff-hy-stub --check`)が展開の子の pool を使いたくなった時は、`take_slot` + `expander_limit` を通す(守りが強いる)。

## 修正の履歴

- 試作 1(doeff 0dfe61d56・dotfiles 4b9b243b9): 盲検の前。
- 盲検 A・B・構成レビューの後(doeff 8b81009f9): 実物の正本の検が env の名と規則の答えを正本に問う形へ(A)・`limit` の既定を外し既定の数を読む場所を 1 つにする守り(B)。事前の文(design-pre-blind.md)は書き換えていない。
- 守りの検の検で見つかった穴(盲検の前): 初版の名の走査は `from x import width_for_child` の import の別名を見ず、設計者の失敗例の表で 1 件赤になった(evidence/G-guard-own-mutant-found-a-hole.log.txt: 1 failed・23 passed)→ import の別名も走査する形に直し 24 本が緑(試作 1 の中)。
