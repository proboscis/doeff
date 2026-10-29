# 旧い版の coordinator の置き場(doeff ec727f20)

`tests/test_deployed_state_upgrade.hy` が読む fixture。本番の coordinator が今動いている版 ec727f20 の **本物の置き場の code**
(`foundation/wal_store.hy` の `WalStore` — snapshot + log の file)が書いた状態で、この版の起動の読み(`coordinator/entry/main.hy` の
`load-state`)が読み直せるかを確かめる(#2788)。**本番の状態の file ではない**(模擬の coordinator の状態)。

| file | 中身 |
|---|---|
| `wal/snapshot.json` | 1 回目の走りの後の状態(鍵 20 — Service `beacon`・worker `sim-worker`・置き場の Program 2 つ・終えて保持中の切り離した task `k-done`・盤の行 `beacon/a` と `note/a`・出来事の記録)を `WalStore.checkpoint` で書いた物 |
| `wal/wal.jsonl` | 2 回目の走り(同じ状態から起き直して 6 秒回した)で積まれた書き 10 件を `WalStore.persist` で書いた log |

## 作り方(作り直す時)

ec727f20 の作業木を作って環境を揃え、下の script を `packages/doeff-cluster` の dir で `hy gen_old_state.hy <置き場の dir>` として
走らせる。模擬(`sim-cluster`)は memory の置き場しか受けないので、1 回目の中身を `WalStore` の checkpoint で snapshot に、2 回目に積まれた
Persist の delta を `WalStore` の persist で log に書かせる。

```sh
git worktree add --detach <旧い版の作業木> ec727f2058a63923e594632bb17d3aeb51e10ad4
cd <旧い版の作業木> && make sync          # doeff-vm も作り直す(重い — 機体の負荷が低い時に)
cd packages/doeff-cluster && uv run --frozen --no-sync hy gen_old_state.hy <置き場の dir>
```

2026-10-02 のこの fixture は、機体の負荷を避けて VM の extension を作り直さず、今の版の環境の VM で旧い版の source を走らせて作った
(ec727f20 から今の版までの VM の差は、生成器の定義の呼び出しを Python の振り分けに回さない速さの近道 1 つだけで、振る舞いは同じ)。

```hy
(require doeff-hy.macros [defk <- val])
(import sys)
(import doeff [run])
(import doeff_time [Delay])
(import doeff_cluster.foundation.wal_store [WalStore])
(import doeff_cluster.coordinator.entry.handler_sets [MemoryWalStore])
(import doeff_cluster.sim.local [sim-cluster])
(import doeff_cluster.shared.intent.detached_model [SubmitDetached AwaitDetached DetachedSucceeded])
(import doeff_cluster.shared.intent.shared_model [WriteShared])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons])
(import tests.detached_rig [slow-add])

(val WAL (get sys.argv 1))

(defk first-run []
  {:pre [] :post [(: % DetachedSucceeded)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 旧い版の状態に Service・worker・保持中の task・盤の行を残すため。"
  (<- (Delay 12.0))
  (<- (SubmitDetached (slow-add 0.5 1) :key "k-done" :needs (frozenset #("cluster-net")) :lease-seconds 60.0 :retain-seconds 172800.0))
  (<- done (AwaitDetached "k-done"))
  (<- (WriteShared "note/a" {"v" 1}))
  (<- (Delay 3.0))
  done)

(defk second-run []
  {:pre [] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: snapshot の後ろに log の行を積むため(起き直して少し回す)。"
  (<- (Delay 6.0))
  True)

(val first-store (MemoryWalStore))
(print "1 回目:" (run (sim-cluster (beacons sim-foundation) (first-run) :store (fn [] first-store))))
(val disk (WalStore WAL))
(.load disk)
(setv disk.kv (dict first-store.kv))
(.checkpoint disk)
(val second-store (MemoryWalStore))
(setv second-store.kv (dict first-store.kv) second-store.seq first-store.seq)
(print "2 回目:" (run (sim-cluster (beacons sim-foundation) (second-run) :store (fn [] second-store))))
(for [delta second-store.deltas]
  (.persist disk delta))
```
