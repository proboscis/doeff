;;; 汎用の「純粋な計算を回して答えを値で受ける」effect(agora-redesign #802 便 4・最初の消費者 = agora の画面の処理ループの外の畳みと
;;; 問いの答え — 旧 FoldLater・AnswerLater)。業務の語を持たない土台の語彙で、process_effects.hy と同じ段。答え手は仕組みごとに差し替える:
;;;   thread-pool-compute-handler   呼び手が渡す thread の pool で回す(thread_pool_compute.hy)。待つのは撃った task だけで、scheduler の
;;;                                 他の task は回り続ける(scheduled の下で使う — 外から完了させる promise で待つ)
;;;   inline-compute-handler        I/O なし — その場で同期に回す(inline_compute.hy)。仮想の時計の模擬で決定的に回る
;;;
;;;   Compute   program を handler を 1 つも付けない新しい VM で回し、答えを Computed(値)か ComputeFailed(例外)で返す。
;;;             program は純粋であること — effect を出すと届く handler が無く ComputeFailed になる(呼び手の handler には届かない。
;;;             どちらの答え手でも同じ)。成否の解釈は呼び手が持つ(raise しない)。
;;;   ReadComputeLoad
;;;             答え手が今 抱えている計算の負荷の断面(ComputeLoad)を読む: 並び(出して、まだ回り始めていない数)・走り(回っている数)・
;;;             回り始めた数の累計と、その仕事が出されてから回り始めるまで待った秒の累計。pool の詰まり(大きな計算が pool の全部の
;;;             thread を塞ぎ、後の計算が列で待つ)を、使い手の計器(例 GET /metrics)へ数で出すため。累計は答え手を組んだ時からの物で、
;;;             2 回読んだ差を数の差で割れば、その間に回り始めた仕事の待ちの平均の秒になる。その場で回す答え手は列を持たないので全部 0。
;;;
;;; 時刻で起こす事(後で起こす・遅らせて回す)はこの effect の範囲ではない — doeff-time の ScheduleAt / Delay を使う。
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [DoExpr EffectBase])


(defrecord Computed
  "Compute の答え — program が返した値。"
  (#^ object value))


(defrecord ComputeFailed
  "Compute の答え — program が落ちた(例外を値で運ぶ。解釈は呼び手)。"
  (#^ Exception error))


(defclass [(dataclass :frozen True)] Compute [EffectBase]
  "純粋な program を回して答えを値で受ける(頭の註)。答え = Computed | ComputeFailed。"
  (#^ DoExpr program))


(defrecord ComputeLoad
  "ReadComputeLoad の答え — 答え手が今 抱えている計算の負荷の断面: queued = 出して、まだ回り始めていない仕事の数・running = 回っている
   仕事の数・started = 回り始めた仕事の累計・waited-seconds = 回り始めた仕事が出されてから回り始めるまで待った秒の累計(started 個の和)。"
  {:check [(>= queued 0) (>= running 0) (>= started 0) (>= waited-seconds 0.0)]}
  (#^ int queued)
  (#^ int running)
  (#^ int started)
  (#^ float waited-seconds))


(defclass [(dataclass :frozen True)] ReadComputeLoad [EffectBase]
  "答え手が今 抱えている計算の負荷の断面を読む(頭の註)。答え = ComputeLoad。")
