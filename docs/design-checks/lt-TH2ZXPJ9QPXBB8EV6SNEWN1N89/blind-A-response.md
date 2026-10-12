失敗例を 1 つ見つけ、実験で確かめました。「1 つの会話が同時に持てる枠を M 本までにする」を正本(M-canon)の中だけで足すと、正本の公開の名も `take` の引数も変わりません。それなのに doeff-hy-check だけが、名乗りの行を 1 つも出さずに上限の外で走ります。直すには doeff の M-slot(`check_slot.py`)が「正本の `take` が env のどの鍵を読むか」を知って写す必要があります。これは M-canon が隠すはずの判断が漏れている例です。

## 1. 変更要求とその理由

- **要求**: 1 つの会話(その subagent・long-command の task・xdist の子を含む)が同時に持てる機体の検の枠を M 本までにする。M は `machine_check_slots.json` の 1 つの値(例 `perConversation`)に置く。
- **理由**:
  - 同じ Pod で会話が数十並び、K = 8 です。正本の xdist の註のとおり xdist の子はそれぞれ 1 枠を取るので、1 会話の `pytest -n 8` で 8 枠全部が埋まります。すると他の会話の検は待ちの上限 1200 秒で未実行になりえます。
  - subagent と long-command の task は同じ `AGORA_CONVERSATION_ID` を持って並んで走ります。
  - 正本は既に `conversation_suffix(environ)` で会話の id を env から読んでいます。なので「`take` の中で同じ読みをすれば、口の名も引数も変えずに足せる」と判断するのが自然です。

## 2. 元の主張・前提と、予想に反して変わる物

**守っている前提**(契約の適用範囲の内です)
- 1 枠 = process 木 1 つで、子孫は 2 つ目を取りません。`enclosing_holder` を先に判じる順も変えません。
- 値は宣言の json の 1 つだけが持ち、env で値を替える口は作りません。会話の id は値ではなく保持者の名で、正本が既に読んでいる物です。
- 枠は Pod の中で数えます(S6 ではありません)。口の名と `take` の形は変えません(S3 ではありません)。

**関係する主張**
- S1・S2 の理由「doeff は値を読まず、`read_policy` の答えをそのまま `take` へ渡す」。ここから、正本の方策の変更は正本の側だけで済むと読めます。
- 責務の表では、M-canon が「取り方」を持ち「祖先の判定」を隠します。M-slot が隠すのは「正本の読み込み・口の名」だけです。
- S3 の守り(`CANON_NAMES` と TypeError の捕まえ)。

**予想と実際**
- 予想される波及は M-canon だけです。
- 実際には M-slot も変える必要があります。変えるまでの間、最も重い検(RSS 約 10 GiB)である doeff-hy-check が、両向きで会話ごとの数えの外に出ます。

## 3. どの知識がどこへ漏れているか

漏れの在処は `packages/doeff-hy/src/doeff_hy/check_slot.py` の 124〜126 行です。
```python
marker_name: str = module.HOLDER_ENV
marker = slot_holder_setting(marker_name)
environ = {} if marker is None else {marker_name: marker}
```

- **他の入口との違い**: 正本の `take(policy, label, environ, out)` の environ は、型が `Mapping[str, str]` なだけの開いた口です。正本自身の入口(`hold_for_pytest`・`hold_phase`・`run`)と agora-controllers の `check-slot` は `os.environ` を丸ごと渡します。doeff-linter は env を継いだまま `run` を撃ちます。doeff-hy の M-slot だけが、「今の正本は `HOLDER_ENV` しか読まない」という祖先の判定の中身を前提に、鍵 1 つだけの写しへ絞っています。
- **絞らざるをえない訳**: doeff_hy の中では DOEFF004(`os.environ` を参照しない)と、M-env の「変数を名指して読む」決めがあります。このため env を丸ごと渡せず、正本が読む鍵を doeff の側で列挙するしかありません。
- **持ち主の無い共同の不変条件**: 結果として「正本の `take` が読む鍵 ⊆ doeff が写す鍵」という条件が、dotfiles の先端(正本)と使い手の pin(doeff)にまたがって在ります。この条件には持ち主も検もありません。

**区別**
- **公開契約の拡張ではないと読みます。** 正本の公開の名(`CANON_NAMES` の 5 つ)も `take` の形も変わりません。`AGORA_CONVERSATION_ID` は正本が名として出していない文字列です。正本の他の入口・agora-controllers・doeff-linter は変更なしで新しい方策に従います。
  - ただし、environ の契約を「`HOLDER_ENV` だけ」と定めれば、今回は契約の拡張で、doeff の直しは配線の変更になります。その契約はどこにも書かれておらず、検もありません。どちらの読みを採るかは未決です。
- **配線の変更とも違います。** 直しの形は鍵 1 つの追加で配線に見えます。しかし繋ぐ相手の名が正本の公開の口に無く、「正本が会話ごとに数える」という内部の判断を doeff が知らなければ書けません。
- **黙って崩れます。** S3 の守りは口の名と呼び方の食い違いしか見ないので、名乗りの行が出ません。module 契約・DOEFF004 にも当たりません。
- **波及の範囲**:
  - M-entry は変えずに済みます。
  - M-env も今回は変えずに済みました。名乗りのための `conversation_setting`・`CONVERSATION_ENV` が偶然在ったからです。正本が別の鍵を読むようになれば、その名がまた doeff に写ります。
  - 直した doeff が各使い手の実行環境に届くまで、食い違いが続きます。

## 4. 最小の再現手順と観測結果

場所は自分専用の写し `/tmp/blind-A-ki26eb` だけです。pytest は撃っていません(root の conftest の受付が、機体の本物の枠を取るためです)。

1. blind-input の手順どおり、`cc8f9e7df` を `/tmp/blind-A-ki26eb` へ展開し `uv sync --group dev` しました。
2. 検体の home `_exp/conv/home/dotfiles/agent/tests/` に正本の写しを置き、`_exp/tools/make_conv_canon.py` で正本の中だけを変えました。
   - 変えた所は `Policy`・`policy_of`・`Holder`・`holder_at`・`try_take`・`take`。中身は、枠の file の 2 行目に会話の id を書くことと、`take` が `environ.get("AGORA_CONVERSATION_ID")` を読み、同じ会話の保持が上限に達していれば待つことです。
   - 宣言は `{"slots": 2, "dir": ".../_exp/conv/slots", "waitLimitSeconds": 5, "perConversation": 1}` です。
3. blocker として、正本の `run` を `AGORA_CONVERSATION_ID=c-SAME` で起こし、1 枠を持たせます。
4. 同じ会話 c-SAME で 3 つの入口を撃ちます(`_exp/tools/drive.py conv`)。逆向きは `_exp/tools/reverse.py` です。

| 撃ったもの | 結果(実測) | 新しい方策での正解 |
|---|---|---|
| 正本の `run` | rc 75・5.3 秒・待ちと上限越えの名乗りあり | 待って rc 75(合っている) |
| env を丸ごと渡す口(check-slot.hy と同じ渡し方) | 上限を越えて SlotWaitExpired・5.2 秒 | 合っている |
| **doeff-hy-check を直に撃つ** | **rc 0・0.9 秒・pyright を 1 回撃った・`check-slot:` の行なし** | 待って exit 2 |
| doeff が書いた枠の file | `'pid … doeff-hy-check repo (c-SAME)\nconversation '`(会話の id が空) | 会話の id が入る |
| 逆向き: doeff が枠を持つ間に、同じ会話の正本の `run` | rc 0・0.4 秒・待たない | 待って rc 75 |
| 対照: 変更前の正本の写し | 全部 rc 0(空き枠が在るので正しい) | — |
| M-slot の写しに会話の id を足した後 | doeff-hy-check は rc 2・5.7 秒・pyright 0 回。逆向きは rc 75 | 合っている |

M-slot の写しに入れた直しは、`CONVERSATION_ENV`・`conversation_setting` を import して environ に会話の id を足す 3 行です。測った後に元へ戻し、元の版と同じ事を diff で確かめました。

## 5. 未確認の前提・不足する情報

**推測(実測していない事)**
- 正本の持ち主が、会話ごとの上限を environ の読みで実装するかは推測です。私の正本の変更は、ありうる実装の 1 つにすぎません。
  - 新しい必須の引数で足すなら S3 の道になり、名乗りつきで素通しします。
  - 省略できる引数で足すなら、全部の口が黙って従いません。
  - label を解析して数えるなら、doeff の `slot_label` の形が共同の契約になり、それも漏れです。
  - どの形でも doeff の側に変更か偶然の一致が要る、というのも推測です。
- 既存の検 4 本は、検が書く宣言に perConversation が無いので緑のまま、と読みで判じました。pytest は撃っていません。
- 会話ごとの上限が実際に計画に在るかは知りません。利用状況からの推定です。

**読み切れなかった事**
- code-quality の `quality/machine_slot.py` は在処を見つけられず、読んでいません。
- doeff の版が使い手へ届く道は詳しく確かめていません。agora-controllers の pyproject は doeff を兄弟の path で読み、版は RuntimeEnv の宣言が選ぶと註にあります。uv.lock の固定ではありません。
- doeff-linter が env を継ぐ事は、Rust の `Command` の既定から読んだだけで、撃っていません。

**主題の外の副次の実測**(S2 の境に当たるので書いておきます)
- 宣言の dir を、作れない path(試しに `/proc/...`)にすると、正本の `try_take` の OSError を M-slot が捕まえません。
- 結果は rc 1・`--json` の stdout が空でした。1 は M-entry の語彙で「新しい赤」です。
- `hy_type_check.hy` の `tip-measured` は、rc が 0 か 1 で stdout が空だと「測れた・赤 0 件」と読む形になっています(読みのみ)。
- ただし gate の子は祖先が枠を持つので、この道には着かないと読みました。作れない path を使ったのは人工的な置き換えです。

**残した物**
- 実験の写しと script は `/tmp/blind-A-ki26eb/_exp/` に残しました。`/tmp/agora-check-slots`・共有の木・dotfiles は変えていません。
