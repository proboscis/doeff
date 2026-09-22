# 実装依頼書 — R16 の「第 2 の合成点」の針を form の読みへ(差し戻し 1 の直し)

- 親の調査(計画段): 郵便 `lt-KR8T10F3PWDB5M2DJWDJTWWDDW` / card `acp:kanban-issue:ki-7a7dd5cc6727`
- 直す元: 着地 `4e4d3eb1` の受入 7(差し戻し `lt-YMAW77605Z9EXP3PYNBTK6JQFZ`)
- 基準: `origin/main` = `09309e77ecd24d3f555b012000e910782b734fa0`
- 触る file: `docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` **1 本だけ**
- 設計本文: 同じ dir の `design.md` / 事前の主張 `claims.md` / 実行記録 `evidence.md` /
  最小実験の差分 `prototype.diff` / 反例の実測 `mutations.json` / 設計検証の報告 `report.json`

## 0. 正本の読み口(この便が従う定義点)

| 何 | 正本 |
|---|---|
| なぜ行の字面ではなく form を読むのか | `design.md` §1〜3 |
| 覆う範囲と覆わない範囲(註にそのまま書く文) | `design.md` §4 |
| 手番の文の読み手の集合 | 冊に新設する `MAIL-TURN-TEXT-CONSUMERS`(**1 か所**・針の中に写しを置かない) |
| 形の病の名前 | herdr-hud `law spelling-pins-proxy-for-shape`(**名指す 1 行だけ**・条文を doeff へ写さない) |
| form を読む口 | Hy 自身の reader `hy.read-many`(`hy_003` の冊が既に使っている。第 2 の reader を書かない) |

## 1. 結合核の突合結論

**当たらない。** doeff には `docs/coupling-core-watchlist.md` が無く(実測)、dotfiles の艦隊の区画
`coupling-core-fleet-paths` は `.agents/land-queue.toml` の 1 本ちょうど。触るのは冊 1 本なので
どちらにも当たらない ⇒ 通常の着地でよい。

## 2. 起動・停止・排水・同時性の扱い

この便は**検査の表現だけ**を変える。`sessionhost/**` の実装コードは **1 行も触らない**。
- 起動 / 停止 / 排水: 振る舞いは変えない(針が読む口が字面 → form になるだけ)。
- 同時性: 針はソースを読むだけで並行実行の性質を持たない。`hy.read-many` の読みは 1 file
  あたり 1.5 秒未満(実測)で、焦点の走行は 11〜19 秒に収まる。

## 3. 確定した決定(そのまま実装してよい)

`prototype.diff` が現に緑 + 反例 16 形で期待どおり(`mutations.json`)。これを採る。

1. **form で読む部品 4 つ**を冊の「針の照準の部品」の節(`call-args-of` の直後)へ足す:
   `form-use-head` / `top-form-name-of` / `child-parent-pairs` / `bound-calls-of` と値の型 `BoundCall`。
   import に `hy` / `hy.models [Expression List Sequence Symbol]` / `dataclasses [dataclass]` を足す。
2. **名簿 `MAIL-TURN-TEXT-CONSUMERS`** を「集合の宣言(名簿)」の節(`IO-FAILURE-EDGES` の直後)へ。
   値 = `{"judgment.hy" {"bodies.append" …} "agentd.hy" {"SessionInterject" …}}`。
3. **針の本体**(`test-adr-doe-agents-012-headless-first-turn-carries-the-mail` の中)の
   再束縛の 2 正規表現を落とし、次の 3 つの assert へ:
   - form 上の呼びは 1 つ
   - 呼びを包む form の頭が `<-` でなければ赤
   - 束ねた名の読みの集合が名簿の鍵の集合と**一致**しなければ赤
   走査の口を `lines` から `path` へ変える(`call-args-of` は `(code-lines path)` を渡す)。
4. **註を主張の範囲へ揃える**(`design.md` §4)。「塞いだ」と書かず、**何を赤にするか**と
   **何を赤にしないか**(別の form へ渡った後・handler の実 I/O の中・呼ばずに作る形)を並べ、
   由来(元からの穴・盲検 B が見つけた・最初の針は 1 行の `setv` だけ捕まえた・差し戻しで
   form の読みへ作り替えた)を 1 段落で残す。

## 4. 確定した決定(やらないこと)

- **law の `:statement` は 1 文字も変えない。** 法は元から満たされている。
- **`sessionhost/**` の実装は触らない。**
- **禁止の綴り(`setv` / `setx` / `let` …)を針の中に列挙しない。** それ自体が
  `law spelling-pins-proxy-for-shape` ①(b) の形(今回の失敗そのもの)。
- **drain の針(`replace` / `settings` / `draining` が同じ 1 行)は触らない。** 同族だが
  安全な向きに倒れる(折れると 0 件 ⇒ 赤)。card `ki-7a7dd5cc6727` の棚に 1 行置いてある。
- 残る約 405 本の字面の針は触らない(別 card の射程)。

## 5. 未確定事項(実装者の裁量・戻せる決定)

- 赤の文言の日本語の言い回し(読めれば可)。
- 部品の置き場の細部(節の中の順)。
- `form-use-head` の綴りの規約を変えるなら、名簿の鍵も同じ便で合わせること(綴りの定義点は 1 つ)。

## 6. 手順

1. 作業樹を切る: `git -C ~/repos/doeff worktree add ~/.worktrees/doeff-wt-<slug> -b wt/<slug> origin/main`
2. `prototype.diff` を当てる(または同じ意味の実装を書く)。触るのは冊 1 file。
3. **焦点の走行**(これだけ・全数は 1 本も撃たない):
   ```
   PYTHONPATH=<作業樹を指す editable の路> .venv/bin/python -m pytest \
     docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy -k first_turn_carries --no-header -q
   ```
   併せて**冊 1 file の全 59 本**(触った file ちょうどなので焦点の内)。
   ⚠ 作業樹で走らせるには editable の `.pth` の路を作業樹へ読み替えた `PYTHONPATH` が要る。
   作り方は `mutations-runner.py` の冒頭と `blind-input.md` §6 に逐語で在る。
4. **反例で赤**を再演する(`mutations.json` の 11 形のうち少なくとも
   `setv1` / `rebind` / `setv2` / `foldcall` / `consume` / `wrap` / `dropuse` / `newreader` の 8 形)。
   `mutations-runner.py` をそのまま撃ってよい(16 形・約 4 分・後始末つき)。
5. 変更コードの品質検査 `--scope changed` を走らせ、違反は出荷を拒否する(未実行を合格にしない)。
6. 着地して本線へ。

## 7. 制約(守れない時は報告して止める)

- **全数テスト・全体検証を 1 本も走らせない。** 触った file(冊 1 本)と `-k` 絞りだけ。
- **開発中の検査は 1 分以内に終わるものだけ**(実測: 焦点 1 本 = 11〜19 秒・冊の全数 = 40 秒前後)。
- 型検査(pyright 等)は commit ごとに撃たない。名指しで 1 回だけ。
- 作業樹は `~/.worktrees/` の中だけ。`git checkout -- <file>` で戻さない(hook が止める —
  file を名指しの手編集で戻す)。
- **起票はしない。** 新しい所見は card `ki-7a7dd5cc6727` のスレッドへ 1 行。

## 8. 受入条件(依頼者が現物で測る)

1. 基準 `origin/main` に当てて、焦点の 1 本(`-k first_turn_carries`)が**緑**(命令の逐語と所要秒つき)。
   ⚠ `-k` は**下線**の綴り(`first-turn-carries` は 59 deselected で 0 本になる)。
2. 冊 1 file の全 59 本が**緑**(他の針を壊していない)。
3. **反例で赤** — 上の 8 形。それぞれ赤の文言つきで、**狙った構造の assert が撃った**ことが
   文言から読めること(名前の誤り等の別の理由の赤を数えない)。
4. **検査が噛む証拠の対**が両方向で成り立つ:
   - 読み手を足して名簿へ宣言しない ⇒ **赤**(`newreader`)
   - 同じ読み手を足して名簿へ 1 行宣言する ⇒ **緑**(`newreaderdeclared`)
5. **正当な整理が緑**: 束ねた名の改名 / 呼びの折り直し / 送り先の欄の順の入れ替え。
6. law の `:statement` の差分が **0**。`sessionhost/**` の差分が **0**。変更 file は **1 本**。
7. 註が `design.md` §4 の範囲と**一致**する — 「塞いだ」と書かず、赤にする範囲と
   **赤にしない範囲**(別の form へ渡った後・handler の実 I/O の中・呼ばずに作る形)が
   名指しで並んでいること。**これが差し戻しの本題**(前の便は註が閉じたと主張していた)。
