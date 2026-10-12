失敗例を 1 つ具体化し、提示された検査がすべて通ることと責務違反が起きることを、同じ変更を当てた作業木で実測しました。作業木は元に戻してあり、commit・push はしていません。

## 1. 機能要求とコード

**要求(ありそうな形)**: 「NTP の step で壁の時計が進むだけで job が止まる偽の自己停止をなくしたい。この設計が範囲の外に残した壁の時計の比べ(子の停止の猶予・準備の作り直し・backoff)も step から守りたい。入口で worker の中の壁の時計を『起動の刻 + 単調な時計の経過』に替える」

新しい file `packages/doeff-cluster/src/doeff_cluster/worker/protocol/steady_wall.hy`:

```hy
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_time [GetMonotonic GetTimeEffect])
(import doeff_cluster.shared.core.clock [datetime-of-epoch-ms monotonic-ms-of])

(defhandler steady-wall [#^ int started-ms #^ int started-mono-ms]
  (GetTimeEffect []
    (<- mono float (GetMonotonic))
    (resume (datetime-of-epoch-ms (+ started-ms (- (monotonic-ms-of mono) started-mono-ms))))))
```

`worker/entry/main.hy` の差分(要点):

```diff
+(import doeff_cluster.worker.protocol.steady_wall [steady-wall])
-(defk production-handlers [... warm boot-file]
+(defk production-handlers [... warm boot-file started-ms started-mono-ms]
-  [(await-handler) (async-time-handler) (http-production-handler) slog-handler ...
+  [(await-handler) (async-time-handler) (steady-wall started-ms started-mono-ms) (http-production-handler) slog-handler ...
-                                           places.status codes envs warm (.get machine-env BOOT-FILE-VAR))))
+                                           places.status codes envs warm (.get machine-env BOOT-FILE-VAR)
+                                           started-ms started-mono-ms)))
```

起動の刻の対は main が既に読んでいる `started-ms`・`started-mono-ms` をそのまま渡すので、J4(同じ源)は守っています。

## 2. 本来の責務所有者と、違反する契約

- **持ち主は silence-measure**(`heartbeat_rules.hy` の `silent-ms`)。「壁の差が suspend を拾い、単調な差が壁の戻りを守る」という知識は、GetTime が眠りの間も進む生の壁の時計であることを前提にしています。
- **侵すのは worker-entry**。「どの時計の handler を並べるか」という自分の権限の中で、壁の時計の性質を変えます。
- **起きること**: 口が `silent-ms` に渡す壁の差が、単調な差と恒等になります。max の片方が死に、`silent-ms` の docstring が禁じる「単調な差だけを判断に渡す」形になります。`silent-ms`・起点の欄・口・宿・規則は 1 文字も変わりません。
- **移る知識**: 「壁の時計が何を数えるか(眠り・step)」という計りの前提の半分が、silence-measure から入口の handler の並びへ移ります。
- **破る契約**:
  - 固定の要件「suspend で実際に過ぎた時間を下回る沈黙を読まない」。
  - 「戻った最初の周で止める」(#2806)。
  - timing-contract の前提「worker の沈黙は coordinator の沈黙を下回らない」。ここから C4・C2 が崩れます。
  - J2。本番だけ計りが変わり、宿は世界の時計のままなので、模擬は本番と違う計りを確かめ続けます。
- **設計の主張との関係**: S1 は時計の源の差し替えを「worker-entry だけ」で収まるとし、共同の不変条件は単調な時計の J4 しかありません。壁の時計の源についての不変条件はなく、同じ流儀の差し替えを止める物がありません。

## 3. 提示された各検査が拒否しない理由(すべて変更を当てた状態で実行)

| 検査 | 拒否しない理由 | 実測 |
| --- | --- | --- |
| semgrep の規則(main.hy と steady_wall.hy を名指し) | 4 つの欄の名の読み書きだけを拾う。変更は起点の欄に触れず、`started-ms`・`started-mono-ms` は dot の付かない局所の名 | 0 findings |
| 規則の検 `-k silence_anchor` | 検体は不変、実の木の走査も 0 件 | 2 passed |
| `test_silence_wall_clock_step.hy` | `seen-on` が自分で組を並べ、`production-handlers` を通らない | 8 passed |
| `test_wall_clock_step_keeps_one_place.hy` | sim-cluster は `worker-on` に宿の組を渡す。main.hy は import されるだけ | 5 passed |
| `test_worker_wakes.hy`・`test_sim_host_wakes.hy` | 同じく自前の組 | 12 passed |
| `:pre`/`:post`(int) | 値はすべて int のまま | 上の検の中で違反なし |
| `test_cluster_timing.hy`(C4 の値) | 値の関係だけを見る | 5 passed |

提示外ですが、本物の入口を子 process で起こす `tests/test_worker_registers.hy` も名指しで 1 回実行し、2 passed でした。変更した入口は実時間で起動し、heartbeat の返事を受けます。

## 4. 再現の手順と期待結果

確かめの検は `/tmp/blind-b-ki069f/test_blind_b_entry_steady_wall.hy` です。契約の検と同じ台本・偽の coordinator・本物の口を使い、`steady-wall` を入口と同じ位置(機体の時計のすぐ内側)に並べた組と、並べない対照を比べます。

```
sh /tmp/blind-b-ki069f/apply.sh
cd /work/toolset/worktrees/doeff-wt-blind-b-ki069f
# BLIND_INPUT の検査の命令をそのまま(semgrep は main.hy と steady_wall.hy を名指し)
cd packages/doeff-cluster && uv run pytest tests/test_blind_b_entry_steady_wall.hy -q -s
sh /tmp/blind-b-ki069f/restore.sh
```

期待結果は 4 passed で、数字は次のとおりです(実測)。

| 台本 | 対照(契約の検と同じ組) | 入口の組(`steady-wall` あり) |
| --- | --- | --- |
| A: 届いている worker が 30 秒眠る(契約の検の suspend と同じ) | 眠りの刻 5000 ms に自己停止を決める | 自己停止を 1 度も決めない(cut-off-ms = None) |
| B: 届かなくなった時に 70 秒眠り、戻っても届かない | 10000 ms(起きた刻)に決める | 27501 ms に決める(起きてから 17.5 秒動かし続ける) |

台本 A では、契約の検の assert「cut-off-ms is not None」が赤になる形です。台本 B では、最後に届いた heartbeat からの実際の沈黙が 90001 ms で、移し替えの 60000 ms を越えています。

## 5. 未確認の前提・不足する情報

- **実行していない検査**: doeff-linter、`.pyi` の作り直しの検、architecture の検、package 全体・repo 全体の検。これらが新しい module や入口の変更を拒むかは分かりません。
- **確かめの検の限界**: `production-handlers` そのものは仮想の時計で回せないので、同じ handler を同じ相対位置に並べた組で確かめました。本番の list に入って動くことは `test_worker_registers.hy` が通ったことでしか確かめていません。実機の suspend は未実測です。
- **suspend の模し方**: 契約の検と同じ「単調は進まず壁だけ進む」です。実機の単調な時計が眠りを数えないことは BLIND_INPUT の記述に依っています。
- **2 か所で走ることそのもの**: 未実測です。宿は入口の組を通らないので sim-cluster では再現できず、台本 B の数字からの推論です。
- **「実装されそうか」**: 推測です。根拠は、設計が偽の自己停止を代償と明記していること、子の停止の猶予などを範囲の外に残していること、S1 が源の差し替えを入口だけの変更と位置づけていることです。
- **副次の観察(semgrep だけ実測、振る舞いは未実測)**: 規則は形だけを見るので、次の 3 つは発火しませんでした。対照の `(- now-ms state.last-ok-ms)` は発火します。
  - heartbeat の受け入れ以外の出来事で対を書く。
  - 対のまま読み直して先へずらす: `(setv s.last-ok-ms (+ s.last-ok-ms g) s.last-ok-mono-ms (+ s.last-ok-mono-ms g))`。
  - `silent-ms` の 2 番目に起点を渡す: `(silent-ms now-ms s.last-ok-mono-ms s.last-ok-ms s.last-ok-mono-ms)`。

  1 つ目は「400 の断りも届いたと数える」修正として書かれえます。coordinator は断った heartbeat で last-seen を進めません(`api_policy.hy` の 400 の枝は state をそのまま返す、コードを読んだだけ)。契約の検がこれを通すかは未実測です。

作業 file はすべて `/tmp/blind-b-ki069f/` にあります(`apply.sh`・`restore.sh`・`steady_wall.hy`・`main.hy.patch`・`main.modified.hy`・`orig/main.hy`・確かめの検)。

WAIT: work — 呼び出し元の次の依頼
