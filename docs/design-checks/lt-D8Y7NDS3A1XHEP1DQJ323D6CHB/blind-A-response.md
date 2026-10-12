## 失敗例: coordinator が止まる時の送りの待ちを、coordinator の止め方に合わせて伸ばす(S-conc の軸)

4 つの主張(S-store・S-dist・S-conc・S-eff)を調べ、S-conc で 1 つ失敗例を作りました。本物の coordinator の入口で実際に起こしています。

### 1. 変更要求と、それが要る理由
- **要求:** 版上げで Pod を入れ替える時、前の Pod の最後の行(「coordinator: 止まりました」まで)を、送り先が遅くても落とさないようにする。そのために、coordinator が止まる時の送りの上限を、k8s の止めの猶予の内で数秒に伸ばす。猶予は coordinator.yaml に `terminationGracePeriodSeconds` が書かれていないので、k8s の既定の 30 秒です。
- **理由:**
  - 要件(前の Pod の行を読む)で一番要るのは、止まる直前の行です。
  - Recreate で旧い Pod が消えると、container の log も消えます。送り先へ届かなかった行は、どこにも残りません。
  - 今の上限は全部の送り手で共通の `SHIP_LIMITS.end_seconds = 0.5` 秒です。POST が飛んでいる最中に止まると、止まりの行は送られないまま process が終わります(4 で実測)。
  - その間、coordinator は 30 秒の猶予のうち 1 秒前後しか使っていません。
  - 受入の「版上げ 1 回の前後で確かめる」でこの欠けを見た人が、自然に出す要求です。

### 2. 関係する主張・前提と、予想に反して変わる物
- **関係する主張:**
  - S-conc の予想の範囲は「なし(M-stamp の不変条件)」「M-entry・M-core は変わらない」。
  - 不変条件は「止まりの行は ShipLimits.end_seconds の内で送ってから終わる」。
  - M-stamp の隠す物は「送りの上限」、M-entry は「送り先の値・形は知らない」。
- **前提は守っています:** k8s の Pod と Recreate のまま。取り決め job-log/1 と collector も変えません。送り先が遅いのは S-conc 自身の条件です。上限つきで止まる性質も保ち、変えるのは上限の値だけです。
- **予想に反して変わる物:**
  - **M-stamp の公開契約。** `process_lines(plan, merged)` から `stamped_streams`、`shipping_of(plan)`、`JobLogShipper(plan)`(既定の SHIP_LIMITS を使う)と続く道に、上限を渡す口がありません(signature で実測)。coordinator だけ値を変える道は 2 つで、どちらも M-stamp の複数の責務を同時に触ります。
    - (a) 流れの差し替えの関数と、送り手を組む関数の両方に limits を足す。
    - (b) 送り先の決め(`coordinator_log_plan` / `JobLogTarget`)に上限を持たせる。送り先の型に止め方の方針が入り、`JobLogTarget` を組んでいる agora-controllers の `controllers/agora_sim/tests/test_job_log_route.hy` にも波及します。
    - (b) の変形として M-decl に env(例 `DOEFF_JOB_LOG_END_SECONDS`)を置く道もあります。この場合 M-entry は変わりませんが、M-stamp の名の組・決め・組み立て・流れを一度に触ります。
  - **M-entry((a) の場合)。** coordinator の値を選んで渡す役が要ります。値は M-decl の猶予(書かれていない 30 秒)と、Recreate の窓の兼ね合いで決まります。伸ばした秒だけ、全部の worker が届かない窓も延びます(今回の事故そのもの)。
  - **全体の既定値を上げる道は塞がっています。** ShipLimits の註が end_seconds を「shim の掃除の余裕 WorkerPolicy.shim-sweep-margin-ms 1.5 秒の内に、DRAIN_LIMIT_SECONDS(1.0)と並べて収まる値」と定めています。1.0 + 0.5 = 1.5 で、余りはありません。

### 3. 漏れている知識と、契約拡張との区別
- **漏洩(隠すはずの知識):**
  - 「止まる時に送りへ何秒使えるか」は、送り手が載っている所の止め方の知識です。shim は worker の WorkerPolicy で 1.5 秒、Pod の worker は猶予 120 秒、coordinator は既定 30 秒と Recreate の窓です。
  - M-stamp はこれを 1 つの値に固めていて、その値は worker の領域の shim の取り決めから導かれています。そのため coordinator は shim の時間の取り決めに縛られています。
  - 表は「送りの上限を隠す」と書きますが、この値は M-stamp が決められる物ではありません。だから、載っている所を 1 つ変える要求が、3 つの送り手(shim・worker・coordinator)を抱える M-stamp の公開契約を開かせます。code-quality の responsibility の文にも 3 つが並んでいます。
- **公開契約の拡張:** limits の引数を足すこと自体は拡張です。ただし表の「隠す物: 送りの上限」と逆向きで、漏洩の結果として要る物です。意図した拡張点ではありません。
- **配線の変更:** M-entry が値を渡すこと、M-decl が env を置くこと。
- **共同の不変条件:**
  - 既存: end_seconds + DRAIN ≤ shim の掃除の余裕。註にあるだけで、検での強制はありません(grep で確認)。
  - 新しく生まれる物: coordinator の上限 + 止まる時間 ≤ M-decl の猶予。今の M-decl には猶予の値がありません。
- M-core・M-upper・M-store は変わりません。

### 4. 最小の再現と観測結果(実測)
作業木の根 `/home/ubuntu/.worktrees/doeff-wt-design-check-0b0a` で、`uv run --group dev python` から実行しました。script は `/tmp/tmp.Us3SzEeOzd/` に置き、共有の file は書き換えていません。

**A. process の中だけの再現(`repro_a.py`)。** 本物の `JobLogShipper` と `SHIP_LIMITS` を使い、送り先だけ「答えに 0.8 秒かかる代わり」に置き換えました。
```python
s = JobLogShipper(TARGET, SHIP_LIMITS, lambda _e: SlowPoster(0.8)); s.begin()
s.take("coordinator: :8080 で受けます\n".encode(), time.time_ns())
time.sleep(1.2)   # 1 つ目の束の POST が飛んでいる最中
s.take("coordinator: 止まりました\n".encode(), time.time_ns())
s.end()           # atexit の StampedStreams.end が呼ぶのと同じ
```
- end_seconds 0.5: end は 0.50 秒で戻りました。送れた束は 0 で、止まりの行は溜めに残ったままです。数えは `ShipCounts(seen=2, dropped=0)` で、捨てたとも数えていません。process が終われば送りの thread ごと消え、後の行も無いので、`doeff.dropped` の印もどこにも付きません。
- end_seconds 5.0: 1.40 秒で止まりの行まで送れました。

**B. 本物の coordinator の入口(`repro_b.py <遅れの秒> <dir> <止めるまでの秒>`)。** 既存の検 `coordinator_run` と同じ起こし方です。stdout と stderr を別の file にし、送り先と POD_NAME・POD_NAMESPACE を置き、答えに 2 秒かかる受け手へ向けて、`/livez` が答えてから 1.5 秒後に SIGTERM を送りました。
- 5 回のうち 4 回、stdout の最後の行「coordinator: 止まりました」が受け手に届きませんでした。1 回はその直前の「遅い fsync」の行も届いていません。
- SIGTERM から終わりまでは 0.87〜1.58 秒で、猶予の大半が残っていました。
- 対照として受け手の遅れを 0.05 秒にすると、全部の行が届きました。

**観測すべき点:** stdout の末尾の行が送り先に無いこと、終わるまでの時間、送り先に欠けの印が無いこと。直した後は、同じ条件で止まりの行が届き、終わりが猶予の内に収まり、shim の検の時間が変わらないことを見ます。

### 5. 未確認の前提・不足する情報
- **推測:** 本番の otel-collector が、止まる刻に 0.5 秒を越えて答えない頻度は測っていません。失う条件は「飛んでいる POST の残り + 次の POST」が上限を越えることです。
- **推測:** 模擬の受け手は、送り手が待ちを切った後も、飛んでいた本文を受けました。そのため失ったのは、飛んでいる POST の後に溜まった行です。本物の collector が同じように受けるかは確かめていません。
- **別の原因の可能性(推測・責務の分離とは別の話):** `foundation/otlp_post.py` は接続を使い回し、失敗した POST をやり直しません。collector が遊んでいる接続を閉じる設定なら、静かな時間の後の最初の束が落ちえます。OTel の既定がそうなっているかは未確認です。本番で止まりの行が欠けた原因がこちらなら、直す所は 1 か所で、この例の波及は起きません。
- **推論・未試行:** 既定値を全体で上げた時の shim への害(掃除の KILL に先を越される、終了コード)は、註から推し量っただけで試していません。
- **探索した他の候補(主な例にしなかった理由):**
  - **1 行の上限の変更:** `line_bytes` 4096 は、collector 側の宣言の annotation と、agora-controllers の検が doeff の `SHIP-LIMITS` を import して揃えている共同の不変条件です。変えるのは job-log/1 の数の値の変更で、S-store の適用範囲外です。
  - **認証の要る店:** job-log/1 の送り方の拡張で、範囲外です。
  - **Pod の worker の自分の行:** 次の 2 つで code の変更が要りそうですが、主張の外の役で、動かしてはいません。
    - merged を使うかの判断が、各入口に直に書かれている(coordinator の入口は `:merged True` と書く)。
    - 世代が重なると、同じ WORKER_NAME の 2 つの process を、店が doeff.instance で読み分けられない。
  - **S-eff:** `slog_handler` が呼ばれた時点の sys.stderr へ書くことを確かめ、主張どおりでした。
  - **S-dist:** code の変更は見つかりませんでした。
  - 全数は調べていません。

WAIT: work — 呼び手(設計検証の会話)の次の依頼
