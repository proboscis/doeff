;;; sim-cluster の世界の区切りの印(card ki-a27b195a3f92)。
;;;
;;; 歩数の上限の plugin は、模擬の世界を回した検を、世界が立ち上がってから止め始めるまでの走りの歩数だけで判じる。sim-main は始まり・
;;; 立ち上がり(宣言の job が全部 起きた後、世界が初めて静まった時)・止め始め・終わりの印を、その時の doeff-vm の積み上げの歩数と
;;; 残す(doeff_core_effects.world_phases)。ここでは本物の世界を回して確かめる: 起動の時だけ仕事をする部品を足しても走りの歩数は
;;; 変わらず(足した部品の起動の仕事は起動の歩数に入る)、空回り(周ごとに起きる仕事)を 2 倍にすると走りの歩数が宣言の値(土台の
;;; 走りの 1.02 倍)を越える。宣言との比べと文は plugin の側の検が確かめる。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass field])  ; defrecord の展開が名指す
(import math)
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_core_effects.world_phases [open-phase-log close-phase-log])
(import doeff_cluster.sim.local [sim-cluster])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.phase_programs [phase-world phase-world-plus-part phase-world-double-idle])


(defrecord WorldSplit
  "1 回の世界の歩数を印で分けた値: starting = 始まりから立ち上がりまで・running = 立ち上がりから止め始めまで・stopping = 止め始めから
   終わりまで・marks = 残った印の名(起きた順)。"
  (#^ int starting)
  (#^ int running)
  (#^ int stopping)
  (#^ tuple marks))


(defk ten-quiet-seconds []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 何もせず 10 秒待つ(その間の世界の仕事は周の service の空回りだけ)。"
  (<- (Delay 10.0))
  None)


(defk mark-steps [marks phase]
  {:pre [(: marks tuple) (: phase str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "印 phase が残った時の積み上げの歩数を引くため(1 回の世界では各印は 1 つ)。"
  (. (next (gfor mark marks :if (= mark.phase phase) mark)) steps))


(defk measured-world [system]
  {:pre [(: system "defsystem の関数")] :post [(: % WorldSplit)] :tags {:context "doeff-cluster-test" :role "program"}}
  "系を 10 秒の筋書きで 1 回回し、その世界の歩数を印で分けた値を得るため(印の記録は検の中で開け閉めする — doeff の検は歩数の上限の
   plugin を開かない)。"
  (open-phase-log)
  (<- (sim-cluster :notice-broker (MemoryBroker) (system sim-foundation) (ten-quiet-seconds)))
  (val marks (close-phase-log))
  (<- begun int (mark-steps marks "begun"))
  (<- up int (mark-steps marks "up"))
  (<- stopping int (mark-steps marks "stopping"))
  (<- ended int (mark-steps marks "ended"))
  (WorldSplit :starting (- up begun) :running (- stopping up) :stopping (- ended stopping)
              :marks (tuple (gfor mark marks mark.phase))))


(deftest test-the-world-marks-its-phases-in-order-and-is-up-after-its-start-up-work
  (<- base WorldSplit (measured-world phase-world))
  (assert (= base.marks #("begun" "up" "stopping" "ended")) base)
  ;; 起動の service の仕事(2,000 回)は起動に入る — 立ち上がりは宣言の job が全部 起きて静まった後。
  (assert (> base.starting base.running) base)
  (assert (> base.stopping 0) base))


(deftest test-a-part-that-only-starts-up-keeps-the-running-steps-and-doubled-idle-goes-over-the-declared-value
  (<- base WorldSplit (measured-world phase-world))
  (<- plus WorldSplit (measured-world phase-world-plus-part))
  (<- double WorldSplit (measured-world phase-world-double-idle))
  ;; 宣言の値の決め方の例: 測った走りの歩数の 1.02 倍。
  (val declared (math.ceil (* base.running 1.02)))
  ;; 部品を 1 つ足すと起動の歩数は増えるが、走りの歩数は宣言の内(前の数え方 = 全部の歩数なら宣言を越えていた)。
  (assert (> plus.starting (+ base.starting 2000)) #(base plus))
  (assert (<= plus.running declared) #(base plus declared))
  (assert (> (+ plus.starting plus.running plus.stopping) (* (+ base.starting base.running base.stopping) 1.02)) #(base plus))
  ;; 空回りを 2 倍にすると、走りの歩数が宣言を越える。
  (assert (> double.running declared) #(base double declared)))
