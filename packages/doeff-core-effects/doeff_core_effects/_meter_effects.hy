;;; 計器の effect — 動き続ける process の中で、数え上げ・秒の観測・今の値を積み、断面を読む(ADR-DOE-CORE-EFFECTS-004)。
;;; 業務の語を持たない土台の語彙。名の綴り(Prometheus の名など)は呼び手が決める。
;;;
;;;   CountMetric     counter の name に amount を足す(既定 1)。amount = 0 は「0 を置く」— 無いことと 0 を読み手が区別しなくて済む。答え = None。
;;;   ObserveSeconds  秒の合計と回数に seconds を積む。設定に桁の表があれば、当たる桁ごとの累積の counter <name>_<label> と
;;;                   <name>_<inf の label> も同じ 1 回で進める。答え = None。
;;;   ObserveSecondsBatch
;;;                   秒の観測(SecondsObservation — 名と秒)の列 observations を 1 回で積む — ObserveSeconds を列の順に 1 つずつ撃ったのと
;;;                   同じ断面になる(秒の合計・回数・桁の counter とも、名ごとに列の順で足す)。1 つの出来事で名を変えて何度も秒を観測する
;;;                   呼び手(記録の service の要求ごとの区間の秒 — 1 要求で 7〜9 個)が、効果を撃つ回数を 1 回にするため。
;;;                   答え = None。
;;;   SetGauge        gauge の name を value に置き換える。答え = None。
;;;   ReadMeter       今の断面(MeterSnapshot)を読む。
;;;
;;; 答え手: process-meter-handler(process_meter.hy — 本物。process に 1 つの置き場を名前で共有し、別の run・別の thread が同じ計器を
;;; 読み書きする。GC の停止も積める)と memory-meter-handler(memory_meter.hy — 1 つの run の中の状態だけ。時計・gc・thread を読まない)。
;;; 2 つは同じ契約のテストを通す(tests/test_meter_contract.hy)。
;;;
;;; 守る事: 1 回の書きは 1 つの名の中(秒の合計・回数・その名の桁の counter)で 1 度に見える。
;;; 守らない事: 名をまたぐ書き(全数と内訳のように 2 つの名へ積む)は 1 度に見えるとは限らない — 間の読みは片方だけ進んだ断面を見る。
;;;             ObserveSecondsBatch の列の名をまたぐ書きも同じく約束しない(今の答え手 2 つは 1 回で差し替えるが、読み手はそれに頼らない)。
;;;
;;; 断面の形(counters・gauges・秒の合計と回数)は doeff-cluster の ReportMetrics の報告の形と同じ 3 つに揃えてある(後で断面を報告へ渡せる)。
;;; 下の純関数(metered・counted・observed-batch・observed・gauged)は 2 つの答え手が同じ計算をするための 1 か所で、I/O を持たない。
;;; 数えと秒の観測の計算は metered の 1 か所(書きの列 — CountMetric・ObserveSeconds・ObserveSecondsBatch を撃った順 — を 1 度に積む。
;;; counted・observed-batch・observed は書き 1 つの列)。memory の答え手は書きを溜めて、読みの時か溜まりが METERED-WRITES 本に届いた時に
;;; metered で 1 度に畳む(書きのたびに断面の写像を丸ごと写さない)。
(require doeff-hy.macros [defeffect defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import functools [reduce])
(import itertools [groupby])
(import operator [add itemgetter])
(import doeff_hy._frozen [FrozenMap])


(defrecord SecondsTotal
  "秒の観測 1 系列: total = 観た秒の合計・count = 観た回数。"
  {:tags {:context "meter" :role "type"}
   :check [(>= count 0)]}
  (#^ float total)
  (#^ int count))


(defrecord MeterSnapshot
  "計器の断面: counters = 名 → 積んだ量・gauges = 名 → 今の値・durations = 名 → 秒の合計と回数。"
  {:tags {:context "meter" :role "type"}}
  (#^ (get FrozenMap float) counters)
  (#^ (get FrozenMap float) gauges)
  (#^ (get FrozenMap SecondsTotal) durations))


;; 何も積んでいない計器。
(val EMPTY-METER (MeterSnapshot :counters (FrozenMap) :gauges (FrozenMap) :durations (FrozenMap)))


(defrecord MeterBucket
  "秒の観測の桁 1 つ: label = counter の名の末尾(例 le_200ms)・ceiling = この桁に入る秒の上限(以下)。"
  {:tags {:context "meter" :role "type"}}
  (#^ str label)
  (#^ float ceiling))


(defrecord MeterSettings
  "計器の設定: buckets = 秒の観測の桁の表(空なら桁の counter を積まない)・inf-label = 全部を数える桁の名の末尾・
   gc-pause-name = GC の停止を積む秒の観測の名(None なら積まない — 本物の答え手だけが読む。memory の答え手は GC を見ない)。"
  {:tags {:context "meter" :role "type"}}
  (setv #^ (get tuple #(MeterBucket ...)) buckets #())
  (setv #^ str inf-label "le_inf")
  (setv #^ (| str None) gc-pause-name None))


(defrecord SecondsObservation
  "秒の観測 1 つ(ObserveSecondsBatch の列の 1 項): name = 積む秒の観測の名・seconds = 観た秒。ObserveSeconds の欄と同じ。"
  {:tags {:context "meter" :role "type"}}
  (#^ str name)
  (#^ float seconds))


(defeffect CountMetric
  "counter の name に amount を足す(頭の註)。"
  {:fields [(: name str) (: amount float 1.0)]
   :answer None
   :tags {:context "meter" :role "foundation"}})


(defeffect ObserveSeconds
  "秒の合計と回数に seconds を積み、桁の counter も同じ 1 回で進める(頭の註)。"
  {:fields [(: name str) (: seconds float)]
   :answer None
   :tags {:context "meter" :role "foundation"}})


(defeffect ObserveSecondsBatch
  "秒の観測の列 observations を、ObserveSeconds を列の順に 1 つずつ撃ったのと同じ断面へ 1 回で積む(頭の註)。空の列は何も積まない。"
  {:fields [(: observations (get tuple #(SecondsObservation ...)))]
   :pre [(: observations tuple) (all (gfor observation observations (isinstance observation SecondsObservation)))]
   :answer None
   :tags {:context "meter" :role "foundation"}})


(defeffect SetGauge
  "gauge の name を value に置き換える(頭の註)。"
  {:fields [(: name str) (: value float)]
   :answer None
   :tags {:context "meter" :role "foundation"}})


(defeffect ReadMeter
  "今の断面を読む(頭の註)。"
  {:fields []
   :answer MeterSnapshot
   :tags {:context "meter" :role "foundation"}})


;; 名と量の対の名を引く(書きの列を名ごとにまとめる鍵)。
(val NAME-OF-PAIR (itemgetter 0))


(defk grouped-in-order [pairs]
  {:pre [(: pairs (get tuple #(tuple ...)))] :post [(: % dict)] :tags {:context "meter" :role "judgment"}}
  "名と値の対の列を、名 → その名の値の列(列の順)にするため。名の並びは最初に現れた順(写像に足す順を、1 つずつ積んだ時と同じにする)。
   名で安定に並べてからまとめるので、列が長くても名の数 × 列の長さの照らしにならない。"
  (val grouped (dfor #(name group) (groupby (sorted pairs :key NAME-OF-PAIR) :key NAME-OF-PAIR)
                     name (tuple (gfor #(_ value) group value))))
  (dfor name (dict.fromkeys (gfor #(name _) pairs name)) name (get grouped name)))


(defk metered [snapshot settings writes]
  {:pre [(: snapshot MeterSnapshot) (: settings MeterSettings) (: writes (get tuple #((| CountMetric ObserveSeconds ObserveSecondsBatch) ...)))]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "数えと秒の観測の書きの列 writes(撃った順)を、1 つずつ積んだのと同じ新しい断面へ 1 度に積むため(元の断面は変えない — 頭の註): 名ごとに
   書きの順の量と秒を、元の値から列の順に 1 つずつ足すので、浮動小数の丸めまで 1 つずつ積んだのと同じ(列の秒を先に足し合わせてから積むと
   丸めが変わる)。値(SecondsTotal)と写像の写しは名ごと・写像ごとに 1 度だけ作る — 書きを溜めて畳む memory の答え手が、溜まりが長くても
   書き 1 つあたり定まった手間で畳めるように。"
  (val counters snapshot.counters)
  (val durations snapshot.durations)
  ;; 書きごとの秒の観測(名と秒の対 — 数えは秒を積まない)と、counter へ足す量(名と量の対 — 数えはその名へ amount・秒の観測は桁の表の
  ;; 当たる桁と inf の桁へ 1 つずつ・桁の表が空なら足さない)を、撃った順の列に開く。
  (val per-write (tuple (gfor write writes
                              (cond (isinstance write ObserveSecondsBatch) (tuple (gfor observation write.observations
                                                                                        #(observation.name observation.seconds)))
                                    (isinstance write ObserveSeconds) #(#(write.name write.seconds))
                                    True #()))))
  (val increments (! (grouped-in-order
                       (tuple (gfor #(write pairs) (zip writes per-write)
                                    pair (if (isinstance write CountMetric)
                                             #(#(write.name write.amount))
                                             (tuple (gfor #(name seconds) (if settings.buckets pairs #())
                                                          counter (+ (lfor bucket settings.buckets :if (<= seconds bucket.ceiling)
                                                                           (+ name "_" bucket.label))
                                                                     [(+ name "_" settings.inf-label)])
                                                          #(counter 1.0))))
                                    pair)))))
  (val observed (! (grouped-in-order (tuple (gfor pairs per-write pair pairs pair)))))
  (val bumped (dfor #(name amounts) (.items increments) name (reduce add amounts (float (.get counters name 0.0)))))
  (val rows (dfor #(name seconds) (.items observed)
                  name (let [row (.get durations name)]
                         (SecondsTotal :total (reduce add seconds (if (is row None) 0.0 row.total))
                                       :count (+ (if (is row None) 0 row.count) (len seconds))))))
  (MeterSnapshot :counters (if bumped (.updated counters bumped) counters)
                 :gauges snapshot.gauges
                 :durations (if rows (.updated durations rows) durations)))


(defk counted [snapshot name amount]
  {:pre [(: snapshot MeterSnapshot) (: name str) (: amount float)]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "counter の name に amount を足した新しい断面(元の断面は変えない — 計算は metered の書き 1 つの列)。"
  (! (metered snapshot (MeterSettings) #((CountMetric :name name :amount amount)))))


(defk gauged [snapshot name value]
  {:pre [(: snapshot MeterSnapshot) (: name str) (: value float)]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "gauge の name を value に置き換えた新しい断面。"
  (MeterSnapshot :counters snapshot.counters :gauges (.updated snapshot.gauges {name value})
                 :durations snapshot.durations))


(defk observed-batch [snapshot settings observations]
  {:pre [(: snapshot MeterSnapshot) (: settings MeterSettings) (: observations (get tuple #(SecondsObservation ...)))]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "秒の観測の列 observations を列の順に積んだ新しい断面を作るため(元の断面は変えない): 観測ごとに、その名の秒の合計と回数に秒を積み、
   桁の表の当たる桁と inf の桁の counter を進める。ObserveSeconds を列の順に 1 つずつ積んだのと同じ断面になる — 秒の合計も counter も
   名ごとに列の順で 1 つずつ足すので、浮動小数の丸めまで同じ(列の秒を先に足し合わせてから積むと丸めが変わる)。計算は metered の
   書き 1 つの列。"
  (! (metered snapshot settings #((ObserveSecondsBatch :observations observations)))))


(defk observed [snapshot settings name seconds]
  {:pre [(: snapshot MeterSnapshot) (: settings MeterSettings) (: name str) (: seconds float)]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "秒の合計と回数に seconds を積み、桁の表の当たる桁と inf の桁の counter を進めた新しい断面を作るため(1 つの名の中を 1 度に変える —
   計算は observed-batch の観測 1 つの列)。"
  (! (observed-batch snapshot settings #((SecondsObservation :name name :seconds seconds)))))
