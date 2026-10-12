## 失敗例: doeff-hy-check の 1 枠の中の並走数を、宣言の processesPerSlot に従わせる

失敗例は 1 つ見つかりました。この例は前提を変えていません。ただし変更シナリオ S1〜S6 には無い機能追加で、S2 の「形が変わる」主張そのものの反例ではありません。試しているのは、責務の表で M-slot が隠すとした「正本の方策の中身」と、それを守る AST の検(強制方法の表)です。

### 1. 変更要求と、それが要る理由

要求は「doeff-hy-check が 1 枠の中で並べる展開の子の数を、機体の宣言の `processesPerSlot` に揃える。機体ごとの手の値 `DOEFF_HY_CHECK_EXPANDERS` を使わず、値の持ち主を宣言 1 つにする」です。

理由:
- 正本(fed74e9fe・card ki-f8107ed7ccc8)は `processesPerSlot = 2` を導入しました。正本の頭の註には「中で file を並べる runner の入口がそれを並走の上限にする」「2 なら 1 枠が 1 本 0.5〜2.5 core の約束に収まる」とあります(machine_check_slots.py 26〜31 行)。
- doeff-hy-check は 1 本で展開の子 4 本と pyright を起こし、RSS は約 10 GiB です(check_slot.py の頭の註)。上限は `EXPANDER_LIMIT = 4` で、10-11 に load 163 になって 8 から 4 へ下げました(static_check.py 697 行)。注釈には「機体ごとに替える時は環境変数 EXPANDERS_ENV」とあります。
- つまり機体の並走の値に、doeff 側の 2 つ目の持ち主があります。これは正本の決め「値は 1 つの宣言が持つ・env で値を替える口は作らない」とぶつかります。片付ける card が切られるのは自然です。

### 2. 関係する主張・前提と、予想に反して変わる所

守っている前提:
- 値は正本の宣言だけが持つ。
- M-slot は方策を中を読まずに take へ渡す。
- doeff-hy-check は main の中で `take_slot` を呼んで枠を取る(`run` の包みを経由しない)。
- 正本は dotfiles の先端を追い、doeff は使い手の pin で固定される。
- 正本の口が揃わない時は素通しする。

予想と実際:

| | 設計の予想 | 実際に要る変更 |
|---|---|---|
| M-slot(check_slot.py) | S2 では変わらない | 結果の型 `SlotHeld` に幅を足す。`SlotHeld(None)` が混ぜている「祖先が枠を持つ」と「正本が無い」を分ける。口の揃いの判定(`unfit_names`)を、必須の口と任意の口に分ける。 |
| 強制方法(test_the_canon_policy_goes_straight_into_take) | M-slot を守る | 方策を正本の関数へ不透明に渡すだけの正しい形まで断る(E1)ので、検を書き直す必要がある。 |
| 正本(M-canon) | doeff からは中を読まれない | 中で `take` を呼ぶ入口へ幅を渡す口が無い。今は `run_held` が子の env `AI_CHECK_SLOT_PROCESSES` に置くだけ。 |
| static_check の main | 範囲外 | 幅を `project_closure` の limit へ渡す。`EXPANDERS_ENV` とどちらを優先するか、pyright を 1 本と数えるかを決める。 |

### 3. 漏れる知識と、契約拡張との区別

まず、欠陥ではない部分です。
- **契約拡張(意図したもの)**: 正本が「中で take を呼ぶ入口へ幅を返す」公開の口を持つこと。`SlotHeld` が幅を持つこと。
- **配線**: main が幅を `project_closure(..., limit)` へ渡すこと。
- **共同の不変条件**: 「1 枠の process 木の並走は宣言以下」。正本と各入口が共に守る約束で、今は正本の註に node の例として書いてあるだけです。契約として明文化が要ります。

漏洩にあたる部分は 3 つです。
1. **M-slot の口の判定が全か無か**: 口の揃い(どの関数が在るか)と、枠を取るか取らないかが 1 つの判断に束ねられています。幅の口を `CANON_CALLS` に足すと、その口が無い正本の機体では枠そのものを失います(E2b)。避けるには、どの口がどの版の正本から在るかという正本の版の知識を M-slot が持つ必要があります。正本が後でこの口の名を替えた時も、pin された doeff はすべて枠を取らずに走ります。
2. **入れ子の時の幅の決め方**: 祖先の枠の内側では「受け継いだ値が宣言以下ならそれ」という規則があります(正本の `width_for_child` が持つ)。M-slot は `take` の答えの None を `SlotHeld(None)` に潰すので、この場面を区別できません。直す道は 2 つあります。
   - doeff が env 名 `AI_CHECK_SLOT_PROCESSES` と宣言の値を読んで規則を写す。これは方策の中身の漏洩です。
   - 正本の公開関数 `width_for_child(policy, nested, inherited)` に方策を渡す。ところが方策を名に束ねると AST の検が断ります(E1)。検の実際の条件は「read_policy の答えは take の 1 つ目の引数にしか現れない」で、守りたい知識(方策の中身を読まない)より狭いのです。結果として、doeff 内部の検が正本の公開 API の形を縛ります(方策を受け取る正本の関数は使えない)。
3. **2 つ目の持ち主の優先の判断に置き場が無い**: `EXPANDER_LIMIT` と `EXPANDERS_ENV` と宣言の幅の関係(min にするか、上書きか、pyright を数えるか)は方策についての判断です。M-slot に置くと「方策の判断をここに置かない」に反します。main に置くと、正本の数え方(何を 1 process と数えるか)を main が知ることになります。

### 4. 最小の変更例・再現手順・観測すべき結果

**R0: 今の隙間(変更なし)**
- `grep -rn "AI_CHECK_SLOT_PROCESSES\|PROCESSES_ENV\|processes_per_slot" packages/*/src` を実行します。期待は 0 件です(.py・.rs・.hy で実測 0 件。当たるのは test の宣言だけ)。
- 正本の包みの子には幅が置かれます(E3 で実測: `AI_CHECK_SLOT_PROCESSES = 1`)。それでも doeff-hy-check は `min(4, CPU 数, その段の引けない file の数)` 本の子を使います(コードを読んだ結果)。
- 任意の実測案: `_scene` と同じ世界で、`probe.hy` が `.hy` を 4 本 import するようにします。各 file の展開の中で macro が自分の pid を file に追記して 1 秒眠るようにし、`--no-cache` で走らせます。期待は、宣言が 1 でも別々の pid が最大 4 つ出ることです(推測)。

**R1: 正本の公開関数へ方策を不透明に渡す道**(check_slot.py 144 行)

```python
-        return SlotHeld(module.take(module.read_policy(), label, EnvironmentByName(), out))
+        policy = module.read_policy()
+        environ = EnvironmentByName()
+        holding = module.take(policy, label, environ, out)
+        return SlotHeld(holding, int(module.width_for_child(policy, holding is None, environ.get(module.PROCESSES_ENV))))
```

`SlotHeld` には `width: int | None = None` を足します。続けて次を実行します。

`uv run pytest packages/doeff-hy/tests/test_static_check_slot.py::test_the_canon_policy_goes_straight_into_take`

期待は赤です。この検と同じ AST の論理を、記憶の中の書き換えた source に当てた結果は `False` でした(実測)。

もう 1 本、`test_the_declared_policy_and_any_environment_name_reach_the_canon_take` も赤になると見ています(推測)。口だけの正本は `width_for_child` を持たないので、AttributeError が広い `except Exception` に捕まって「take が落ちた」の素通しになります。

付随する危険もあります(推測)。take の後の処理で例外が出ると、本物の正本では取った Holding の fd が閉じられません。process が終わるまで枠を持ったまま、名乗りは「枠を取らずに走る」と出ます。広い except が take の後の処理まで覆っているためです。

**R2: 口を必須の口に足す道**
`CANON_CALLS = ("read_policy", "take", "slot_width")` にして(正本にはまだ無い口)、`test_a_full_slot_holds_a_directly_started_doeff_hy_check_until_it_frees` を実行します。期待は赤です。枠が埋まっている間に pyright が撃たれ、stderr に「口が揃わない(slot_width)ので枠を取らずに走る」が出ます。

実測(記憶の中で上書きし、正本の写しは /tmp に置き、K = 1 の唯一の枠を別の process が持つ状態):
- 今の口: `SlotExpired`(2.0 秒待った)
- 口を足した形: `SlotHeld(holding=None)` が 0.0 秒で返り、上の名乗りが出た

### 5. 未確認の前提・不足する情報

**実測したこと**:
- E1: AST の検と同じ論理を、記憶の中で書き換えた source の 2 通りに当てた。pytest の実行そのものではない。
- E2a・E2b・E3: 正本 fed74e9fe(dotfiles HEAD e1f3ecbc7)を /tmp に写して動かした。Python は doeff の venv ではなく系の 3.14.7 で、doeff_hy は `__init__` を走らせずに読んだ。
- grep の 0 件。

作業木と dotfiles は変えていません。一時の dir は消しました。

**推測・未確認のこと**:
- 正本の持ち主が `processesPerSlot` を doeff-hy-check にも当てる意図か(註の例は node の `--test` だけ。card ki-f8107ed7ccc8 は読んでいない)。
- 正本が中で take を呼ぶ入口へ幅を渡す口を作る意向があるか。
- pyright を 1 process と数えるか。
- doeff-hy-check を `run` の包みの下で撃つ呼び手が在るか(agora-controllers の `check_slot.hy`、doeff-linter の `check_slot.rs`、code-quality は読んでいない)。
- 正本の版が doeff より古い機体が実際に在るか(E2b が実際に害になる頻度)。
- doeff-hy-check が実際に並べる process の数は測っておらず、コードからの推測です。

WAIT: work — 利用者の次の入力
