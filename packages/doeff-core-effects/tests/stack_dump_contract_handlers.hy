;;; 全 thread の stack を書く見張りの契約テストの解釈器(composition root)— 同じ契約の Program を、見張りの答え手と時計だけ替えて
;;; 走らせる(agora-redesign #2748)。
;;;
;;;   faulthandler-stack-dump  本物: faulthandler-stack-dump-handler(faulthandler の C の thread が実時間で期限を数えて書く)・
;;;                            doeff-time の実時計 sync-time-handler
;;;   memory-stack-dump        fake: memory-stack-dump-handler(書かずに台帳だけ)・doeff-time の仮想の時計 sim-time-handler
;;;
;;; 契約の Program は刻を doeff-time の GetMonotonic で読み、Delay で待つ(時計の答え手だけが解釈器ごとに違う)。
;;; 本物は走らせる間だけ sys.stderr を一時の file に据える(見張りが書く本文を検が WrittenText で読む・検の出力を汚さない)。走った後は
;;; 見張りを外し、sys.stderr を戻し、一時の dir を消す。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk defhandler defeffect <- val])
(import functools [partial])
(import faulthandler)
(import pathlib [Path])
(import sys)
(import tempfile)
(import datetime [datetime timezone])
(import doeff [Program with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.faulthandler_stack_dump [faulthandler-stack-dump-handler])
(import doeff_core_effects.memory_stack_dump [memory-stack-dump-handler])
(import doeff_time [sim-time-handler sync-time-handler])

(val FAULTHANDLER-STACK-DUMP "faulthandler-stack-dump")
(val MEMORY-STACK-DUMP "memory-stack-dump")
;; 仮想の時計の始まりの刻。
(val SIM-START (datetime 2026 10 2 :tzinfo timezone.utc))


(defeffect WrittenText
  "本物の見張りが sys.stderr に書いた本文を読む(本物の解釈器だけが答える)。"
  {:fields [] :answer str :tags {:context "stack-dump-test" :role "foundation"}})


(defhandler written-log [#^ str path]
  "WrittenText に、本物の解釈器が sys.stderr に据えた一時の file の本文で答える。"
  ;; 引数に残す理由: path = 解釈器が走らせるたびに作る一時の file(Ask で読む設定ではない)。
  (WrittenText []
    (resume (.read-text (Path path) :encoding "utf-8"))))


(defk under-faulthandler [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "stack-dump-test" :role "foundation"}}
  "本物の答え手と実時計の下で program を走らせるため(頭の註 — sys.stderr を一時の file に据え、終わったら見張りを外して戻す)。"
  (val folder (tempfile.TemporaryDirectory))
  (val path (str (/ (Path folder.name) "stderr.log")))
  (val log (open path "w" :encoding "utf-8"))
  (val saved sys.stderr)
  (setattr sys "stderr" log)
  (try
    (<- answer (with_handlers [(state) (sync-time-handler) (written-log path) faulthandler-stack-dump-handler] program))
    answer
    (finally
      (faulthandler.cancel-dump-traceback-later)
      (setattr sys "stderr" saved)
      (.close log)
      (.cleanup folder))))


(defk under-memory [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "stack-dump-test" :role "foundation"}}
  "memory の答え手と仮想の時計の下で program を走らせるため。"
  (<- answer (with_handlers [(state) (sim-time-handler :start-time SIM-START) memory-stack-dump-handler] program))
  answer)


(val INTERPRETERS {FAULTHANDLER-STACK-DUMP under-faulthandler
                   MEMORY-STACK-DUMP under-memory})
