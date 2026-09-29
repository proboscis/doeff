;;; 子 process の契約テストの解釈器(composition root)— 同じ契約の Program を、子 process の handler だけ替えて走らせる。
;;;
;;;   subprocess        本物: subprocess-handler(本当の子 process と os.environ)+ os-file-handler(一時 dir)
;;;   scripted-process  fake: scripted-process-handler(I/O なし — 台本)+ memory-file-handler + state
;;;
;;; 契約の世界は解釈器ごとに同じ形で用意する:
;;;   * 置き場の根(ContractRoot の答え): 本物 = 走るたびに新しい一時 dir(実の path)・fake = memory の置き場の MEMORY-ROOT
;;;   * 呼び手の環境(INHERITED): 本物 = 走る間だけ os.environ に置き、走った後に前の値へ戻す・fake = ProcessScript の env
;;;   * 命令: 本物 = /bin/sh と sleep。fake = 台本の sh と sleep で、契約が使う `sh -c <文>` の文ごとに「本物の sh ならこう答える」を
;;;     SHELL-ANSWERS に書く(本物が要求のどの欄 — env・cwd・stdin — から答えを作るかを、台本も同じ欄から作る)
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk defhandler <- val var])
(import dataclasses [dataclass])
(import os)
(import tempfile)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.file_effects [MemoryFiles])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.process_effects [EnvEntry ProcessOutcome RunProcess timed-out-outcome])
(import doeff_core_effects.scripted_process [ScriptedCommand ProcessScript scripted-process-handler])

(val SUBPROCESS "subprocess")
(val SCRIPTED-PROCESS "scripted-process")
(val MEMORY-ROOT "/contract-root")

;; 契約の世界の呼び手の環境(子が継ぐ・ReadEnvironment が読む)。
(val INHERITED #((EnvEntry :name "DOEFF_INHERITED" :value "継いだ") (EnvEntry :name "DOEFF_SHADOWED" :value "親")))

;; 契約が `/bin/sh -c <文>` で走らせる文。
(val OUT-ERR-EXIT "echo out; echo err >&2; exit 3")
(val OUT-ERR "echo out; echo err >&2")
(val KILLED "kill -9 $$")
(val CAT "cat")
(val PWD "pwd")
(val ENV-PROBE "printf '%s|%s|%s' \"$DOEFF_INHERITED\" \"$DOEFF_SHADOWED\" \"$DOEFF_ADDED\"")
(val PROBED-NAMES #("DOEFF_INHERITED" "DOEFF_SHADOWED" "DOEFF_ADDED"))


(defclass [(dataclass :frozen True)] ContractRoot [EffectBase]
  "契約の置き場の根(在る dir の絶対 path)を求める効果。")


(defhandler contract-root [root]
  ;; 引数に残す理由: 根は解釈器ごとに組み立ての側で決まる値(本物は走るたびに作る一時 dir)。
  (ContractRoot []
    (resume root)))


(defk child-env [request]
  {:pre [(: request RunProcess)] :post [(: % dict)] :tags {:context "process-test" :role "judgment"}}
  "台本が受けた要求の子の環境(env が None なら呼び手の環境 INHERITED を継ぐ — 本物の子が os.environ を継ぐのと同じ)。"
  (val entries (match request.env
                  None INHERITED
                  given given))
  (dfor e entries e.name e.value))


(defk exit-3-with-output [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "本物の sh の OUT-ERR-EXIT の答え。"
  (ProcessOutcome :exit-code 3 :stdout "out\n" :stderr "err\n"))


(defk exit-0-with-output [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "本物の sh の OUT-ERR の答え。"
  (ProcessOutcome :exit-code 0 :stdout "out\n" :stderr "err\n"))


(defk killed-by-signal-9 [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "本物の sh の KILLED の答え(signal で終わった子の returncode は負のまま)。"
  (ProcessOutcome :exit-code -9 :stdout "" :stderr ""))


(defk echo-stdin [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "本物の sh の CAT の答え(子の stdin をそのまま出す)。"
  (ProcessOutcome :exit-code 0 :stdout (or request.stdin "") :stderr ""))


(defk print-cwd [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "本物の sh の PWD の答え(子の作業 dir)。"
  (match request.cwd
    None (raise (ValueError "契約の PWD は cwd を渡して走らせる"))
    cwd (ProcessOutcome :exit-code 0 :stdout (+ cwd "\n") :stderr "")))


(defk print-probed-env [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "本物の sh の ENV-PROBE の答え(子の環境の PROBED-NAMES を | で繋ぐ — 無い名は空)。"
  (<- env dict (child-env request))
  (ProcessOutcome :exit-code 0 :stdout (.join "|" (gfor name PROBED-NAMES (.get env name ""))) :stderr ""))


;; 台本の sh が `-c <文>` の文ごとに答える物(本物の sh ならこう答える)。
(val SHELL-ANSWERS {OUT-ERR-EXIT exit-3-with-output
                    OUT-ERR exit-0-with-output
                    KILLED killed-by-signal-9
                    CAT echo-stdin
                    PWD print-cwd
                    ENV-PROBE print-probed-env})


(defk shell-script [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "台本の命令 sh: `sh -c <文>` を SHELL-ANSWERS の文の答えで答える(契約の世界に無い文は検の誤り)。"
  (match (tuple (cut request.argv 1 None))
    #("-c" script) :if (in script SHELL-ANSWERS) (do (<- answer ProcessOutcome ((get SHELL-ANSWERS script) request))
                                                     answer)
    other (raise (ValueError (.format "契約の世界に無い sh の引数: {!r}" other)))))


(defk sleep-script [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "台本の命令 sleep: 秒数が timeout を超えれば本物と同じ時間切れの答え(timed-out-outcome)、超えなければ 0 で終わる。"
  (val seconds (float (get request.argv 1)))
  (match request.timeout
    limit :if (and (is-not limit None) (< limit seconds)) (do (<- timed-out ProcessOutcome (timed-out-outcome "" ""))
                                                timed-out)
    _ (ProcessOutcome :exit-code 0 :stdout "" :stderr "")))


(val SCRIPT (ProcessScript :commands #((ScriptedCommand :name "sh" :run shell-script) (ScriptedCommand :name "sleep" :run sleep-script))
                           :env INHERITED
                           :work-root (+ MEMORY-ROOT "/jobs")))


(defk restore-environment [saved]
  {:pre [(: saved dict)] :post [(: % None)] :tags {:context "process-test" :role "foundation"}}
  "os.environ の名を saved の値(None = 無かった)へ戻す。"
  (for [#(name value) (.items saved)]
    (match value
      None (.pop os.environ name None)
      _ (.update os.environ {name value})))
  None)


(defk under-subprocess [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "process-test" :role "foundation"}}
  "本物の subprocess-handler の下で program を走らせる。根は新しい一時 dir(実の path)、呼び手の環境 INHERITED は走る間だけ
   os.environ に置き、走った後に前の値へ戻す。"
  (var answer None)
  (with [directory (tempfile.TemporaryDirectory)]
    (val saved (dfor e INHERITED e.name (.get os.environ e.name)))
    (.update os.environ (dfor e INHERITED e.name e.value))
    (try
      (<- ran (with_handlers [(contract-root (os.path.realpath directory)) os-file-handler subprocess-handler] program))
      (:= answer ran)
      (finally
        (<- (restore-environment saved)))))
  answer)


(defk under-scripted-process [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "process-test" :role "foundation"}}
  "fake の scripted-process-handler の下で program を走らせる。外から順に: state・根の答え手・memory の置き場(根だけが在る)・台本。"
  (<- answer (with_handlers [(state) (contract-root MEMORY-ROOT) (memory-file-handler (MemoryFiles :dirs #(MEMORY-ROOT)))
                             (scripted-process-handler SCRIPT)]
               program))
  answer)


(val INTERPRETERS {SUBPROCESS under-subprocess
                   SCRIPTED-PROCESS under-scripted-process})
