# 設計検証 lt-TH2ZXPJ9QPXBB8EV6SNEWN1N89 — doeff-hy-check が機体の検の枠を取る(card ki-26ebd3f636e0)

## この文書の目的
読み手が次の 3 つを判断できるようにする。
- doeff-hy-check の入口が機体の検の枠を取る設計で、将来の変更がどこまで波及するか。
- 責務の境界を何が強制しているか。
- 盲検 A・B の失敗例を受けて何を直したか。

## 完了の範囲
**実装完了まで**。経過は次のとおり。

| 段 | 版 | 中身 |
|---|---|---|
| 盲検の前の実装 | doeff cc8f9e7dfe09(L2198 で main に着地) | 入口の main が枠を取る |
| 盲検の前の主張 | 7e2493ee6 | `design-pre-blind.md`(sha256 67b8a588…)を保存 |
| 盲検の後の直し | 77175f1ce | env を名指しの写しにする・take の中の失敗を素通しにする・方策の受け渡しを AST の検で守る |

この文書と証拠は、直しと一緒に main へ入れる。

## 全体の図(直した後)

```
 会話の直・gate の子・日次の hy_type_trend・pytest の中の main([...])
        │
        ▼
 doeff_hy.static_check.main                                    [M-entry]
   引数の検め → take_slot(Path.home(), slot_label(根, 会話の id), stderr)
        │                         │ SlotExpired ならここで exit 2(展開も pyright も起こさない)
        ▼                         ▼
 doeff_hy.check_slot.take_slot                                  [M-slot]
   正本を home の path で読む → 口の確かめ(unfit_names)
   → module.take(module.read_policy(), label, EnvironmentByName(), out)
        │   方策は名に束ねずそのまま渡す(AST の検)
        │   正本の take の中の失敗は 1 行名乗って素通し
        │                         │ 名指しで 1 つずつ読む
        ▼                         ▼
 ~/dotfiles/agent/tests/       doeff_hy.env_places.EnvironmentByName  [M-env]
 machine_check_slots.py          _read(name) → env_var_ask(foundation の答え手)
 + machine_check_slots.json  [M-canon]
   値(K・置き場・待ちの上限)・flock・入れ子の判定・待ちの名乗り
        │ 枠を持つ間
        ▼
 展開(子 4 本)と pyright → 終わったら SlotHeld.release()
```

## 責務(直した後)

| id | 持つ判断・知識 | 隠す物 | 口 |
|---|---|---|---|
| M-canon | 枠の値・取り方・入れ子の判定・待ちの上限。take が env のどの名を読むか | lock の file の形・ポーリング・祖先の判定・読む env の名 | `read_policy()`・`take(policy, label, environ, out)`・`PolicyUnreadable`・`SlotWaitExpired` |
| M-slot | 正本の在処・module の鍵・口が揃うかの判定・素通しの判断・名乗りの形 | 正本の読み込み・口の名 | `take_slot(home, label, out) -> SlotHeld \| SlotExpired`・`slot_label`・`SlotHeld.release()` |
| M-entry | 枠を持つ区間・待ちの上限の時の exit 2 | 枠の取り方 | CLI の終了 0 / 1 / 2 |
| M-env | 環境変数を読む唯一の所。名指しの読みの写し | os.environ の読み方 | `EnvironmentByName`(Mapping・名指しの読みだけ)・`conversation_setting()` |
| M-callers | 0・1 以外を「測れなかった」と読む・gate は自分で枠を持つ | — | doeff-hy-check を子 process で撃つ |

直す前との違いは次のとおり。
- M-slot は「正本の take は保持者の印 `HOLDER_ENV` だけを読む」を知らなくなった(盲検 A)。
- 正本の口の名から `HOLDER_ENV` を外した。

## 変更シナリオ — 盲検の前の主張と実測

盲検の前の主張は `design-pre-blind.md` のまま残し、ここでは実測と直しを足す。

| id | 軸 | 盲検の前の主張(予想の波及) | 実測 | 直し |
|---|---|---|---|---|
| S1 | hardware | K を変えても M-canon の宣言だけ | 宣言の写し(K = 1)で doeff を変えずに待った。値を doeff に写す変異は受入の検と形の検が断った | 不要 |
| S2 | storage | 置き場を移しても M-canon の宣言だけ | S1 と同じ検・同じ変異で確かめた | 不要 |
| S3 | effects | 正本の口が先端で変わっても、doeff は 1 行名乗って素通しし、語彙 0/1/2 は変わらない | 盲検 B: 環境変数で待ちを縮める口(方策を作り直す)が全部の検を通った。正本が欄を改名すると exit 1、NamedTuple 化で枠の外。主張の「方策は中を読まずに渡す」を強制する物が無かった | AST の検を足した。変異 mutant-S3-B-knob はこの検だけが断る(ほか 6 本は緑)。直した版は正本の内部の改名・NamedTuple 化でも宣言どおり待って exit 2 |
| S3b | effects | (同じ主張の中)正本の中の失敗でも語彙は変わらない | 盲検 A の副次の実測: take の中の OSError が traceback で抜け、exit 1(= 新しい赤) | take の中の失敗を名乗って素通しにした。TypeError だけを捕まえる変異は検が断る |
| S4 | effects | 待ちの上限では展開も pyright も起こさず exit 2・呼び手は変えない | 上限の検が緑。枠を取らない変異・75 を返す変異は検が断る | 不要 |
| S5 | concurrency | 祖先が持てば 2 つ目を取らない・K = 1 でも止まらない | 入れ子の検が緑。祖先の判定を通らず自分で取る変異は、入れ子の検(5 秒で exit 2)と形の検が断る | 不要 |
| S8 | concurrency | (盲検の前は S1・S2 の理由「方策の変更は正本の側だけで済む」に含めていた)正本が会話ごとの上限を足す | 盲検 A: 正本が take の中で会話の id を読むと、doeff だけが名乗り無しで数えの外を走った(cc8f9e7df で rc 0・pyright 1 回) | env を名指しの写し(EnvironmentByName)に替えた。直した版は待って rc 2・pyright 0 回。env を絞る変異は知らない名の読みの検が断る |
| S6 | distribution | 適用外 | — | 枠は Pod の資源(親の決め)。cluster で数えるのは M-canon の take の中身の差し替えで、前提の変更 |
| S7 | simulation | 適用外 | — | 枠は OS の flock と実 process の資源。doeff-hy-check は模擬の世界の Program ではない |

### S8 の波及の区別
- 予想の波及は M-canon だけだった。直す前の実測では、M-slot(と、名を写すなら M-env)も変える必要があった。
- 正本の公開の口は変わっていないので、これは契約の拡張ではない。M-canon が隠すはずの「take が読む env の名」が M-slot へ漏れていた。
- 直した後は、正本が新しい名を読んでも M-slot・M-env は変わらない(`repro-A-after-77175f1ce.log.txt`)。

## 強制方法(直した後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 枠を取ってから走る | 受入の検(埋まった枠の間 pyright を撃たない) | `packages/doeff-hy/tests/test_static_check_slot.py::test_a_full_slot_holds_a_directly_started_doeff_hy_check_until_it_frees` | `make test-changed`・日次の全体の回 | 本物の pyright の CPU は測らない |
| 値を持たない・方策を作り直さない | AST の形の検: `read_policy()` の呼びは `take(...)` の 1 つ目の引数そのもの | 同 `test_the_canon_policy_goes_straight_into_take` | 同上 | `module.take` と `module.read_policy` の綴りで判じる。別名で呼ぶ形は見ない(その時は呼びが無いので「read_policy を呼んでいない」で赤) |
| 方策の同一・env の名を知らない | 口だけの正本で、受けた方策が read_policy の答えそのものか・知らない名の読みが届くかを見る | 同 `test_the_declared_policy_and_any_environment_name_reach_the_canon_take` | 同上 | — |
| 正本の中の失敗で語彙を偽らない | 口だけの正本の take が OSError を投げる検 | 同 `test_a_failing_canon_take_lets_the_check_run_and_says_why` | 同上 | 正本の読み込みの失敗は既存の canon_module の捕まえ(検は無い) |
| 上限の語彙 | 上限の検(exit 2・pyright を撃たない) | 同 `test_doeff_hy_check_runs_nothing_when_the_slot_wait_expires` | 同上 | 呼び手の読みは agora-controllers の側 |
| 入れ子で 2 つ目を取らない | 入れ子の検(K = 1・正本の run の子) | 同 `test_doeff_hy_check_inside_a_slot_holder_takes_no_second_slot` | 同上 | /proc の無い機体(env の印)の道は検で走らない |
| env を読むのは M-env だけ | doeff-linter DOEFF004 | `packages/doeff-linter/src/rules/doeff004_no_os_environ.rs` | pre-commit の hook・日次の lint の段 | 「値を替える口を作るか」は見ない(盲検 B)— それは形の検が持つ |
| 依存の向き | code-quality の module 契約(check_slot → env_places だけ) | `.agents/code-quality.d/doeff-hy-check-slot.json` | 出荷前の `code-quality --scope changed`・日次 | path で読む正本は依存として数えない |

## 盲検 A・B と再現
起動の記録は `blind-meta.md`、返答は加工せずに `blind-A-response.md`・`blind-B-response.md` に置いた。

| 失敗例 | 設計者の再現 | 成立 | 直し | 直した後 |
|---|---|---|---|---|
| A: 正本が take の中で会話の id を読み、会話ごとの上限を数える | `evidence/repro-A-before-cc8f9e7df.log.txt`: doeff-hy-check rc 0・1.7 秒・pyright 1 回。枠の file の会話の id が空 | 成立 | EnvironmentByName | `evidence/repro-A-after-77175f1ce.log.txt`: 待ちを名乗って rc 2・5.5 秒・pyright 0 回 |
| A の副次: 作れない置き場で OSError → exit 1 | 口だけの正本の take が OSError を投げる形で再現(`mutant-S3-typeerror-only.log.txt` = 直す前の捕まえ方で OSError が抜ける) | 成立 | 名乗って素通し | `P-slot-tests.log.txt` の該当の検が緑 |
| B: 環境変数で待ちを縮める口が全部の検を通る | `evidence/mutant-S3-B-knob.diff.txt` を直した版に当てると、既存の 6 本は緑(B の主張どおり) | 成立 | AST の形の検 | 同じ変異を形の検が断る(1 failed・6 passed) |
| B の副次: 正本の欄の改名・NamedTuple 化で落ちる / 枠の外 | B の script を直した版に向けた `repro-B-after-*.log.txt` | B の口が在る時だけ成立 | 方策を読まない形の強制 | 3 通りとも宣言どおり待って exit 2・pyright 0 回 |

## 修正の履歴
1. 盲検の前に `design-pre-blind.md` と `blind-input.md` を commit(7e2493ee6)。
2. 盲検 A・B を同時に起こし、返答を保存(565debc7f)。
3. 直し(77175f1ce)を入れた。内容は次の 4 つ。
   - `env_places.slot_holder_setting` → `EnvironmentByName`。
   - `check_slot` の口の確かめを `unfit_names` へ替えた(呼べる関数・例外の class か)。
   - take の中の失敗を名乗って素通しにした。
   - 検 3 本を足した。
4. 証拠を取った(`evidence/evidence.py.txt` を変異の試しの木で撃った)。

## 確かめの記録(正常例)
- `P-slot-tests.log.txt`: 枠の検 7 本と env_places の検 4 本が緑(77175f1ce)。
- `P-test-changed.log.txt`: `make test-changed` が選んだ 5 file が赤 0。逆依存は linter が PATH に無く選ばれなかった。cc8f9e7d の時に linter を渡して選んだ逆依存 43 本のうち 42 本は 60 秒の上限で未測で、次の全体の回で測る。
- `P-code-quality.log.txt`: code-quality(changed・fast)は passed。型検査は含まない。
- `P-pre-commit.log.txt`: semgrep(Python)と doeff-linter が Passed。

## 限界(受け入れて残す物)
- /proc の無い機体の入れ子の判定(env の印)は、検で走らせていない。読みは EnvironmentByName を通る。
- 型検査(pyright・日次の段)は撃っていない(決まり type-check-is-not-per-commit)。
- 形の検は綴り(`.take`・`.read_policy`)で判じる。正本の口が改名された時は、口の確かめ(unfit_names)で素通しになる。
