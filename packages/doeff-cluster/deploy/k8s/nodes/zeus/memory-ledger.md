# zeus の機体の memory の取り分の表

zeus に回収されない memory(tmpfs・共有 memory)を足す変更は、この表の行を改めます。この表の持ち主は doeff-cluster の zeus の宣言(この dir)で、
上に載る系の宣言の値も、機体の余白を分け合う物はここに行を置きます。訳: 2026-11-03〜05 に PostgreSQL の shared_buffers 16 GB・/tmp・
/dev/shm・/cargo-build を別々の card が足し、どれも「機体の空きはまだ足りる」と別々に見積もった結果、走るターンの数が同じ(25〜27 本)でも
余白が 46 GiB から 14 GiB に減って、新しいターンを受けられなくなりました(card ki-fc49e5455300)。

余白 = MemAvailable − 床。余白が足りないと、会話のターンは配られずに待ちます(上に載る系の CLI ホストの判断 ram-short?)。

## 表(MemTotal 125.5 GiB・測り 2026-11-05 17:4x JST・走るターン 25 本)

| 行 | 宣言の場所 | 取り分 | 測った使い | 決めた事 |
|---|---|---|---|---|
| 床(機体に残す) | 上に載る系の CLI ホストの NODE-FLOOR-SHARE | 20% = 25.1 GiB | — | 変えない(下の節) |
| kernel(slab・page table など) | — | 13 GiB | 11〜13 GiB(11-01 から同じ) | 宣言の外の読み |
| PostgreSQL の shared_buffers | 上に載る系の repo の deploy/pg | 8 GB | 16.4 GiB(16 GB の時) | 16 GB → 8 GB |
| /cargo-build | cargo-build-in-memory.yaml | 上限 16Gi | 14 GiB | 上限は変えない。作り直せる中間物なので、ターンより先に返す |
| /tmp | tmp-in-memory.yaml | 上限 8Gi | 1.4 GiB | 変えない(使った分だけ memory を取る) |
| /dev/shm | dev-shm-in-memory.yaml | 上限 8Gi | 14 MiB | 変えない(同じ) |
| /memory-scratch | ../../worker/worker.yaml | 上限 1536Mi | 16 MiB | 変えない(同じ) |
| worker の常駐の層(services・worker・jobs) | cgroup.yaml | 6 GiB | 5.4 GiB | 読みの値 |
| worker の外の Pod の anon | 各 repo | 12 GiB | 約 12 GiB(Longhorn の instance-manager 4 GiB を含む) | 読みの値 |
| 会話のターンと命令(clis・commands の anon) | cgroup.yaml | 残り | ターン 1 本 約 1 GiB | 余白はここから減る |

## 床を 20% のままにする訳

- 床を 15% にすると余白は 6.3 GiB 増えるが、見張りが job を止めに入る線(空き 10%・12.5 GiB)との間が 12.6 GiB から 6.3 GiB になる。
- 直近 14 日の MemAvailable の急落の最大は 5 分で 33.8 GiB(11-04 21:57 JST)・10 分で 35.8 GiB。床があっても最小は 26.3 GiB に留まった。
- doeff の worker の memory の見張りは同じ 20% で逼迫(TIGHT)と言い、1 回きりの task を待たせる(`_worker/core/memory_rules.hy` の tight-available・
  `_worker/core/policy.hy` の MEMORY-TIGHT)。床だけを下げると、見張りが task を止めている間にターンを受け続ける。
