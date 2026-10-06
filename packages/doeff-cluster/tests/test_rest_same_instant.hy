;; 模擬の時計の下で sim の宿が worker の静かな拍をまとめて眠っても(sim/local.hy の rest-quietly・旗 skip-idle)、同じ刻に起きる出来事
;; (worker の拍・宿の真実の書き換え・筋書きの読み)の順と、その刻に見える宿の真実が、1 拍ずつの走り(skip-idle 偽)と食い違わない事の検
;; (#3054 の根 F-1・#3065)。根 F-2(#3066)で眠りを 1 回の待ちにし、起きた刻までの拍をまとめて写す形に替える時の門。
;;
;; 眠っている宿の約束: 宿は、起きた時に通った拍をまとめて写すので、読む刻には宿の真実が数拍遅れて見えることがある。ただし写した拍は、1 拍ずつの走りが
;; 届けた heartbeat の列の頭と同じ数・同じ刻・同じ中身。比べる物:
;;   - 各読みで、眠る走りの worker ごとの宿の真実が、1 拍ずつの走りの同じ読みの「頭」: 遅れた拍の数 × 拍の間隔 = 最後に届いた刻の差
;;     (0 以上 — 拍の欠けも余りも無い)。
;;   - 同じ(届いた拍の数・最後に届いた刻)に着いた読みどうしで、送った状態の報告(中身)が同じ。
;;   - coordinator の置き場の書きの列(判断とその刻 — test_idle_skip.hy の same-decisions)。
;; 読む刻 = 静かな区間の中の刻・拍の刻ちょうど(その刻の拍の timer より前に登録した眠りで着く読みと、後に登録した眠りで着く読み — 同じ刻の
;; 前後は timer を登録した刻の順で決まる・#2850)・拍の刻ちょうどに宿の真実を書き換えた(宣言し直し — 眠っている宿の呼び鈴が鳴る)直後と、
;; その後の静かな区間の中。
;; 失敗ケース = 眠りの中の拍を 1 つ飛ばして写す形(届いた拍の数と最後に届いた刻が合わない)と、送った状態の中身が違う形は、比べ
;; (truth-breaches)が読みの名で名指す — 見本の列を 1 つ書き換えて確かめる。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])
(import collections.abc [Callable])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.sim.local [HostTruthOf HostTruth Redeclare])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [quitters quitters-v2])
(import tests.test_idle_skip [TWO-WORKERS QUIET-POLICY QUIET-TICK-SECONDS Trace trace-of same-decisions])

;; worker の拍の間隔(QUIET-TICK-SECONDS = 宿の刻み — 拍は起きた刻から 10 秒ごと)。
(val TICK-MS 10000)


(defrecord TruthSeen
  "1 つの worker の宿の真実のうち、拍が届くたびに動く欄: beats = 届いた拍の数・last-ok = 最後に届いた拍の刻(epoch ms)・fresh = 新しさ・
   woken = 拍の間の呼び鈴が鳴った印・sent = 最後に送った状態の報告(綴り)。"
  (#^ int beats)
  (#^ int last-ok)
  (#^ bool fresh)
  (#^ bool woken)
  (#^ str sent))


(defrecord TruthSample
  "筋書きの読み 1 回: label = どの読みか・at = 読んだ刻(epoch ms)・seen = worker w1 と w2 の宿の真実の欄。"
  (#^ str label)
  (#^ int at)
  (#^ (get tuple #(TruthSeen ...)) seen))


(defk seen-of [truth]
  {:pre [(: truth HostTruth)] :post [(: % TruthSeen)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "宿の真実から、拍が届くたびに動く欄を写すため。"
  (TruthSeen :beats truth.beats :last-ok truth.last-ok-ms :fresh truth.fresh :woken truth.woken :sent (repr truth.sent-statuses)))


(defk sample [label]
  {:pre [(: label str)] :post [(: % TruthSample)] :tags {:context "doeff-cluster-test" :role "program"}}
  "今の刻と worker w1・w2 の宿の真実の欄を 1 つの見本に読むため。"
  (<- at int (now-epoch-ms))
  (<- w1 HostTruth (HostTruthOf "w1"))
  (<- w2 HostTruth (HostTruthOf "w2"))
  (<- one TruthSeen (seen-of w1))
  (<- two TruthSeen (seen-of w2))
  (TruthSample :label label :at at :seen #(one two)))


(defk sleep-until [target]
  {:pre [(: target int)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "刻 target(epoch ms)まで眠るため(今がその刻か後なら眠らない)。"
  (<- now int (now-epoch-ms))
  (when (> target now)
    (<- (Delay (/ (- target now) 1000.0))))
  None)


(defk beat-at [index]
  {:pre [(: index int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker w1 の index 番目の拍の刻(起きた刻 + index 拍)を知るため。"
  (<- truth HostTruth (HostTruthOf "w1"))
  (+ truth.boot-at (* index TICK-MS)))


(defk read-at-beat [index early]
  {:pre [(: index int) (: early bool)] :post [(: % TruthSample)] :tags {:context "doeff-cluster-test" :role "program"}}
  "index 番目の拍の刻ちょうどに着いて読むため。early = その刻の拍の timer(前の拍の刻に登録される)より前に眠りを登録する(1.5 拍前から
   眠る — その刻の拍より先に起きる)・偽 = 後に登録する(半拍前から眠る — 拍の後に起きる)。"
  (<- at int (beat-at index))
  (<- (sleep-until (- at (if early (+ TICK-MS (// TICK-MS 2)) (// TICK-MS 2)))))
  (<- (sleep-until at))
  (<- seen TruthSample (sample (.format "拍 {} の刻ちょうど({})" index (if early "拍より前に登録" "拍より後に登録"))))
  seen)


(defk quiet-reads []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 静かな区間の中・拍の刻ちょうど(2 通りの登録の順)・拍の刻ちょうどの宣言し直しの直後とその後で、宿の真実を読んだ見本の列を
   返すため。"
  (var samples #())
  (<- (Delay 35.0))
  (<- first TruthSample (sample "静かな区間の中(35 秒)"))
  (:= samples (+ samples #(first)))
  (for [#(index early) [#(6 True) #(8 False) #(11 True) #(13 False)]]
    (<- seen TruthSample (read-at-beat index early))
    (:= samples (+ samples #(seen))))
  (for [step (range 3)]
    (<- (Delay 7.3))
    (<- mid TruthSample (sample (.format "静かな区間の中({} 回目)" (+ step 1))))
    (:= samples (+ samples #(mid))))
  ;; 拍の刻ちょうどに宿の真実を書き換える(宣言し直し — 眠っている宿の呼び鈴が鳴る)。拍より後に登録した眠りで着く。
  (<- redeclare-at int (beat-at 18))
  (<- (sleep-until (- redeclare-at (// TICK-MS 2))))
  (<- (sleep-until redeclare-at))
  (<- (Redeclare (quitters-v2 sim-foundation)))
  (<- after TruthSample (sample "拍 18 の刻ちょうどの宣言し直しの直後"))
  (:= samples (+ samples #(after)))
  (for [step (range 3)]
    (<- (Delay 9.1))
    (<- later TruthSample (sample (.format "宣言し直しの後の静かな区間の中({} 回目)" (+ step 1))))
    (:= samples (+ samples #(later))))
  (<- last TruthSample (read-at-beat 26 True))
  (:= samples (+ samples #(last)))
  samples)


(defk lag-breach [label reference resting]
  {:pre [(: label str) (: reference TruthSeen) (: resting TruthSeen)] :post [(: % (| str None))]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "1 つの読みの 1 つの worker で、眠る走りの宿の真実 resting が 1 拍ずつの走りの reference の頭か(遅れた拍の数 × 拍の間隔 = 最後に
   届いた刻の差・0 以上)を判じるため。答え = 食い違いの文か None。"
  (val lag (- reference.beats resting.beats))
  (val gap (- reference.last-ok resting.last-ok))
  (if (and (>= lag 0) (= (* lag TICK-MS) gap))
      None
      (.format "{}: 届いた拍の数の遅れ {} と最後に届いた刻の差 {} ms が合わない({!r} と {!r})" label lag gap reference resting)))


(defk truth-breaches [every skipped]
  {:pre [(: every tuple) (: skipped tuple)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "1 拍ずつの走り every と、宿がまとめて眠る走り skipped の見本の列の食い違いを名指すため(空 = どの読みでも眠る走りの宿の真実が
   1 拍ずつの走りの頭で、同じ拍に着いた読みどうしの送った状態が同じ)。"
  (val counts (if (= (len every) (len skipped)) [] [(.format "読みの数 {} と {}" (len every) (len skipped))]))
  (var lags [])
  (for [#(a b) (zip every skipped)]
    (for [#(reference resting) (zip a.seen b.seen)]
      (<- breach (| str None) (lag-breach a.label reference resting))
      (when (is-not breach None)
        (:= lags (+ lags [breach])))))
  ;; 同じ(届いた拍の数・最後に届いた刻)の読みどうしで、送った状態の報告が同じ(拍の中身)。
  (val reference-sent (dfor sample every #(index seen) (enumerate sample.seen) #(index seen.beats seen.last-ok) seen.sent))
  (val contents (lfor sample skipped #(index seen) (enumerate sample.seen)
                      :if (and (in #(index seen.beats seen.last-ok) reference-sent)
                               (!= (get reference-sent #(index seen.beats seen.last-ok)) seen.sent))
                      (.format "{}: 拍 {} の送った状態が違う({} と {})" sample.label seen.beats
                               (get reference-sent #(index seen.beats seen.last-ok)) seen.sent)))
  (+ counts lags contents))


(deftest test-a-resting-host-shows-the-same-truth-at-every-instant-as-one-beat-at-a-time
  ;; 1 拍ずつの走り(skip-idle 偽)と、宿が静かな拍をまとめて眠る走り(skip-idle 真)で、どの読みも同じ刻に同じ宿の真実を見て、
  ;; coordinator の置き場の書きの列も一致する。まとめて眠る走りは宿の眠りを使っている(拍の数が少ない)。
  (<- every Trace (trace-of (quitters sim-foundation) (quiet-reads) True :workers TWO-WORKERS :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
  (<- skipped Trace (trace-of (quitters sim-foundation) (quiet-reads) False :workers TWO-WORKERS :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
  (assert (is-not every.answer None) "走りは答えを返している")
  (assert (> (len every.answer) 10) every.answer)
  ;; 読みの間に拍が届いている(比べが空でない — 届いた拍の数が読みごとに増える)。
  (val beats (lfor seen every.answer (. (get seen.seen 0) beats)))
  (assert (< (get beats 0) (get beats -1)) beats)
  (<- truth list (truth-breaches every.answer skipped.answer))
  (assert (= truth []) truth)
  ;; 判断の比べは置き場の書きの列だけ(筋書きの答え = 見本の列は上の頭の比べで見る — 眠る走りは数拍遅れて見えてよい)。
  (<- decisions list (same-decisions every (Trace :deltas skipped.deltas :steps skipped.steps :final skipped.final :answer every.answer :takes skipped.takes
                                                   :deposits skipped.deposits :heard-wakes skipped.heard-wakes)))
  (assert (= decisions []) decisions)
  (assert (< skipped.takes every.takes) #(skipped.takes every.takes)))


(defk with-w1-seen [samples label change]
  {:pre [(: samples tuple) (: label str) (: change Callable)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "見本の列のうち label で始まる読み 1 つの worker w1 の欄を change(TruthSeen → TruthSeen)で書き換えた写しを作るため(失敗ケースの見本)。"
  (val at (lfor #(i sample) (enumerate samples) :if (.startswith sample.label label) i))
  (assert (= (len at) 1) at)
  (val index (get at 0))
  (val target (get samples index))
  (val changed (replace target :seen #((change (get target.seen 0)) (get target.seen 1))))
  (+ (cut samples 0 index) #(changed) (cut samples (+ index 1) None)))


(deftest test-the-counterexamples-a-skipped-beat-and-a-different-report-are-named
  ;; 失敗ケース: 眠りの中の拍を 1 つ飛ばして写す形(届いた拍の数が 1 つ少ないのに最後に届いた刻は同じ)と、同じ拍に着いた読みで送った
  ;; 状態の報告が違う形は、比べが読みの名で名指す。
  (<- every Trace (trace-of (quitters sim-foundation) (quiet-reads) True :workers TWO-WORKERS :policy QUIET-POLICY :tick-seconds QUIET-TICK-SECONDS))
  (<- skipped-beat tuple (with-w1-seen every.answer "拍 11" (fn [seen] (replace seen :beats (- seen.beats 1)))))
  (<- lag list (truth-breaches every.answer skipped-beat))
  (assert (= (len lag) 1) lag)
  (assert (in "拍 11" (get lag 0)) lag)
  (<- other-report tuple (with-w1-seen every.answer "拍 13" (fn [seen] (replace seen :sent "[]"))))
  (<- content list (truth-breaches every.answer other-report))
  (assert (= (len content) 1) content)
  (assert (in "拍 13" (get content 0)) content))
