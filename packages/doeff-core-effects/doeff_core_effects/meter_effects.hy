;;; 計器の effect — 動き続ける process の中で、数え上げ・秒の観測・今の値を積み、断面を読む(agora-redesign #1440・ADR-DOE-CORE-EFFECTS-004)。
;;; 業務の語を持たない土台の語彙。名の綴り(Prometheus の名など)は呼び手が決める。
;;;
;;;   CountMetric     counter の name に amount を足す(既定 1)。amount = 0 は「0 を置く」— 無いことと 0 を読み手が区別しなくて済む。答え = None。
;;;   ObserveSeconds  秒の合計と回数に seconds を積む。設定に桁の表があれば、当たる桁ごとの累積の counter <name>_<label> と
;;;                   <name>_<inf の label> も同じ 1 回で進める。答え = None。
;;;   SetGauge        gauge の name を value に置き換える。答え = None。
;;;   ReadMeter       今の断面(MeterSnapshot)を読む。
;;;
;;; 答え手: process-meter-handler(process_meter.hy — 本物。process に 1 つの置き場を名前で共有し、別の run・別の thread が同じ計器を
;;; 読み書きする。GC の停止も積める)と memory-meter-handler(memory_meter.hy — 1 つの run の中の状態だけ。時計・gc・thread を読まない)。
;;; 2 つは同じ契約のテストを通す(tests/test_meter_contract.hy)。
;;;
;;; 守る事: 1 回の書きは 1 つの名の中(秒の合計・回数・その名の桁の counter)で 1 度に見える。
;;; 守らない事: 名をまたぐ書き(全数と内訳のように 2 つの名へ積む)は 1 度に見えるとは限らない — 間の読みは片方だけ進んだ断面を見る。
;;;
;;; 断面の形(counters・gauges・秒の合計と回数)は doeff-cluster の ReportMetrics の報告の形と同じ 3 つに揃えてある(後で断面を報告へ渡せる)。
;;; 下の純関数(counted・observed・gauged)は 2 つの答え手が同じ計算をするための 1 か所で、I/O を持たない。
(require doeff-hy.macros [defeffect defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff_hy.frozen [FrozenMap])


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


(defk counted [snapshot name amount]
  {:pre [(: snapshot MeterSnapshot) (: name str) (: amount float)]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "counter の name に amount を足した新しい断面(元の断面は変えない)。"
  (val counters snapshot.counters)
  (MeterSnapshot :counters (FrozenMap (| (dict counters) {name (+ (float (.get counters name 0.0)) amount)}))
                 :gauges snapshot.gauges :durations snapshot.durations))


(defk gauged [snapshot name value]
  {:pre [(: snapshot MeterSnapshot) (: name str) (: value float)]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "gauge の name を value に置き換えた新しい断面。"
  (MeterSnapshot :counters snapshot.counters :gauges (FrozenMap (| (dict snapshot.gauges) {name value}))
                 :durations snapshot.durations))


(defk observed [snapshot settings name seconds]
  {:pre [(: snapshot MeterSnapshot) (: settings MeterSettings) (: name str) (: seconds float)]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "秒の合計と回数に seconds を積み、桁の表の当たる桁と inf の桁の counter を進めた新しい断面(1 つの名の中を 1 度に変える)。"
  (val row (.get snapshot.durations name (SecondsTotal :total 0.0 :count 0)))
  (val bucket-names (if settings.buckets
                        (+ (lfor bucket settings.buckets :if (<= seconds bucket.ceiling) (+ name "_" bucket.label))
                           [(+ name "_" settings.inf-label)])
                        []))
  (val counters (dict snapshot.counters))
  (for [counter bucket-names]
    (setv (get counters counter) (+ (float (.get counters counter 0.0)) 1.0)))
  (MeterSnapshot :counters (FrozenMap counters) :gauges snapshot.gauges
                 :durations (FrozenMap (| (dict snapshot.durations)
                                          {name (SecondsTotal :total (+ row.total seconds) :count (+ row.count 1))}))))
