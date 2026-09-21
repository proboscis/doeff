# 実行記録(すべて実測・機体 CA-20038667・作業樹 ~/.worktrees/doeff-wt-adr012-head)

走行の口はすべて:
`.venv/bin/python -m pytest docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy -k "<名>" --no-header -q`

## R0 赤の再現(断面 68f075d2708664d07905ad1f509f3b1489845c66・作業樹 doeff-wt-adr012-verify)

```
-k "attachment_spelling_lives_in_the_dialogue or headless_first_turn_carries_the_mail
    or stop_drains_declared_nodes_before_closing or turn_credential_rides_the_turn"
→ 4 failed, 52 deselected, 1 warning in 33.64s   (real 0m34.558s)
```
名乗った 4 つの断り:
- `resume の params の名簿に添付が在る(R31)`
- (文言なしの assert — 実体は `mail-turn-text-of` の 3 引数の字面 2 本)
- `loop は drain の合図を settings.draining に写す(R39)`
- `手番ごとの env は送りの腕へ渡る(R30・添付は R31)`

## R1 隠れた 2 段目の断りの発掘(字面だけ合わせた探り・同断面)

5 か所の字面を現在の綴りに合わせると **3 passed / 1 failed** になり、
`turn_credential` の**後ろにもう 1 件**在ることが判った:
```
E AssertionError: session_env の関所の呼びが 3 か所ちょうどでない(実測 4:
  ['(setv env-error (session-env-admission-error session-env method))',
   '(setv send-env-error (session-env-admission-error session-env "session.send"))',
   '(setv env-error (session-env-admission-error session-env "session.launch"))',
   '(setv admission (session-env-admission-error (dict pairs) "join.seat_env"))'])
```
名簿の数も直すと **4 passed in 1.71s**。⇒ 隠れた退行は無い(断りは 5 か所の針ちょうど)。

## R2 現在の先端(abd03fd2)での実勢

```
(針は基準のまま)全 59 本 → 3 failed, 56 passed, 1 warning in 42.01s
```
`stop_drains` は先端では緑 — ただし緑にしたのは `20c50b4e` が**新しい字面へ合わせ直した**だけ。

## R3 再照準後の正常例(基準 abd03fd2 + prototype.diff)

```
-k "<4 本>"        → 4 passed, 55 deselected, 1 warning in 2.56s
冊の全数(1 file)  → 59 passed, 1 warning in 0.90s
```

## R4 変異(直した側を 1 か所だけ壊すと現に赤くなる)

| 変異 | 壊した点 | 走らせた検査 | 結果 |
|---|---|---|---|
| M1 | agentd.hy の合成の呼びを `(setv text body)` に置換 | headless_first_turn | **赤** `agentd.hy は手番の文を 1 度だけ組む(R16): 実測 0` |
| M2 | 送りの腕の呼びから `session-env` を落とす | turn_credential | **赤** `役 session-env が ['sid','message','awaiting','turn-charter','attachments'] に無い` |
| M3 | 送りの口の動詞を `"session.probe"` に改名 | turn_credential | **赤** `送りの口の関所は launch と同じ 1 点(R30)` |
| M3b | launch.hy に 5 つ目の口 `"session.rehydrate"` を足す(判断は再利用) | turn_credential | **赤** `関所を呼ぶ口が名簿と違う … 実測 [… "session.rehydrate" …] / 名簿 […]` |
| M4 | resume の名簿から添付を外す | attachment_spelling | **赤** `resume の params に添付が乗らない(R31) … 実測 None` |
| M5 | `(replace settings :draining control.draining)` → `… :draining False` | stop_drains | **赤** `定数を書くと level-triggered が壊れ、排水が下ろせない` |

## R5 盲検 A の反例(実行で確認)

反例: `resume-params-of` の欄の一覧を module の定数 `RESUME-ATTACHMENT-KEYS` へ括り出す。
- **当初案**(`readers-of` で撃つ)に当てる → **赤** `resume の params の名簿(judgment.resume-params-of)に添付が在る(R31)`
  (添付は継がれているのに赤 = 偽陽性 = 反例は成立)
- **改訂後**(挙動で撃つ)に当てる → **緑**(1 passed in 1.73s)
- 改訂後に本物の違反(`RESUME-ATTACHMENT-KEYS` を `#()` に)を重ねる → **赤** `実測 None`

## R6 盲検 B の反例(実行で確認)

反例: `mail-turn-text-of` を呼んだ**後で**返り値へ見出しを足す(条件つき)。
- **改訂前の再照準**に当てる → **緑**(4 passed in 20.98s)= 反例は成立
- **旧い字面の針**(綴りだけ合わせ直した版)に当てる → **緑**(1 passed in 1.49s)
  ⇒ 元から在った穴で、本設計が持ち込んだ後退ではない
- **改訂 3 の針**(再束縛を撃つ)を足して当てる → **赤**
  `agentd.hy が合成した文を呼びの後で作り直している … ['(setv text (+ "【優先度: 緊急】\n" text)))']`
- 現行コードでの誤検知 → **0**(judgment.hy 0 件・agentd.hy 0 件)

## 盲検の起動記録

| | 役 | 要求モデル / effort | 起動口 | 結果 |
|---|---|---|---|---|
| A 1 回目 | 責務波及の反例 | gpt-6-astra / low | `codex exec -m gpt-6-astra -c model_reasoning_effort=low -s read-only` | **未実施** — AGENTS.md の「調査・設計は Fable」を読んで辞退 |
| B 1 回目 | 検査通過の反例 | 同上 | 同上 | **未実施** — 同じ理由で辞退 |
| A 2 回目 | 同上 | 同上 | 同上(不足点のみ補填: この役に限る operator 指定の例外を明記) | **反例を返した**(採用・実行で確認) |
| B 2 回目 | 同上 | 同上 | 同上 | **反例を返した**(採用・実行で確認) |

- A と B は互いの返答を見ない別々の新規文脈。会話の fork / resume は使っていない。
- 渡した材料 = `blind-input.md`(設計・事前の主張・helper の実体・現在の針・現在の実装・検査の走らせ方)。
  設計者の自己評価・既知の反例・望む結論・親会話は渡していない。
- 実際に観測できたモデルは**未確認**(起動時の要求値は記録した)。1 回目の辞退は実行環境の制約で、
  反例の不在を示すものではない。

## R7 hardware 軸(器の種類が増える)

- 正常例: 現行コードで `headless_first_turn_carries_the_mail` → **緑** 1 passed in 0.41s
- 反例: agentd.hy に `(when (= settings.backend-kind "headless") …)` を足す
  (3 つ目の器を足す便でやりがちな形)→ **赤**
  `agentd.hy は backend の語を比較しない(R16): (when (= settings.backend-kind "headless")`

## R8 simulation 軸(偽の器で回る挙動の節)

- 正常例: 現行コードで同検査 → **緑** 1 passed in 9.76s
- 反例: `judgment.first-turn-carries-inputs` の判定を `(and False …)` に倒す
  (= headless の launch が郵便を運ばなくなる)→ **赤**(World / FakeSessions の tick の assert)
  ⚠ この assert は文言を持たないので `AssertionError` としか出ない — 実装の便で文言を足すとよい(任意)。
