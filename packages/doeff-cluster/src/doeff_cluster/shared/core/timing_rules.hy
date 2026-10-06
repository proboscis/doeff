;;; 時間の不変条件 — 条 C4 timing-outlasts-the-self-stop(packages/doeff-cluster/architecture.hy の coordinator の :invariants が名指す・
;;; #2806)。coordinator が連絡の途絶えた worker の印の無い job を他へ移す時刻(ClusterTiming.reassign-after-ms)は、その
;;; worker が自分で job を止め切る時刻より後でなければならない — 先なら、止まり切る前の古い process と新しい担い手の process が重なる
;;; (条 C2 one-place-per-job の破り)。
;;;
;;; 止め切りの最悪の道: 最後に成功した heartbeat の直後に送った heartbeat が、返事の上限と接続の上限まで答えない(その間 worker の周期は
;;; 返事を待って止まっている — 周期の頭の自己停止の判断 desired-after-silence も走らない)→ 失敗の枝で fence を越えたと判じて止めの合図
;;; → 子の停止の猶予(止めの合図から待つ秒 + 強く止めてから待つ秒)。なので
;;;   移し替え ≥ fence + 返事の上限 + 接続の上限 + 子の停止の猶予
;;; C4 は C4b(止め切りの後、job の子孫は 1 つも生きていない — worker の条 stopped-job-leaves-no-descendant・#2940 の 2 段目)が成り立つ
;;; 前提の上で意味を持つ。
;;; 値の定義の置き場は 1 か所にまとまっていない(fence・移し替え・返事の上限 = shared/intent/protocol の ClusterTiming・接続の上限 =
;;; foundation/coordinator_http の CONNECT-SECONDS・停止の猶予 = worker/intent/worker_model の WorkerPolicy)ので、判断は
;;; 値を受け取る純関数にし、値を集めるのは呼び手(worker の入口の組み立てと、条の検 tests/test_cluster_timing.hy)。
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import doeff_cluster.shared.intent.protocol [ClusterTiming])

(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})


(defrecord SelfStopSpans
  "条 C4 の記録 = worker が連絡の途絶から自分の job を止め切るまでの時間の内訳(ms)。fence = 途絶で止め始めるまで・reply = heartbeat の
   返事を待つ上限・connect = 接続の上限・stop-grace = 子へ止めの合図を送ってから待つ時間・kill-grace = 強く止めてから終わりを待つ時間。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ int fence-ms)
  (#^ int reply-ms)
  (#^ int connect-ms)
  (#^ int stop-grace-ms)
  (#^ int kill-grace-ms))


(defrecord ReassignTooEarly
  "条 C4 の破り 1 つ: 移し替え reassign-ms が、worker の止め切り needed-ms(spans の内訳の和)より前。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ int reassign-ms)
  (#^ int needed-ms)
  (#^ SelfStopSpans spans))


(defk self-stop-ms [spans]
  {:pre [(: spans SelfStopSpans)] :post [(: % int)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker が連絡の途絶から自分の job を止め切るまでの最悪の時間(内訳の和)を、条 C4 の比べと破りの名指しの両方で同じ数にするため。"
  (+ spans.fence-ms spans.reply-ms spans.connect-ms spans.stop-grace-ms spans.kill-grace-ms))


(defk timing-outlasts-the-self-stop [reassign-ms spans]
  {:pre [(: reassign-ms int) (: spans SelfStopSpans)] :post [(: % (get tuple #(ReassignTooEarly ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "条 C4: 移し替えが worker の止め切りより前なら、その破りを 1 つ返す(空なら緑)— coordinator が他へ置いた job と、まだ止まり切って
   いない古い process が重ならないことを、値の組から判じるため(頭の註の最悪の道)。"
  (<- needed int (self-stop-ms spans))
  (if (< reassign-ms needed)
      #((ReassignTooEarly :reassign-ms reassign-ms :needed-ms needed :spans spans))
      #()))


(defk scaled-timing [ratio]
  {:pre [(: ratio int)] :post [(: % ClusterTiming)] :tags {:context "doeff-cluster" :role "judgment"}}
  "本番の既定の時間の窓を全部、同じ比 ratio で延ばした設定を作るため — 長い仮想の時間を回す模擬の筋書きが、heartbeat と待ちの送り直しの
   間隔を延ばす唯一の入口(窓をばらばらに渡さない・Mac の調整役の決定 2026-10-07 04:4x の条件 (b)・#3865)。順の検めは
   ClusterTiming が作る時に当てる。"
  (val base (ClusterTiming))
  (ClusterTiming :lease-ms (* ratio base.lease-ms) :fence-ms (* ratio base.fence-ms) :reassign-after-ms (* ratio base.reassign-after-ms)
                 :silent-worker-wait-ms (* ratio base.silent-worker-wait-ms) :keep-fence-ms (* ratio base.keep-fence-ms)
                 :worker-forget-ms (* ratio base.worker-forget-ms) :watch-max-ms (* ratio base.watch-max-ms)
                 :client-reply-ms (* ratio base.client-reply-ms) :inbox-reply-ms (* ratio base.inbox-reply-ms)))
