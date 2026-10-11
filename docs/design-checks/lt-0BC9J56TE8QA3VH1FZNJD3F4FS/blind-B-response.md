提示された検をすべて通るのに責務の契約を破る実装を 1 つ作り、写しの木で「検は緑」と「違反の値」の両方を実際に確かめました。

## 1. 機能の要求と差分

**要求(もっともらしい形):** R1 は「戻りの刻が毎回 agora まで届く」、R3 は「ターンの id を持たない知らせを outside に落とさない」を求めています。そこで実装者がこう考える、という想定です。「口座の枠は会話の process の口座の状態で、ターンの物ではない。だから会話ごとに最新の断面を 1 つ重ねて持ち、利用枠切れの事実はそこから作る。そうすれば、ターンの中で知らせが届かなかった時も刻が出る」。

**差分**(`packages/doeff-codex/src/doeff_codex/handler.hy` だけ。全文は `/tmp/blind-B/lt0bc9-account-limits.diff`):

```diff
@@ SessionRuntime.__init__
-    (setv #^ tuple self.late #()))
+    (setv #^ tuple self.late #())
+    (setv #^ tuple self.account-limits #()))
@@ absorb
     (lines.RateLimits)
-    (do (setv log (.running-log runtime))
+    ;; 口座の枠は会話の口座の状態 — 走っているターンの外で届いた知らせも、会話の口座の断面に重ねる(R3: outside に落とさない)。
+    (do (<- account (merged-limits runtime.account-limits record))
+        (setv runtime.account-limits account)
+        (setv log (.running-log runtime))
         (if (is log None) ...                       ; ここから下は元のまま(TurnLog.limits もターンごとに重ね続ける)
     (lines.TurnEnded)
     (do (setv log (.log-of runtime record.turn-id))
-        (<- hit (usage-limit-hit-of record log.limits))
+        (<- hit (usage-limit-hit-of record runtime.account-limits))
```

SessionRuntime の docstring にも `account-limits` の説明を足しました。検の無効化・cast・抑制・動的 import は使っていません。

## 2. 本来の持ち主と、破る契約

- **破る契約(材料 §3・S2):** 「`TurnLog.limits` はターンごと。ターンをまたいで持ち越さない」「ターンの間に届いた枠の知らせは次のターンへ持ち越さない」。
- **codex-handler の責務:** 本来は「ターンの記録ごとに枠の知らせを重ね、終わりに利用枠切れの事実を載せる」です。差分の後、事実の材料は会話の一生にわたる `SessionRuntime.account-limits` になります。これは前のターン・outside・起こし直しの前の process の知らせまで全部重ねた物です。
- **割り当ての判断が抜け落ちる:** 「どの知らせがこのターンの物か」は absorb の振り分け(running-log か outside か)の 1 か所で決まるはずでした。それが「この会話で見た知らせは全部」に置き換わり、振り分けが事実の材料から外れます。`TurnLog.limits` は記録として残るだけで使われません。実測では u1 の `limits=0` なのに、u1 の事実は窓を持っていました。
- **codex-limits の口の契約:** `usage-limit-hit-of` の `limits` は「そのターンで重ねた枠の知らせ」で、名乗られなければ None(刻を発明しない)と決まっています。純粋な関数は変えていませんが、handler が契約と違う引数を渡すので、そのターンでは名乗られていない古い刻が事実として出ます。
- **口座の知識の移動(コードを読んだ推論):** 「口座 = 会話」という前提が codex-handler に入ります。口座と配る判断の持ち主は agora-consumer です。`start-turn`(handler.hy の 431〜441 行目)は、宣言が違って process を起こし直しても同じ SessionRuntime を使い回します。そのため、借りた口座(auth-json)が替わっても前の口座の断面が持ち越されます。

## 3. 提示された検が拒否しない理由

写しの木で 3 file・23 件を走らせ、23 件とも緑でした(実測)。理由はコードを読んだ説明です。

- **test_limits の turn/start の答えの直後の検:** 新しい SessionRuntime に知らせが 1 つ来るだけなので、会話の断面とターンの limits が同じになり、事実も同じです。`outside == #()` も、ターンの中の知らせは今までどおり running-log へ積むので変わりません。
- **test_limits の turn/started の直後の検:** u0 には知らせが無く、u1 の 1 つだけです。`u0.limits == #()` は、ターンごとの limits を今も重ねているのでそのまま通ります。前のターンが知らせを持ち、後のターンの事実を読む筋書きはありません。
- **test_limits の残り 5 件:** 純粋な関数と窓の名の grep で、`limits.hy` も綴りも変えていないので通ります。
- **test_turn_records:** `lines.hy` を触っていません。この検は記録の型の欄と `TurnRecord` の一致だけを見ます。
- **test_scenarios(fake と stub):** 検ごとに FreshThread で新しい SessionRuntime になります。利用枠切れの筋書きは 1 ターンだけで、録った知らせは誤りより前に両方の窓を名乗ります。2 ターンの筋書きには利用枠切れがありません。
- **「答えを待つ側が current-turn を書かない」:** 強制方法は無く、差分も触れていません。
- **agora の結合の検:** 走らせておらず、中身も読んでいません。通るかどうかは言えません。

## 4. 確かめの手順と結果

1. 写しを作ります(済み)。`git -C /home/ubuntu/.worktrees/doeff-wt-dc-lt0bc9 archive HEAD | tar -x -C /tmp/blind-B/doeff` の後、`/tmp/blind-B/lt0bc9-account-limits.diff` を当てて `make sync` します。写しの venv が写しの handler.hy を読むことも確かめました。
2. 検: `uv run --no-sync --frozen --project /tmp/blind-B/doeff pytest <写しの>/packages/doeff-codex/tests/{test_limits,test_turn_records,test_scenarios}.hy -v -p no:cacheprovider` で 23 件が緑でした(実測・log は `/tmp/blind-B/lt0bc9-patched-pytest.log`)。元の木では test_limits と test_turn_records の 8 件が緑でした。
3. 違反: `uv run --no-sync --project <木> hy /tmp/blind-B/lt0bc9-probe.hy` で、読みの順のとおり absorb を呼びます。続きの会話の 2 つの筋書きです。
   - **筋書き 1:** u0 は答え・turn/started・知らせ{codex, 5 時間の窓 100%・戻り 1893456000, 7 日の窓 37%}・error・失敗の終わり。u1 は答え・turn/started・error・失敗の終わりで、知らせはありません。
   - **筋書き 2:** u0 の知らせは{5 時間の窓 40%, 7 日の窓 100%・戻り 1893888000}。u1 の知らせは{5 時間の窓 100%・戻り 1893474000, 7 日の窓は null}。

| | 元の木(契約どおり・実測) | 差分の後(実測) |
|---|---|---|
| 筋書き 1 の u1 | 窓も刻も None(文だけ) | 300 分・1893456000(u0 の古い刻) |
| 筋書き 2 の u1 | 300 分・1893474000 | 10080 分・1893888000(u0 の 7 日の窓) |

adapter の `account-limit-of` は、筋書き 2 の事実を `AgentAccountLimit(window='seven_day', resets_at=1893888000)` に写しました(実測)。

## 5. 確かめていない前提と足りない情報

- **推測:** 本物の codex が、知らせの無い利用枠切れのターンや、窓を名乗らない知らせを出すかは確かめていません。録った usage-limit では、誤りの前に両方の窓を名乗る知らせが来ます。two-turns の録り(偽の上流)では、両方の窓が null の知らせが来ます。ただ、元の検 `test-an-unstated-return-stays-unstated` は知らせの無い場合を想定に入れています。
- **推測:** agora が同じ thread を別の口座で同じ CodexHost に続けるかは読んでいません。doeff-codex 側が SessionRuntime を使い回すことと、adapter が LaunchEffect ごとに資格を引き換えることは、コードで確かめました。
- **推測:** agora が過ぎた刻や違う窓をどう扱うかは読んでいません。過ぎた刻なら、limits.hy の註のとおり「早く戻してまた断られる」と見ていますが、確かめていません。
- **走らせていない物:** agora の結合の検、lint・pyright・semgrep・doeff-hy-check(提示されていないので何も言いません)、元の木の test_scenarios。
- **見えない所:** 提示された検には、前のターンが知らせを持ち、後のターンの事実を読む 2 ターン以上の筋書きがありません。ここが拒否されない所です。
- **調べたが試していない他の候補:** start-turn から mark-running を呼ぶ(材料自身が強制方法は無いと書いている)。ターンの id を省ける記録を TurnRecord に入れず、handler で振り分ける(test_turn_records は必須の欄だけを見る)。

作った物はすべて `/tmp/blind-B/` の下にあります。写しの木 `doeff/`、`lt0bc9-account-limits.diff`、`lt0bc9-probe.hy`、`lt0bc9-adapter.hy`、log 2 つです。前から在った file には触れていません。

WAIT: work — 呼び手の次の指示
