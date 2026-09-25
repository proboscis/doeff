反例が見つかりました。「`turn-record-ended-status` に、手番の終わりにだけ数える消費の材料を 1 つ足す」変更です。主張では変わるのは W か J だけのはずですが、要件 1(巡回は消費を書かない)を新しい材料にも保つには M1 と M1c も変える必要があります。M1 を変えなければ、巡回がその材料を書いても M1 は緑のままでした(実測)。

## 1. 変更要求と、それが必要になる理由

- **要求:** 手番の記録に model ごとの消費 `status.modelUsage` を置く。J に `[model-usage None]` を足し、手番の終わりの書き込み(`end-turn-record`)がそれを渡す。
- **理由:** Claude の result 行には `modelUsage` があり、本体の model と CLI の下読み(haiku)が並びます(judgment.hy 6981 行の註。註を読んだだけで、実物は見ていません)。単価は model ごとに違うので、合計の usage だけでは週ごとの費用を合算できません。
- **現実性:** J の引数はこの 5 日で 3 回増えています。`git log -S` で確かめました。
  - cache-observation(2bcc4a40・09-21)
  - responses(7d5e89f1・09-23)
  - conditions(f3858b94・09-25)
- **材料の出所:** usage・responses と同じく `turn-batch-of`(agentd.hy 3072 行)が `job.start-offset` から読みます。手番の開始 offset は memory にしかないので、巡回はこの材料を持っていません。

## 2. 元の主張・前提と、予想に反して変わるもの

**前提は守っています。**
- 巡回は `turn-record-ended-status` の呼びで status を組み、`AcpPutStatus :row record :status ended` で書きます。
- 機体・保存・分散・並行・模擬の軸は使っていません。
- この変更は、主張が「M1 を変えずに緑」と言っている類そのもの(呼び先が既定値つきの引数を足す)です。

**実際に変わるもの:**

| 対象 | 必要な変更 |
| --- | --- |
| J | 引数と欄を足す(想定内) |
| W | `end-turn-record` と `turn-batch-of` が材料を組んで渡す(想定内) |
| **M1** | `SWEEP-FORBIDDEN-MATERIAL`(ADR ファイル 646 行)に `"model_usage"` を足す。足さないと、新しい材料を巡回が書いても赤にならない |
| **M1c** | `SWEEP-ENDED-PARAMS`(709 行)と、引数の並びを文字列で持つ例を直し、違反例を 1 つ足す。M1 に役を足した途端、並びが古いままの作り物の呼び先が全部「呼び先の定義に役 model_usage が無い」で赤になるため(実測) |

逆向きの結びつきもあります。M1 だけを先に直すと、J に役がないので赤になります(実測)。つまり J・M1・M1c は同じ commit で一緒に変える必要があります。

## 3. どの知識がどこへ漏れているか

- **公開契約の拡張(正当なもの):** J の公開の口である「引数の並び」が 1 つ増えるだけです。
- **M1 の公開の口:** `sweep-violations ended-roles get-roles put-roles sweep-body` は変わりません。M1 の契約拡張ではありません。
- **配線の変更:** ありません。検査 1 の 1881〜1885 行の呼び方は同じです。
- **共同の不変条件:** 「巡回は手番の memory から来る材料を渡さない」という条件が、J の役の集合と M1 の禁止表の 2 か所にまたがっています。両者の対応を確かめる検査はありません。
- **漏洩している知識:** 「どの役が、開始 offset(memory)から数える材料か」という分類です。
  - この分類は本来、材料の出所を知る W(`end-turn-record` / `turn-batch-of`)と、引数の意味を書く J の側の知識です。ところが判定の唯一の置き場は、M1 の中の役の名の表になっています。
  - J の docstring の語だけでは分類が決まりません。conditions と responses はどちらも「終わりの書きでだけ置く」材料です。それでも conditions は対の行から読めるので巡回が渡してよく、responses は駄目です。
  - 同じ規則が要る書き手がもう 1 つあります。退役の書き込み `end-retired-records`(agentd.hy 4967 行)です。ここは M1 の検出範囲の外です。
- **失敗の向き:**
  - M1 は禁止表なので、表にない役には何の制約もかかりません。分類されていない役は黙って緑になります。
  - 旧来の字面一致は閉じ括弧まで照合していたので、引数を足せば必ず赤でした。今回の変更で偽陽性は消えましたが、J の新しい役に対しては偽陰性の側に倒れています。
- **既に 1 件ある例:** cache-observation です。
  - `turn-batch-of` で `job.start-offset`・`job.request-start-lower-bound-ms`・`job.materials-cover-the-turn`(すべて memory)から組む材料ですが、禁止表に入っていません。
  - 巡回が `:cache-observation x` を渡しても緑になります(実測)。
  - R49 の条文は usage しか名指さないので、これが違反かどうかは所有者が決める必要があります。今はその判断を、表に書いていないことで M1 が黙って下しています。

## 4. 最小の再現手順と観測結果

作業樹 /home/kento/.worktrees/doeff-wt-639-adr012-blind-a(b7f836b0)で行いました。

1. **作り物の巡回で試す(ソースは変更しない):** `uv run python /tmp/blind-a-probe.py` を実行。
   - 今日の並びで `:cache-observation session-cache` を渡す → 緑
   - `[model-usage None]` を足した並びで `:model-usage session-model-usage` を渡す → 緑
   - 対照として、usage を渡す → 「巡回が usage に usage-total を渡している」で赤
2. **本物のソースに将来の変更を入れる:**
   - judgment.hy 4945 行に `[model-usage None]`・`:pre`・`modelUsage` の欄を足す。
   - agentd.hy 5332 行の呼びを `(<- swept-model-usage (| dict None) (model-usage-of-session record))` と `... pair-conditions :model-usage swept-model-usage` に変える。
   - `model-usage-of-session` は存在しない名前です。静的な検査は form を読むだけで、実行はしません。
   - 指定のコマンドで 2 本とも PASSED(`2 passed in 8.39s`)。ここが、巡回が消費を書いているのに赤にならない状態です。
3. **M1 の表に `"model_usage"` を足す:**
   - 本物の巡回は「巡回が model_usage に swept-model-usage を渡している(R49 — model ごとの消費は手番の終わりの 1 回)」で赤。
   - 巡回を元に戻すと、検査 1 は PASSED。検査 2 は FAILED で「正常例 今日の呼び: 期待 () ・実際 ['呼び先の定義に役 model_usage が無い(契約の変更 — 検査も直す)']」。
4. **J・W を元の版に戻し、M1 の表の追加だけ残す:** 「呼び先の定義に役 model_usage が無い」で赤。

作業樹はすべて HEAD の内容へ戻してあり、`git status` は空です。commit・stash はしていません。/tmp には probe と、途中で退避した `.future.hy` のコピーとログが残っています。

## 5. 未確認の前提・不足する情報

- **実測したもの:** 上記の probe 出力、名指しで走らせた 2 本の pytest の結果、`git log -S` の日付。初回の実行は .venv の作成込みで 224 秒かかりました。
- **推測のもの:**
  - model ごとの消費を記録するという要求と、その動機(週ごとの費用の合算)は私が置いた設定です。
  - Claude CLI の `modelUsage` が手番単位か session の累積かは確かめていません。反例自体は、材料を usage と同じく `job.start-offset` から数えるという設計上の仮定に依っています。
- **解釈に依るもの:** R49 の「usage」に model ごとの消費が含まれるかどうか。私は「消費の和は手番の終わりの 1 回だけ」という理由から含むと読みました。所有者が含まないと決めるなら、この反例は「R49 の適用範囲を M1 の表が決めてしまっている」という指摘に縮みます。
- **走らせていないもの:** 振る舞いの検査 6 本。うち `sessionhost_acp_turn_events_deftests.hy` の 1186 行が `"usage"` の綴りしか見ないことは、読んで確かめただけです。全数の検査もしていません。
- **合格・安全の結論は出していません。** 探索したのは J への役の追加という 1 方向だけです。ほかにも、M1 が鍵を作る関数の名 `turn-record-key-of` と、effect の欄の名 `row` / `key` を綴りで固定している点に気づきましたが、反例としては組み立てていません。
