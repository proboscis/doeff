import json, pathlib
D = pathlib.Path.home() / ".worktrees/doeff-wt-adr012-design/docs/design/adr012-pin-reaim/sendback-1"
rows = json.loads((D / "mutations.json").read_text())
by = {r["case"]: r for r in rows}
bad = [r for r in rows if r["ok"] != "OK"]
def jp(v): return {"green": "緑", "red": "赤"}.get(v, v)
table = "\n".join(f"| {r['case']} | {r['what']} | {jp(r['expect'])} | **{jp(r['verdict'])}** | {r['seconds']} |" for r in rows)
reds = [r for r in rows if r["verdict"] == "red"]
def group(pred): return " / ".join(r["case"] for r in reds if pred(r["message"]))
stray = group(lambda m: "名簿の外の読み手" in m)
wrap = group(lambda m: "で包んでいる" in m)
unread = group(lambda m: "実際には読んでいない" in m)
once = group(lambda m: "1 度だけ組む" in m)
exc = group(lambda m: "FileNotFoundError" in m)
text = f"""# 実行記録(差し戻し 1・2 版)

- 測った機体: 会社 Mac `CA-20038667`(Darwin)・python 3.14t・hy 1.3.0(この拍の実測。会話は機体を移るので報告のたびに書く)
- 基準版: `origin/main` = `09309e77ecd24d3f555b012000e910782b734fa0`
- 隔離の作業樹: `~/.worktrees/doeff-wt-adr012-sendback`(detached `09309e77`)。反例の行列はここで撃ち、
  各周の後に 3 file(冊・judgment.hy・agentd.hy)を素へ戻す(`mutations-runner.py` の restore)。
  主 checkout `~/repos/doeff` は 1 byte も触っていない。
- **検査器の撃ち方(逐語・`-k` は下線)**:
  ```
  PYTHONPATH="$(cat /tmp/sb-pp.txt)" /Users/s22625/repos/doeff/.venv/bin/python -m pytest \\
    docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy -k first_turn_carries --no-header -q
  ```
  `/tmp/sb-pp.txt` = 主 checkout の editable の `.pth` の路を作業樹へ読み替えた PYTHONPATH(作り方は
  `blind-input.md` §6)。1 本・2〜30 秒(冊が変わるたび Hy の再 compile が入るので反例の周は 20〜30 秒)。
  **全数・全体検証は 1 本も撃っていない。**

## R1. 差し戻された穴の再現(前の版の針)

前の手番の機体(`agentd-pool-1`)で、依頼者の指摘 3 形 + 当席の追加 3 形の**6 形が素通り**することを
実射で確認済み(その機体の記録はこの機体に届かないため、下の R2 で**新しい針に対して**同じ形を
撃ち直し、赤になることを測った)。

## R2. 反例 {len(rows)} 形の実測(`mutations.json` が正本・食い違い {len(bad)})

| 形 | 何の形か | 期待 | 実測 | 秒 |
| --- | --- | --- | --- | --- |
{table}

赤 {len(reds)} 形はすべて**狙った assert(か、読めない時の例外)**が撃った(文言で確認・`mutations.json` の `message`):

- 「名簿の外の読み手が読んでいる」(文言に運ぶ名の組が出る)= {stray}
- 「合成の呼びを `<名>` で包んでいる」= {wrap}
- 「名簿の読み手が実際には読んでいない」= {unread}
- 「手番の文を 1 度だけ組む: 実測 0」(空の母集団を緑にしない)= {once}
- `FileNotFoundError`(file が読めない ⇒ 例外 = 赤・安全な向き)= {exc}

## R3. 依頼者 §3 の 3 行(空の母集団が緑に倒れないこと)

| §3 の行 | 形 | 実測 |
| --- | --- | --- |
| 呼び `mail-turn-text-of` を scan の外へ移す(呼びの行を消す) | `movecall` | **赤**「agentd.hy は手番の文を 1 度だけ組む(R16): 実測 0」 |
| 名簿の送り先の片方を消す(judgment の `.append` が文を読まない) | `dropusej` | **赤**「名簿の読み手が実際には読んでいない … 名簿 ['bodies.append', 'tuple'] 実測 ['tuple']」 |
| 検査器が読む path を存在しない名へ 1 字差し替える | `badpath` | **赤**(`FileNotFoundError` の例外) |

agentd 側の同じ形は `dropuse`(赤)。

## R4. 盲検 A・B(`blind-A.md` / `blind-B.md`)

- B の形(`numbered`)は **1 版の試作を緑で通過**(主張 1 の反例)。2 版で**赤**・文言に運ぶ名 `('bodies', 'text')` と
  `(enumerate bodies)` / `(len bodies)` / `(setv bodies numbered)` の 3 つが出る。
- A の形(`lencheck`)は組み替えていないのに**赤** = 過剰に赤くする範囲の実例。名簿へ `"len"` 1 行で**緑**(`lencheckdeclared`)。
  註にこの範囲と直し方(緩めない・名簿を人が直す)を書いた。

## R5. 間違った理由の赤を数えていない証拠(1 件見つけて外した — 1 版の拍)

`addarg` を最初に「呼びと宣言の引数だけ」足す形で撃つと **5.7 秒で赤**になった。文言を読むと
針ではなく **`defk` の既存の不変条件**(各引数に `{{:pre [(: 名 型)]}}` の型の検査を要求する)が
撃っていた。当席の変異が**不正な変更**だったため。型の検査も足した正しい形で撃ち直して **緑**
(2 版でも `addarg` = 緑)。この 1 件は「狙った違反の検出」に数えていない。

## R6. 受入 2・6 の実測(2 版・この機体 `CA-20038667`・作業樹 `~/.worktrees/doeff-wt-adr012-sendback`)

- 冊 1 file の全 **59 本 = 59 passed**(30.59 秒 / real 30.9 秒)
- 変更 file = **冊 1 本**(`git diff --name-only`)/ `sessionhost/**` の差分 = **空** /
  law の `:statement` の差分 = **0 行** / 新しい `(law ` の定義 = **0 件** /
  `spelling-pins-proxy-for-shape` は**名指し 3 行**だけ(条文を写していない) /
  名簿 `MAIL-TURN-TEXT-CONSUMERS` の追加行 4 / 冊の差分 = +200 / -25 行
- `prototype.diff`(2 版・262 行)は素の本線 `09309e77` に `git apply --check` で当たり、当てた焦点 1 本 = 緑
  (16.48 秒・盲検 A の作業樹 `doeff-wt-blind-a2` で確認)

## R7. 部品の速さ(1 分の制約に対する実測)

焦点 1 本 = 温 2.4 秒 / 冷(冊の再 compile)16〜30 秒。冊の全 59 本 = 30.6 秒。どれも 1 分の内。
反例の行列 28 形の合計 = 698.9 秒(1 形 平均 25 秒)— これは**設計検証の道具**で、開発の検ではない
(実装段が撃つのは受入 3 の 12 形・約 5 分)。
"""
(D / "evidence.md").write_text(text, encoding="utf-8")
print("evidence.md:", len(text.splitlines()), "行 / 形", len(rows), "/ 食い違い", len(bad))
