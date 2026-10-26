;; 手入れの係が要求の答えを待たせない(card ki-3b942d3af8da — ADR-DOE-RECORDS-002 R7): 起き直しの直後、手入れの 1 回目の送りの関数が
;; effect を出さずに長く計算する間(本番 2026-10-27 01:26〜01:44・02:08〜02:16 JST — 送り先から読み戻した 19 万行を 1 行ずつ照らす計算が
;; 4 分ずつ 2 回)にも、口へ一斉に届いた読みは、その計算の終わりを待たずに答えられる。
;;   - 本物の待ち受け(127.0.0.1 の空き port)・本物の時計の検の殻に、送りの関数が BUSY-SECONDS 秒 effect を出さずに計算する手入れの計画を
;;     渡して口を開く。送りが計算を始めた後に、READS 本の読み(read-row)を並べて撃つ。
;;   - どの読みも ANSWER-BOUND 秒の内に答えられ、答えが揃った時に送りの計算はまだ終わっていない(読みが計算の後ろに並んでいない)。
;;   - 送りの計算はその後に終わる(手入れは捨てられずに回り切る)。口を閉じると、手入れの別の thread も CLOSE-SECONDS 秒の内に終わる。
;; 失敗ケース(直す前の形): 手入れの係を要求と同じ scheduler の thread の task で回す — 読みは送りの計算の終わり(BUSY-SECONDS 秒)まで
;; 受けの列で待ち、答えが揃うのは計算が終わった後になる。
(require doeff-hy.macros [deftest defk <- val var])
(import threading)
(import time)
(import doeff [with_handlers])
(import doeff_core_effects.scheduler [Spawn Gather])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_time [GetMonotonic async-time-handler])
(import doeff_records.values [Missing Unreachable])
(import doeff_records.effects [ReadRow])
(import doeff_records.laws [LAW-SCHEMA])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.http_client [RecordsEndpoint http-records-handler records-unwaited])
(import doeff_records.http_server [MaintenancePlan RecordsServerConfig RunningServer records-server-config start-records-server])

;; 送りの関数が effect を出さずに計算する秒(本番の 4 分の代わり — 読みの答えの上限の数倍)・並べて撃つ読みの数・読み 1 本の答えの上限の秒・
;; 送りが計算を始めるのと終わるのを待つ上限の秒・口を閉じた後に手入れの別の thread の終わりを待つ上限の秒・run-apart が立てる thread の名。
(val BUSY-SECONDS 3.0)
(val READS 8)
(val ANSWER-BOUND 1.0)
(val START-SECONDS 10.0)
(val FINISH-SECONDS 30.0)
(val CLOSE-SECONDS 10.0)
(val APART-THREAD "doeff-apart")


(defk next-count [n]
  {:pre [(: n int)] :post [(: % int)]}
  "送りの計算の 1 歩(effect を出さない Program — 本番の 1 行の照らしの defk と同じく VM の歩を使う)。"
  (+ n 1))


(defk busy-archive-of [started finished]
  {:pre [(: started threading.Event) (: finished threading.Event)] :post [(: % "送りの関数")]}
  "BUSY-SECONDS 秒 effect を出さずに計算してから送り先に届かないと答える送りの関数を作るため(始めと終わりを started・finished に立てる)。"
  (defk busy-archive [keep-seconds]
    {:pre [(: keep-seconds float)] :post [(: % Unreachable)]}
    "effect を出さずに BUSY-SECONDS 秒 計算する送り(答え = 送り先に届かない — 刈らない)。"
    (.set started)
    (val until (+ (time.monotonic) BUSY-SECONDS))
    (var steps 0)
    (while (< (time.monotonic) until)
      (<- counted int (next-count steps))
      (:= steps counted))
    (.set finished)
    (Unreachable (.format "検の送り — {} 歩 計算した" steps)))
  busy-archive)


(defk unsent-events [events]
  {:pre [(: events tuple)] :post [(: % Unreachable)]}
  "出来事の送り(送ってから外す列の宣言の在る置き場の手入れに要る口)— 送り先に届かないと答え、何も外させない。"
  (Unreachable "検の出来事の送り — 送らない"))


(defk timed-read []
  {:pre [] :post [(: % tuple)]}
  "読み 1 本を撃ち、#(答え 掛かった秒) を返すため。"
  (<- began float (GetMonotonic))
  (<- answer (ReadRow "parts" #("p1")))
  (<- ended float (GetMonotonic))
  #(answer (- ended began)))


(defk reads-together [count]
  {:pre [(: count int)] :post [(: % tuple)]}
  "読みを count 本 並べて撃ち、全部の #(答え 掛かった秒) を返すため(起き直しの直後の一斉の呼び)。"
  (var tasks #())
  (for [_ (range count)]
    (<- task (Spawn (timed-read)))
    (:= tasks (+ tasks #(task))))
  (<- answers list (Gather #* tasks))
  (tuple answers))


(defk client-reads [server count]
  {:pre [(: server RunningServer) (: count int)] :post [(: % tuple)]}
  "口 server へ本物の HTTP と時計で読みを count 本 並べて撃つため。"
  (<- answers tuple (with_handlers [(await-handler) (async-time-handler) (http-production-handler) records-unwaited
                                    (http-records-handler (RecordsEndpoint server.url :writer "reader"))]
                                   (reads-together count)))
  answers)


(deftest test-reads-are-answered-while-the-maintenance-computes
  ;; 失敗ケース = 手入れの係を要求と同じ scheduler の thread で回す(頭の註)。
  (val started (threading.Event))
  (val finished (threading.Event))
  (val store (MemoryStore LAW-SCHEMA))
  (<- archive (busy-archive-of started finished))
  (<- config RecordsServerConfig
      (records-server-config LAW-SCHEMA (fn [writer] (memory-records-handler store writer))
                             :maintenance (MaintenancePlan :interval-seconds 3600.0 :keep-seconds 60.0 :archive archive
                                                           :archive-events unsent-events)))
  (val before (frozenset (gfor t (threading.enumerate) :if (= t.name APART-THREAD) t)))
  (val server (start-records-server config))
  (try
    (assert (.wait started START-SECONDS) "手入れの送りが計算を始めない")
    (<- answers tuple (client-reads server READS))
    (val computing (not (.is-set finished)))
    (val slowest (max (gfor answer answers (get answer 1))))
    (assert (all (gfor answer answers (isinstance (get answer 0) Missing))) answers)
    (assert computing (.format "読みの答えが揃ったのは送りの計算が終わった後(読みが手入れの後ろに並んだ)— 一番遅い読み {:.2f} 秒" slowest))
    (assert (< slowest ANSWER-BOUND) (.format "手入れの計算の間の読みが {:.2f} 秒 掛かった(上限 {} 秒)" slowest ANSWER-BOUND))
    (assert (.wait finished FINISH-SECONDS) "手入れの送りの計算が終わらない")
    (finally
      (.close server)))
  ;; 口を閉じたら、手入れの別の thread も終わる(受けの終わりが手入れの task を取り消し、通い路が閉じる)。
  (val left (tuple (gfor t (threading.enumerate) :if (and (= t.name APART-THREAD) (not-in t before)) t)))
  (for [t left]
    (.join t CLOSE-SECONDS))
  (assert (not (any (gfor t left (.is-alive t)))) "口を閉じた後も手入れの別の thread が残った(手入れの task が取り消されない)"))
