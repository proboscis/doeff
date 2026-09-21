# 実装依頼書 — ADR-012 の針 5 か所を構造へ再照準する

- 親の調査(計画段): 郵便 `lt-KR8T10F3PWDB5M2DJWDJTWWDDW` / card `acp:kanban-issue:ki-7a7dd5cc6727`
- 基準: `abd03fd2a58dbad29bcb21f435539451315db523`(origin/main 2026-09-22 08:19 JST)
- 触る file: `docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` **1 本だけ**
- 設計本文: 同じ dir の `design.md` / 事前の主張 `claims.md` / 実行記録 `evidence.md` /
  最小実験の差分 `prototype.diff` / 設計検証の報告 `report.json`

## 0. 正本の読み口(この便が従う定義点)

| 何 | 正本 |
|---|---|
| 針が読んでよい 4 種と読んではいけない 5 種 | `design.md` §4 |
| 針の方針(「数ではなく名前の集合・字面ではなく呼び先と引数の役」) | 冊の 205〜215 行(既存・この便は**発明しない**) |
| 関所の口の名簿 | 冊に新設する `SESSION-ENV-ADMISSION-MOUTHS`(**1 か所**・針の中に写しを置かない) |
| 5 か所それぞれの「守るもの」と変異 | `design.md` §5 + `evidence.md` R4〜R6 |

## 1. 結合核の突合結論

**当たらない。** doeff には `docs/coupling-core-watchlist.md` が無く(実測)、dotfiles の艦隊の区画
`coupling-core-fleet-paths` は `.agents/land-queue.toml` の 1 本ちょうど。この便が触るのは
`docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` 1 本なので、どちらにも当たらない。
⇒ 通常の着地でよい(結合核の一括出荷の形は要らない)。

## 2. 起動・停止・排水・同時性の扱い

この便は**検査の表現だけ**を変える。`sessionhost/**` の実装コードは **1 行も触らない**。
- 起動 / 停止 / 排水: 振る舞いは変えない。(f) の針が読む点が `acp/` 配下のどの file でも
  通るようになるだけ(排水の意味論そのものは不変)。
- 同時性: 冊の針はソースを読むだけで、並行実行の性質を持たない。

## 3. 確定した決定(そのまま実装してよい)

`prototype.diff` が最小実験として現に緑 + 変異で赤になった形。これを採る。

1. **(a)(b) `mail-turn-text-of`** — 3 引数の字面 2 本を、`call-args-of` による
   「呼びは各 file 1 つ / 引数に `body` の役が在る」+「合成の座は judgment に 1 つ」へ。
   さらに**呼んだ後で返り値を作り直さない**針(束ねた名がその頂点の form の中で `setv`
   され直したら赤)を足す ← 盲検 B の反例。
2. **(c) `headless-send-program`** — 引数の並びの字面を、`call-args-of` による
   「呼びは 1 つ / 引数に `session-env` と `attachments` の役が在る」へ。
3. **(d) 関所の口** — 「3 か所ちょうど」の**数**を、呼びの**第 2 引数(動詞)の集合**と
   名簿 `SESSION-ENV-ADMISSION-MOUTHS` の突合へ。
   ⚠ **囲む form の名で数えない** — host.hy の 2 口は同じ `dispatch-method` に在るので畳まれる。
4. **(e) resume の添付** — 字下げまで含む行の一致を、`resume-params-of` を**実際に走らせて**
   添付が params に乗るかを測る形へ ← 盲検 A の反例。
   (`MESSAGE-ATTACHMENTS-KEY` と `resume-params-of` を冊の import に足す)
5. **(f) drain** — `worker_loop.hy` の字面を、**file を名指さず** `acp/` 配下から
   「settings に draining を書く点」を 1 つ探す形へ。定数(`True` / `False`)を書いていたら赤。

## 4. 確定した決定(やらないこと)

- **law の `:statement` は 1 文字も変えない。** 4 本とも法は現在のコードで満たされている
  (`design.md` §1 の表)。法の改訂は不要。
- **`sessionhost/**` の実装は触らない。** 今回の赤は実装の退行ではない。
- **字面の針の ratchet は置かない**(`design.md` §8 改訂 4)。それ自体が「数で撃つ針」で、
  冊が明文で禁じている形。
- **残る約 405 本の字面の針は触らない。** 別 card の射程(§6)。

## 5. 未確定事項(実装者の裁量・戻せる決定)

- 赤の文言の日本語の言い回し。読めれば可。
- 新設の名簿 `SESSION-ENV-ADMISSION-MOUTHS` を冊のどの名簿の近くに置くか
  (`prototype.diff` は `IO-FAILURE-EDGES` の直前に置いた)。
- (任意)`headless_first_turn` の挙動の節の assert が文言を持たない点
  (`evidence.md` R8 で `AssertionError` としか出なかった)。文言を足すのは歓迎だが必須ではない。

## 6. 手順

1. 作業樹を切る: `git -C ~/repos/doeff worktree add ~/.worktrees/doeff-wt-<slug> -b wt/<slug> origin/main`
2. `prototype.diff` を当てる(または同じ意味の実装を書く)。触るのは冊 1 file。
3. **焦点の走行**(これだけ・全数は 1 本も撃たない):
   ```
   .venv/bin/python -m pytest docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy \
     -k "attachment_spelling_lives_in_the_dialogue or headless_first_turn_carries_the_mail \
         or stop_drains_declared_nodes_before_closing or turn_credential_rides_the_turn" \
     --no-header -q
   ```
   併せて**冊 1 file の全 59 本**も走る(触った file ちょうどなので焦点の内): 59 passed を確かめる。
4. **変異で赤**を 5 件 + 盲検 2 件、実演して記録する(`evidence.md` R4〜R6 と同じ 7 件)。
   命令の逐語と所要秒を報告に書く。
5. 変更コードの品質検査 `--scope changed` を走らせ、違反は出荷を拒否する(未実行を合格にしない)。
6. 着地して本線へ。

## 7. 制約(守れない時は報告して止める)

- **全数テスト・全体検証を 1 本も走らせない。** 触った file(冊 1 本)と `-k` 絞りだけ。
- **開発中の検査は 1 分以内に終わるものだけ。** 実測: 焦点 4 本 = 2.56s・冊の全数 = 0.90〜42s
  (初回の import が重い。2 回目以降は 1 秒台)。
- 型検査(pyright 等)は commit ごとに撃たない。名指しで 1 回だけ。
- 作業樹は `~/.worktrees/` の中だけ。
- **起票はしない。** 新しい所見は card `ki-7a7dd5cc6727` のスレッドへ 1 行で足す。

## 8. 受入条件(依頼者が現物で測る)

1. 基準 `origin/main` に当てて、焦点の 4 本が**緑**(60 秒以内・命令の逐語と所要秒つき)。
2. 冊 1 file の全 59 本が**緑**(他の針を壊していない)。
3. **変異で赤** 5 件 — M1〜M5(`evidence.md` R4 の表と同じ壊し方)。それぞれ赤の文言つき。
4. **盲検の 2 反例**が意図どおりに振る舞う:
   - A(名簿を module の定数へ括り出す)を当てて **緑**(継承は保たれている = 偽陽性が消えた)
   - B(呼びの後で返り値を作り直す)を当てて **赤**
5. law の `:statement` の差分が **0**。`sessionhost/**` の差分が **0**。
6. 本線へ着地し、翌日の日次(03:30 JST)の台帳 `verify.failed` からこの 4 本が消える。
