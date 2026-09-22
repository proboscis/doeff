# 実装依頼書 — R16 の「第 2 の合成点」の針を form の読みへ(差し戻し 1 の直し・2 版)

- 親の調査(計画段): 郵便 `lt-KR8T10F3PWDB5M2DJWDJTWWDDW` / card `acp:kanban-issue:ki-7a7dd5cc6727`
- 直す元: 着地 `4e4d3eb1` の受入 7(差し戻し `lt-YMAW77605Z9EXP3PYNBTK6JQFZ`・依頼者の合意と追補 `lt-XX7TCWJ2D55AAY6AGAQ5RRHJ0R`)
- 基準: `origin/main` = `09309e77ecd24d3f555b012000e910782b734fa0`(先端が進んでいても冊 1 file の差分なので当たるはず・当たらなければ同じ意味で書く)
- 触る file: `docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` **1 本だけ**
- 設計本文: 同じ dir の `design.md`(2 版)/ 事前の主張 `claims.md` とその結果 `claims-outcome.md` /
  実行記録 `evidence.md` / 盲検の記録 `blind-A.md` `blind-B.md` / 最小実験の差分 `prototype.diff`(2 版)/
  差分を作る道具 `prototype-patcher.py` / 反例の実測 `mutations.json` とその道具 `mutations-runner.py` /
  設計検証の報告 `report.json`

## 0. 正本の読み口(この便が従う定義点)

| 何 | 正本 |
|---|---|
| なぜ行の字面ではなく form を読むのか・向きの規則(開いた集合 → 閉じた集合への反転) | `design.md` §1〜3 |
| 覆う範囲・覆わない範囲・**過剰に赤くする範囲**(註にそのまま書く 3 つ) | `design.md` §4 |
| 手番の文の読み手の集合 | 冊に新設する `MAIL-TURN-TEXT-CONSUMERS`(**1 か所**・針の中に写しを置かない) |
| 形の病の名前 | herdr-hud `law spelling-pins-proxy-for-shape`(**名指す 1 行だけ**・条文を doeff へ写さない) |
| form を読む口 | Hy 自身の reader `hy.read-many`(第 2 の reader を書かない)。⚠ `hy_003` の冊の先例は**文字列**を読む先例で、file を読む軸(path・存在・解析)はこの便が初めて建てる — `design.md` §2 の表 |

## 1. 結合核の突合結論

**当たらない。** doeff には `docs/coupling-core-watchlist.md` が無く(実測)、dotfiles の艦隊の区画
`coupling-core-fleet-paths` は `.agents/land-queue.toml` の 1 本ちょうど。触るのは冊 1 本なので
どちらにも当たらない ⇒ 通常の着地でよい。

## 1b. 既知の形に照らす(responsibility-boundary-design 段 4a)

この便は**概念・状態・境界・仕組みを 1 つも足さない** — 既存の control plane(ACP の腕)の
**検査の表現**が「行の字面を読む」から「form を読む」へ変わるだけ。新しい実行時の部品・新しい
状態・新しい境界は 0 で、台帳  のどの形の規律にも
触らない。⇒ 既知の形の突合は「該当なし(検査器の内側の変更)」。

## 2. 起動・停止・排水・同時性の扱い

この便は**検査の表現だけ**を変える。`sessionhost/**` の実装コードは **1 行も触らない**。
- 起動 / 停止 / 排水: 振る舞いは変えない(針が読む口が字面 → form になるだけ)。
- 同時性: 針はソースを読むだけで並行実行の性質を持たない。焦点 1 本の走行は 2〜30 秒(compile の
  cache の温冷で変わる)。

## 3. 確定した決定(そのまま実装してよい)

`prototype.diff`(2 版)が現に緑 + 反例 28 形で期待どおり(`mutations.json`)。これを採る。

1. **form で読む部品 6 つ**を冊の「針の照準の部品」の節(`call-args-of` の直後)へ足す:
   `form-use-head` / `top-form-name-of` / `child-parent-pairs` / `assignment-target?` / `carriers-of` /
   `bound-calls-of` と値の型 `BoundCall`(欄 = top / parent / bound / carriers / uses)。
   import に `hy` / `hy.models [Expression List Sequence Symbol]` / `dataclasses [dataclass]` を足す。
   節の頭の註に**先例の読み方**(hy_003 は文字列・file の軸はここが建てる・倒れる向き)と
   **同時に死ぬ 3 族**を書く(`prototype.diff` の文言をそのまま使ってよい)。
2. **名簿 `MAIL-TURN-TEXT-CONSUMERS`** を「集合の宣言(名簿)」の節(`IO-FAILURE-EDGES` の直後)へ。
   値 = `{"judgment.hy" {"bodies.append" … "tuple" …} "agentd.hy" {"SessionInterject" …}}`。
   註に**向きの規則 1 行**(禁止の列挙 = 開いた集合 / 読み手の名簿 = 閉じた集合)を書く。
3. **針の本体**(`test-adr-doe-agents-012-headless-first-turn-carries-the-mail` の中)の
   再束縛の 2 正規表現を落とし、次の 4 つの assert へ:
   - form 上の呼びは 1 つ(0 件 = 赤・空の母集団を緑にしない)
   - 呼びを包む form の頭が `<-` でなければ赤
   - **運ぶ名の閉包**の読みに名簿の外の頭が在れば赤(文言に運ぶ名の組を出す)
   - 名簿の読み手の集合と実測の読みの集合が**一致**しなければ赤
   走査の口を `lines` から `path` へ変える(`call-args-of` は `(code-lines path)` を渡す)。
4. **註を主張の範囲へ揃える**(`design.md` §4)。「塞いだ」と書かず、**赤にする範囲**・**赤にしない範囲**
   (別の form へ渡った後・handler の実 I/O の中・呼ばずに作る形)・**過剰に赤くする範囲**(組み替えない
   読み `(len 文)`・積む先の名の改名・読み手の追加 — 直し方は名簿へ 1 行・**緩めない**)の 3 つを並べ、
   由来(元からの穴・盲検 B が見つけた・最初の針は 1 行の `setv` だけ・差し戻しで form の読みへ・
   作り替えの盲検 B が積んだ先の組み替えで素通りしたので閉包へ)を 1 段落で残す。

## 4. 確定した決定(やらないこと)

- **law の `:statement` は 1 文字も変えない。** 法は元から満たされている。
- **`sessionhost/**` の実装は触らない。**
- **禁止の綴り(`setv` / `setx` / `let` …)を針の中に列挙しない。** それ自体が
  `law spelling-pins-proxy-for-shape` ①(b) の形(今回の失敗そのもの)。
- **正当な整理が赤になっても検査を緩めない。** 註の「過剰に赤くする範囲」に該当を書き、直し方は名簿を人が
  1 行直す形だけ(依頼者 §5)。数や範囲を書き換えて緑にする直しは禁止。
- **範囲を広げない**(依頼者 §6)。drain の針(`replace` / `settings` / `draining` が同じ 1 行)も、残る約 405 本の
  字面の針も触らない(card `ki-7a7dd5cc6727` の棚に 1 行置いてある・別の便の母集団)。

## 5. 未確定事項(実装者の裁量・戻せる決定)

- 赤の文言の日本語の言い回し(読めれば可)。
- 部品の置き場の細部(節の中の順)。
- `form-use-head` の綴りの規約を変えるなら、名簿の鍵も同じ便で合わせること(綴りの定義点は 1 つ)。

## 6. 手順

1. 作業樹を切る: `git -C ~/repos/doeff worktree add ~/.worktrees/doeff-wt-<slug> -b wt/<slug> origin/main`
2. `prototype.diff` を当てる(または `prototype-patcher.py` を `SB_WT=<作業樹> python3` で撃つ・
   または同じ意味の実装を書く)。触るのは冊 1 file。
3. **焦点の走行**(これだけ・全数は 1 本も撃たない)。⚠ **`-k` は下線**の綴り:
   ```
   PYTHONPATH=<作業樹を指す editable の路> .venv/bin/python -m pytest \
     docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy -k first_turn_carries --no-header -q
   ```
   併せて**冊 1 file の全 59 本**(触った file ちょうどなので焦点の内・約 25〜40 秒)。
   ⚠ 作業樹で走らせるには editable の `.pth` の路を作業樹へ読み替えた `PYTHONPATH` が要る。
   作り方は `mutations-runner.py` の冒頭と `blind-input.md` §6 に逐語で在る。
4. **反例で赤**を再演する。`mutations-runner.py` を `SB_WT=<作業樹> PYTHONUNBUFFERED=1 python3` で
   そのまま撃ってよい(28 形・約 3〜7 分・後始末つき。PYTHONPATH は `/tmp/sb-pp.txt` を読むので、
   自分の作業樹の路を同じ file 名に書くか script の 1 行を直す)。最低限 = 受入 3 の 12 形。
5. 変更コードの品質検査 `--scope changed` を走らせ、違反は出荷を拒否する(未実行を合格にしない)。
6. 着地して本線へ。着地したら card `ki-7a7dd5cc6727` へ 1 行。
7. **依頼者(計画段 `c-XYP9ZM3W0GG8GPB4J60GK2AF49`)へ `ai reply <この依頼の郵便 id> --kind report` で返す。**
   報告に必ず書く物: 着地の commit・列の鍵・**検査器の撃ち方の 1 行(逐語・`-k first_turn_carries`)**・
   焦点と全 59 本の passed 数と秒・反例の赤の文言(受入 3 の 12 形)・測った機体名(その拍の実測)。

## 7. 制約(守れない時は報告して止める)

- **全数テスト・全体検証を 1 本も走らせない。** 触った file(冊 1 本)と `-k` 絞りだけ。
- **開発中の検査は 1 分以内に終わるものだけ**(実測: 焦点 1 本 = 2〜30 秒・冊の全数 = 25〜40 秒)。
- 型検査(pyright 等)は commit ごとに撃たない。名指しで 1 回だけ。
- 作業樹は `~/.worktrees/` の中だけ。`git checkout -- <file>` で戻さない(hook が止める —
  `git show HEAD:<file> > <file>` か file を名指しの手編集で戻す)。
- **起票はしない。** 新しい所見は card `ki-7a7dd5cc6727` のスレッドへ 1 行。

## 8. 受入条件(依頼者が現物で測る)

1. 基準に当てて、焦点の 1 本(`-k first_turn_carries`)が**緑**(命令の逐語と所要秒つき)。
   ⚠ `-k` は**下線**の綴り(`first-turn-carries` は 59 deselected で 0 本になる)。
2. 冊 1 file の全 59 本が**緑**(他の針を壊していない)。
3. **反例で赤** — 次の 12 形。それぞれ赤の文言つきで、**狙った構造の assert が撃った**ことが
   文言から読めること(名前の誤り等の別の理由の赤を数えない):
   - 差し戻しの 3 形 + 1 行の setv: `setv1` / `rebind` / `setv2` / `foldcall`
   - その場で組む・包む: `consume` / `wrap`
   - **盲検 B(2 版の由来)**: `numbered` — 積んだ先 `bodies` を歩いて見出しを書き足す(文言に運ぶ名 `('bodies', 'text')` と `(enumerate bodies)` 等が出る)
   - 名簿の側: `dropuse`(agentd)/ `newreader`
   - **依頼者 §3 の 3 行(空の母集団が緑に倒れない)**: `movecall`(呼びの行を消す → 実測 0 で赤)/
     `dropusej`(judgment の `.append` が文を読まなくなる → 名簿と食い違って赤)/
     `badpath`(読む path を 1 字違いに → 例外で赤)
4. **検査が噛む証拠の対**が両方向で成り立つ(2 対):
   - 読み手を足して名簿へ宣言しない ⇒ **赤**(`newreader`)/ 同じ読み手を名簿へ 1 行宣言 ⇒ **緑**(`newreaderdeclared`)
   - 組み替えない読み `(len text)` ⇒ **赤**(`lencheck`・盲検 A)/ 名簿へ `"len"` を 1 行宣言 ⇒ **緑**(`lencheckdeclared`)
5. **正当な整理が緑**: 束ねた名の改名(`renamebound`)/ 呼びの折り直し(`foldonly`)/ 送り先の欄の順の入れ替え
   (`foldinter`)/ 呼びに欄を足す(`addarg`)/ 註に禁止の綴りを書く(`commentmention`)/ 文字列 literal に禁止の
   綴り(`stringmention`)。
6. law の `:statement` の差分が **0**。`sessionhost/**` の差分が **0**。変更 file は **1 本**。
7. 註が `design.md` §4 の**3 つの範囲**と**一致**する — 「塞いだ」と書かず、赤にする範囲・**赤にしない範囲**
   (別の form へ渡った後・handler の実 I/O の中・呼ばずに作る形)・**過剰に赤くする範囲**(組み替えない読み・
   積む先の改名・読み手の追加 — 直し方は名簿を人が直す・緩めない)が名指しで並んでいること。
   **これが差し戻しの本題**(前の便は註が閉じたと主張していた)。
8. 名簿の註に**向きの規則 1 行**(禁止の列挙 = 開いた集合 / 読み手の名簿 = 閉じた集合)、部品の節の頭に
   **先例の読み方**(hy_003 は文字列・file の軸はここが建てる)と**同時に死ぬ 3 族**が在ること。
