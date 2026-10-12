失敗例を 1 件、範囲内で再現しました。ただし突く時間帯が狭く(検の組で周あたり約 1 ms)、現実の頻度は低いはずです。S1・S3・S5・S6 の主張そのものは崩せていないので、合格・安全とは結論しません。

## 1. 変更要求と理由

**要求**: 起きる期限(WakeSet.due)を組み立てた後、周の間の待ちが待つ長さを決める前に機体の壁の時計が戻っても、途絶の柵の判断が戻った分だけ遅れないようにする。

**理由**: 固定の要件そのものが、この時間帯の step では HEAD で満たされていません(実測)。既に在る台本は壁を「届かなかった最初の heartbeat の時」と「ObserveWorld」でしか動かしません。同じ card の取りこぼしとして来うる直しです。

## 2. 関係する主張・前提と、予想に反して変わる物

- **固定の作りは守ります**: silent-ms は int のまま、関数は 1 つ、max はその中だけ。DueAt も壁の枠の刻のままです。
- **S1〜S6 の前提は変えていません**: 崩れるのは責務の表の次の 3 つです。
  - link-anchors の「時計を読む所・起点を今の壁の時計の枠へ写す事」
  - worker-due の「last-ok-ms は今の壁の時計の枠へ写した刻」
  - tick-program の公開の形 `WorkerWakes [began began-mono-ms]`
- **試作で実際に変えた物**(4 file、+46 −18。これで再現の検が緑になりました):
  - `worker/intent/worker_model.hy`: WorkerWakes と AwaitNextTick に基準の対(壁と単調)の欄を足す。AwaitNextTick は表に無い effect です。
  - `worker/core/program.hy`: 調整ループが対を 1 度読み、両方の effect へ渡す。
  - `worker/protocol/coordinator_link.hy`: WorkerWakes の節は自分で時計を読まず、渡された対を「今」にする。「時計を読む所」が口から調整ループへ移ります。
  - `worker/protocol/tick_pauses.hy`: 表に無い module。silent-ms の 3 人目の呼び手になります。
- **推測で、さらに要る物**: sim-host の WorkerWakes の節(J2)、anchor-rule の拡張、台本の追加、使い手の歩数の測り直し(J3)。

## 3. どの知識がどこへ漏れるか

- **隠すはずの知識の漏洩**: 「DueAt がどの『今』の枠の刻か」は link-anchors の知識です。口は `coordinator_link.hy` 616 行で読んだ壁の時計の枠で期限を出します。ところが `tick_pauses.hy` 44-45 行は自分で壁の時計を読み直し、`due.at − now` を待つ長さにします。DueAt は生の壁の刻 1 個で枠の基準を運ばないので、待ちの側が同じ枠だと暗黙に仮定しています。口だけでも tick-pauses だけでも閉じられません。
- **意図した公開契約の拡張**: 2 つの effect に欄を足す事自体は拡張です。既定値つきなので、他の handler の節(env_store・warm_host・probes・code_store・process_host・no-wakes)は無変更で済む見込みです(節は欄の名で受ける、という実読による)。
- **配線の変更**: ありません。handler の並びは同じです。
- **共同の不変条件**: J2(宿も同じ対を使う)と J3(1 周の effect の数)に当たります。J1 に当たる対がもう 1 組増えます。
- **規則の限界**: 規則は 4 つの欄名だけを見るので、新しい対は見えません。試作の 4 file に semgrep を当てて 0 件でした(実測)。

## 4. 再現手順と観測結果

**入力**: `tests/test_silence_wall_clock_step.hy` の道具をそのまま使い、10 秒目から届かない台本にします。足すのは一番内側の代役 1 つで、AwaitNextTick を見た時に壁の時計を戻してから tick-pauses へ通します。拍の外の送りは既定のまま有効です。

```
cd /work/toolset/worktrees/doeff-wt-blind-a-ki069f/packages/doeff-cluster
cp /tmp/blind-a-ki069f/test_blind_a_step_before_the_wait.hy tests/
uv run pytest tests/test_blind_a_step_before_the_wait.hy -q -s
rm tests/test_blind_a_step_before_the_wait.hy
```

`tests.*` の import の都合で、実行の間だけ `tests/` に置きます。

**HEAD の実測**(自己停止を決めた時の、最後に届いた heartbeat からの沈黙):

| 筋書き | 沈黙 | 意味 |
| --- | --- | --- |
| 戻さない(対照) | 20001 ms | fence の所で決める |
| 沈黙 19.5 秒の待ちの直前に 9 秒戻す | 29000 ms | 9 秒遅れ。C4 の余白 8 秒を越える |
| 途絶の最初の待ちの直前に 45 秒戻す | 48000 ms | 28 秒遅れ |
| 沈黙 19.5 秒の待ちの直前に 45 秒戻す | 65000 ms | 移し替えの 60000 ms より後 |

結果は 3 failed, 1 passed です。拍の外の送りは動いていますが、届かなかった時の途絶の絞りは捨てられ(`coordinator_link.hy` 531 行の `_beaten`)、拍を起こしません。柵の判断は延びた待ちが明けるまで走りません。

**試作の適用後**: `git apply /tmp/blind-a-ki069f/f1-prototype.patch` で 4 本とも 20001 ms になりました。契約の検 4 file は 1 file ずつ回して緑でした(8・9・3・5 本)。

**窓の幅**: `test_blind_a_window_width.hy` で、口の読みから tick-pauses の読みまでを測りました。検の組・この機体で中央値 1.2 ms、p95 2.8 ms、最大 5.4 ms(205 周)です。途絶の間の周は 500 ms ごとなので、無作為な刻の step が窓に落ちる割合はおよそ 0.2〜1% です。

## 5. 未確認の前提・不足する情報

**実測していない物**:
- 本番の窓の幅は未測定です。本番は WorkerWakes に答える handler が多く、待つ子ごとの Spawn も入るので、検の組より広いはずです(推測)。
- sim-host は試作で変えていません。sim-cluster で同じ窓を突く筋書きも書けていません。
- J3 の歩数、doeff-linter、`.pyi` の再生成、package 全体の検は実行していません。回したのは契約の検 4 file と自作 3 file だけです。
- 試作は直し方の 1 つです。口が相対の timer を背景の task で持つ別案は試していません。

**崩せなかった主張**:
- S1: `async_time.py`・`sync_time.py` の 121 行が `monotonic` を引数で受けるので、入口だけで替えられます(実読)。
- S5: link-silence は起点の対を effect を挟まずに読み、max が負の壁の差を吸います(実読、未実行)。
- S6: 正準の筋書きは収まる見込みです。ただし前提「StallWorker で調整ループが回らない」は実装と違い、heartbeat を止めるだけで周も子も動きます(`sim/local.hy` 94-95 行)。世界は起き直した世代の最初の起点を世界の時計で書くので(同 3385-3405 行)、壁と単調の両方がずれた機体では沈黙を短く読むはずです(推測、未実行)。
- S3: 既に在る自己停止の記録は `process_host.hy` 136-153 行の止めの計時の行です。その silent-ms は、判断 2 つが作る CutOff から来ます(`heartbeat_rules.hy` 60・74 行)。この行に 2 つの差を載せる読み方なら、silence-judgments か口での付け替えが要ります。口が別の 1 行を出す読み方なら収まります(実読のみ)。

**副次の観測**:
- 範囲内、既定でない構成だけで再現: 拍が heartbeat を送る構成(beat-beside 偽)で、返事の処理の途中に 30 秒 suspend させると、自己停止の決定が 215001 ms まで遅れました(対照は 5000 ms)。`program.hy` 136 行が拍の頭の刻を単調な差だけで導くためです。既定(beat-beside 真)では再現しませんでした。
- 範囲外(設計が自分で外に置いた所): `policy.hy` 164 行の TERM から KILL までの猶予は壁の差だけで比べています。C4 の内訳の停止の猶予に当たりますが、実行していません。

作業 file は `/tmp/blind-a-ki069f/` にあります:
- `test_blind_a_step_before_the_wait.hy`
- `test_blind_a_suspend_inside_the_reply.hy`
- `test_blind_a_window_width.hy`
- `f1-prototype.patch`

作業木の source は元に戻してあり、commit・push はしていません。

WAIT: work — 呼び出した担当の次の依頼
