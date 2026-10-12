読みと確かめが揃いました。以下が構成レビューの結果です。

# 結論(先に一覧)

| 決め | 判定 | 一言 |
|---|---|---|
| D1 従わせる | **条件つきで採る** | 筋は通る(値の持ち主を 1 つにする前提と整合)。条件 = 日次の冷えた回の測りを「実装の card の受入(事後)」でなく **agora-controllers の pin を進める前の門** にし、越えた時の手(`--timeout-s` を上げる余地 ≈ 450 秒)を先に決める |
| D2 env を外し定数は既定として残す | **採る** | 使い手 0 件は実測で裏が取れた。既定 4 が「古い正本の機体では宣言 2 を越える」のは移行期の性質で、名乗りがあるので許容 |
| D3 pyright の thread は min(env, 宣言) | **条件つきで採る** | 機械的には正しいが R1 の文言(「doeff に環境変数を残さない」)と食い違う。R1 を「thread は宣言が上限・値は入口が選ぶ」と書き直すか、thread = 宣言にして env を消すかのどちらかに揃える |
| D4 正本に `process_width` を公開し `width_for_child` を置き換える | **採る** | 規則の座が 1 つになり、純粋関数で検も単純。使い手が dotfiles 内だけなのも実測で裏が取れた |
| D5 `SlotHeld(let_go, width)`・口を必須/任意に分ける | **採る** | 持ち物を預からない形は守りを単純にする。幅の口の失敗で枠を失わない配線も読みで確認 |
| D6 守りを文法 + 名の組 + 名の走査に替える | **条件つきで採る** | 実測で 8/8 の開く変異を捕まえた(下記)。ただし守りの範囲は src だけで、doeff の **検の層** に宣言の鍵と env の名が残っている — I2・S2・S3 の記述をそれに合わせて直す |

# 1. 決めの理由と根拠

## D1(従わせる)

**採る理由(読み)**: 却下案の検討は妥当。「env だけを読む入口」は会話が直に撃つ doeff-hy-check(包みの外)に幅が届かないので不可 — 実コードでも `static_check.main` は自分で `take_slot` し(`/home/ubuntu/.worktrees/doeff-wt-ppslot-blind/packages/doeff-hy/src/doeff_hy/static_check.py:1304`)、包みの中では `take` が None を返して正本の `process_width` が env を読む(`/home/ubuntu/.worktrees/dotfiles-wt-ppslot-blind/agent/tests/machine_check_slots.py:460-464`)。直の入口と包みの中で同じ口 1 つで済む構成になっている。

**日次への影響(推測の節)の妥当性**:
- 実測で確かめた前提: 日次 hy-type-trend は `DOEFF_HY_CHECK_PYRIGHT_THREADS=2 … --timeout-s 3300`(agora-controllers origin/main `.agents/land-queue.toml:363`)。`hy_type_trend.hy` の `measure` は doeff-hy-check を子 process で直に撃ち、枠を自分では取らない(`scripts/hy_type_trend.hy:465-474` に slot の呼びなし)— つまり日次の doeff-hy-check は **自分で枠を取る入口**で、正本が在れば幅は宣言の 2 になる。
- 未確認: 「2,523.6 秒」「HOME に正本が在る」「展開 1→8 で 3.1 倍」。land-queue.toml:355-357 には別の回で **3,164 秒**(pyright 1 本の時)の記録があり、上限まで 136 秒しか無かった回が既に在る。thread 2 で pyright が縮んだ後の値がどこまで下がったかは私には読めない。
- 推測: 日次は atlas(Pod の CPU 上限 8・land-queue.toml:345)で pytest 3 session と並ぶので、展開の子 4→2 の遅れは空いた機体での線形(1.5〜2 倍)より小さい可能性がある。逆に、設計の見積り(+350〜750 秒)を素直に足すと 2,900〜3,300 秒で上限に接する。
- 判定: 筋は通るが、**測る順**を直すべき。dotfiles → doeff の着地では日次は変わらず、agora-controllers の **pin を進めた時**に初めて日次が変わる。だから「pin を進める card の受入 = 冷えた回の測り」ではなく、pin の前に atlas 相当で 1 回測る(か、同じ card で `--timeout-s` を 3,300 → 3,700 前後へ上げる余地を先に確かめる — land-queue.toml:359 の算術 1,048 + 3,300 = 4,348 ≤ 4,800 から約 450 秒の余りがある)。

## D2
- 実測: `DOEFF_HY_CHECK_EXPANDERS` を **置く**側は 11 repo の origin の先端で 0 件。文だけが agora-controllers `scripts/hy_type_check.hy:59`。doeff 自身の `docs/design/**/evidence/**/run2.sh.txt` に設定する行があるが、記録の写しで生きた使い手ではない。(設計は「9 repo」と書くが `~/repos` には 11 dir — どの 9 かは未確認。結論に影響なし。)
- 読み: 既定 4 は「正本の無い宿」では妥当。「正本は在るが口が無い機体」(S5)では宣言 2 の枠で 4 本走る — 設計が避けたい 2 つ目の持ち主と同じ振る舞いだが、名乗り(I4)があり移行期だけなので許容。より守りを固くするなら「正本が在って口が無い時だけ既定を 1 か 2 に」も考えられるが、複雑さに対して得が小さい。

## D3
- 読み: `pyright_threads(configured, declared)` は `min(wanted, declared)`(static_check.py:1062-1072)。既定 1 なので宣言が 2 でも env 無しでは 1 本 — **値を選んでいるのは doeff の既定と env** で、宣言は上限。これは R1「doeff に 2 つ目の持ち主(定数・環境変数)を残さない」の文言と合わない。枠の前提(越えない)には反しないので実害は無いが、設計書で R1 を「展開の子 = 宣言の値・pyright の thread = 宣言を上限に入口が選ぶ」と明記するか、thread も宣言に揃えて env を消すか、どちらかに揃えるべき。前者を推す(小さい検で `--threads 2` は起動の分だけ損・land-queue.toml:357-360 の memory の考慮も env 側の判断)。

## D4
- 実測: 置き換えの差分は `machine_check_slots.py:450-464` と `run_held` の 1 行(:486)。`width_for_child` の使い手は dotfiles の正本と検だけ(11 repo の origin の先端を grep・agora-controllers・code-quality・agora に無し)。
- 読み: 公開の口にした以上、`(policy, holding, environ) -> int` の形は正本の互換の約束になる。設計の M-canon の行にその旨がある。採る。

## D5
- 読み: `take_slot` は `let_go = holding.release` だけを預け(check_slot.py:167)、幅の口は take の囲いの外で別に捕まえる(:173-179)。`_positive` は形の確かめだけ(:183-190)。採る。

## D6
- **実測**(/tmp に写した script で、blind の木を読むだけで実行。pytest は使わず検の関数を直に呼んだ): 3 本の検は通る。開く変異 10 種のうち、方策の欄の読み・持ち物の `.policy`・持ち物の横流し・`module.PROCESSES_ENV`・env の名の文字列・`getattr`・別名への写し・`release.__self__` の 8 種を捕まえた。`vars(module)["processes_"+"per_slot"]` は捕まえない(検の docstring が認める限界)。`module.process_width(module.read_policy(), …)` は通す(口の引数に直に入れる形で、開いてはいない — 妥当)。
- 条件: 走査の母集団は `src/doeff_hy` だけ(test_check_slot_opacity.py:46, 223-235)。doeff の **検**には `"processesPerSlot"`(`tests/test_static_check_slot.py:126`)と `AI_CHECK_SLOT_PROCESSES=`(同 :383)が文字どおり在る。設計の I2「宣言の鍵・env の名は M-canon にだけ在る」は「doeff の src には無い」に直し、検の層はどうするかを書く(下の S2・S3)。
- 単純化の案: 文法の検は `canon_module(...)` への代入と `ModuleType` 注釈という書き方に依存している(:53-66)。同じ守りの大半は、M-slot-scene の口だけの正本を **最小**にする(`POLICY = object()` は既にそう。持ち物も `release` 以外の属性を持たない class にする)ことで実行時に得られる。いま在る AST の検は効くことを実測したので外す必要は無いが、残すなら「名の組の固定」と「名の走査」を必須、文法の検を補助と位置づけると、将来 check_slot.py を書き換えた時の維持の負担が読める。

# 2. 責務の割り方

- 重なり: 無し。`expander_limit(declared)` は 1 行(static_check.py:804-806)で module の境界を示すだけの部品になった — 残しても害は無い。
- 漏れ(足りない部品・記述):
  1. **外側の入口が内側を複数並べる時の約束**。正本の入れ子の規則は「受け継いだ値を残す・無ければ宣言の値」(machine_check_slots.py:460-464)なので、祖先が枠を持ちつつ内側の doeff-hy-check を N 本同時に起こすと木全体は 2×N になる。配るのは外側の仕事(I1 の「共同の約束」)だが、どの入口にもそれを強いる検は無い。hy_type_check.hy の gate が「file ごとの doeff-hy-check」を直列に撃つか並べるかは未確認。設計に「外側が並べるなら width/N を配る」の 1 行を足すべき。
  2. **hold_phase の使い手**(agora-controllers `scripts/check_slot.hy` は `hold_phase` だけを引く・:48)は `policy` を手に持たないので、`process_width(policy, holding, environ)` の形をそのままでは呼べない。今回は不要だが、次の Python/Hy の使い手(code-quality の `quality/machine_slot.py` は read_policy/take を持つので呼べる)が来る時に口の揃いの問題になる。設計に「hold_phase 系の入口は対象外(幅は子へ宣言の値が届く)」と明記するか、正本側で持ち物から幅を答える口を将来の論点として書く。
- 不要な部品: 無し。
- I1 の数え方の註: 展開は親 + 子 N・pyright は本体 + worker N なので、process の **数**は宣言 + 1 になる。node-test も runner + N で同じ数え方なので不整合ではないが、「並走 = worker の数」と書いた方が正確。

# 3. 変更シナリオの言い過ぎ・取りこぼし

- **S1**: 「展開の子と thread が 3 に従う」は言い過ぎ。従うのは展開の子だけ。thread は `min(env or 1, 3)` で、日次の 2 は 2 のまま。また `machine_check_slots.json` は dotfiles に 1 つ(per-host の版は無い・実測)なので「別の機体の種類で」ではなく「追随する全機体で一斉に」が実態。zeus(36 core)と atlas(CPU 8)が同じ数を使う点は D1 の日次の論点と結びつく。
- **S2**: 「正本の中だけで終わる」は取りこぼし。(a) 宣言の **JSON の鍵**を改名すると doeff の `tests/test_static_check_slot.py:121-133` と agora の `scripts/check-slot.test.mjs:48` が書く写しの宣言を正本が断る(`policy_of`)→ 枠を取らずに走り、枠を前提にする縁の検が赤になる。agora の検は「鍵は正本の公開の契約」と明記している(:9)。S2 は「Policy の欄(内部)の改名 → 正本だけ」「JSON の鍵(公開の契約)→ 使い手の縁の検も」に分けるべき。(b) `CANON_OWNED` は名の写し(opacity:32-44)なので、改名後も古い名を見続け新しい名は見ない — 設計が認めている限界だが、S2 に「doeff の検の組を足す」を書く。
- **S3**: 同じく取りこぼし。`tests/test_static_check_slot.py:383` が `AI_CHECK_SLOT_PROCESSES=` を文字どおり書く。env の名を替えると hand-down の検が 1 を配れず赤。正本の `PROCESSES_ENV`(M-canon の公開の定数として設計に載っている)を検の中で正本 module から読めば S3 の主張が成り立つ。
- **S4**: 主張どおり(読み: dotfiles 側 `test_a_nested_wrapper_keeps_the_smaller_width…`・doeff 側 `test_a_check_inside_the_wrapper_keeps_the_smaller_width…`)。上の「漏れ 1」(複数の内側)は S4 の範囲外として明記するとよい。
- **S5**: 主張どおり。両方向の着地順が安全なことも読みで確認(古い正本 + 新 doeff → `absent_optional` が名乗って 4・新正本 + 古い doeff → `process_width` を呼ばず 4)。
- **S6**: 除外は妥当。

# 4. 事実の節(2)の確かめ

| 記述 | 結果 |
|---|---|
| dotfiles cd7a49008: processesPerSlot = 2・`width_for_child(policy, nested, inherited) -> str`・Python の入口の口なし | **実測一致**(json:3・diff の削除行) |
| M-slot 基準: `take(read_policy(), …)`→`SlotHeld(holding)`・口は全部必須 | **実測一致**(a4abf5218 の check_slot.py) |
| 守りの名 `test_the_canon_policy_goes_straight_into_take` | **実測一致**(基準の test_static_check_slot.py:288) |
| `EXPANDER_LIMIT = 4`・`DOEFF_HY_CHECK_EXPANDERS`・pyright env 既定 1・日次 2 | **実測一致**(doeff origin/main static_check.py:803・env_places.py:14・land-queue.toml:363) |
| 使い手 0 件・hy_type_check.hy:59 は文だけ | **実測一致**(「9 repo」の内訳は未確認) |
| system.hy:84・land-queue.toml に 4 を前提の数え | **実測一致**(:84・:345) |
| 日次 2,523.6 / 3,300 秒 | 3,300 は一致・**2,523.6 は未確認** |
| 日次の HOME に正本が在る | **未確認**(doeff-lint-whole の run の行 land-queue.toml:273 が `$HOME/dotfiles/…/machine_check_slots.py run` を通るので、通っていれば在る) |
| 展開 上限 1 = 55.8 秒 / 8 = 17.6〜18.4 秒 | **未確認**(blind の木の docs に見つからず) |
| 機体の今(枠 3 本・load 113) | **未確認**(時刻が過ぎている) |
| 機体の正本に幅の口が無い | **実測一致**(`~/dotfiles` → `/work/toolset/dotfiles`、`width_for_child` :449・`process_width` 無し)。dotfiles origin/master は 17eb56835 へ進んでいるが同じく口が無い(基準 cd7a49008 からの rebase が要る・衝突の有無は未確認) |
| 「実際の数は min(この数, CPU, file 数)」 | **実測一致**(static_check.py:876・:890) |

# 5. 設計に載っていない論点

1. **着地の順と、日次が変わる点**: 日次が変わるのは agora-controllers の doeff の pin を進めた時。測りをその前に置く(D1 の条件)。
2. **agora-controllers の註の直し方**: `system.hy:84` の「4 × 6 = 24」を「2 × 6」と書き直すと、宣言の値の写しを別 repo に作る(設計が避けたい形)。「processesPerSlot × 6」と数を書かない形にするか、long-command の上限 6 自体を見直すかを card に書く。
3. **1 つの宣言を全機体が共有する**こと(zeus 36 core と atlas CPU 8)。日次を速くしたい時に上げられる値が会話側にも効く。per-host の宣言は無い(実測)。
4. **検の層の名の写し**(S2・S3)— 守りの母集団を src に限った決めを明記し、検は正本の公開の定数を読む形へ。
5. **doeff の pin を持つ他の使い手**への影響は、口が任意(D5)なので着地順に依らず安全 — 設計に明記されていないが読みで確認。

# 推測と実測の区別
- 実測: 上の表の「実測一致」・opacity の変異 10 種の結果・各 repo の grep。
- 読み(code を読んだ判断): D1 の入口の形・S4・S5 の配線・責務の漏れ。
- 推測: atlas での展開の遅れが線形より小さい可能性・日次の秒の見積り。
- 未実行: 日次の実測・dotfiles の検の実行(機体が混んでいて枠を取る pytest は走らせなかった)。

WAIT: work — 構成レビューを頼んだ会話(設計検証 lt-5KHSMYM9R1HW7AKJBRG32NNF4S)の次の依頼
