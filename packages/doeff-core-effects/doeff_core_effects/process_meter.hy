;;; 計器の本物の答え手 process-meter-handler(agora-redesign #1440・ADR-DOE-CORE-EFFECTS-004)— CountMetric・ObserveSeconds・SetGauge・
;;; ReadMeter に、process に 1 つの置き場で答える。
;;;
;;; 何のためか: 動き続ける process では、処理ループの run が計器へ積み、別の run(probe の HTTP — 処理ループが計算している間も答える)が
;;; 同じ計器を読む。session の値は 1 つの run の中にしか無いので、この module が「名前 → 置き場」を process に 1 つ持ち、同じ名前で入れた
;;; 答え手どうし(別の run・別の thread)が同じ置き場を読み書きする。class は作らない(ADR-DOE-HY-007 R3・R4)— 置き場の中身は
;;; 変わらない値の断面と、書き手の lock と、GC の停止の箱だけ。process に 1 つの物を module が持つ先例 = handlers.py の Await の橋。
;;;
;;;   書き  lock を取り、GC の停止の箱を畳んでから、純関数(meter_effects.hy の counted・observed・gauged)で新しい断面を作って差し替える。
;;;         差し替えは 1 回の書きで 1 度だけ — 1 つの名の中(秒の合計・回数・桁の counter)は 1 度に見える。
;;;   読み  書き手の lock を待たずに今の断面の参照を 1 つ取る(読みが塞がった書き手の後ろに並ばない)。lock が空いていれば GC の停止の箱を
;;;         畳んでから読む(空いていなければ畳まずに読む)。
;;;   GC    設定に gc-pause-name があれば、置き場を作る時に gc.callbacks へ 1 つ繋ぎ、回収 1 回の start → stop の秒を箱へ積む。callback は
;;;         lock を取らない — 回収は起こした thread で callback を呼ぶので、lock を持った thread の中で回収が始まると、取り直す callback が
;;;         その thread に自分を永久に待たせる(threading.Lock は取り直せない)。
;;;
;;; 同じ名前で違う設定を入れると ValueError で断る(桁の表が食い違ったまま同じ置き場を書かない)。置き場は process の終わりまで残る。
;;; 時計・gc・thread に触るのはこの module だけ(memory の答え手 memory_meter.hy は触らない)。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "meter" :role "foundation"})
(require doeff-hy.record [defrecord])
(import collections [deque])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import gc)
(import threading)
(import time)
(import doeff_core_effects.meter_effects [CountMetric EMPTY-METER MeterSettings MeterSnapshot ObserveSeconds ReadMeter SetGauge
                                         counted gauged observed])


(defrecord MeterPlace
  "名前 1 つの置き場: settings = 入れた時の設定・lock = 書き手の lock・pauses = GC の callback が積む停止の秒の箱。断面そのものは
   SNAPSHOTS(名前 → 断面)に置き、書きのたびに差し替える。"
  {:tags {:context "meter" :role "foundation"}}
  (#^ MeterSettings settings)
  (#^ object lock)
  (#^ deque pauses))


;; process に 1 つ: 名前 → 置き場・名前 → 今の断面・置き場を作る時の lock。
(val PLACES {})
(val SNAPSHOTS {})
(val PLACES-LOCK (threading.Lock))


(defk gc-pause-watch [pauses]
  {:pre [(: pauses deque)] :post [(: % Callable)] :tags {:context "meter" :role "foundation"}}
  "回収 1 回の start → stop の秒を pauses へ積む callback を作るため(lock を取らない — 頭の註)。作った callback は gc.callbacks に繋がり、
   回収の最中に VM の外から素の関数として呼ばれる。"
  (val started [None])
  (fn [phase info]
    (setv now (time.monotonic))
    (if (= phase "start")
        (setv (get started 0) now)
        (do
          (setv began (get started 0))
          (setv (get started 0) None)
          (when (is-not began None)
            (.append pauses (- now began)))))))


(defk meter-place [name settings]
  {:pre [(: name str) (: settings MeterSettings)] :post [(: % MeterPlace)] :tags {:context "meter" :role "foundation"}}
  "答え手を入れる時に、name の置き場を引くため(無ければ作り、設定に gc-pause-name があれば GC の callback を繋ぐ)。同じ名前で違う設定なら断る。"
  (with [PLACES-LOCK]
    (var place (.get PLACES name))
    (when (is place None)
      (:= place (MeterPlace :settings settings :lock (threading.Lock) :pauses (deque)))
      (setv (get PLACES name) place)
      (setv (get SNAPSHOTS name) EMPTY-METER)
      (when (is-not settings.gc-pause-name None)
        (<- watch Callable (gc-pause-watch place.pauses))
        (.append gc.callbacks watch)))
    (when (!= place.settings settings)
      (raise (ValueError (.format "計器の置き場 {!r} は別の設定で入れてある: {!r}(今の設定 {!r})" name place.settings settings))))
    place))


(defk with-pauses [snapshot settings pauses]
  {:pre [(: snapshot MeterSnapshot) (: settings MeterSettings) (: pauses tuple)]
   :post [(: % MeterSnapshot)] :tags {:context "meter" :role "judgment"}}
  "箱から取り出した GC の停止の秒を、設定の名の秒の観測として断面へ積むため(名が無ければそのまま)。"
  (var folded snapshot)
  (when (is-not settings.gc-pause-name None)
    (for [seconds pauses]
      (<- next MeterSnapshot (observed folded settings settings.gc-pause-name seconds))
      (:= folded next)))
  folded)


(defk rewritten [name place change]
  {:pre [(: name str) (: place MeterPlace) (: change Callable)]
   :post [(: % None)] :tags {:context "meter" :role "foundation"}}
  "書き手の lock の中で、GC の停止を畳んだ断面に change(断面 → 新しい断面の Program)を当てて 1 度だけ差し替えるため。"
  (with [place.lock]
    (val pauses (tuple (gfor _ (range (len place.pauses)) (.popleft place.pauses))))
    (<- base MeterSnapshot (with-pauses (get SNAPSHOTS name) place.settings pauses))
    (<- next MeterSnapshot (change base))
    (setv (get SNAPSHOTS name) next))
  None)


(defhandler process-meter-handler [#^ str place-name #^ MeterSettings settings]
  "CountMetric・ObserveSeconds・SetGauge・ReadMeter に、process に 1 つの置き場 place-name で答える(頭の註)。"
  ;; 引数に残す理由: place-name は別の run と同じ置き場を共有する鍵、settings は置き場を作る時の桁の表と GC の停止の名 — どちらも入れる所ごとに
  ;; 決まる値で、Ask では区別できない(1 つの組に名前の違う計器を並べられる)。
  (session val place (! (meter-place place-name settings)))
  (CountMetric [name amount]
    (<- (rewritten place-name place (fn [snapshot] (counted snapshot name amount))))
    (resume None))
  (ObserveSeconds [name seconds]
    (<- (rewritten place-name place (fn [snapshot] (observed snapshot place.settings name seconds))))
    (resume None))
  (SetGauge [name value]
    (<- (rewritten place-name place (fn [snapshot] (gauged snapshot name value))))
    (resume None))
  (ReadMeter []
    (when (.acquire place.lock :blocking False)
      (try
        (val pauses (tuple (gfor _ (range (len place.pauses)) (.popleft place.pauses))))
        (<- folded MeterSnapshot (with-pauses (get SNAPSHOTS place-name) place.settings pauses))
        (setv (get SNAPSHOTS place-name) folded)
        (finally
          (.release place.lock))))
    (resume (get SNAPSHOTS place-name))))
