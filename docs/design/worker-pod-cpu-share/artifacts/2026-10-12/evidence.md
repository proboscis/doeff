# 検証記録 — worker の Pod の中の CPU の類(card ki-757b489f8a14・2026-10-12)

対象の版: doeff main 811234f1(準備の argv)・agora-controllers main c429237c(long-command の argv)。測った機体: zeus(36 CPU = 18 core × 2・kernel 7.0.0-34・k3s v1.32.5・containerd 2.0.5-k3s1.32)。
測りはすべて読むだけ。例外は、自分の起こした probe の scheduling の変更と、主張 C3 の生の A/B(本物の service の thread の slice を掛けて外す — 終わりに全部外したことを `sched_ab.py show` で確かめた)。

## 主張と結果

| 主張 | 方法 | 結果 | 証拠 |
|---|---|---|---|
| C1 満ちた刻の service の待ちは、Pod の外の CPU の多さと一緒に動く | `monitor.py` を 20 秒の窓で 67 回(14:35〜14:58 JST)。機体の CPU は /proc/stat、Pod は cgroup の cpu.stat、外 = 機体 − Pod | 成立(実行): 外 ≥ 8 core の 50 窓は service の和の待ち 平均 19%、< 8 core の 17 窓は 6%。相関 +0.78 | `evidence/data/monitor.jsonl`・`mon_table.py` |
| C2 重み 20 の Pod の外の群は、kubepods(重み 1407)の service より先に CPU を取れる | node-exporter の Pod から機体の cgroup の usage と cpu.pressure を 15 秒 × 3 | 成立(実行): imagebuild.slice 10.0〜11.1 core・PSI 2.2〜5.4%、worker の Pod 16.7〜18.3 core・PSI 11.3〜18.2% | 本文の表(命令は card ki-28d9 の発言に写した) |
| C3 service に短い slice を掛けても待ちは揺れの外までは下がらない | `sched_ab.py` で service の全 thread に 100 μs を掛ける窓と外す窓を交互(ab-bash1・ab3-1・ab4-onall)と、probe の A/B | 成立(実行): 28.2% → 22.0%・15.9% → 15.6%・ABBA の後半 8 窓で差なし。probe 43% → 48%・21% → 23% | `evidence/data/ab-*`・`probe-70` |
| C4 Pod の中の nice 10 の束の山は、service の待ちを上げる | `measure2.py` の窓の細目 | 成立(実行・1 窓): nice 10 の python 31.5 本・19.7 core の窓で service の和 59%、前後の窓は 15〜16% | `evidence/data/ab4-onall/p05-off.json` |
| C5 Pod の中の重みの分け方(service の分)は、待ちと相関しない | C1 の窓で、走れる本数 × 重み(nice 0 = 1024・nice 10 = 110・SCHED_IDLE = 3)の service の分 | 成立(解析): 0.58〜0.82・相関 −0.13 | `mon_table.py` |
| C6 起きた SCHED_OTHER の thread は、重みの低い回り続ける相手を待たずに押しのける | 1 つの CPU に固定した probe(nice 10)と相手(なし・SCHED_IDLE・nice 19) | 成立(実行): 相手ありで 1 回の待ち 10〜40 μs(相手なしの 150〜250 μs より短い — 相手が CPU を埋め、他の仕事がよそへ移る) | `evidence/data/pin-test` |
| C7 custom slice は特権なしで掛かり、runtime 0 で戻り、thread・fork・exec に継がれる。RESET_ON_FORK は thread にも効き、特権なしでは外せない | `fork_test.py`・`reset_test.py`(long-command の task = SCHED_OTHER nice 10 から) | 成立(実行) | 本文 |
| C8 案 4 は満ちた刻の service の待ちを下げる | 入れた後の満ちた刻に `monitor.py` | 未実行: 準備の分は次の worker の版上げの後、long-command の分は配備の列 r243 の後 | — |

## 失敗例と直し
- 測りの重なり: 生の A/B の ab-70(long-command の task)と ab4-onall(会話の Bash)が 14:27〜14:30 JST に重なり、互いの on と off を打ち消した。ab4-onall の前半 8 窓と ab-70 の 2・3 組目は使わない。
- 別の Pod を立てた probe(Pod の外の cgroup で同じ probe を測る)は、規則「agora は doeff-worker 以外の Pod を持たない」に当たるので、立てた直後(窓を 1 つも取る前)に消した。値は無い。

## 残る不足
- 重み 20 の群が先に回る仕組み(CPU ごとの重みの配り方)は kernel の trace が無く確かめていない(読み)。
- C8 は未実行。受入の条 2 として、入れた後の満ちた刻で測る。
