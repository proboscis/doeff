;;; 止めの合図の契約テストの解釈器(composition root)— 同じ契約の Program を、止めの合図の handler だけ替えて走らせる
;;; (agora-redesign #1159)。
;;;
;;;   os-signal  本物: os-signal-stop-handler(process に届く本当の SIGINT / SIGTERM)
;;;   scripted   fake: scripted-stop-handler(I/O なし — RaiseStop で合図を起こす)
;;;
;;; 契約の Program は SendStop の効果で合図を起こす。合図を起こす手段だけが解釈器ごとに違う:
;;;   os-signal → 検の process へ本当の signal を送る(os.kill(os.getpid(), signum))
;;;   scripted  → RaiseStop で本物の箱が書くのと同じ理由の文字列(signal-reason)を起こす
;;; StopHandlerUnderTest は外側に state の handler を置かずに走らせる反例のために、試している handler そのものを答える。
;;; os-signal の組み立ては走る前の SIGINT / SIGTERM の受け手を覚え、走った後に戻す(本物の handler は受け手を戻さない)。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk defhandler <- val])
(import dataclasses [dataclass])
(import functools [partial])
(import os)
(import signal)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.stop_signal_effects [RaiseStop])
(import doeff_core_effects.stop_signal_handlers [STOP-SIGNALS os-signal-stop-handler scripted-stop-handler])

(val OS-SIGNAL "os-signal")
(val SCRIPTED "scripted")
(val STOP-HANDLERS {OS-SIGNAL os-signal-stop-handler
                    SCRIPTED scripted-stop-handler})


(defclass [(dataclass :frozen True)] SendStop [EffectBase]
  "契約の Program が止めの合図 signum を起こす効果(答え = None)。"
  (#^ int signum))


(defclass [(dataclass :frozen True)] StopHandlerUnderTest [EffectBase]
  "試している止めの合図の handler そのものを求める効果(外側に state の handler を置かない反例のため)。")


(defk signal-reason [signum]
  {:pre [(: signum int)] :post [(: % str)] :tags {:context "stop-signal-test" :role "judgment"}}
  "signum の合図を受けた本物の箱(StopBox)が書く理由の文字列。scripted の側はこれを RaiseStop の理由にする。"
  (+ "signal " (str (int signum))))


(defhandler send-by-os-signal
  "SendStop を、検の process 自身へ本当の signal を送って起こす(Python は次の bytecode の区切りで受け手を呼ぶ)。"
  (SendStop [signum]
    (os.kill (os.getpid) signum)
    (resume None)))


(defhandler send-by-raise-stop
  "SendStop を、内側の scripted-stop-handler へ RaiseStop を出して起こす。"
  (SendStop [signum]
    (<- reason str (signal-reason signum))
    (<- (RaiseStop reason))
    (resume None)))


(val SENDERS {OS-SIGNAL send-by-os-signal
              SCRIPTED send-by-raise-stop})


(defhandler stop-handler-under-test [handler]
  ;; 引数に残す理由: 試す handler は解釈器の名ごとに組み立ての側で決まる値で、契約の Program の側に区別する名が無い。
  (StopHandlerUnderTest []
    (resume handler)))


(defk under-stop-signal [name program]
  {:pre [(: name str) (in name STOP-HANDLERS) (: program Program)]
   :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "stop-signal-test" :role "foundation"}}
  "name の止めの合図の handler の下で program を走らせる。外から順に: state・試す handler の答え手・止めの合図の handler・
   合図の起こし手(scripted の起こし手が出す RaiseStop を止めの合図の handler が受けるので、起こし手が一番内側)。
   走る前の SIGINT / SIGTERM の受け手を走った後に戻す。"
  (val handler (get STOP-HANDLERS name))
  (val saved (dfor signum STOP-SIGNALS signum (signal.getsignal signum)))
  (try
    (<- answer (with_handlers [(state) (stop-handler-under-test handler) handler (get SENDERS name)] program))
    answer
    (finally
      (for [#(signum receiver) (.items saved)]
        (signal.signal signum receiver)))))


(val INTERPRETERS {OS-SIGNAL (partial under-stop-signal OS-SIGNAL)
                   SCRIPTED (partial under-stop-signal SCRIPTED)})
