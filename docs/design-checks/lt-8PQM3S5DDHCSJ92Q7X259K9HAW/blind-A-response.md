失敗例が 1 つ見つかりました。worker の外の「見張り」に、もっともな記録の行を 1 行足すだけで、doeff-cluster 側が隠しているはずの fd の差し替えにぶつかります。手元の代わりの process で再現できました。共有の file は書き換えておらず、git の操作もしていません。

## 1. 変更の要求と、要る理由

**要求:** doeff-core-effects の見張り(`doeff_lifeline` の `guard`)に、1 行の記録を足す。起こした側(worker)が終わったのを知って TERM を送った時と KILL を送った時に、相手を stderr へ書く。

**理由:** 見張りは #3866 で入った仕組みで、起こした側が死んだ後に残った子を止めるのが役目です。機体の worker が再起動した前後に「どの job を止めたか・猶予の内に止まらず KILL したか」を log で追いたい場面は自然に起きます。この行は、今回の要件(機体の worker の行を会話から読む)と同じ調べ物のための行です。極端な規模でも、無関係な要求でも、意図的な破壊でもありません。

## 2. 関係する主張・前提と、予想に反して変わるもの

**前提は守っています。** 機体の worker で、stdout と stderr が同じ file を指し、送り先(DOEFF_JOB_LOG_OTLP)が在る状態です。S1〜S3・S5 の範囲の中で、S4(Pod)と S6(模擬)には触れません。

**関係する主張:**
- M-writer は「pipe と fd の扱い」を隠し、M-process は「atexit・same_file」を隠す。
- S3「file は全部残る」。
- S5「M-entry と業務の Program は thread を知らない」。
- 終わりの項が挙げる残る穴は「SIGKILL の時に pipe に残った分を失う」だけです。

**実際の流れ:**
- `process_lines`(line_stamp.py L650-655・L666-670)は、`os.dup2(sink, 1/2)` で fd 1・2 を pipe の書き口にします。`dup2` で作った fd は既定で子へ継がれます。この pipe を読むのは、worker の中の thread だけです。
- 見張りは、worker が最初に StartProcess した時に起きます(os_process.hy L564-568)。起こし方は `Popen(..., stdout=DEVNULL, start_new_session=True)` で、**stderr を指定していません**(doeff_lifeline L164-169)。そのため見張りは pipe を fd 2 として継ぎます。
- main.hy では `process-lines`(L284)が worker-on より前に呼ばれます。見張りが起きるのは必ず差し替えの後です。

**変わる所:** 見張りの役目は、起こした側が終わった後に動くことです。その時点で pipe を読む側は必ず居ません。したがって見張りが書く行は、file にも送り先にも届く道がありません。正しく実装するには、次のどれかが要ります。

| 案 | 触る所 | 問題 |
|---|---|---|
| (a) 見張りの stderr を別の宛先へ向ける | doeff-core-effects(foundation の層) | 機体の log の path を知らない。元の file の fd は StampedLines の外へ出ていない |
| (b) 子へ元の file を継がせる | doeff-cluster の M-writer・M-process | 公開の口を広げる必要がある |
| (c) 見張りが送り手を知る | 見張り | 層を破る |

どの案も、2 つの package に跨って複数の責務を変えます。

## 3. どの知識が漏れているか(契約の拡張との区別)

- 漏れるのは「この process の fd 2 は、worker の中の thread だけが読む pipe である」という M-writer・M-process の隠した知識です。fd を継ぐ子孫から、この事実が見えてしまいます。
- **公開の契約の拡張ではありません。** StartProcess の lifetime の約束も、JobLogPlan も変わりません。
- **配線の変更でもありません。** main.hy の組み立ては変わりません。
- **共同の不変条件として宣言も強制もされていません。** 「worker より長く生きる子孫は、継いだ fd 1・2 へ書かない」という約束はどこにも無く、どの強制方法(module 契約・DOEFF106・本物の process の検・契約の検)も見ていません。shim の頭の註は子孫の残りの扱いを書いていますが、worker の側には書いてありません。
- **今の検は緑のままです(読んで確かめた範囲):**
  - lifeline の検(test_process_contract.hy L520-529)は、起こす側の stderr を DEVNULL にして起こすので、見張りの stderr は /dev/null になる。
  - その検の子は `sleep 600` で TERM で止まるので、KILL が送られなくても気付かない。
  - 今回の worker の検は子を起こさない。

## 4. 再現の手順と観測の結果

場所は `/tmp/tmp.KxqoMMULeU` です。
- `lifeline_with_log.py`: 本物の `doeff_lifeline` の写しに、変更例の 1 行(`watched_until_eof` の後に TERM を送った旨を stderr へ書く)を足し、検を短くするため猶予を 1 秒にしたもの。
- `worker_like.py`: main.hy と同じ順で `process_lines(worker_log_plan(...))` を呼び、StartProcess と同じ形で TERM を無視する子を立て、Lifeline と同じ形で見張りを起こして終わる。
- `driver.py`: systemd の `append:` と同じく同じ path を 2 度開いて走らせ、終わりの後の様子を読む。

実行の形は次の通りです。
```
cd /home/ubuntu/.worktrees/doeff-wt-dc-lt-8pqm
uv run --no-sync python -B /tmp/tmp.KxqoMMULeU/driver.py <case> <lifeline の file> <on|off> <exit|kill> <待つ秒>
```

| case | 送り先 | 見張り | 見張りの fd 2 | 見張りの行が file に在るか | TERM を無視する子 |
|---|---|---|---|---|---|
| B(差し替えの前と同じ形) | off | 変更例 | worker.log | 在る | 止まった |
| C | on・正常に終わる | 変更例 | `pipe:[…]` | 無い | **3 秒後も生きている** |
| D | on・worker を SIGKILL | 変更例 | `pipe:[…]` | 無い | **生きている** |
| E(今の code) | on | 元のまま | `pipe:[…]` | ―(行を書かない) | 12 秒後に止まった |

- 見張りを直接の子として、読む側を閉じた pipe を stderr に付けて走らせると、終了コードは 120 で、子は KILL されずに残りました。
- 既存の検 `test_the_worker_process_ships_its_own_lines_and_its_crash_traceback` は 1 本だけ走らせて、1 passed でした(この形を見ていないことの確認)。

**観測すべき結果:** 同じ変更が、差し替えの前の形では file に行を残して子も止めます。今の形では行を失い、見張りが KILL の前に止まって子が残ります。

## 5. 未確認の前提と不足する情報(推測と実測の区別)

**実測したこと:**
- 見張りの fd 2 が pipe になること。
- 変更例の行が失われ、見張りが KILL を送らずに終わること。

**推測にとどまること:**
- 見張りが止まった原因は BrokenPipeError と見ています。終了コード 120 は終わりの flush の失敗の code ですが、traceback は書き先が無く読めません。
- 記録の行を KILL の後に置く形なら、子は止まって行だけを失うはずです。これは走らせていません。
- 再現は代わりの script で行いました。本物の main.hy の worker でも同じ並びになることは、code を読んで判じただけです。

**本番で効く度合いは未確認です:**
- zeus は systemd の unit で KillMode が既定なので、worker が終わると systemd が cgroup ごと止めるはずです。その場合、孫が残るのは TimeoutStopSec(30 秒)までと見ていますが、確かめていません。
- Mac の launchd が、別の session の子を止めるかどうかは確かめていません。
- 行を失うことは、どちらの機体でも条件なしに起きます。

**主の例にしなかった候補(参考):**
- **環境変数の名の重複:** worker が読む環境変数の名が、main.hy の L252 と `worker_log_plan` の L241 の 2 か所に別々に並んでいます。新しい変数(会社の node の collector の資格など)を足すと main.hy も直す必要があり、2 つを結ぶ検はありません(検は `worker_log_plan(os.environ)` を直に呼ぶ)。ただし M-entry の役目は環境を読むこと(DOEFF106)なので、配線か漏れかは境目です。読んだだけで、走らせていません。
- **読み手の制約:** 読み手は `--since` と、新しい方から最大 10000 行しか持たず、`--until` もありません。また世代(doeff.instance)を返さないので、同じ worker の再起動の前後の行が混ざります。どちらも直すなら M-contract と M-reader の契約の拡張で、漏れではありません。ただ、古い時間の窓を読めるかは行の量しだいで、量は測っていません。

この 1 例が見つかったからといって、他に失敗例が無いとは言えません。全数の検査はしていないので、合格・安全とは結論しません。
