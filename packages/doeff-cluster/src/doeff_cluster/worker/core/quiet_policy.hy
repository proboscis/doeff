;;; worker の先の拍が何も変えないか(純粋な判断・I/O はしない — #2781)。
;;;
;;; 調整ループ(core/program.run-worker)は拍ごとに宣言を読み、観測し、plan の action を撃ち、状態の報告を出す。模擬の時計の下では、
;;; 何も変えない拍を 1 つずつ回す費用が検の所要の大半になる(拍 10 秒の worker 2 台で、仮想の 1 時間あたり約 3.3 秒 — #2769)。ここは
;;; 拍の後の記憶 state から、先の拍を本番の拍と同じ判断の関数(policy.plan・policy.statuses)で 1 拍ずつ試し、action が出るか状態の
;;; 報告が変わる最初の拍までの拍の数を答える。期限(準備の揃い・起こし直しの間・止めの猶予)を別に見積もらない — 判断そのものを試すので、
;;; 期限の求め忘れは起こり得ない。
;;; 観測は world-at(刻 → その刻の WorldView を答える Program — 模擬の宿が宿の真実から作る)で読む。process の終わりのように観測が前もって
;;; 知らない出来事は、宿がそれの起きた刻に知らせる(この判断の外)。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import collections.abc [Callable])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy WorkerState WorldView])
(import doeff_cluster.worker.core.policy [plan statuses sweep-actions declared-jobs declared-warm])


(defk quiet-beats [state policy world-at now limit tick-ms]
  {:pre [(: state WorkerState) (: policy WorkerPolicy) (: world-at Callable) (: now int) (: limit int) (>= limit 1) (: tick-ms int)]
   :post [(: % int) (<= 1 % limit)] :tags {:context "worker" :role "judgment"}}
  "now の拍の後、次に何かが変わる拍まで眠ってよい拍の数(1 以上 limit 以下 — 1 拍 = 模擬の宿の刻み tick-ms)を知るため。1 拍先から 1 拍ずつ、
   本番の拍と同じ判断(plan・sweep-actions・statuses)を試し、action が出るか、状態の報告が now の拍の報告と違う最初の拍までの数を答える。limit 拍の
   内に無ければ limit(その拍は試さずに打つ)。間の拍は何も変えないので、次に打つ拍とそこでの判断は 1 拍ずつ打った時と同じになる。"
  (<- desired tuple (declared-jobs state.declaration))
  (<- warm tuple (declared-warm state.declaration))
  (<- here WorldView (world-at now))
  (<- reported tuple (statuses now desired here state.records policy))
  (var beats 1)
  (var found None)
  (while (and (is found None) (< beats limit))
    (val at (+ now (* beats tick-ms)))
    (<- seen WorldView (world-at at))
    (if (or (! (plan at desired seen state.records policy :warm warm))
            (! (sweep-actions state.declaration seen))
            (!= (! (statuses at desired seen state.records policy)) reported))
        (:= found beats)
        (:= beats (+ beats 1))))
  (if (is found None) limit found))
