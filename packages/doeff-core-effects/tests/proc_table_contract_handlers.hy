;;; 機体の process の表の契約テストの解釈器(composition root)— 同じ契約の Program を、process の表の handler だけ替えて走らせる
;;; (card ki-7b934e2538f9)。
;;;
;;;   os-proc-table        本物: os-proc-table-handler(/proc と os.kill)+ 検の中で起こす本物の眠る子(sleep 30 — 検の process の子)
;;;   scripted-proc-table  fake: scripted-proc-table-handler(台本の表 — 親と眠る子の 2 行)
;;;
;;; 契約の世界は解釈器ごとに同じ形で用意する(ProcWorld の答え): 眠る子の pid と、その親の pid。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import os)
(import subprocess)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [ProcRow])
(import doeff_core_effects [os-proc-table-handler])
(import doeff_core_effects [scripted-proc-table-handler])

(val OS-PROC-TABLE "os-proc-table")
(val SCRIPTED-PROC-TABLE "scripted-proc-table")
;; 台本の世界の pid と起動の刻(本物の値と混ざらない大きさ)。
(val SCRIPTED-PARENT 70000)
(val SCRIPTED-SLEEPER 70001)
(val SCRIPTED-TICKS 900000)


(defrecord ProcPids
  "契約の世界の process(sleeper = 眠る子の pid・parent = その親の pid)。"
  (#^ int sleeper)
  (#^ int parent))


(defclass [(dataclass :frozen True)] ProcWorld [EffectBase]
  "契約の世界の process(ProcPids)を求める効果。")


(defhandler proc-world [#^ ProcPids pids]
  ;; 引数に残す理由: pid は解釈器ごとに組み立ての側で決まる値(本物は走るたびに起こす子)。
  (ProcWorld []
    (resume pids)))


(defk under-os-proc-table [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")] :tags {:context "proc-table-test" :role "foundation"}}
  "本物の os-proc-table-handler の下で program を走らせる。眠る子(sleep 30)をこの検の process の子として起こし、走った後に止めて刈る。"
  (val sleeper (subprocess.Popen ["sleep" "30"]))
  (var answer None)
  (try
    (<- ran (with_handlers [(proc-world (ProcPids :sleeper sleeper.pid :parent (os.getpid))) os-proc-table-handler] program))
    (:= answer ran)
    (finally
      (.kill sleeper)
      (.wait sleeper)))
  answer)


(defk under-scripted-proc-table [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")] :tags {:context "proc-table-test" :role "foundation"}}
  "fake の scripted-proc-table-handler の下で program を走らせる。表 = 親と、その子の眠る子の 2 行。外から順に: state・世界・表。"
  (val rows #((ProcRow :pid SCRIPTED-PARENT :ppid 1 :start-ticks SCRIPTED-TICKS :rss-bytes 8192 :comm "parent")
              (ProcRow :pid SCRIPTED-SLEEPER :ppid SCRIPTED-PARENT :start-ticks (+ SCRIPTED-TICKS 1) :rss-bytes 4096 :comm "sleep")))
  (<- answer (with_handlers [(state) (proc-world (ProcPids :sleeper SCRIPTED-SLEEPER :parent SCRIPTED-PARENT))
                             (scripted-proc-table-handler rows)]
               program))
  answer)


(val INTERPRETERS {OS-PROC-TABLE under-os-proc-table
                   SCRIPTED-PROC-TABLE under-scripted-proc-table})
