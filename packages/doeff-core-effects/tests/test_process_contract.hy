;;; 子 process の契約テスト — 同じ効果(RunProcess・ExecutableAt・ReadEnvironment・WorkingDirectory)に答える本物(subprocess-handler)と
;;; fake(scripted-process-handler)が、同じ deftest を通る。解釈器の組み立てと契約の世界は
;;; process_contract_handlers.hy。本物は短い実の命令(/bin/sh -c と sleep — 合わせて 1 秒ほど)で、fake は台本で同じ答えを得る。
;;;
;;;   * returncode は丸めない(signal で終わった子は負)・stdout / stderr は別々に text で
;;;   * stdin は子へ渡る
;;;   * utf-8 でない bytes は可逆の文字列(utf-8 と surrogateescape)で行き来する — 子の出力も stdin も encode し直すと元の bytes
;;;   * 子の環境: env None = 呼び手の環境を継ぐ・REPLACE(既定)= 渡した組が全部・EXTEND = 継いで足す(同じ名は足した方が勝つ)・
;;;     env-drop は EXTEND で継ぐ名から外す。ReadEnvironment は在る名だけを names の順で
;;;   * cwd の dir で子が走る
;;;   * 起こせない子は答え(exit-code 127・started False・OSError の文)— 断る順は cwd が先、命令が後
;;;   * 時間切れは答え(exit-code 124・timed-out True)・時間内なら普通の答え
;;;   * output-path の末尾へ stdout・stderr の順に足す(答えも出力を持つ)。足せない output-path は OSError が上がる
;;;   * ExecutableAt は起こせる命令と起こせない命令を分ける・dir(命令と同じ名でも)と無い path は False・WorkingDirectory は在る dir の絶対 path
;;;   * ReadMachineName は空でない機体の名を答える(agora-redesign #3050)
;;;   * ProcessAlive は生きている pid(init = 1)と終わった子の pid を分ける・0 以下の pid は生きていない(agora-redesign #2184)
;;;   * process-group は、時間内に終わった後に背景へ回った孫を止め、時間切れでは孫ごと group を止める。stream-output の output-path は、
;;;     時間切れで止めた子が出した分も持つ(agora-redesign #2184)
;;;   * 立てたらすぐ返す子(agora-redesign #2223): StartProcess は待たずに pid を返し、PollProcess で終わるまで問える(終わりを答えた子は
;;;     忘れる)・StopProcess は走る子を SIGTERM で止める(-15)・group を止めると孫も止まる・立てられない形は ProcessNotStarted(出力の
;;;     file・cwd・命令)・立てていない pid には触らない・出力は file へ流れるので pipe の容量を超えても子は止まらない
;;;   * 本物を thread で回す offloaded-subprocess-handler も、本物と同じ答えを返す(同じ deftest を通る)
;;; 本物だけの性質(WorkingDirectory が自分の process の作業 dir)と fake だけの性質(job ごとの作業 dir・台本から台本を走らせる・
;;; 台本が env None を None で受ける)は test_process_file_effects.hy。
(require doeff-hy.macros [defk deftest <- val var])
(import contextlib)
(import os)
(import signal)
(import sys)
(import doeff_core_effects.file_effects [MakeDirectory PathKind PathStat ReadText StatPath WriteText])
(import doeff_core_effects.process_effects [EnvEntry EnvMode ExecutableAt ProcessAlive ProcessOutcome ReadEnvironment RunProcess
                                            WorkingDirectory StartProcess PollProcess StopProcess ProcessStarted ProcessNotStarted
                                            ProcessRunning ProcessExited ProcessNotChild SignalProcess ProcessSignal ProcessSignalled
                                            WriteProcessInput ProcessInputWritten
                                            ReadInterpreter ReadMachineName ResolveModule InterpreterFacts ModuleFound
                                            ModuleNotFound environment-mapping])
(import process_contract_handlers [BIG-OUTPUT BIG-OUTPUT-TEXT CAT ContractRoot ENV-PROBE FIRST-THEN-WAIT KILLED LEFT-BEHIND LEFT-BEHIND-THEN-WAIT NOT-UTF-8
                                   NOT-UTF-8-BYTES OUT-ERR OUT-ERR-EXIT OWN-PID PWD TWO-LINES
                                   PROBE-MODULE NAMESPACE-MODULE MISSING-MODULE])

(val MISSING-COMMAND "/nonexistent/doeff-command")
(val GIVEN-ENV #((EnvEntry :name "DOEFF_SHADOWED" :value "足した") (EnvEntry :name "DOEFF_ADDED" :value "足した")))


(defk shell [script #** options]
  {:pre [(: script str) (: options dict)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "program"}}
  "/bin/sh -c script を 1 回走らせる(options は RunProcess の欄)。"
  (<- outcome ProcessOutcome (RunProcess :argv #("/bin/sh" "-c" script) #** options))
  outcome)


(defk refused [detail]
  {:pre [(: detail str)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "起こせない子の答えの期待(契約の側で独りで書く — 答え手の関数を借りない)。"
  (ProcessOutcome :exit-code 127 :stdout "" :stderr "" :started False :start-error detail))


(deftest test-the-exit-code-and-the-output-are-kept-raw
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- failed ProcessOutcome (shell OUT-ERR-EXIT))
  (<- killed ProcessOutcome (shell KILLED))
  (assert (= failed (ProcessOutcome :exit-code 3 :stdout "out\n" :stderr "err\n")) failed)
  (assert (= killed (ProcessOutcome :exit-code -9 :stdout "" :stderr "")) (.format "signal 9 で終わった子の答え {}" killed)))


(deftest test-stdin-is-given-to-the-child
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- echoed ProcessOutcome (shell CAT :stdin "入力\n2行目"))
  (assert (= echoed (ProcessOutcome :exit-code 0 :stdout "入力\n2行目" :stderr "")) echoed))


(deftest test-bytes-that-are-not-utf-8-survive-the-round-trip
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; 子が出した utf-8 でない bytes は可逆の文字列で返る(壊れた bytes は surrogate の文字)— encode し直すと元の bytes(git の diff を
  ;; patch-id へ渡す使い手が bytes を保てる — agora-redesign #2160)。
  (<- printed ProcessOutcome (shell NOT-UTF-8))
  (assert (= printed.exit-code 0) printed)
  (assert (= (.encode printed.stdout "utf-8" "surrogateescape") NOT-UTF-8-BYTES) (.format "子の出力の bytes {!r}" printed.stdout))
  ;; stdin に置いた可逆の文字列は、元の bytes のまま子へ渡り、そのまま戻る(有効な utf-8 と NUL も混ぜる)。
  (val given (.decode b"\xfe\x00\xff\xe3\x81\x82\n" "utf-8" "surrogateescape"))
  (<- echoed ProcessOutcome (shell CAT :stdin given))
  (assert (= echoed (ProcessOutcome :exit-code 0 :stdout given :stderr "")) echoed))


(deftest test-the-child-environment-follows-the-env-mode
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- inherited ProcessOutcome (shell ENV-PROBE))
  (<- replaced ProcessOutcome (shell ENV-PROBE :env GIVEN-ENV))
  (<- extended ProcessOutcome (shell ENV-PROBE :env GIVEN-ENV :env-mode EnvMode.EXTEND))
  (<- dropped ProcessOutcome (shell ENV-PROBE :env GIVEN-ENV :env-mode EnvMode.EXTEND :env-drop #("DOEFF_INH*" "DOEFF_ADDED")))
  (assert (= inherited.stdout "継いだ|親|") (.format "env None の子の環境 {}" inherited))
  (assert (= replaced.stdout "|足した|足した") (.format "既定(REPLACE)の子の環境 {}" replaced))
  (assert (= extended.stdout "継いだ|足した|足した") (.format "EXTEND の子の環境 {}" extended))
  ;; env-drop は継ぐ名だけを外す(足した名は外さない)。
  (assert (= dropped.stdout "|足した|足した") (.format "EXTEND と env-drop の子の環境 {}" dropped)))


(deftest test-the-own-environment-is-read-by-name
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- seen tuple (ReadEnvironment #("DOEFF_MISSING" "DOEFF_SHADOWED" "DOEFF_INHERITED")))
  (assert (= seen #((EnvEntry :name "DOEFF_SHADOWED" :value "親") (EnvEntry :name "DOEFF_INHERITED" :value "継いだ"))) seen))



(deftest test-the-own-environment-is-read-by-prefix
  ;; prefixes(#2472)は頭で始まる名も拾う — names の分の後に名の順で続き、names に在る名は 2 度出さない。
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- seen tuple (ReadEnvironment #("DOEFF_SHADOWED") :prefixes #("DOEFF_INHERIT" "DOEFF_SHADOW")))
  (assert (= seen #((EnvEntry :name "DOEFF_SHADOWED" :value "親") (EnvEntry :name "DOEFF_INHERITED" :value "継いだ"))) seen)
  (<- none tuple (ReadEnvironment #() :prefixes #("DOEFF_NO_SUCH_")))
  (assert (= none #()) none))


(deftest test-the-whole-own-environment-is-a-mapping
  ;; environment-mapping(#3012)は ReadEnvironment の接頭辞 "" の答えを 名 → 値 の写像にする — 子 process の env の元に環境を丸ごと
  ;; 渡す呼び手が os.environ を参照しないための 1 か所。呼び手の環境の名(契約の世界の 2 つを含む)が全部入る。
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- mapping (get dict #(str str)) (environment-mapping))
  (assert (= #((.get mapping "DOEFF_INHERITED") (.get mapping "DOEFF_SHADOWED")) #("継いだ" "親")) mapping))

(deftest test-the-child-runs-in-the-given-directory
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (<- shown ProcessOutcome (shell PWD :cwd root))
  (assert (= shown (ProcessOutcome :exit-code 0 :stdout (+ root "\n") :stderr "")) shown))


(deftest test-a-child-that-cannot-start-is-an-answer
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (<- (WriteText (+ root "/file") "dir でない"))
  (<- no-command ProcessOutcome (RunProcess :argv #(MISSING-COMMAND)))
  (<- no-cwd ProcessOutcome (shell PWD :cwd (+ root "/none")))
  (<- file-cwd ProcessOutcome (shell PWD :cwd (+ root "/file")))
  (<- neither ProcessOutcome (RunProcess :argv #(MISSING-COMMAND) :cwd (+ root "/none")))
  (<- want-no-command ProcessOutcome (refused (.format "[Errno 2] No such file or directory: {!r}" MISSING-COMMAND)))
  (<- want-no-cwd ProcessOutcome (refused (.format "[Errno 2] No such file or directory: {!r}" (+ root "/none"))))
  (<- want-file-cwd ProcessOutcome (refused (.format "[Errno 20] Not a directory: {!r}" (+ root "/file"))))
  (assert (= no-command want-no-command) (.format "無い命令の答え {}" no-command))
  (assert (= no-cwd want-no-cwd) (.format "無い cwd の答え {}" no-cwd))
  (assert (= file-cwd want-file-cwd) (.format "file を cwd にした答え {}" file-cwd))
  ;; 命令も cwd も無ければ cwd で断る(本物の子は exec の前に cwd へ移る)。
  (assert (= neither want-no-cwd) (.format "命令も cwd も無い答え {}" neither)))


(deftest test-a-timeout-is-an-answer
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- slow ProcessOutcome (RunProcess :argv #("sleep" "5") :timeout 0.2))
  (<- quick ProcessOutcome (RunProcess :argv #("sleep" "0") :timeout 5.0))
  (assert (= slow (ProcessOutcome :exit-code 124 :stdout "" :stderr "" :timed-out True)) (.format "時間切れの答え {}" slow))
  (assert (= quick (ProcessOutcome :exit-code 0 :stdout "" :stderr "")) (.format "時間内に終わった答え {}" quick)))


(deftest test-the-output-is-appended-to-the-output-path
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (val log (+ root "/out.log"))
  (<- first ProcessOutcome (shell OUT-ERR :output-path log))
  (<- second ProcessOutcome (shell OUT-ERR-EXIT :output-path log))
  (<- logged str (ReadText log))
  (assert (= first (ProcessOutcome :exit-code 0 :stdout "out\n" :stderr "err\n")) first)
  (assert (= second.exit-code 3) second)
  (assert (= logged "out\nerr\nout\nerr\n") (.format "output-path に足された出力 {!r}" logged)))


(deftest test-an-output-path-that-cannot-be-written-raises
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (val log (+ root "/none/out.log"))
  (var raised None)
  (try
    (<- (shell OUT-ERR :output-path log))
    (except [error OSError]
      (:= raised (str error))))
  (assert (= raised (.format "[Errno 2] No such file or directory: {!r}" log))
          (.format "足せない output-path で上がった例外の文 {!r}" raised)))


(deftest test-executables-are-told-apart
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- runnable bool (ExecutableAt :path "/bin/sh"))
  (<- missing bool (ExecutableAt :path MISSING-COMMAND))
  (assert runnable "/bin/sh が起こせない")
  (assert (not missing) (.format "{} が起こせる" MISSING-COMMAND)))


(deftest test-a-directory-or-a-missing-path-is-not-executable
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; dir は実行の bit があっても起こせる file ではない — 命令と同じ名の dir も、置き場の根も False。置き場の無い path も False。
  (<- root str (ContractRoot))
  (val named-dir (+ root "/sh"))
  (<- (MakeDirectory named-dir))
  (<- root-seen bool (ExecutableAt :path root))
  (<- named-dir-seen bool (ExecutableAt :path named-dir))
  (<- missing-seen bool (ExecutableAt :path (+ root "/none")))
  (assert (= #(root-seen named-dir-seen missing-seen) #(False False False))
          (.format "ExecutableAt の答え(置き場の根・命令と同じ名の dir・無い path){}" #(root-seen named-dir-seen missing-seen))))


(deftest test-the-own-interpreter-is-read
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; interpreter の事実(agora-redesign #2347): prefix は在る dir の絶対 path・pid は生きている process。
  (<- facts InterpreterFacts (ReadInterpreter))
  (<- seen PathStat (StatPath facts.prefix))
  (<- alive bool (ProcessAlive facts.pid))
  (assert (os.path.isabs facts.prefix) facts)
  (assert (= seen.kind PathKind.DIRECTORY) (.format "prefix {} の種類 {}" facts.prefix seen.kind))
  (assert alive (.format "pid {} が生きていない" facts.pid)))


(deftest test-the-own-machine-name-is-read
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; 機体の名(agora-redesign #3050): 答えは空でない文字列。本物が socket.gethostname と同じ値・台本が ProcessScript の machine-name を
  ;; 答えることは片方だけの性質(test_process_file_effects.hy)。
  (<- name str (ReadMachineName))
  (assert (and (isinstance name str) (> (len name) 0)) (.format "機体の名の答え {!r}" name)))


(deftest test-a-module-name-is-resolved-to-its-place-without-importing-it
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; import が解く置き場(#2347): file の module は file の path・__init__ の無い package は file を持たず探す dir だけ・解けない名は値で。
  (<- root str (ContractRoot))
  (<- probe (| ModuleFound ModuleNotFound) (ResolveModule PROBE-MODULE))
  (<- namespace (| ModuleFound ModuleNotFound) (ResolveModule NAMESPACE-MODULE))
  (<- missing (| ModuleFound ModuleNotFound) (ResolveModule MISSING-MODULE))
  (assert (= probe (ModuleFound :name PROBE-MODULE :origin (+ root "/" PROBE-MODULE ".py") :search-locations #())) probe)
  (assert (= namespace (ModuleFound :name NAMESPACE-MODULE :origin None :search-locations #((+ root "/" NAMESPACE-MODULE)))) namespace)
  (assert (= missing (ModuleNotFound :name MISSING-MODULE)) missing))


(deftest test-the-own-working-directory-is-an-existing-directory
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- here str (WorkingDirectory))
  (<- seen PathStat (StatPath here))
  (assert (os.path.isabs here) here)
  (assert (= seen.kind PathKind.DIRECTORY) (.format "作業 dir {} の種類 {}" here seen.kind)))


(defk gone-soon [pid]
  {:pre [(: pid int)] :post [(: % bool)] :tags {:context "process-test" :role "program"}}
  "pid の process が 2 秒の内に死んだか — 止めた孫は init が拾うまでの間だけ生きて見える(zombie にも signal 0 は届く)ので、0.05 秒ずつ
   見直す。"
  (var alive True)
  (var tries 0)
  (while (and alive (< tries 40))
    (<- seen bool (ProcessAlive pid))
    (:= alive seen)
    (when alive
      (<- (RunProcess :argv #("sleep" "0.05")))
      (:= tries (+ tries 1))))
  (not alive))


(deftest test-whether-a-process-is-alive-is-told
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- init bool (ProcessAlive 1))
  (<- finished ProcessOutcome (shell OWN-PID))
  (<- gone bool (ProcessAlive (int (.strip finished.stdout))))
  (<- zero bool (ProcessAlive 0))
  (<- negative bool (ProcessAlive -1))
  (assert init "init(pid 1)が生きていない答え")
  (assert (not gone) (.format "終わった sh(pid {})が生きている答え" (.strip finished.stdout)))
  (assert (= #(zero negative) #(False False)) (.format "0 以下の pid の答え {}" #(zero negative))))


(deftest test-a-group-stops-what-the-child-left-behind
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- finished ProcessOutcome (shell LEFT-BEHIND :process-group True))
  (assert (= finished.exit-code 0) finished)
  (<- stopped bool (gone-soon (int (.strip finished.stdout))))
  (assert stopped (.format "背景に回した孫(pid {})が、group の後始末の後も生きている" (.strip finished.stdout))))


(deftest test-a-timeout-stops-the-whole-group
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- slow ProcessOutcome (shell LEFT-BEHIND-THEN-WAIT :process-group True :timeout 0.5 :stop-grace 2.0))
  (assert (and slow.timed-out (= slow.exit-code 124)) (.format "時間切れの答え {}" slow))
  (<- stopped bool (gone-soon (int (.strip slow.stdout))))
  (assert stopped (.format "時間切れで止めた group の孫(pid {})が生きている" (.strip slow.stdout))))


(deftest test-the-streamed-output-path-keeps-what-a-stopped-child-printed
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (val log (+ root "/streamed.log"))
  (<- whole ProcessOutcome (shell TWO-LINES :output-path log :stream-output True))
  (<- stopped ProcessOutcome (shell FIRST-THEN-WAIT :output-path log :stream-output True :process-group True :timeout 0.5
                                    :stop-grace 2.0))
  (<- logged str (ReadText log))
  (assert (= whole (ProcessOutcome :exit-code 0 :stdout "one\ntwo\n" :stderr "")) whole)
  (assert (= stopped (ProcessOutcome :exit-code 124 :stdout "first\n" :stderr "" :timed-out True)) (.format "時間切れの答え {}" stopped))
  (assert (= logged "one\ntwo\nfirst\n") (.format "流しながら書いた output-path {!r}" logged)))


;; ---- 立てたらすぐ返す子(StartProcess・PollProcess・StopProcess — agora-redesign #2223)---------------------------------------------

(defk exited-soon [pid]
  {:pre [(: pid int)] :post [(: % (| ProcessExited ProcessRunning ProcessNotChild))] :tags {:context "process-test" :role "program"}}
  "立てた子を 5 秒の内に終わるまで 0.05 秒ずつ問う — 立てた直後の本物の子はまだ走っていることがあるので。答え = 最後の PollProcess の答え。"
  (var seen (ProcessRunning :pid pid))
  (var tries 0)
  (while (and (isinstance seen ProcessRunning) (< tries 100))
    (<- polled (PollProcess pid))
    (:= seen polled)
    (when (isinstance seen ProcessRunning)
      (<- (RunProcess :argv #("sleep" "0.05")))
      (:= tries (+ tries 1))))
  seen)


(defk first-line-of [path]
  {:pre [(: path str)] :post [(: % str)] :tags {:context "process-test" :role "program"}}
  "file に 1 行目が書かれるまで 2 秒の内 0.05 秒ずつ読み直し、その行を返す — 立てたらすぐ返す子の出力は、返った時にはまだ無いことがある。"
  (var text "")
  (var tries 0)
  (while (and (not-in "\n" text) (< tries 40))
    (<- read str (ReadText path))
    (:= text read)
    (when (not-in "\n" text)
      (<- (RunProcess :argv #("sleep" "0.05")))
      (:= tries (+ tries 1))))
  (get (.split text "\n") 0))


(deftest test-a-started-child-is-polled-until-it-exits
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (<- started (StartProcess :argv #("/bin/sh" "-c" OUT-ERR-EXIT) :stdout-path (+ root "/out") :stderr-path (+ root "/err")))
  (assert (isinstance started ProcessStarted) started)
  (<- exited (exited-soon started.pid))
  (<- out str (ReadText (+ root "/out")))
  (<- err str (ReadText (+ root "/err")))
  (<- again (PollProcess started.pid))
  (assert (= exited (ProcessExited :pid started.pid :exit-code 3)) exited)
  (assert (= #(out err) #("out\n" "err\n")) (.format "子の出力の file {!r}" #(out err)))
  ;; 終わりを答えた子は回収して忘れる — もう一度問うと立てた子でない。
  (assert (= again (ProcessNotChild :pid started.pid)) again))


(deftest test-a-running-child-is-stopped
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- started (StartProcess :argv #("sleep" "30")))
  (assert (isinstance started ProcessStarted) started)
  (<- running (PollProcess started.pid))
  (<- stopped (StopProcess :pid started.pid :stop-grace 2.0))
  (<- again (StopProcess :pid started.pid :stop-grace 2.0))
  (assert (= running (ProcessRunning :pid started.pid)) running)
  (assert (= stopped (ProcessExited :pid started.pid :exit-code -15)) (.format "SIGTERM で止めた子の答え {}" stopped))
  (assert (= again (ProcessNotChild :pid started.pid)) again))



;; ---- 標準入力の pipe を握る子と、終わった group の残りを止める子(StartProcess の hold-stdin・reap-group — #2471)----------------------
;; 消費者 = doeff-cluster の worker の子 process(shim は標準入力の EOF で job の group を止める・終わった job の group の孫を回収する)。

(deftest test-a-child-holding-stdin-keeps-running-until-it-is-collected
  ;; 台本の世界に標準入力の pipe は無いので本物の答え手だけ。hold-stdin の子は EOF を読まずに走り続け、無い子(DEVNULL)はすぐ EOF で終わる。
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (<- held (StartProcess :argv #("/bin/sh" "-c" "cat >/dev/null; exit 5") :hold-stdin True))
  (<- free (StartProcess :argv #("/bin/sh" "-c" "cat >/dev/null; exit 5")))
  (<- (RunProcess :argv #("sleep" "0.3")))
  (<- held-seen (PollProcess held.pid))
  (<- free-seen (exited-soon free.pid))
  (assert (= held-seen (ProcessRunning :pid held.pid)) (.format "標準入力を握った子が EOF を読んだ: {}" held-seen))
  (assert (= free-seen (ProcessExited :pid free.pid :exit-code 5)) free-seen)
  (<- stopped (StopProcess :pid held.pid :stop-grace 2.0))
  (assert (isinstance stopped ProcessExited) stopped))


(deftest test-collecting-a-reap-group-child-stops-what-it-left-behind
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (val out (+ root "/pid"))
  (<- started (StartProcess :argv #("/bin/sh" "-c" LEFT-BEHIND) :stdout-path out :process-group True :reap-group True))
  (assert (isinstance started ProcessStarted) started)
  (<- grandchild str (first-line-of out))
  (<- exited (exited-soon started.pid))
  (assert (= exited (ProcessExited :pid started.pid :exit-code 0)) exited)
  (<- gone bool (gone-soon (int grandchild)))
  (assert gone (.format "reap-group の子を回収した後も、背景に回した孫(pid {})が生きている" grandchild)))

;; ---- 待たずに signal を送る(SignalProcess — #2461)------------------------------------------------------------------------------
;; 消費者 = doeff-cluster の worker の段ごとの止め(拍ごとに TERM を送り、止まらなければ KILL・終わりは PollProcess で確かめる)。

(deftest test-a-signalled-child-ends-and-is-collected-by-poll
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (for [#(sent code) [#(ProcessSignal.TERM -15) #(ProcessSignal.KILL -9)]]
    (<- started (StartProcess :argv #("sleep" "30")))
    (assert (isinstance started ProcessStarted) started)
    (<- answer (SignalProcess :pid started.pid :signal sent))
    (assert (= answer (ProcessSignalled :pid started.pid :delivered True)) #(sent answer))
    ;; 送るだけで待たない — 終わりは PollProcess が答えて回収する。
    (<- exited (exited-soon started.pid))
    (assert (= exited (ProcessExited :pid started.pid :exit-code code)) #(sent exited))
    (<- again (SignalProcess :pid started.pid :signal sent))
    (assert (= again (ProcessNotChild :pid started.pid)) again)))


(deftest test-an-ended-child-is-not-signalled-and-its-end-is-kept-for-poll
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- started (StartProcess :argv #("/bin/sh" "-c" OUT-ERR-EXIT)))  ; 出力して 3 で終わる台本
  (assert (isinstance started ProcessStarted) started)
  ;; 本物の子が終わるのを待つ(問うと回収してしまうので、問わずに待つ)。
  (<- (RunProcess :argv #("sleep" "0.3")))
  (<- answer (SignalProcess :pid started.pid :signal ProcessSignal.TERM))
  (<- exited (PollProcess started.pid))
  (assert (= answer (ProcessSignalled :pid started.pid :delivered False)) answer)
  (assert (= exited (ProcessExited :pid started.pid :exit-code 3)) exited))


(deftest test-a-pid-that-is-not-a-started-child-is-not-signalled
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; 立てていない pid(init = 1)には送らない — 他人の process を止めない。
  (<- answer (SignalProcess :pid 1 :signal ProcessSignal.KILL))
  (<- init bool (ProcessAlive 1))
  (assert (= answer (ProcessNotChild :pid 1)) answer)
  (assert init "init(pid 1)が生きていない答え"))

;; ---- 握った標準入力の pipe へ書く(WriteProcessInput — #3672)-------------------------------------------------------------------
;; 消費者 = doeff-cluster の worker(shim の標準入力へ job の退きの知らせの行を送る — shim が job の知らせの pipe へ中継する)。

(deftest test-a-line-written-to-a-held-stdin-reaches-the-child
  ;; 台本の世界に標準入力の pipe は無いので本物の答え手だけ。hold-stdin の子は、書いた行をその場で読める(子が終わるまで待たない)。
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (<- root str (ContractRoot))
  (val out (+ root "/line"))
  (<- started (StartProcess :argv #("/bin/sh" "-c" "read line; echo \"got $line\"; cat >/dev/null") :stdout-path out :hold-stdin True))
  (assert (isinstance started ProcessStarted) started)
  (<- written (WriteProcessInput :pid started.pid :text "retired\n"))
  (<- line str (first-line-of out))
  (assert (= written (ProcessInputWritten :pid started.pid :delivered True)) written)
  (assert (= line "got retired") line)
  (<- stopped (StopProcess :pid started.pid :stop-grace 2.0))
  (assert (isinstance stopped ProcessExited) stopped))


(deftest test-input-is-not-written-without-a-held-stdin-or-after-the-child-ended
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; pipe を握っていない子(DEVNULL)には書かない。
  (<- free (StartProcess :argv #("sleep" "30")))
  (<- unheld (WriteProcessInput :pid free.pid :text "retired\n"))
  (assert (= unheld (ProcessInputWritten :pid free.pid :delivered False)) unheld)
  (<- (StopProcess :pid free.pid :stop-grace 2.0))
  ;; 終わっていた子には書かず、終わりは PollProcess のために残す。
  (<- ended (StartProcess :argv #("/bin/sh" "-c" OUT-ERR-EXIT) :hold-stdin True))
  (<- (RunProcess :argv #("sleep" "0.3")))
  (<- late (WriteProcessInput :pid ended.pid :text "retired\n"))
  (<- exited (PollProcess ended.pid))
  (assert (= late (ProcessInputWritten :pid ended.pid :delivered False)) late)
  (assert (= exited (ProcessExited :pid ended.pid :exit-code 3)) exited)
  ;; 立てていない pid(init = 1)には書かない。
  (<- stranger (WriteProcessInput :pid 1 :text "retired\n"))
  (assert (= stranger (ProcessNotChild :pid 1)) stranger))


(deftest test-stopping-a-group-stops-what-the-child-left-behind
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (val out (+ root "/pid"))
  (<- started (StartProcess :argv #("/bin/sh" "-c" LEFT-BEHIND-THEN-WAIT) :stdout-path out :process-group True))
  (assert (isinstance started ProcessStarted) started)
  (<- grandchild str (first-line-of out))
  (<- stopped (StopProcess :pid started.pid :stop-grace 2.0))
  (assert (= stopped (ProcessExited :pid started.pid :exit-code -15)) (.format "group を止めた子の答え {}" stopped))
  (<- gone bool (gone-soon (int grandchild)))
  (assert gone (.format "group を止めた後も、子が背景に回した孫(pid {})が生きている" grandchild)))


(deftest test-a-child-that-cannot-be-started-is-an-answer
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (<- no-command (StartProcess :argv #(MISSING-COMMAND)))
  (<- no-cwd (StartProcess :argv #("/bin/sh" "-c" PWD) :cwd (+ root "/none")))
  (<- no-output (StartProcess :argv #("/bin/sh" "-c" OUT-ERR) :stdout-path (+ root "/none/out")))
  (assert (= no-command (ProcessNotStarted :detail (.format "[Errno 2] No such file or directory: {!r}" MISSING-COMMAND))) no-command)
  (assert (= no-cwd (ProcessNotStarted :detail (.format "[Errno 2] No such file or directory: {!r}" (+ root "/none")))) no-cwd)
  (assert (= no-output (ProcessNotStarted :detail (.format "[Errno 2] No such file or directory: {!r}" (+ root "/none/out")))) no-output))


(deftest test-a-pid-that-is-not-a-started-child-is-not-touched
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; 立てていない pid(init = 1)には signal を送らずに答える — 他人の process を止めない。
  (<- polled (PollProcess 1))
  (<- stopped (StopProcess :pid 1 :stop-grace 0.1))
  (<- init bool (ProcessAlive 1))
  (assert (= #(polled stopped) #((ProcessNotChild :pid 1) (ProcessNotChild :pid 1))) #(polled stopped))
  (assert init "init(pid 1)が生きていない答え"))


(deftest test-a-large-output-does-not-stop-the-child
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; 出力は file へ流れるので、pipe の容量を大きく超えても子は止まらずに終わる。
  (<- root str (ContractRoot))
  (<- started (StartProcess :argv #("/bin/sh" "-c" BIG-OUTPUT) :stdout-path (+ root "/big")))
  (assert (isinstance started ProcessStarted) started)
  (<- exited (exited-soon started.pid))
  (<- big str (ReadText (+ root "/big")))
  (assert (= exited (ProcessExited :pid started.pid :exit-code 0)) exited)
  (assert (= (len big) (len BIG-OUTPUT-TEXT)) (.format "子の出力の長さ {}" (len big))))


;; ---- 起こした process が終わったら、その子も終わる(StartProcess の lifetime — agora-redesign #3866)------------------------------------
;; 起こす側は別の process(lifeline_starter.py — 本物の答え手で子を StartProcess する)で、検はそれを外から止める・普通に終わらせる。
;; 片づけ(StopProcess)を走らせずに起こした側が終わっても、子と、子が別の session に起こした孫が残らない。lifetime を OUTLIVES-STARTER
;; と書いた子だけは残る。台本の世界に別の process は無いので本物の答え手だけ。検が赤の時も process を残さないよう、断言の前に残りを止める。

(val STARTER (os.path.join (os.path.dirname (os.path.abspath __file__)) "lifeline_starter.py"))
;; 起こす側の起こし方: python で起こす形と、Hy の入口から起こす形(sys.executable が hy の起動口になる — doeff-cluster の worker と同じ)。
(val PYTHON-STARTER #(sys.executable STARTER))
(val HY-STARTER #((os.path.join (os.path.dirname sys.executable) "hy")
                  (os.path.join (os.path.dirname (os.path.abspath __file__)) "lifeline_starter_hy.hy")))


(defk killed-leftovers [pids]
  {:pre [(: pids tuple)] :post [(: % None)] :tags {:context "process-test" :role "program"}}
  "検が見つけた pid のうち、まだ生きている物を SIGKILL する — 赤の検が起こした process を残さないため(この検の子ではないので
   SignalProcess は ProcessNotChild と答える)。"
  (for [pid pids]
    (with [(contextlib.suppress ProcessLookupError)]
      (os.kill pid signal.SIGKILL)))
  None)


(defk starter-and-child [launch mode root child-out]
  {:pre [(: launch tuple) (: mode str) (: root str) (: child-out str)] :post [(: % tuple)] :tags {:context "process-test" :role "program"}}
  "起こす側を launch(起こし方)と mode で立て、起こす側が書いた子の pid を待って読む。答え = #(起こす側の pid 子の pid)。"
  (val out (+ root "/child-" mode))
  ;; first-line-of は在る file を読み直すので、起こす側が書く前に空で置く。
  (<- (WriteText out ""))
  (when (!= child-out "-") (<- (WriteText child-out "")))
  (<- started (StartProcess :argv #(#* launch mode out child-out)))
  (assert (isinstance started ProcessStarted) started)
  (<- child str (first-line-of out))
  #(started.pid (int child)))


(deftest test-a-child-ends-when-its-starter-is-killed
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (<- root str (ContractRoot))
  (<- pids tuple (starter-and-child PYTHON-STARTER "hold" root "-"))
  (val starter (get pids 0))
  (val child (get pids 1))
  (<- (SignalProcess :pid starter :signal ProcessSignal.KILL))
  (<- (exited-soon starter))
  (<- gone bool (gone-soon child))
  (<- (killed-leftovers #(child)))
  (assert gone (.format "起こした側を SIGKILL した後も、子(pid {})が生きている" child)))


(deftest test-a-child-ends-when-its-starter-exits-without-stopping-it
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (<- root str (ContractRoot))
  (<- pids tuple (starter-and-child PYTHON-STARTER "exit" root "-"))
  (val starter (get pids 0))
  (val child (get pids 1))
  (<- exited (exited-soon starter))
  (<- gone bool (gone-soon child))
  (<- (killed-leftovers #(child)))
  (assert (= exited (ProcessExited :pid starter :exit-code 0)) exited)
  (assert gone (.format "起こした側が StopProcess を呼ばずに終わった後も、子(pid {})が生きている" child)))


(deftest test-a-grandchild-in-another-session-ends-with-the-first-starter
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  ;; 子(起こす側 hold)が更に孫を新しい session に StartProcess する。一番上の起こす側を SIGKILL すると、子も孫も残らない。
  (<- root str (ContractRoot))
  (val grand-out (+ root "/grandchild"))
  (<- pids tuple (starter-and-child PYTHON-STARTER "hold" root grand-out))
  (val starter (get pids 0))
  (val child (get pids 1))
  (<- grandchild str (first-line-of grand-out))
  (<- (SignalProcess :pid starter :signal ProcessSignal.KILL))
  (<- (exited-soon starter))
  (<- child-gone bool (gone-soon child))
  (<- grandchild-gone bool (gone-soon (int grandchild)))
  (<- (killed-leftovers #(child (int grandchild))))
  (assert child-gone (.format "一番上の起こす側を SIGKILL した後も、子(pid {})が生きている" child))
  (assert grandchild-gone (.format "一番上の起こす側を SIGKILL した後も、別の session の孫(pid {})が生きている" grandchild)))


(deftest test-a-child-that-outlives-its-starter-is-left-running
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (<- root str (ContractRoot))
  (<- pids tuple (starter-and-child PYTHON-STARTER "outlive" root "-"))
  (val starter (get pids 0))
  (val child (get pids 1))
  (<- exited (exited-soon starter))
  (<- gone bool (gone-soon child))
  (<- (killed-leftovers #(child)))
  (assert (= exited (ProcessExited :pid starter :exit-code 0)) exited)
  (assert (not gone) (.format "OUTLIVES-STARTER の子(pid {})が、起こした側の終わりで止まった" child)))


(deftest test-a-child-ends-when-its-starter-started-from-hy-is-killed
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  ;; Hy の入口は sys.executable を hy の起動口に書き換える。起こす側がそうでも、見張りは起き、起こす側の SIGKILL で子が終わる。
  (<- root str (ContractRoot))
  (<- pids tuple (starter-and-child HY-STARTER "hold" root "-"))
  (val starter (get pids 0))
  (val child (get pids 1))
  (<- (SignalProcess :pid starter :signal ProcessSignal.KILL))
  (<- (exited-soon starter))
  (<- gone bool (gone-soon child))
  (<- (killed-leftovers #(child)))
  (assert gone (.format "Hy の入口から起きた起こす側を SIGKILL した後も、子(pid {})が生きている" child)))
