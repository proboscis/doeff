;;; 待ちの子の契約テストの解釈器(composition root)— 同じ契約の Program を、待ちの子の handler だけ替えて走らせる。
;;;
;;;   os-warm         本物: os-warm-process-handler(unix socket と /proc)+ 検の中で起こす偽の待ちの子(fake_warm_child.py — socket ごとに
;;;                   1 つの別の process)+ subprocess-handler(問い直しの間の sleep)
;;;   scripted-warm   fake: scripted-warm-process-handler(I/O なし — WarmScript)+ scripted-process-handler(台本の sleep)+ memory の置き場
;;;
;;; 契約の世界は解釈器ごとに同じ形で用意する(WarmPaths の答え):
;;;   * socket 4 つ: 受ける(accepting)・断る(refusing — 断りの文 REFUSAL)・答えない(silent)・無い(missing)
;;;   * 入口 3 つ: EXIT-3(すぐ終了 code 3 で終わる)・SLEEPER(止められるまで走る)・VANISH(子 A を道連れに、exit の file を書かずに消える)
;;;     本物 = 走るたびの一時 dir に module の file を置く・fake = WarmScript の runs に同じ終わり方を書く
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import os)
(import subprocess)
(import sys)
(import tempfile)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.file_effects [MemoryFiles])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.process_effects [ProcessOutcome RunProcess])
(import doeff_core_effects.scripted_process [ScriptedCommand ProcessScript scripted-process-handler])
(import doeff_core_effects.os_warm_process [os-warm-process-handler])
(import doeff_core_effects.scripted_warm_process [WarmScript WarmSocket WarmSocketAnswer WarmRun scripted-warm-process-handler])

(val OS-WARM "os-warm")
(val SCRIPTED-WARM "scripted-warm")
(val MEMORY-ROOT "/warm-root")
;; 断る socket の断りの文(偽の待ちの子と台本が同じ文を答える)。
(val REFUSAL "待ちの子は今 頼みを受けない(契約テストの断り)")
;; 契約の入口の名(本物 = 一時 dir の module・fake = WarmScript の runs)。
(val EXIT-3 "warm_entry_exit3")
(val SLEEPER "warm_entry_sleep")
(val VANISH "warm_entry_vanish")
;; 本物の入口の中身(子 B が同じ process の中で走らせる module)。VANISH は親の子 A を SIGKILL で道連れにする — exit の file が書かれない。
(val ENTRY-SOURCES {EXIT-3 "import sys\nprint('out')\nsys.exit(3)\n"
                    SLEEPER "import time\ntime.sleep(30)\n"
                    VANISH "import os, signal\nos.kill(os.getppid(), signal.SIGKILL)\n"})
(val FAKE-WARM-CHILD (os.path.join (os.path.dirname (os.path.abspath __file__)) "fake_warm_child.py"))


(defrecord WarmPaths
  "契約の世界の置き場(root = exit の file と log を置く dir・socket 4 つ — 頭の註)。"
  (#^ str root)
  (#^ str accepting)
  (#^ str refusing)
  (#^ str silent)
  (#^ str missing))


(defclass [(dataclass :frozen True)] WarmWorldPaths [EffectBase]
  "契約の世界の置き場(WarmPaths)を求める効果。")


(defhandler warm-world-paths [#^ WarmPaths paths]
  ;; 引数に残す理由: 置き場は解釈器ごとに組み立ての側で決まる値(本物は走るたびに作る一時 dir)。
  (WarmWorldPaths []
    (resume paths)))


(defk paths-under [root]
  {:pre [(: root str)] :post [(: % WarmPaths)] :tags {:context "warm-test" :role "judgment"}}
  "root の下の契約の置き場を決めるため(本物と fake で同じ名)。"
  (WarmPaths :root root
             :accepting (os.path.join root "accepting.sock")
             :refusing (os.path.join root "refusing.sock")
             :silent (os.path.join root "silent.sock")
             :missing (os.path.join root "missing.sock")))


(defk under-os-warm [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")] :tags {:context "warm-test" :role "foundation"}}
  "本物の os-warm-process-handler の下で program を走らせる。根は新しい一時 dir、入口の module をそこへ置き、偽の待ちの子を socket ごとに
   起こす(missing の socket は起こさない)— それぞれが listen を始めた知らせの 1 行を読むまで待つ(時間で待たない)。走った後に止める。"
  (var answer None)
  (with [directory (tempfile.TemporaryDirectory)]
    (val root (os.path.realpath directory))
    (for [#(entry source) (.items ENTRY-SOURCES)]
      (with [f (open (os.path.join root (+ entry ".py")) "w" :encoding "utf-8")] (.write f source)))
    (<- paths WarmPaths (paths-under root))
    (val starts #(#(paths.accepting "accept") #(paths.refusing (+ "refuse:" REFUSAL)) #(paths.silent "silent")))
    (val children (lfor #(socket-path mode) starts
                        (subprocess.Popen [sys.executable FAKE-WARM-CHILD socket-path mode] :stdout subprocess.PIPE :text True)))
    (try
      (for [child children]
        (val first (if (is child.stdout None) "" (.readline child.stdout)))
        (when (!= (.strip first) "ready")
          (raise (RuntimeError (.format "偽の待ちの子が起きない: {!r}" first)))))
      (<- ran (with_handlers [(warm-world-paths paths) subprocess-handler os-warm-process-handler] program))
      (:= answer ran)
      (finally
        (for [child children]
          (.kill child)
          (.wait child)))))
  answer)


(defk scripted-sleep [commands request]
  {:pre [(: commands (of tuple ScriptedCommand ...)) (: request RunProcess)] :post [(: % ProcessOutcome)]
   :tags {:context "warm-test" :role "program"}}
  "台本の sleep: 時間の経過は台本の世界に無いので、すぐ終わる。"
  (ProcessOutcome :exit-code 0 :stdout "" :stderr ""))


(val SCRIPT (WarmScript :sockets #((WarmSocket :path (+ MEMORY-ROOT "/accepting.sock"))
                                   (WarmSocket :path (+ MEMORY-ROOT "/refusing.sock") :answer WarmSocketAnswer.REFUSES :refusal REFUSAL)
                                   (WarmSocket :path (+ MEMORY-ROOT "/silent.sock") :answer WarmSocketAnswer.SILENT))
                        ;; 本物の入口と同じ終わり方: EXIT-3 は 2 度走ってから 3・SLEEPER は止められるまで・VANISH は exit の file を書かずに消える。
                        :runs #((WarmRun :entry EXIT-3 :polls 2 :exit-code 3)
                                (WarmRun :entry SLEEPER :polls 1000000)
                                (WarmRun :entry VANISH :polls 1 :writes-exit False))))


(defk under-scripted-warm [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")] :tags {:context "warm-test" :role "foundation"}}
  "fake の scripted-warm-process-handler の下で program を走らせる。外から順に: state・置き場・memory の置き場・台本の sleep・待ちの子の台本。"
  (<- paths WarmPaths (paths-under MEMORY-ROOT))
  (<- answer (with_handlers [(state) (warm-world-paths paths) (memory-file-handler (MemoryFiles :dirs #(MEMORY-ROOT)))
                             (scripted-process-handler (ProcessScript :commands #((ScriptedCommand :name "sleep" :run scripted-sleep))))
                             (scripted-warm-process-handler SCRIPT)]
               program))
  answer)


(val INTERPRETERS {OS-WARM under-os-warm
                   SCRIPTED-WARM under-scripted-warm})
