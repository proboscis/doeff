# 実行記録(差し戻し 1)

- 測った機体: 会社 Mac `CA-20038667`(Darwin)・python 3.14t・hy 1.3.0
- 基準版: `origin/main` = `09309e77ecd24d3f555b012000e910782b734fa0`
- 隔離の作業樹: `~/.worktrees/doeff-wt-adr012-sendback`(detached `09309e77`)。
  主 checkout `~/repos/doeff` は 1 byte も触っていない(各周の後に `git status --porcelain` を確認 —
  残るのは冊 1 file の `M` = 試作そのもの)。
- 焦点の命令(逐語):
  ```
  PYTHONPATH="$(cat /tmp/sb-pp.txt)" /Users/s22625/repos/doeff/.venv/bin/python -m pytest \
    docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy -k first_turn_carries --no-header -q
  ```
  1 本・11〜30 秒(初回は Hy の再 compile を含む)。**全数・全体検証は 1 本も撃っていない。**

## R1. 差し戻された穴の再現(前の版の針)

前の手番の機体(`agentd-pool-1`)で、依頼者の指摘 3 形 + 当席の追加 3 形の**6 形が素通り**することを
実射で確認済み(その機体の記録はこの機体に届かないため、下の R2 で**新しい針に対して**同じ形を
撃ち直し、赤になることを測った)。

## R2. 反例 17 形の実測(`mutations.json` が正本・食い違い 0)

| 形 | 何の形か | 期待 | 実測 | 秒 |
| --- | --- | --- | --- | --- |
| base | 素の本線(試作を当てただけ) | 緑 | **緑** | 16.1 |
| setv1 | 1 行の `setv`(旧い針が捕まえた唯一の形) | 赤 | **赤** | 16.6 |
| rebind | `<-` で束ね直す(依頼者の指摘 1) | 赤 | **赤** | 17.5 |
| setv2 | `setv` を 2 行に折る(依頼者の指摘 2) | 赤 | **赤** | 16.5 |
| foldcall | 呼びの行を折る + 1 行の `setv`(依頼者の指摘 3) | 赤 | **赤** | 18.6 |
| consume | 送り先の引数でその場で組む | 赤 | **赤** | 17.7 |
| let | `let` で覆って組み替える | 赤 | **赤** | 14.1 |
| wrap | 呼びを別の form で包む(親が `<-` でなくなる) | 赤 | **赤** | 15.2 |
| alias | 別の名へ写してから渡す | 赤 | **赤** | 16.3 |
| dropuse | 名簿の読み手が読まなくなる(文を捨てる) | 赤 | **赤** | 17.4 |
| rosterempty | 名簿から読み手の項を落とす | 赤 | **赤** | 15.5 |
| newreader | 読み手を足して名簿へ宣言しない | 赤 | **赤** | 14.7 |
| renamebound | 束ねた名を改名する | 緑 | **緑** | 11.2 |
| foldonly | 呼びを 3 行に折るだけ | 緑 | **緑** | 11.9 |
| foldinter | 送り先を折り直して欄の順を替える | 緑 | **緑** | 11.5 |
| newreaderdeclared | 読み手を足して名簿へ 1 行宣言する | 緑 | **緑** | 11.9 |
| addarg | 呼びに欄を足す(宣言と型の検査も足す) | 緑 | **緑** | 26.9 |

赤 12 形はすべて**狙った構造の assert** が撃った(文言で確認・`mutations.json` の `message`):

- 「名簿の外の読み手が読んでいる」= setv1 / rebind / setv2 / foldcall / consume / let / alias /
  rosterempty / newreader(9 形)
- 「合成の呼びを `<名>` で包んでいる」= wrap(1 形)
- 「名簿の読み手が実際には読んでいない」= dropuse(1 形・`名簿 ['SessionInterject'] 実測 []`)
- rosterempty は名簿を空にしたので stray 側で撃った

## R3. 間違った理由の赤を数えていない証拠(1 件見つけて外した)

`addarg` を最初に「呼びと宣言の引数だけ」足す形で撃つと **5.7 秒で赤**になった。文言を読むと
針ではなく **`defk` の既存の不変条件**(各引数に `{:pre [(: 名 型)]}` の型の検査を要求する)が
撃っていた:

```
E     Fix — add type checks for each parameter:
E       (defk mail-turn-text-of [attachment-count body message-id spec status]
E         {:pre [(: attachment-count SomeType) …
ERROR docs/adr/…_012_agentd_acp_arms.hy - hy.errors.HyMacro…  (1 error during collection)
```

⇒ これは当席の変異が**不正な変更**だったため。型の検査も足した正しい形で撃ち直して **緑**(26.9 秒)。
この 1 件は「狙った違反の検出」に数えていない。

## R4. 部品の速さ(1 分の制約に対する実測)

`hy.read-many` で 1 file を form に読むのは `agentd.hy`(103 の頂点 form)1.5 秒未満・
`judgment.hy`(379 の頂点 form)1.7 秒未満(前の機体での実測)。この機体の焦点 1 本の総時間
11〜30 秒に収まっており、冊の他の 58 本の走行時間を押していない。

## R5. 盲検の記録

`blind-A.md` / `blind-B.md` を参照(起動口・模型・fallback の理由つき)。
