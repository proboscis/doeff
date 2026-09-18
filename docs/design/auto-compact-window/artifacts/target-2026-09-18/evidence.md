# 検証記録 — 会話の圧縮の閾値

対象版: doeff origin/main `14f1a783`(snapshot-cfe2da070c4f)。
実測はすべて 2026-09-18 の会社 Mac(CA-20038667)で、実際のコマンドと出力に基づく。

## E1 — CLI が受ける閾値の幅と、外れた時の失敗(実行済み・成立)

主張: `--autocompact` は `auto` か 100k〜1M しか受けず、外れると argv 解釈の段で死ぬ。

```
$ claude --autocompact bogus -p "hi"
error: option '--autocompact <auto|tokens>' argument 'bogus' is invalid.
       It must be 'auto', or between 100k and 1M (e.g. 500k, 200000, or 200 as shorthand)
$ claude --autocompact 50k -p "hi"
error: ... It must be 'auto', or between 100k and 1M ...
```

CLI 2.1.274 の実体から読んだ解決の順は env → settings → clientdata → experiment → model-default で、
実効の窓 = min(指定値, モデルの窓)、閾値 = 実効の窓 − 要約バッファ。
`CLAUDE_CODE_DISABLE_1M_CONTEXT` だけでは窓は縮まらない(CLI 自身が
「強制するなら `CLAUDE_CODE_AUTO_COMPACT_WINDOW` を設定しろ」と言う)。

封じたログ: `evidence/e1-cli-autocompact-range.log`(`200000` は受理され、argv 解釈を通って
本体まで進んだことも同じログに残っている)。

**結論**: R2 は成立。幅の関門を argv を組む側に置く根拠になる。

## E2 — いま起きている席は閾値を 1 つも名乗っていない(実行済み・成立)

```
$ ps -eo pid,etime,command | grep 'bin/claude|claude ' → 49 本
   --autocompact を持つ本数: 0 / 49
$ 起動元を辿る:
   claude -p --input-format stream-json ... --effort xhigh --model claude-opus-5 --session-id ...
     └ 親 pid 14230 = doeff-sessionhost join --config ~/.local/state/doeff/acp-agentd/agentd.toml
```

dotfiles 側の別の走行係(`agentcli/headless.py` `AUTOCOMPACT_DEFAULT_TOKENS = 600_000`)は
必ず名乗るのに、ACP の agentd を通る席は名乗らない。**判断が 2 か所に割れている**。

封じたログ: `evidence/e2-running-seats-declare-nothing.log`。

**結論**: observed design の中心の主張は成立。

## E3 — 文脈の大きさが枠の消費を支配している(実行済み・成立)

全 profile の会話記録 3.68 GB を走査し、`message.id` で dedup した 119,085 手番を集計
(集計器と出力は `/tmp/fable-audit/` — scan.py / agg.py / deep.py / ts.py / daily.py)。
擬似コスト u は API 価格換算(in 15 / cache-write 18.75 / cache-read 1.5 / out 75 per Mtok)。

| 見たもの | 値 |
| --- | --- |
| 直近 24h の Fable | 15,996u / 15,783 手番 |
| 費用の内訳 | cache_read **77%** / cache_write 13% / output 10% |
| 1 手番の平均の文脈 | **550k** token |
| 600k 超の手番 | 手番の 42% ・費用の **59%** |
| 観測した最大の文脈 | **967k** token(3,005 手番が 800k 超) |
| 日次の Fable | 09-13 47u → 09-15 4,697u → 09-16 13,913u → 09-17 **16,976u** |
| 同期間の Opus | 09-15 13,359u → 09-17 1,608u(担い手の入れ替え) |

cache_write は **100% が 1 時間 TTL**(5 分 TTL は 0)で、書き込み単価が高い側。

封じたログ: `evidence/e3-fable-cost-by-model-and-profile.log`(model / profile 別)・
`evidence/e3-context-length-distribution.log`(文脈長と出力長の分布・作業場別・席別)・
`evidence/e3-daily-by-model.log`(日次)。

**結論**: R1 は成立。閾値を下げれば 1 手番の値段が下がるという前提が数で裏づいた。

## E4 — 閾値の導出(claude-autocompact-value)の入力ごとの戻り(実行済み・成立)

実装した純関数を実際に呼んだ結果(worktree `doeff-wt-autocompact-window`):

| 入力 | 戻り | 意味 |
| --- | --- | --- |
| 欄が無い | `400000` | 床 |
| `200000`(int) | `200000` | 宣言を通す |
| `"200000"`(str) | `200000` | JSON の文字列も通す |
| `"auto"` / `"AUTO"` | `auto` | 大小を問わない |
| `50000` / `2000000` | `auto` | 幅の外は縮退 |
| `400000.0`(float) | `400000` | 整数になる float は通す |
| `True`(bool) | `auto` | bool を数として読まない |
| `"nope"` | `auto` | 読めない値は縮退 |
| `""`(空文字) | `400000` | 宣言なしと同じ |

封じたログ: `evidence/e4-autocompact-derivation.log`。

**結論**: 契約 `autocompact-derivation` の不変条件(戻りは auto か 100k〜1M の 10 進整数のみ)は、
少なくともこの母集団で成立。

## 未実行 / 未確認

- **U1**: 追加した deftest 群(argv に必ず載る・charter の値が通る・幅の外は auto・resume でも載る)を
  pytest の口から走らせていない。この repo の `.hy` deftest は pytest の収集口が要り、
  焦点で走らせようとした 1 回は `-p no:randomly` を使ったために受付の門で止められた
  (門は正しく働いた — 全数・広範囲の走行は許可の札が要る)。**この主張は未実行。**
- **U2**: 実際の agentd を入れ替えて席を起こし、`ps` に `--autocompact` が出ることを見ていない。
  配備(uv tool の再導入 + 常駐の入れ替え)が要る。
- **U3**: 閾値を下げたことで畳む回数がどれだけ増え、要約のコストがどれだけ乗るかは未測定。
  400k / 200k は実測前の初期値。
- **U4**: 段 2(役ごとの値の宣言)は未着手。CC1 の「agentd の code に役の語が現れない」という
  主張は、段 2 の実装が無い現時点では構造上の主張にとどまる。

U1 が済むまで「実装が契約を守っている」とは名乗れない。U2 が済むまで「本番で効いている」とは
名乗れない。設計の成立(target design)は U1 / U2 に依存しない。
