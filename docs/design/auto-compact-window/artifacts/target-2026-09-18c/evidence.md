# 検証記録 — 会話の圧縮の閾値

対象版: doeff origin/main `14f1a783`(snapshot-cfe2da070c4f)から切った worktree
`doeff-wt-autocompact-window`。実測はすべて 2026-09-18 の会社 Mac(CA-20038667)で、
実際のコマンドと出力に基づく。

**版 b** — E1〜E4 は版 a(`../target-2026-09-18/`)に封じたまま参照し、E5 以降をここに足す。

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

封じたログ: `../target-2026-09-18/evidence/e1-cli-autocompact-range.log`(`200000` は受理され、argv 解釈を通って
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

封じたログ: `../target-2026-09-18/evidence/e2-running-seats-declare-nothing.log`。

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

封じたログ: `../target-2026-09-18/evidence/e3-fable-cost-by-model-and-profile.log`(model / profile 別)・
`../target-2026-09-18/evidence/e3-context-length-distribution.log`(文脈長と出力長の分布・作業場別・席別)・
`../target-2026-09-18/evidence/e3-daily-by-model.log`(日次)。

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

封じたログ: `../target-2026-09-18/evidence/e4-autocompact-derivation.log`。

**結論**: 契約 `autocompact-derivation` の不変条件(戻りは auto か 100k〜1M の 10 進整数のみ)は、
少なくともこの母集団で成立。

## E5 — 追加した焦点の検(実行済み・成立)

`packages/doeff-agents` の worktree で、変更した箇所とその依存先だけを走らせた
(受付の門が `focused — 4 本(焦点走 — 許可不要)` と名乗っている)。

| 走らせたもの | 結果 |
| --- | --- |
| argv の検(impls・`-k "autocompact or threshold or argv_golden or golden_wiring or hooks_inherit"`) | **12 passed** / 2,288 deselected |
| 蘇生の腕の名簿の検(acp rehydrate・`-k "compaction_threshold or carries_the_attachment"`) | **4 passed** / 2,296 deselected |

固定した主張:
- `test_claude_argv_always_declares_the_compaction_threshold` — 旗が常に載り、値は床 400000。
  凍結接頭 4 語が同じで、`--effort` が index 4 のまま。`--model` の後・`--mcp-config` の前。
- `test_claude_argv_carries_the_threshold_the_conversation_declared` — charter の 200000 が通る。
- `test_claude_argv_never_carries_a_threshold_that_kills_the_turn` — 50000 / 2000000 / "nope" /
  True / {} / -1 の 6 通りが全部 `auto` に縮退する。
- `test_claude_resume_argv_declares_the_threshold_too` — resume の argv にも載り、末尾は `--resume`。
- `test_every_arm_that_wakes_the_seat_carries_the_compaction_threshold` — resume の params が
  名簿を通って閾値を運ぶ。宣言の無い手番では欄を作らない。

封じたログ: `evidence/e5-focused-tests.log`。

**結論**: 実装は `autocompact-derivation` と `launch-argv` の不変条件を守っている。

## E6 — S13 適合検査は本線でも落ちる(実行済み・この変更の所為ではない)

`conformance/test_s13_argv_wiring.py` は worktree で 3 本とも落ちるが、**この変更が 1 byte も
触っていない codex の検も同じ理由で落ちる**。本線(`~/repos/doeff` の checkout・この変更なし)でも
同じ 1 本が同じ理由で落ちる。原因は harness が agentd の unix socket に繋げないこと
(`FileNotFoundError` at `io_handlers.hy:108`)で、環境側の不足。

封じたログ: `evidence/e6-conformance-baseline.log`。

**結論**: 本変更による退行ではない。ただし **R6(凍結接頭と `--effort` の位置)の適合を
実際に起こした process の argv で確かめてはいない** — 確認できたのは E5 の単体側の位置 pin まで。

## E7 — 反例(成立): CLI 側の閾値が、既存の `compactAt` を撃たなくする

この系には**既にもう 1 つの圧縮の仕組み**がある。会話が `status.agent.compactAt`
(文脈の使用率 % ・`ai conv open --agent compactAt=<0..100>` で宣言・閉語彙は
model / profile / effort / compactAt の 4 欄)を宣言すると、agentd は直前の手番の使用率が
それ以上の時、温かい session を片付けて**記録の service の履歴から再開**する
(段 10f 便 2・agora-redesign #82・operator 2026-09-14「that routing agent should compact
itself with some threshold」・計器 `agentd_compactions_total`)。

使用率の分母は実コード `judgment.context-percent-of` が `result.modelUsage[model].contextWindow`、
つまり**モデルの窓**(1M)で、畳む閾値ではない。実際に両方の純関数を呼んだ結果:

| tokens | 窓 | 使用率 | compactAt=70 | compactAt=80 | compactAt=35 |
| --- | --- | --- | --- | --- | --- |
| 550,000(観測した平均) | 1,000,000 | 55% | 撃たない | 撃たない | 撃つ |
| 967,000(観測した最大) | 1,000,000 | 96% | **撃つ** | **撃つ** | 撃つ |
| 400,000(段 1 の床の後) | 1,000,000 | 40% | 撃たない | 撃たない | 撃つ |

封じたログ: `evidence/e7-compactat-is-shadowed.log`。

**結論**: 段 1 の床(400k)を入れると使用率が約 40% で頭打ちになり、**40 より上の `compactAt` を
宣言した会話では agentd 側の圧縮が二度と撃たなくなる**(ACP の検が使っている宣言値は 70 と 80)。
これは出荷済みで operator が要求した機能の効き方を変えるので、設計の判断として operator へ戻す。

## 未実行 / 未確認

- **U1**: 解消(E5)。最初の 1 回は `-p no:randomly` を付けたために受付の門で止められた
  (門は正しく働いた)。`.hy` の deftest は `tests/test_*.py` の shim 経由で pytest が拾う。
- **U2**: 実際の agentd を入れ替えて席を起こし、`ps` に `--autocompact` が出ることを見ていない。
  配備(uv tool の再導入 + 常駐の入れ替え)が要る。**未実行。**
- **U5**: E7 の反例の解き方(床が `compactAt` を覆うことを受け入れるか、宣言のある会話では
  CLI 側を `auto` にするか)は operator の判断待ち。**未決。**
- **U6**: ACP の control plane(`acp-control.taildd050.ts.net:8868`)へこの機体から繋がらず、
  現に `compactAt` を宣言している会話が何本あるかを数えていない。E7 は仕組みの成立を
  実コードで示したもので、影響する会話の本数は**未確認**。
- **U3**: 閾値を下げたことで畳む回数がどれだけ増え、要約のコストがどれだけ乗るかは未測定。
  400k / 200k は実測前の初期値。
- **U4**: 段 2(役ごとの値の宣言)は未着手。CC1 の「agentd の code に役の語が現れない」という
  主張は、段 2 の実装が無い現時点では構造上の主張にとどまる。

U1 が済むまで「実装が契約を守っている」とは名乗れない。U2 が済むまで「本番で効いている」とは
名乗れない。設計の成立(target design)は U1 / U2 に依存しない。
