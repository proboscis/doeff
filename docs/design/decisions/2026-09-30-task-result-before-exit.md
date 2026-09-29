# task の結果を、子プロセスが終わる前に coordinator へ直接届ける

- 決定日時: 2026-09-30(JST)
- 決定担当: Claude(この変更の実装担当・personal profile)
- 根拠: proboscis/agora-redesign #1387(親 #989 の 3 番目)の「直しの向き」に書かれた推奨を採った。戻せる決定なので、実装担当が決めて記録する。

## 問題

doeff-cluster の task は次の順で結果を運んでいた。

1. worker が起動した子プロセスが task を実行し、結果を worker のディスク(`<task-dir>/<id>.result`)に書いて終了コード 0 で終わる。
2. worker が次の heartbeat の状態報告に結果を載せて coordinator へ送る。

1 と 2 の間(heartbeat の間隔は 0.5 秒)に worker が死ぬと、結果は死んだ worker のディスクに残ったまま coordinator に届かない。

| task の種類 | 同じ名前の worker が起動し直した後に起きること |
|---|---|
| 呼び出し側が待つ task(`RemoteJob`) | coordinator は task をまだ実行中と見ており、新しい worker に同じ task を渡し直す。成功済みの task が 2 回実行される |
| 切り離した task(`SubmitDetached`) | 置いた時の worker の世代にしか渡さないので再実行はしないが、lease が切れて「失われた」扱いになる。成功した結果が呼び出し側に届かない |

## 決定

```
子プロセス: 実行 → 結果を file に書く → POST /tasks/<id>/result → 終了コード 0
                                            │
                                            ▼
coordinator: task が実行中で、送り手がその task を置いた worker なら「結果つきで終了」に進める
             既に終わっている task への 2 回目の結果は何も変えずに 200 を返す(冪等)

届かなかった時だけ: worker が file を読み、次の heartbeat で運ぶ(従来の経路)
```

1. 子プロセス(`job_entry` の task の入口)は、結果を file に書いた後、終了する前に coordinator の `POST /tasks/<id>/result` へ結果を送る。本文は `{worker, instance, result, format}`。要求の形は `report_client.task-result-request` の 1 か所で作り、本番の子プロセスと手元のシミュレーション(`local.hy`)が同じ関数を使う。
2. coordinator(`cluster_policy.absorb-task-result`)は次のように答える。
   - task が実行中で、送り手がその task を置いた worker: 結果つきで終了させる(切り離した task も同じ)。200。
   - task が既に終わっている: 状態を変えずに 200(heartbeat が先に運んだ・2 回目の送信)。
   - 別の worker に置いた task: 409(古い送り手)。
   - 知らない task(呼び出し側が取り下げた・lease 切れ): 404。
   coordinator は状態をディスクに書いてから返事をするので、200 を受けた結果は coordinator が落ちても失われない。
3. heartbeat が運ぶ結果は、既に終わった task に対しては何もしない(従来の取り込みは実行中の task にだけ効く)。したがって、直接届けた結果と heartbeat の結果が両方届いても、task は 1 回だけ終わる。
4. 子プロセスの送信は、接続の段階の再送(`CoordinatorEndpoint` の既定)だけにして、長い再送の繰り返しはしない。

## 理由

- 結果の運び手を worker の heartbeat だけにすると、「子プロセスの終了」と「heartbeat」の間の窓が必ず残る。子プロセス自身が終了前に届ければ、終了コード 0 の時点で結果は coordinator にある。
- file と heartbeat の経路を残すので、古い coordinator(この口を知らず 404 を返す)や、送信の瞬間だけ coordinator に届かない場合でも、従来と同じ動きになる。
- 送信を短く保つ理由: 子プロセスが coordinator への再送で長く止まっていると、worker が連絡の途絶(20 秒)を理由にその子プロセスを止めてしまい、file に書いた結果も「正常終了した task の結果」として運ばれなくなる。直接の送信が失敗したら、すぐ終わって従来の経路に任せる方が安全。

## 採らなかった案

| 案 | 採らなかった理由 |
|---|---|
| worker が子プロセスの終了を見た直後に、次の heartbeat を待たず報告を送る | worker が子プロセスの終了を観測する前に死ぬ窓が残る。窓を狭めるだけで閉じない |
| 呼び出し側の task も置いた worker の世代に固定し、起動し直した worker に渡し直さない | 2 回目の実行は防げるが、結果は失われたまま(呼び出し側は失敗を受ける)。根の「結果が届かない」を直さない |
| 結果をディスクではなく共有の記憶領域に書く | 新しい保存先と権限が要り、影響が大きい。coordinator は既に書いてから返事をする保存を持っている |

## 残る窓

- 子プロセスが結果を送っている最中(coordinator の返事を受ける前)に worker が死ぬと、従来どおり結果は届かない。この場合 task はまだ終了していない(終了コード 0 の前)ので、「実行中に worker が死んだ」扱いと同じになる。
- 送信の瞬間に coordinator へ届かず、しかも次の heartbeat の前に worker が死んだ場合は、従来と同じく結果が失われる。

## 確かめ

- `packages/doeff-cluster/tests/test_task_result_window.hy`: task の子プロセスが終了コード 0 で終わった時刻(次の heartbeat の前)に worker を殺し、同じ名前で起動し直す。呼び出し側が待つ task は 1 回だけ実行されて答えが届き、切り離した task も答えを失わない。直す前はそれぞれ 2 回実行・「失われた」になって失敗する。
- `packages/doeff-cluster/tests/test_task_result_delivery.hy`: coordinator の受け方(実行中なら終了させる・終わった task は変えない・別の worker は 409・知らない task は 404)と、子プロセスの送信(送った本文・届かない時は file の経路に任せる)。

## 戻す手順

この変更の commit を `git revert` する。子プロセスは送信をやめ、coordinator の口は消える。file と heartbeat の経路は変えていないので、戻した後も従来どおり動く(直した窓が再び開くだけ)。新しい子プロセスと古い coordinator、古い子プロセスと新しい coordinator の組み合わせも、404 か「送らない」になるだけで壊れない。
