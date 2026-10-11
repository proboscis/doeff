;; 実行環境の掃除の消しの task(env_store の removing-leftovers → sweep-leftovers → unused-store-entries)が bytecode の保存先を歩く間も、
;; 同じ scheduler の別の task(調整ループの拍と背景の heartbeat の代役)が回る形の検(card ki-0da6a0186492)。
;;
;; 実例(2026-10-11・zeus の worker): 保存先の file 104,735 個を、同期の WalkTree と file ごとの StatPath で歩いた掃除が scheduler の
;; thread を占め(掃除の長さの中央 13.1 秒)、tick も背景の heartbeat も止まった — heartbeat の間の遅れの行 45 行・lease(10 秒)越え 11 行・
;; 最大 19.1 秒。だから:
;;   - 保存先の歩き(file ごとの時刻の読みまで)は木の数えと消し(#3715)と同じく thread で待ち、歩いている間も別の task の拍が進む
;;   - 歩きの答えで選ぶ物は前と同じ(7 日使われない entry だけを消し、使った entry は残す — #3858)
;; 土台: 本番の worker と同じ file の答え手の組(scheduled の下に os-file-handler と内側の offloaded-tree-handler — main.hy の
;; production-handlers)と、tmp の下の本物の file。歩きを止めておく所は os.walk の差し替え — 保存先の walk の頭で、入った事を外の promise で
;; 拍の task へ知らせ、拍の task が歩きの間に BEATS 拍進んでから出す合図を待つ。
;; 反例 = 直す前の形(保存先を WalkTree と file ごとの StatPath で、scheduler の thread で歩く)は、歩きが合図を待つ間 拍の task が回れず、
;; SCENARIO-LIMIT で赤(2026-10-12 に直す前の env_store.hy で確かめた)。
(require doeff-hy.macros [defk deftest <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import os)
(import threading)
(import time)
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects.offloaded_call [ThreadPerCall run-detached])
(import doeff_core_effects.os_file [os-file-handler offloaded-tree-handler])
(import doeff_core_effects.scheduler [scheduled CreatePromise CreateExternalPromise Spawn Wait Task])
(import doeff_cluster.worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster.worker.protocol.env_store [EnvSettings RemovedRoots removing-leftovers])
(import doeff_cluster.worker.core.env_upkeep [CODE-STORE-UNUSED-SECONDS])

;; 筋書き 1 つの上限(実時間の秒)。普段は 1 秒の内に終わる。
(val SCENARIO-LIMIT 5.0)
;; 拍の task の 1 拍(実時間の秒 — 外の合図を待つ。本番の拍の HTTP の待ちの代役)と、歩きの間に進める拍の数。
(val BEAT-SECONDS 0.05)
(val BEATS 3)


(defrecord WalkRun
  "筋書き 1 回の見え方: removed = 掃除が消した path・beats = 歩きの間に進んだ拍の数。"
  (#^ (get tuple #(str ...)) removed)
  (#^ int beats))


(defk outside-signal [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "seconds 秒後に検の殻の thread が完了させる合図を待つため(待つ間 scheduler は他の task を回す — test_offloaded_tree.hy と同じ作法)。"
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer seconds (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  None)


(defk beats-while-walking [settings now-ms walks gate]
  {:pre [(: settings EnvSettings) (: now-ms int) (: walks list) (: gate threading.Event)] :post [(: % WalkRun)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "掃除の消しを本番と同じく別の task で始め、歩きが保存先の walk の頭に入った知らせ(walks に置く外の promise — 差し替えた walk が完了させる)を
   待ち、歩きの間に BEATS 拍進めてから歩きの合図を出し、消しの答えと task の終わりを待つため。歩きが scheduler の thread で待つと、この
   task は歩きの間に拍を進められず、合図を出せない。"
  (<- entered (CreateExternalPromise))
  (.append walks entered)
  (<- done (CreatePromise))
  (<- sweeping Task (Spawn (removing-leftovers settings now-ms done)))
  (<- (Wait entered.future))
  (var beats 0)
  (for [_ (range BEATS)]
    (<- (outside-signal BEAT-SECONDS))
    (:= beats (+ beats 1)))
  (.set gate)
  (<- removed RemovedRoots (Wait done.future))
  (<- (Wait sweeping))
  (WalkRun :removed removed.paths :beats beats))


(defk in-thread [program]
  {:pre [(: program Program)] :post [(: % "program の答え")] :tags {:context "doeff-cluster-test" :role "program"}}
  "program を scheduled と本番の worker と同じ file の答え手の組(os-file-handler と内側の offloaded-tree-handler)の下で、検の thread とは
   別の daemon の thread の新しい VM で 1 回回し、SCENARIO-LIMIT 秒まで答えを待つため(過ぎたら scheduler の thread が塞がったと名指して
   落とす — test_offloaded_tree.hy と同じ作法)。"
  (val running (.submit (ThreadPerCall) run-detached
                        (scheduled (with-handlers [os-file-handler offloaded-tree-handler] program))))
  (try
    (.result running :timeout SCENARIO-LIMIT)
    (except [TimeoutError]
      (raise (AssertionError (.format "{} 秒で答えが無い(保存先の歩きが scheduler の thread を塞いだ)" SCENARIO-LIMIT))))))


(deftest test-the-store-walk-of-the-sweep-lets-other-tasks-beat [tmp-path monkeypatch]
  ;; 保存先に 7 日使われない entry 1 つと使った entry 1 つ。保存先の walk は、拍の task が BEATS 拍進んで合図を出すまで頭で待つ(頭の註)。
  (val store (/ tmp-path "code-store"))
  (.mkdir (/ store "ab") :parents True)
  (val stale (/ store "ab" "old.code"))
  (val used (/ store "ab" "new.imports"))
  (.write-bytes stale b"x")
  (.write-bytes used b"y")
  (val now-s (time.time))
  (val past (- now-s CODE-STORE-UNUSED-SECONDS 60))
  (os.utime stale #(past past))
  (val settings (EnvSettings :state (str (/ tmp-path "state")) :uv-cache (str (/ tmp-path "uv-cache")) :hy-command "hy" :platform "test"
                             :code-prepare PREPARE-TOOL :roots-cap-bytes 0 :code-store (str store)))
  (val walks [])
  (val gate (threading.Event))
  (val original os.walk)
  (.setattr monkeypatch os "walk" (fn [path #** options]
                                    (when (= (str path) (str store))
                                      (.complete (get walks 0) None)
                                      (.wait gate (* 2 SCENARIO-LIMIT)))
                                    (original path #** options)))
  (<- got WalkRun (in-thread (beats-while-walking settings (int (* now-s 1000)) walks gate)))
  (assert (= got.beats BEATS) got)
  (assert (in (str stale) got.removed) got)
  (assert (not (.exists stale)) "7 日使われない entry が残った")
  (assert (.exists used) "使った entry を消した"))
