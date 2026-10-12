# 設計(盲検の前に固定) — doeff-hy-check の 1 枠の中の並走を機体の検の枠の宣言に従わせるか、従わせるなら正本の口と M-slot の守りをどうするか

依頼 lt-5KHSMYM9R1HW7AKJBRG32NNF4S(class=investigate)・card ki-1bfe2c42dce6。完了の範囲は「決めと設計 + 最小実験」まで(本実装は着地しない — 試作は作業 branch に置く)。

## 1. 要件

- R1: doeff-hy-check(`doeff_hy.static_check` の main)が 1 枠の中で並べる process の数(依存の展開の子・pyright の thread)の持ち主は、機体の検の枠の宣言(dotfiles の `machine_check_slots.json`)の 1 つだけにする。doeff に 2 つ目の持ち主(定数 EXPANDER_LIMIT・環境変数)を残さない。
- R2: doeff(M-slot)は、正本の方策の中身(欄の名・値)・入れ子の時の幅の規則・env の名を知らない。正本の公開の口を呼び、方策と持ち物は開かずに渡す。
- R3: 正本の公開の口は「幅を返す」。方策を名に束ねて不透明に渡す正しい形を、M-slot の守りが通す。take の答えや方策から欄を読み戻す形は、守りが断る。
- R4: 正本に口が無い機体(dotfiles を追随する前の機体)でも、枠を失わない。

## 2. 現状(基準 commit)

| 場所 | 基準 | 今の形 |
|---|---|---|
| 正本 dotfiles | master cd7a49008 | 宣言に processesPerSlot = 2。幅の規則は関数 `width_for_child(policy, nested, inherited) -> str`(命令の包み run_held が子の env AI_CHECK_SLOT_PROCESSES へ置く)。Python の入口が幅を受ける口は無い |
| M-slot(doeff `doeff_hy/check_slot.py`) | doeff main a4abf5218 | `take_slot` が `module.take(module.read_policy(), …)` を呼んで `SlotHeld(holding)` を返す。口は read_policy・take が必須で、1 つでも欠ければ枠を取らずに素通し |
| M-slot の守り | 同上 | `test_the_canon_policy_goes_straight_into_take`(read_policy の答えは take の第 1 引数にだけ現れる) |
| doeff-hy-check の展開の子の数 | 同上 | `EXPANDER_LIMIT = 4` と環境変数 `DOEFF_HY_CHECK_EXPANDERS`。pyright の thread は環境変数 `DOEFF_HY_CHECK_PYRIGHT_THREADS`(既定 1・日次が 2) |

事実(全部、調べた版の上での実測・読み):
- 使い手: `DOEFF_HY_CHECK_EXPANDERS` を置く側は 9 repo の origin の先端に 0 件(agora-controllers の `scripts/hy_type_check.hy` 59 行に文だけ)。`EXPANDER_LIMIT` の値 4 を前提に容量を数える文が agora-controllers の `controllers/agent_env/entry/system.hy` 84 行(long-command 6 本 × 展開の子 4 = 24 < 36 core)と `.agents/land-queue.toml` にある。
- 日次(agora-controllers の hy-type-trend): 所要 2,523.6 秒 / 上限 3,300 秒(10-12 07:02 UTC の回)。日次の job の HOME に dotfiles の正本が在る(同じ回の doeff-lint-whole が `$HOME/dotfiles/agent/tests/machine_check_slots.py run` を通る)。hy-type-trend の中の doeff-hy-check が枠を取っているかは log に出ない(正本は待った時だけ名乗る)ので未確認。
- 展開の速さ(doeff commit f7869baa6 の実測・秒): 展開だけ・同じ版・並べる数の上限だけを替える — 上限 1 = 55.8 / 上限 8 = 17.6〜18.4。上限 2 と 4 は実測が無い。
- 機体の今(10:50 UTC 前後・読み取り): 枠 8 本のうち 3 本が doeff-hy-check(展開の子 4 本が 1 本の枠の中で走る形を 1 つ確認)。load average 113 / 36 core。

## 3. 決め

- D1: **従わせる**。doeff-hy-check の 1 枠の中の並走(展開の子の数・pyright の thread の数)の上限は、枠の正本が答える数(宣言の processesPerSlot)にする。
- D2: 展開の子の数の環境変数 `DOEFF_HY_CHECK_EXPANDERS` は外す(使い手 0 件)。定数 `EXPANDER_LIMIT = 4` は「宣言が届かない機体の既定」として残す(値の持ち主ではない)。
- D3: pyright の thread は環境変数の値と宣言の数の小さい方(thread は pyright が起こす子の process)。環境変数は数を下げる口で、宣言を越えて上げる口にしない。日次の値 2 は宣言 2 の内で変わらない。
- D4: 正本は旧い `width_for_child`(env の文字列を返す)を、公開の口 `process_width(policy, holding, environ) -> int` に替える。規則は同じ(枠を取った入口は宣言の値・祖先の枠の内側は受け継いだ宣言以下の値・壊れた値は宣言の値)。run_held の env の値も同じ関数で決める(規則の座は 1 つ)。
- D5: M-slot は `SlotHeld(let_go, width)` を返す。正本の持ち物は預からず、解く口だけを預ける。口は必須(read_policy・take)と任意(process_width)に分け、任意の口が無い正本でも枠は取り、幅は None(呼び手が既定)。幅の口が落ちても取った枠は持ち続ける。
- D6: M-slot の守りを、「開かない物(read_policy の答え・take の答え)が現れてよい位置の文法」+「正本から読んでよい名の組」+「正本が持つ名が code に現れない事」の 3 つに替える。

却下した案: 従わせない(doeff-hy-check を正本の例外の利用者にする)— 機体の合計(K × 幅)が宣言の関数でなくなり、上に挙げた容量の数え(system.hy)が doeff の定数に結ばれたまま残る。env を読む入口だけにする(agora の node-test.mjs と同じ形)— 直の入口(会話が撃つ doeff-hy-check)は命令の包みの外なので env が無く、幅が届かない。take の答えから欄を読む — 正本の中身が doeff へ移る。

日次への影響(推測・未測定): 宣言 2 が日次の job にも効く場合、冷えた展開(約 750 秒・展開の子 4 本の実測)は子 2 本で 350〜750 秒伸びうる(1 → 8 の実測が 3.1 倍なので、2 → 4 は 1.5 倍前後と見る推測)。hy-type-trend の上限 3,300 秒の内に収まるかは、実装の card の受入で日次の機体の冷えた回を測って確かめる。

## 4. 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
|---|---|---|---|---|
| M-canon | 枠の取り方・宣言の鍵と値の検め・1 枠の中の並走の数の規則 | 宣言の鍵・方策の欄の名・入れ子の規則・env の名 AI_CHECK_SLOT_PROCESSES | doeff が何に使うか | `read_policy()`・`take(policy, label, environ, out)`・`process_width(policy, holding, environ) -> int`・`run_held`・例外 PolicyUnreadable・SlotWaitExpired(+ 他言語の入口のための PROCESSES_ENV) |
| M-slot | 宿の home の正本を探して読み、口の揃いを確かめ、正本の答えを SlotHeld / SlotExpired に直す | 正本の置き場・口の名(必須 / 任意)・版のずれの扱い | 方策の中身・持ち物の中身・入れ子の規則・env の名 | `take_slot(home, label, out) -> SlotHeld | SlotExpired`・`SlotHeld(let_go, width).release()` |
| M-main(static_check の main) | 検査の中で、並走の数(展開の子・pyright の thread)をどう使うか | 展開の子の既定 4・thread の既定 1 と環境変数 | 数をどう得たか | `expander_limit(width)`・`pyright_threads(configured, width)` |
| M-env-places | 環境変数を名指しで 1 つずつ読む | 読む名 | 値の意味 | `pyright_threads_setting()` ほか(`expanders_setting` は消える) |
| M-opacity-guard | M-slot が正本の物を開かない事の機械の検 | 許す使い道の文法・宣言した口の組 | — | tests/test_check_slot_opacity.py |
| M-slot-scene | 実物の正本の写しと口だけの正本で、M-slot の契約を確かめる | — | — | tests/test_static_check_slot.py |

不変条件: (I1) 1 枠の process 木の並走 ≤ 宣言の数(正本と各入口の共同の約束)。(I2) 宣言の鍵・方策の欄・env の名・入れ子の規則は M-canon にだけ在る。(I3) 枠を取れた時は、幅が届かなくても枠を持ち続ける。(I4) 正本の口が無い・落ちた時は 1 行名乗る(黙って 2 つ目の持ち主にならない)。

## 5. 変更シナリオ(6 軸)と事前の主張

| id | 軸 | 変更 | 変わる所 | 変わらない所 | 主張(失敗例探しの前) |
|---|---|---|---|---|---|
| S1 | hardware | 別の機体の種類で processesPerSlot を 2 → 3(または別の数) | `machine_check_slots.json` の 1 行 | M-slot・M-main・doeff の検 | doeff-hy-check の展開の子と thread が、コードを 1 行も変えずに 3 に従う |
| S2 | storage | 正本の内部の形を替える(Policy の欄の改名・欄の追加・入れ子の規則を「受け継いだ値 − 1」へ) | M-canon の `process_width` とその検 | M-slot・M-main | doeff は欄の名も入れ子の規則も持たないので、正本の中だけで終わる |
| S3 | effects | 正本が幅を運ぶ env の名を替える・env をやめる | M-canon(と、env を読む他言語の入口) | M-slot・M-main(doeff は env の名を持たない) | Python の入口は口の答えを使うだけなので、doeff は変わらない |
| S4 | concurrency | doeff-hy-check を、外側の入口が小さい幅(1)を配った命令の包みの中で撃つ | なし | M-slot・M-main・M-canon | 内側は外側の配った 1 に従い、宣言の 2 に戻して木全体の並走を倍にしない。直の入口と包みの中の入口で同じ口を使う |
| S5 | distribution | dotfiles を追随する前の機体(正本に process_width が無い)で、新しい doeff が走る / 正本が後で口を改名する | なし(どちらも M-slot の既存の扱い) | M-canon・M-main | 枠は取れる。幅だけが届かず(None)、1 行名乗って EXPANDER_LIMIT = 4 で走る |
| S6 | simulation | — | — | — | 除外: doeff-hy-check の枠の取り方に決定的シミュレーション(時計・乱数の差し替え)を使う面は無い。正本の `clock`・`sleep` の注入は今回触らない |

## 6. 強制方法

| 守る責務 | 強制 | 実装箇所(試作) | 実行経路 | 限界 |
|---|---|---|---|---|
| 開かない物を開かない(入る向き・出る向き) | AST の文法の検 | tests/test_check_slot_opacity.py の `opened_uses`・`test_check_slot_opens_neither_the_canon_policy_nor_what_take_returns` | doeff の pytest(変更時の focused・日次) | check_slot.py の中だけ。`slot.let_go` を static_check 側で開く道は見ない |
| 正本から読んでよい名は宣言した口と例外だけ | 同上(`_name_findings`)+ 口の組の固定(`test_the_mouths_this_module_declares_are_the_ones_this_guard_allows`) | 同上 | 同上 | 組を書き換えれば通る(組は検の固定で、変えるのは契約の変更) |
| 正本が持つ名が doeff に現れない | AST の名・文字列の走査(docstring・コメントを除く) | `canon_owned_names`・`test_no_doeff_hy_source_names_what_the_canon_owns` | 同上 | 名の組は検の写し。正本が名を足した時は足す。別の綴りは見ない |
| 版のずれでも枠を失わない | 口だけの正本の検(任意の口が無い・落ちる・形の違う答え) | tests/test_static_check_slot.py | 同上 | |
| 幅が宣言に従う | 実物の正本の写しを使う検(直の入口・包みの中・外側が小さい幅を配った時) | 同上 | 同上(正本の無い宿は skip) | 正本が口を持たない機体は skip |
| 正本の規則 | 正本の検 | dotfiles agent/tests/test_machine_check_slots.py | dotfiles の pytest | |
| 幅を使う側の配線 | main を偽の take_slot で撃つ検 | tests/test_static_check_parallel.py・test_static_check_pyright_threads.py | doeff の pytest | 展開の子の数の実測(速さ)は検でなく、実装の card の受入 |

## 7. 完了の範囲と予定

- 実施済み(試作・最小実験): dotfiles `4b9b243b9`(wt/investigate-ppslot-canon)・doeff `0dfe61d56`(wt/investigate-check-slot-ppslot)。どちらも main へは入れていない。
- 予定(実装の card): 正本の変更を先に着地(足すだけではなく `width_for_child` の置き換え — 使い手は dotfiles の中だけ)、次に doeff の pin を進めて M-slot・M-main・守りの検・環境変数の廃止を着地、日次の機体で冷えた回を測る、agora-controllers の註(`system.hy` 84 行・`hy_type_check.hy` 59 行・`land-queue.toml`)を宣言の数へ直す。
