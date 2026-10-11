Opus 5.5(claude-opus-5-5)

# 責務分離の失敗例: codex の長い命令の出力を、終わる前から道具カードへ流す

結論から書きます。設計の見立てでは lines と adapter(と公開契約の拡張)だけで済むはずの機能追加で、handler(process とターンの列の責務)も変えなければなりません。原因は、lines が持つべき「どの記録がターンを名乗るか」という知識が、handler の中に class の列挙として漏れていることです。これは本物の codex 0.162.1 の行で確かめました。

同じ調べの途中で、今の変更そのものが S3 の主張どおりに動かない場面も実測しました(後ろで走る命令の結果が出来事に届かない。最後の「副次の実測」に書きます)。

## 1. 変更要求と、それが要る理由

- **要求**: codex の担当が走らせる長い命令(ビルド・検・dev server)の出力を、命令が終わる前から道具カードに出す。
- **理由(実測)**: codex 0.162.1 は `item/commandExecution/outputDelta` を出します。params は threadId・turnId・itemId・delta です。
  - schema に在ることを確かめ、本物の binary でも録りました。
  - 今の写しでは、出力は item/completed の aggregatedOutput が届くまで画面に出ません。長い命令の間、カードは空のままです。

## 2. 関係する主張・前提と、予想に反して変わるもの

**前提との関係**
- S1 の前提は「道具は item/started・item/completed の item で届く」です。outputDelta は item ではなく、item に付く通知です。
- したがって、この例は **S1 の契約の適用範囲外**で、S1 を反証しません。
- 反するのは次の 3 つです(どれも item に限った前提を置いていません)。
  - 責務表の handler の行(持つ知識 = process・読み手の thread・ターンごとの列)
  - S1・S3 で handler を「変わらない」側に置いた見立て
  - handler の不変条件「ターンを名乗る記録は名乗ったターンの列へ積む」

**変わると予想どおりのもの**
- lines: 新しい記録と method の枝を足し、`CodexLine` の和に加える。
- adapter: 写しを足す。
- events と consumer: 新しい出来事の型(例 `AgentToolOutputDeltaEvent`)と、その表示。

**予想に反して変わるもの**
- handler の `absorb`(`packages/doeff-codex/src/doeff_codex/handler.hy` 150 行あたり)。
- 後ろで走る命令の場合は、handler と adapter が共同で守る不変条件と、層 2 の公開 effect `CodexReadTurnEvents`(ターンごとに読む形)にも波及します。

## 3. 漏れる知識と、4 つの区別

**隠すはずの知識の漏洩(handler)**

`absorb` は、ターンを名乗る記録を lines の class の名で列挙しています。

```hy
(| (lines.TurnStarted) (lines.TextDelta) (lines.ReasoningDelta) (lines.AgentMessageDone) (lines.ItemStarted) (lines.ItemDone)
   (lines.TokenUsage) (lines.TurnError))
(.add-event runtime (.log-of runtime record.turn-id) record)
...
_ (do (setv log (.running-log runtime)) (if (is log None) (setv runtime.outside ...) ...))
```

- 「ターンを名乗るか」は codex の params の形から決まる性質で、lines の知識です。
- 列に無い新しい記録は、型の誤りにも検の失敗にもならず、黙って `_` に落ちます。そこでは「走っているターン、無ければ outside」という別の意味で扱われます。
- 配線(新しい部品の登録)と見なさない訳:
  - handler の公開 effect も、handler の責務も変わらない。
  - 列から漏れても止まらず、別の意味で動く。
  - lines が `turn-id-of` のような口を持てば handler は変わらずに済む。つまり避けられる結合です。
- 一方で、`absorb` を「新しい記録ごとに直す振り分けの表」と読む立場もあり得ます。その場合も、S1・S3 と責務表は handler を「変わる」側に書く必要があり、直すことを強いる検査も無い、という欠陥は残ります。

**意図した公開契約の拡張(欠陥ではない)**
- events に新しい出来事を足すことと、consumer の表示。
- R2 のための録りと替え玉の目印の追加(doubles の公開の口の拡張)。

**共同の不変条件(書かれていない前提)**
- handler はターンごとに積み、adapter は `session.turn` だけを読みます(`pull` → `CodexReadTurnEvents turn …`)。終わりを読んだら `close-turn` で `session.turn` を None にし、そのターンをもう読みません。
- 「道具の記録は、そのターンの終わりより前に届く」という前提に頼っていますが、前提の節に書かれていません。
- 実測では、後ろで走る命令の記録は前のターンを名乗ったまま次のターンの間に届きます。

## 4. 最小の入力・変更例と再現(実測)

**実験の材料**
- 対象: doeff の af7bd4857 から切った、自分専用の detached な作業木。
- 録り: 本物の codex 0.162.1(README と同じ sha256)を、`record_app_server_lines.py` の偽の上流につないだ。筋書きを作業木の中で 2 つ足した。
  - (a) slow-command: `for i in 1 2 3; do echo line-$i; sleep 0.7; done`
    - 録れた行: T1 の item/started(call_slow)→ outputDelta × 2(T1)→ item/completed(T1)
  - (b) background-command: T1 で `sleep 1; echo late-1; …; echo late-3` を `yield_time_ms: 250` で走らせる。
    - 録れた行: T1 は item/started だけで終わる。T2 の間に、T1 を名乗る outputDelta × 3 と item/completed(call_bg)が届く。

**変更**
- 設計どおり lines だけを変えた(17 行)。`CommandOutputDelta`(thread-id・turn-id・item-id・text)を足し、`"item/commandExecution/outputDelta"` の枝(既存の `DeltaParamsWire` を使う)と `CodexLine` の和を足した。

**手順**
- 録った行を `classify-line` で分類し、順に `absorb` で `SessionRuntime` に積んだ。
- 2 つの時機で積んだ。
  - (i) turn/start の答えを積んだ直後に current-turn を置く(handler が遅れずに追いつく時)。
  - (ii) 全部を積み終えるまで置かない(替え玉や S3 の「答えの直後に一気に届く」時)。

**観測した結果**

| 場合 | handler そのまま | handler の列に 1 class 足す |
|---|---|---|
| slow (i) | 出力の途中 → T1 の列(`_` を通って、たまたま正しい) | T1 |
| slow (ii) | 出力の途中 → outside(落ちる)。item/completed だけ T1 | T1 |
| background (i) | T1 を名乗る出力の途中 → **T2 の列**(別のターンの物として出る) | T1 の終わりの後(adapter はもう読まない) |

- handler を直しても、後ろで走る命令の分は出ません。adapter と handler の共同の変更が要ります。
- 見るべき点: 記録がどのターンの列に、そのターンの終わりの前と後のどちらに積まれたか。

## 5. 未確認の前提と、不足する情報

**実測したこと**
- outputDelta と、前のターンを名乗る遅れた item/completed の存在。
- この設定で codex が提示する道具の列。exec_command・web_search など 9 個で、update_plan と apply_patch は無い。
  - そのため、最初に考えた候補「計画(turn/plan/updated)を道具カードに出す」は今の版では起き得ないので捨てました。

**推測であること**
- 本番の model がどれほど命令を後ろで走らせるか(yield の既定値は未確認)。
- slow (i) の形では、本番だと handler を直し忘れても目立ちにくいこと。outputDelta は model の答えの後に来るためです(録りでは答えから最初の出力の途中まで 0.5 秒以上)。
- consumer(別の repo の absorb-event)が、結果の無い呼びや前のターンの結果をどう扱うか。読んでいません。
- 偽の上流(gpt-mock など)と本物の上流で、道具の列や挙動が違う可能性。

**していないこと**
- pre-commit・doeff-hy-check・全体の検。
- S1(mcpToolCall)は code を読んだ範囲では lines と adapter で閉じ、R2 のための doubles の追加が加わるだけでした。反例は見つかっていません(合格とは判断しません)。

## 副次の実測

**1. 今の変更で S3 の「取り落とさない」が崩れる(前提の範囲内)**
- 替え玉に (b) の本物の行を流させ、公開 effect だけで読みました。lines と handler は af7bd4857 のままです。
- 結果: `AgentToolUseEvent(call_bg)` は届くが、`AgentToolResultEvent(call_bg)` は T1 にも T2 にも届かない。3 回とも同じでした。
- 原因: handler は名乗った T1 の列へ正しく積みますが、adapter は T1 を読み終えています。
- 画面には、呼んだまま結果の来ない道具カードが残るはずです(表示は推測)。

**2. 名指した検は、変更の前から不安定**
- 対象: `test_headless_codex_usage_limit_carries_the_account_limit_stub` と `test_the_public_codex_handlers_carry_the_account_limit_stub`。
- 素の af7bd4857 で 24 回走らせて 9 回落ちました。落ち方は account_limit の window と resets_at が None です。
- `_` の枝に記録を書き出す仕掛けを入れて 16 回走らせると 5 回落ち、落ちた回と、`RateLimits` が `current-turn=None` のまま outside に積まれた回が、回ごとに一致しました。
- 原因は、turn/start の答えから current-turn を置くまでの間の取りこぼしです。今回の変更が入れた物でも、知識の漏洩でもありません。

## 実験の置き場と、制限からの逸脱

- 実験の差分は `/tmp/ki77e7-blind-A/saved/full.patch` です(lines・handler・検・替え玉・録りの script)。
- 録った行は `/tmp/ki77e7-blind-A/rec-slow/` と `/tmp/ki77e7-blind-A/rec-bg/`、codex の schema は `/tmp/ki77e7-blind-A/schema/` に在ります。
- 作業木は `/tmp` の下に作れませんでした(この環境の検査が止めた)。代わりに `/work/toolset/worktrees/doeff-wt-ki77e7-blind-A` に detached で作りました。
- 作業木の削除も検査に止められました。中の変更は全部戻し、af7bd4857 のきれいな状態で残っています。回収の手順(`ai worktree reap`)は、他の作業木にも及ぶので走らせていません。
- 材料に無い実験として、codex の binary を走らせました(schema の出力と録り)。出力はどれも `/tmp/ki77e7-blind-A` の下です。
- `worktree add` の出力に、HEAD の commit の件名が 1 行出ました。git log や commit の本文は読んでいません。
