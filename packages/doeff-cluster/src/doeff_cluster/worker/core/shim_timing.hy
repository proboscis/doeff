;;; 入れ物 shim の時間(#2940 の 1 段目)— shim の猶予の導き方と、shim の期限が worker の KILL より前に来るかの判断。
;;;
;;; worker は job と入口の検めを shim の下の新しい process group に起こす。job を止める時は group へ止めの合図(TERM)を送り、停止の猶予
;;; (WorkerPolicy.stop-grace-ms)の後の拍で group へ KILL を送る(worker/core/policy の stop-actions — 拍で判じるので KILL は合図の後
;;; 停止の猶予 〜 停止の猶予 + 拍 の間に来る)。shim は合図から shim の猶予だけ job を待ち、job の group を強いて止めて子孫を片づけてから
;;; 終わる(片づけは 2 段目で入れる)。片づけが worker の KILL に先を越されないよう、shim の期限は停止の猶予より後にしない:
;;;   shim の猶予 + 掃除の余裕 ≤ 停止の猶予
;;; shim の猶予は停止の猶予と掃除の余裕(WorkerPolicy.shim-sweep-margin-ms)から導く — 導く所はここ 1 つ(job と検めの shim の引数・
;;; worker の側で shim を止める道の待ち・入口の検め・検が同じ値を読む)。worker の側で shim を止める道(終わりを観測していない子の回収・
;;; 時間切れの検め)も、合図から shim の期限まで待ってから強いて止める。判断は値を受け取る純関数で、値を集めるのは呼び手(worker の
;;; 入口の組み立て worker/entry/main の timing-checked と、検 tests/test_shim_timing.hy)。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy])

(val MODULE-TAGS {:context "worker" :role "judgment"})


(defrecord ShimSpans
  "shim の時間の内訳(ms): shim-grace = 止めの合図から shim が job の group を強いて止めるまで・sweep-margin = shim の掃除の余裕・
   stop-grace = worker が止めの合図から KILL を送るまで(停止の猶予)。"
  {:tags {:context "worker" :role "type"}}
  (#^ int shim-grace-ms)
  (#^ int sweep-margin-ms)
  (#^ int stop-grace-ms))


(defrecord ShimOutlastsTheKill
  "shim の期限が worker の KILL より後になる組 1 つ: deadline-ms = shim の期限(猶予 + 余裕)・kill-ms = worker が KILL を送るまで
   (停止の猶予)・spans = 判じた内訳。"
  {:tags {:context "worker" :role "type"}}
  (#^ int deadline-ms)
  (#^ int kill-ms)
  (#^ ShimSpans spans))


(defk shim-spans [policy]
  {:pre [(: policy WorkerPolicy)] :post [(: % ShimSpans)] :tags {:context "worker" :role "judgment"}}
  "worker の方針から shim の時間の内訳を作るため — shim の猶予は停止の猶予から掃除の余裕を引いた値(負の待ちは無いので 0 で止める —
   余裕が停止の猶予を越える組は shim-ends-before-the-kill が名指す)。"
  (ShimSpans :shim-grace-ms (max 0 (- policy.stop-grace-ms policy.shim-sweep-margin-ms))
             :sweep-margin-ms policy.shim-sweep-margin-ms
             :stop-grace-ms policy.stop-grace-ms))


(defk shim-deadline-ms [spans]
  {:pre [(: spans ShimSpans)] :post [(: % int)] :tags {:context "worker" :role "judgment"}}
  "止めの合図から shim が掃除を終えて終わるまでの上限(猶予 + 余裕)を、判断と、worker の側で shim を止める道の待ちの両方で同じ数に
   するため。"
  (+ spans.shim-grace-ms spans.sweep-margin-ms))


(defk shim-ends-before-the-kill [spans]
  {:pre [(: spans ShimSpans)] :post [(: % (get tuple #(ShimOutlastsTheKill ...)))] :tags {:context "worker" :role "judgment"}}
  "shim の期限が worker の KILL より後なら、その破りを 1 つ返す(空なら緑)— shim の掃除が worker の KILL に先を越されない事を値の組から
   判じるため(頭の註の式)。"
  (<- deadline int (shim-deadline-ms spans))
  (if (> deadline spans.stop-grace-ms)
      #((ShimOutlastsTheKill :deadline-ms deadline :kill-ms spans.stop-grace-ms :spans spans))
      #()))
