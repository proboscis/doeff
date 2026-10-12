# worker の Pod の中の service と会話の CPU の分け方
2026-10-12 ・ card ki-757b489f8a14(設計の card)で比べた案・選んだ形・測りを 1 ページにまとめる

## この文書は何か

### 何の話か
zeus の worker の Pod の中では、動き続ける service(turn-host・agora-records・agora-record ほか)と、会話の claude の本体と、会話が走らせる命令が、1 つの cgroup を分け合っている。機体が満ちた刻(load 100 以上)に、service は CPU を 58〜82% の割合で待ち、手番と記録の道が遅れていた。この card は、service の CPU の取り分が会話の仕事の本数で決まらない形を、doeff-cluster の worker の中で決める設計の card。このページは、比べた案・選んだ形・その根拠の測りを、card を読まずに分かる形で置く。

### ここまでにしたこと
1. Pod の中の thread を scheduling の類(service・会話の本体・束の仕事・SCHED_IDLE)ごとに分け、20 秒の窓で CPU の待ちを測った(67 窓と個別の窓)。
2. 機体の cgroup(Pod の外)の CPU と PSI を node-exporter の Pod から読んだ。
3. 候補 3 つ(子の cgroup・claude の nice・短い slice)のうち試せる物を試した(短い slice は本物の service に掛けて外す A/B)。
4. 束の仕事(実行環境の準備と long-command の命令)を SCHED_IDLE にする変更を main に入れた(doeff 811234f1・agora-controllers c429237c)。

### 今どこにいるか
案の比較と選択・2 つの変更の着地・long-command の分の配備の頼み(列の r243)まで終わった。準備の分は次の worker の版上げで効き、入れた後の満ちた刻の測り(受入の条 2)はまだ。

### 決めてほしいこと
| 決めること | 選択肢 | 推奨と理由 |
|---|---|---|
| 受入の条 2 の「入れた後」の測り方 | A: 案 4 が効いた後の満ちた刻で 1 回測る / B: Pod の外の上限(card ki-28d9)の前と後も同じ列で測る | B ★ — 主因が Pod の外なので、混ぜると案 4 の効きが読めない |
| 機体の build の段の抑え方(card ki-28d9 の判断) | 重み(今 20)のまま / CPUQuota で上限を掛ける | 上限 ★ — 重み 20 のまま 10〜11 core を取り、Pod の service を待たせた |

### 読む順番
結論 → 図 1 → 比べた案 → 図 2 → 測り → まだ分かっていないこと

## 結論
- 束の仕事(会話の Bash の命令・long-command の命令・実行環境の準備)を SCHED_IDLE に揃えた。service の取り分は束を何本並べても変わらない。
- 満ちた刻の service の待ちの主因は Pod の外の CPU を食う群で、cgroup の重みでは止まらない。上限で抑える(card ki-28d9・ki-11b9)。
- 子の cgroup・claude の nice・短い slice は採らない(効きが小さいか揺れの内で、worker の作り直しや会話の速さの決定に当たる)。

## 語の説明
- **service** = worker の Pod の中で動き続ける job(turn-host・記録・画面ほか)
- **束の仕事** = 終わりまで走れば良い計算(テスト・型の検査・bytecode の焼き)
- **SCHED_IDLE** = Linux の一番低い scheduling の類。ふつうの類の thread が起きると必ず譲り、重みも 3 しかない
- **CPU の待ち** = 走れるのに CPU を待った時間 ÷(走った時間 + 待った時間)。thread ごとの /proc の schedstat の差から出す
- **PSI** = cgroup の中の仕事が 1 つ以上 CPU を待っていた時間の割合(cpu.pressure の some)
- **重みと上限** = 重み(cpu.weight)は兄弟の cgroup の間の取り分の比、上限(cpu.max)は使える core の数そのもの

## 構成 — 今と目標
目標で変えたのは、Pod の中の束の仕事 2 つ(long-command の命令・実行環境の準備)の類だけ。service と会話の本体は今のまま。Pod の外の群は別の card で上限を掛ける。

```図 fig-1
題: zeus の CPU の分け方(左 = 今・右 = 目標)。変えたのは Pod の中の束の仕事 2 つの類だけ
見せること: 機体の cgroup の木の上に、worker の Pod の中の 5 つの仕事の類を並べ、今と目標で何が変わるかを見せる
使う数字: 機体 36 core / 機体の根の兄弟 = kubepods.slice 重み 1407・system.slice 重み 1000・user.slice 重み 100(上限 16 core)・imagebuild.slice 重み 20(BuildKit の build の段・満ちた刻に 10〜11 core) / kubepods.slice の下に worker の Pod doeff-worker-zeus(重み 157・1 つの cgroup)/ Pod の中の 5 つ: service(SCHED_OTHER・nice 0・今のまま)・会話の claude の本体と hook(SCHED_OTHER・nice 0・今のまま)・会話の Bash の命令(SCHED_IDLE・ki-bb74 で済み)・long-command の命令(今 SCHED_OTHER nice 10 → 目標 SCHED_IDLE)・実行環境の準備と焼き(今 SCHED_OTHER nice 10・最大 31 本 → 目標 SCHED_IDLE)/ Pod の外の 2 つに「上限を掛ける(card ki-28d9・ki-11b9)」の札
形: 左右 2 枚の同じ配置の図。上に機体の根、その下に 4 つの slice の箱を横に並べ、kubepods.slice の箱の中に worker の Pod の箱、その中に 5 つの類の箱を縦に並べる。右の図では変わる 2 つの箱だけ色を変え、imagebuild.slice と user.slice に上限の札を付ける
```

## 比べた案
| 案 | 要る物 | 効き(測った・読んだ) | 判定 |
|---|---|---|---|
| 1 子の cgroup(service と会話を別の子に入れ、重みを付ける) | cgroup を書ける容器(zeus の containerd 2.0.5 では特権なしだと読むだけ・2.1 の cgroup_writable か privileged が要る)・root の起動の段・worker の雛形の変更と Pod の作り直し・doeff-cluster の job に類の欄 | Pod の中の重みのうち service の分は 0.58〜0.82 で、service の待ちとの相関は −0.13(67 窓)。Pod の外の群には効かない | 採らない — worker の雛形は「very static」、版上げで worker の Pod を作り直さない決まりがある |
| 2 会話の claude の本体の nice を上げる | 本体を起こす所 1 か所(特権は要らない) | 本体の走れる本数は 0.6〜3.7(平均 1.6)。下げた process は戻せない | 採らない — 利用者の決定「zeus は会話の速さを一番にする」に当たる |
| 3 service に短い slice(EEVDF の custom slice) | sched_setattr(特権なしで掛かり、0 で戻ると確かめた) | 本物の service に掛けて外す A/B で 28.2% → 22.0%・15.9% → 15.6%・ABBA 8 窓で差なし。probe でも差なし | 採らない — 効きが揺れの内 |
| **4 束の仕事を SCHED_IDLE にする** | 命令の頭(chrt)2 か所 | 準備の焼き 31.5 本・19.7 core の窓だけ service の待ちが 59%、前後の窓は 15〜16% | **採った** — 会話の Bash の命令と同じ類で筋が通る |
| (外した)別の Pod・役ごとの worker | — | — | 利用者の決定「agora は doeff-cluster の上だけで動く・専用の Pod を持たない」 |

## 測り — 満ちた刻の待ちの主因は Pod の外
Pod の外が 8 core 以上を使った窓と、それ未満の窓で、service の待ちの平均が 3 倍違った。Pod の中の重みの分け方とは相関が無かった。

```図 fig-2
題: service の和の CPU の待ち(平均)を、窓の条件ごとに比べる
見せること: Pod の外の CPU が多い窓ほど service が待つこと、Pod の中の束の山でも上がること
使う数字: Pod の外 8 core 未満の窓(17 窓)= 6% / Pod の外 8 core 以上の窓(50 窓)= 19% / 準備の焼きの山の窓(1 窓・nice 10 が 31.5 本)= 59% / この card の所見(load 103〜141)= agora-record 58〜82%
形: 横棒 4 本。長さが % に比例。4 本目は 58〜82% の範囲を幅で描く
```

| 測り | 窓 | 値 |
|---|---|---|
| Pod の外と service の待ち | 14:35〜14:58 JST・20 秒 × 67 | 外 8 core 以上: 平均 19%(agora-record 40〜75%)/ 8 core 未満: 平均 6%。相関: 外の core +0.78・会話の本体の本数 +0.53・Pod の中の重みの service の分 −0.13 |
| 重み 20 の Pod の外の群 | 14:47〜14:48 JST・15 秒 × 3 | imagebuild.slice 10.0〜11.1 core・PSI 2.2〜5.4% / worker の Pod 16.7〜18.3 core・PSI 11.3〜18.2% |
| Pod の中の束の山 | 14:28 JST・20 秒 | nice 10 の焼き 31.5 本・19.7 core。service の和 59%(turn-host 58%・agora-records 77%・agora-record 89%)。前後の窓は 15〜16% |
| 準備の刻と所見の窓 | 11:03〜11:51 JST | 準備の終わりが 9 回。この card の所見の窓(11:16〜11:26)と重なる |
| 重みと待ち(自分の probe) | load 88〜92 | nice 10: 43%・21% / nice 19: 51%・22% / SCHED_IDLE: 69%・42% |
| 起きた時の押しのけ | 1 つの CPU に固定・load 42〜56 | nice 10 の probe は nice 19 や SCHED_IDLE の回り続ける相手を 10〜40 μs で押しのけた |

## 既存の決定との突き合わせ
- 専用の worker の Pod を持たない(agora の webapp の ADR・2026-10-09 の利用者の決定)— Pod を足していない。
- doeff-cluster の job の入口は handler を足さない・service と task は同じ API(ADR-DOE-CLUSTER-001 の R2・R3)— 入口と API に触れていない。変えたのは準備の process と long-command の命令の起こし方だけ。
- 版上げで worker の Pod を作り直さない(agora の defadr_0010 の law)— 準備の変更は次の worker の版上げに乗せ、作り直しは頼まない。long-command の変更は会話の宣言し直しで乗る。

## 入れた物と効く時
| 変更 | 着地 | 効く時 |
|---|---|---|
| 実行環境の準備を Linux では `chrt --idle 0 nice -n 10` で起こす(macOS の worker は nice だけ) | doeff L2195・811234f1 | zeus の worker がこの版以降の doeff で起きた後(14:29 JST から入れ替わっている worker はこの前の版) |
| long-command の命令を同じ形で起こす | agora-controllers L6406・c429237c | 配備の列 r243(turn-host 3 つと道具の配り)の後 |

## まだ分かっていないこと
- 重み 20 の群がなぜ重み 1407 の kubepods より先に CPU を取れるのか。CPU ごとに重みを配る仕組みで、SCHED_IDLE の多い kubepods の重みが薄い CPU を外の群が取るという読みは、kernel の trace が無く確かめていない。
- 案 4 の満ちた刻での効き。準備の山と満ちた刻が重なる回を、入れた後に待って測る。
- 会話の本体の本数との相関 +0.53 は、機体の混みと一緒に動く分を含む。会話の本体を下げる案 2 を考え直すのは、外の上限が入った後も、外が 8 core 未満の窓で service が待つと分かった時。
