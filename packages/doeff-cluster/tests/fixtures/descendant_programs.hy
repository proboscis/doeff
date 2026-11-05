;;; 模擬の子 process の答え手(_sim/child_processes)の検の service と子の台本(#2940 の条 C4b — 本物の shim の検 test_shim_descendants.hy の
;;; tests/fixtures/sleeper の --detached-grandchild を模擬に写した形)。
;;;
;;; service parent = 子 spawner を起こし、止めの合図を待って終わる(合図で後始末して 0)。子 spawner = 別の session に孫 sleeper-grandchild を
;;; 起こし(setsid -f)、SPAWNER-SECONDS 秒の仕事の後に結果の file を書いて 0 で終わる。孫 = GRANDCHILD-SECONDS 秒眠って結果の file を書く。
;;; 結果の file は /out/<命令の名>-<job の process の世代の名>(片づけられた子孫は書かない)。
(require doeff-hy.macros [defk <- val])
(require doeff-cluster.macros [defsystem])
(import collections.abc [Callable])
(import doeff_core_effects [Ask AwaitStop AppendText MemoryFiles memory-file-handler])
(import doeff_core_effects [ScriptedCommand ProcessOutcome RunProcess StartProcess ProcessStarted])
(import doeff_time [Delay])
(import doeff_cluster [HOST-CONTRACT RunContext SimChildren SimOutside])

(val OUT-DIR "/out")
;; 子の仕事の長さと孫の眠りの長さ(秒 — 筋書きはこの間に止める)。
(val SPAWNER-SECONDS 20.0)
(val GRANDCHILD-SECONDS 3600.0)


(defk result-path [name instance]
  {:pre [(: name str) (: instance str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "子孫 name が job の process instance の下で書く結果の file の path を、書き手と筋書きが同じ綴りで求めるため。"
  (.format "{}/{}-{}" OUT-DIR name instance))


(defk grandchild-run [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster-test" :role "program"}}
  "孫の本体: 長く眠ってから結果の file を書く(止め切りの後も走っていれば、止めの片づけから逃げた孫)。"
  (<- (Delay GRANDCHILD-SECONDS))
  (<- path str (result-path "sleeper-grandchild" (get request.argv 1)))
  (<- (AppendText path "done"))
  (ProcessOutcome :exit-code 0 :stdout "" :stderr ""))


(defk spawner-run [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster-test" :role "program"}}
  "子の本体: 別の session に孫を起こし、仕事の後に結果の file を書くため(片づけられた子は書かない)。"
  (val instance (get request.argv 1))
  (<- started (StartProcess :argv #("setsid" "-f" "sleeper-grandchild" instance)))
  (assert (isinstance started ProcessStarted) started)
  (<- (Delay SPAWNER-SECONDS))
  (<- path str (result-path "spawner" instance))
  (<- (AppendText path "done"))
  (ProcessOutcome :exit-code 0 :stdout "" :stderr ""))


;; 子の台本(spawner と孫 sleeper-grandchild)。
(val COMMANDS #((ScriptedCommand :name "spawner" :run spawner-run) (ScriptedCommand :name "sleeper-grandchild" :run grandchild-run)))


(defk descendant-outside [adoption]
  {:pre [(: adoption bool)] :post [(: % SimOutside)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "検の外の世界: 子孫の結果の file の置き場(memory)と、子の台本(adoption = 入れ物の shim が子孫を引き取るか — 偽 = 引き取りを外した変種)。"
  (SimOutside :handlers [(memory-file-handler (MemoryFiles :dirs #(OUT-DIR)))] :effects #()
              :children (SimChildren :commands COMMANDS :adoption adoption)))


(defk parent-body []
  {:pre [] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "service の本体: 子 spawner を起こし(argv に自分の世代の名を渡す)、止めの合図を待って終わる。"
  (<- ctx RunContext (Ask HOST-CONTRACT.run-context-key))
  (<- started (StartProcess :argv #("spawner" ctx.instance)))
  (assert (isinstance started ProcessStarted) started)
  (<- reason str (AwaitStop))
  reason)


(defk parent-program [foundation]
  {:pre [(: foundation Callable)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: parent-body を土台で包む。"
  (<- reason str (foundation (parent-body)))
  reason)


(defsystem descendant-hosts [#^ Callable foundation]
  "見本の系: 切り離した孫を起こす子を持つ service(版 1 — STEP 1)"
  (parent (parent-program foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :needs #{"cluster-net"} :environ {"STEP" "1"}))


(defsystem descendant-hosts-v2 [#^ Callable foundation]
  "descendant-hosts の版 2(environ の STEP を変えた — 旧い process は退いて終わる)"
  (parent (parent-program foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :needs #{"cluster-net"} :environ {"STEP" "2"}))


(defsystem descendant-hosts-off [#^ Callable foundation]
  "descendant-hosts の取り下げ(:replicas 0 — worker が job を止める)"
  (parent (parent-program foundation) :replicas 0 :peak-mib 200 :settle-mib 100 :needs #{"cluster-net"} :environ {"STEP" "1"}))


;; worker の消失(LoseWorker)の後に、退きの寿命を宣言した handoff の service を入れ物が待つ秒(子 spawner の仕事より長く、孫の眠りより短い —
;; descendant-hosts-outliving の keeper の :readiness の retiredSeconds と同じ値。宣言は数の literal しか書けない)。
(val OUTLIVE-SECONDS 60)


(defsystem descendant-hosts-outliving [#^ Callable foundation]
  "見本の系: 退きの寿命を宣言した handoff の service keeper(worker の消失の後も寿命の上限まで回る — outlives-worker)と、宣言しない service
   parent(消失ですぐ止まる)。どちらも parent-body(子 spawner と別の session の孫を起こし、止めの合図を待つ)"
  (keeper (parent-program foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :needs #{"cluster-net"} :update "handoff"
          :readiness {"windowSeconds" 30 "retiredSeconds" 60} :environ {"STEP" "1"})
  (parent (parent-program foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :needs #{"cluster-net"} :environ {"STEP" "1"}))
