## 結論

検査を通るのに責務を侵す失敗例を 1 つ作りました。中身は「worker の行に限り、溜めが溢れたら最大 2 秒待ってから捨てる」という変更です。line_stamp.py だけを変えます。手で走らせた検査はすべて通りました。一方で、公開の契約「送りは worker の write を止めない」は実測で破れています。

## 1. 機能要求と差分

**要求(もっともらしい形):** 会話から `agora job-log worker/zeus-native` で読みたい行(「worker: heartbeat の間の遅れ」など)が、collector が遅い間に溜めの上限(10,000 行)を越えると捨てられ、番号の飛びしか残らない。worker の行は少なく、調べに欠かせないので、worker の行だけは溢れた時に空きを最大 2 秒待ってから捨てたい。job の行(shim・warm_child)は今のままにする。

**差分**(写しに当てたもの。全文は `/tmp/dc-lt8pqm-TGXX/line_stamp.diff`):
```diff
 class ShipLimits:
     pause_seconds: float = 2.0
+    full_wait_seconds: float = 0.0
 SHIP_LIMITS = ShipLimits()
+WORKER_SHIP_LIMITS = replace(SHIP_LIMITS, full_wait_seconds=2.0)
 class JobLogShipper:
+    def room_for(self, lines: tuple[OpenLine, ...]) -> bool:
+        return self._mut_end_at is not None or admitted(lines, self._mut_counts, len(self.queue), self.limits) == len(lines)
     def queued(self, lines):
         with self.ready:
+            if self.limits.full_wait_seconds > 0:
+                self.ready.wait_for(lambda: self.room_for(lines), timeout=self.limits.full_wait_seconds)
     # next_batch で行を取った後と dropped_rest の後に self.ready.notify_all() を足す
-def shipping_of(plan) / stamped_lines(plan)
+def shipping_of(plan, limits=SHIP_LIMITS) / stamped_lines(plan, limits=SHIP_LIMITS)   # 中で limits を渡す
 def process_lines(plan):
-            lines = stamped_lines(plan)
+            lines = stamped_lines(plan, WORKER_SHIP_LIMITS)
```
import の追加、cast、ignore、検査の設定やbaselineの変更はありません。

## 2. 本来の持ち主と、破れる契約

- **「溢れたら捨てて数え、待たない」は M-shipper の公開契約です。** 呼び手が選べる値ではありませんでした。変更後は M-process(`process_lines`)が「待つか捨てるか」を呼び手ごとに選びます。判断が M-process へ移ります。
- **書く処理が送りに縛られます。** M-writer の書き手の thread(`pumped` から `take` を呼ぶ所)が、M-shipper の溜めの空き、つまり POST の進みを待つようになります。すると pipe が詰まり、worker の業務の thread の write がネットワークの都合で止まります。
- **破れる主張は次の 3 つです。**
  - 公開の契約「送りは worker の write を止めない」
  - S3「worker の write も終わりも送りに待たされず」
  - S5「書き手は送りに待たされない」
- **頭の註が古いまま残ります。** line_stamp.py の頭の註には「書き手は待たない」と書かれたままです。
- **止まるまでの時間が規則の外に出ます(推測を含む)。** worker が止まるまでの時間は WorkerPolicy と timing_rules が持つ知識です。今回できた終わりの待ちはその外にあります。

## 3. 各検査が拒否しない理由

| 検査 | 結果 | 理由 |
|---|---|---|
| code-quality(module 契約) | 未実行(静的に判断) | line_stamp.py 全体が 1 つの module `doeff-cluster-line-stamp` で、paths と public はこの file 1 つです。設計の M-writer・M-shipper・M-plan・M-process は契約の上では分かれていないので、file の中での責務の移動は見えません。新しい依存の辺、型の逃げ道、動的 import もありません。 |
| doeff-linter | 実測で error 0・warning 0 | DOEFF106 は Hy だけが対象です(SPECIFICATION「DOEFF106・107 は Hy だけ」)。DOEFF004 は os.environ と os.getenv への参照しか見ず、今回は足していません。木の source から debug 版の linter を作り、line_stamp.py と architecture.hy に当てました。info は 47 件(DOEFF014 が 4、DOEFF022 が 43)です。warning の基点との比べも rc=0 でした。 |
| test_job_log_ship.py | 実測で 8 本が個別に pass | 溢れの検は、明示した ShipLimits(待ちの既定 0)で JobLogShipper を作るので、捨てる道を通ります。worker の 4 本は答える送り先に 3〜4 行を送るだけで、溜めが満ちません。「worker の道で溜めが満ちる」組を通る検はありません。 |
| test_job_log_route.hy | 実測で 2 本が pass | 1 行の上限と印の名だけを比べています。失敗した POST の検は `replace(SHIP-LIMITS …)` を使い、待ちは既定の 0 のままです。 |

## 4. 確かめ方(どれも実測)

- **最小の入力:** `/tmp/dc-lt8pqm-TGXX/demo/take_wait.py` は、溢れの検と同じ入力(溜め 5 行、答えない送り先、20 行の塊)で、`take` が戻るまでの秒を測ります。
  - 元の版: SHIP_LIMITS で 0.000 秒。
  - 変更後: SHIP_LIMITS は 0.000 秒で、WORKER_SHIP_LIMITS は 2.000 秒です。検自身の上限は 0.5 秒なので、検の上限を越えています。dropped=15 は元の版と変わらず、待っても結局捨てています。
- **worker の代わりの process:** `/tmp/dc-lt8pqm-TGXX/demo/stall_demo.py` は、検の WORKER_PROCESS と同じ呼び方で、systemd と同じく同じ path を 2 度開いて起こします。答えない collector の下で 20,000 行を書き、heartbeat 役の thread が並行して書きます。2 回走らせた結果です。

| | 元の版 | 変更後 |
|---|---|---|
| 20,000 行の書き込み | 0.075 秒 / 0.31 秒 | 6.11 秒 / 4.23 秒 |
| heartbeat の 1 行の write の最大の待ち | 0.0 秒 / 0.10 秒 | 1.98 秒 / 1.99 秒 |
| process の終わりの待ち(2 回目だけ測った) | 0.61 秒 | 4.76 秒 |
| file に入った行 | 20,000 行すべて | 20,000 行すべて |

file は欠けず、破れたのは「write と終わりを止めない」の部分だけです。

## 5. 未確認の前提と不足

- **推測:**
  - worker の heartbeat を送る thread が stderr へ同期で書くかどうかは読んでいません。読んでいれば、2 秒の止まりが heartbeat の遅れになり、coordinator が worker を落ちたとみなす所までつながります。fence の値も読んでいません。
  - 本番で溜めが満ちる頻度は分かりません。答えない送り先では、上限の値からの計算で 1 秒に約 143 行しか流れませんが、worker の実際の行の量は測っていません。
  - 1 つの塊が 10,000 行を越えると、送り先が健全でも毎回 2 秒待つはずです。これは導いただけで、測っていません。
- **未実行の検査:**
  - code-quality は基底の git の比べと dotfiles の venv が要り、pyright を手で走らせない決まりもあるため、走らせていません。
  - linter は木から作った debug 版で、commit の hook が使う固定の binary ではありません。その binary はこの環境に無く、hook のままだと「測れない」と出して rc 0 で通ります(実測)。
  - test_job_log_ship.py は 27 本のうち 8 本だけ走らせました。`test_a_silent_destination_does_not_hold_the_shim_end` は検の枠が空くのを待つ間に時間切れになり、測れていません。
  - 契約の検は 12 本のうち 2 本です。仮の置き場から走らせ、import の形を importlib に替え、root の conftest(受付の門と doeff の最小 commit の確かめ)を外しました。controllers は origin/main でない ~/repos/agora-controllers の作業木から読んでいます。日次の実行と同じ条件ではありません。
- **食い違い:** 設計の文は検を「worker の 4 本と既存の 27 本」としていますが、版 b85e20d81 で集めた項目は合わせて 27 でした。
- **共有の木への副作用:**
  - import によって、doeff の木と ~/repos/agora-controllers の下に `__pycache__` が書かれました。source は変えていません。
  - ~/repos/agora-controllers で読むだけのつもりの `git status` を 1 度実行しました。git が index を書き直した可能性があります。

WAIT: work — 頼んだ側の次の依頼
