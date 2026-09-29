;;; 子 process の契約テスト — 同じ効果(RunProcess・ExecutableAt・ReadEnvironment・WorkingDirectory)に答える本物(subprocess-handler)と
;;; fake(scripted-process-handler)が、同じ deftest を通る。解釈器の組み立てと契約の世界は
;;; process_contract_handlers.hy。本物は短い実の命令(/bin/sh -c と sleep — 合わせて 1 秒ほど)で、fake は台本で同じ答えを得る。
;;;
;;;   * returncode は丸めない(signal で終わった子は負)・stdout / stderr は別々に text で
;;;   * stdin は子へ渡る
;;;   * 子の環境: env None = 呼び手の環境を継ぐ・REPLACE(既定)= 渡した組が全部・EXTEND = 継いで足す(同じ名は足した方が勝つ)・
;;;     env-drop は EXTEND で継ぐ名から外す。ReadEnvironment は在る名だけを names の順で
;;;   * cwd の dir で子が走る
;;;   * 起こせない子は答え(exit-code 127・started False・OSError の文)— 断る順は cwd が先、命令が後
;;;   * 時間切れは答え(exit-code 124・timed-out True)・時間内なら普通の答え
;;;   * output-path の末尾へ stdout・stderr の順に足す(答えも出力を持つ)。足せない output-path は OSError が上がる
;;;   * ExecutableAt は起こせる命令と起こせない命令を分ける・dir(命令と同じ名でも)と無い path は False・WorkingDirectory は在る dir の絶対 path
;;; 本物だけの性質(WorkingDirectory が自分の process の作業 dir)と fake だけの性質(job ごとの作業 dir・台本から台本を走らせる・
;;; 台本が env None を None で受ける)は test_process_file_effects.hy。
(require doeff-hy.macros [defk deftest <- val var])
(import os)
(import doeff_core_effects.file_effects [MakeDirectory PathKind PathStat ReadText StatPath WriteText])
(import doeff_core_effects.process_effects [EnvEntry EnvMode ExecutableAt ProcessOutcome ReadEnvironment RunProcess WorkingDirectory])
(import process_contract_handlers [CAT ContractRoot ENV-PROBE KILLED OUT-ERR OUT-ERR-EXIT PWD])

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
  {:interpreters ["subprocess" "scripted-process"]}
  (<- failed ProcessOutcome (shell OUT-ERR-EXIT))
  (<- killed ProcessOutcome (shell KILLED))
  (assert (= failed (ProcessOutcome :exit-code 3 :stdout "out\n" :stderr "err\n")) failed)
  (assert (= killed (ProcessOutcome :exit-code -9 :stdout "" :stderr "")) (.format "signal 9 で終わった子の答え {}" killed)))


(deftest test-stdin-is-given-to-the-child
  {:interpreters ["subprocess" "scripted-process"]}
  (<- echoed ProcessOutcome (shell CAT :stdin "入力\n2行目"))
  (assert (= echoed (ProcessOutcome :exit-code 0 :stdout "入力\n2行目" :stderr "")) echoed))


(deftest test-the-child-environment-follows-the-env-mode
  {:interpreters ["subprocess" "scripted-process"]}
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
  {:interpreters ["subprocess" "scripted-process"]}
  (<- seen tuple (ReadEnvironment #("DOEFF_MISSING" "DOEFF_SHADOWED" "DOEFF_INHERITED")))
  (assert (= seen #((EnvEntry :name "DOEFF_SHADOWED" :value "親") (EnvEntry :name "DOEFF_INHERITED" :value "継いだ"))) seen))


(deftest test-the-child-runs-in-the-given-directory
  {:interpreters ["subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (<- shown ProcessOutcome (shell PWD :cwd root))
  (assert (= shown (ProcessOutcome :exit-code 0 :stdout (+ root "\n") :stderr "")) shown))


(deftest test-a-child-that-cannot-start-is-an-answer
  {:interpreters ["subprocess" "scripted-process"]}
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
  {:interpreters ["subprocess" "scripted-process"]}
  (<- slow ProcessOutcome (RunProcess :argv #("sleep" "5") :timeout 0.2))
  (<- quick ProcessOutcome (RunProcess :argv #("sleep" "0") :timeout 5.0))
  (assert (= slow (ProcessOutcome :exit-code 124 :stdout "" :stderr "" :timed-out True)) (.format "時間切れの答え {}" slow))
  (assert (= quick (ProcessOutcome :exit-code 0 :stdout "" :stderr "")) (.format "時間内に終わった答え {}" quick)))


(deftest test-the-output-is-appended-to-the-output-path
  {:interpreters ["subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (val log (+ root "/out.log"))
  (<- first ProcessOutcome (shell OUT-ERR :output-path log))
  (<- second ProcessOutcome (shell OUT-ERR-EXIT :output-path log))
  (<- logged str (ReadText log))
  (assert (= first (ProcessOutcome :exit-code 0 :stdout "out\n" :stderr "err\n")) first)
  (assert (= second.exit-code 3) second)
  (assert (= logged "out\nerr\nout\nerr\n") (.format "output-path に足された出力 {!r}" logged)))


(deftest test-an-output-path-that-cannot-be-written-raises
  {:interpreters ["subprocess" "scripted-process"]}
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
  {:interpreters ["subprocess" "scripted-process"]}
  (<- runnable bool (ExecutableAt :path "/bin/sh"))
  (<- missing bool (ExecutableAt :path MISSING-COMMAND))
  (assert runnable "/bin/sh が起こせない")
  (assert (not missing) (.format "{} が起こせる" MISSING-COMMAND)))


(deftest test-a-directory-or-a-missing-path-is-not-executable
  {:interpreters ["subprocess" "scripted-process"]}
  ;; dir は実行の bit があっても起こせる file ではない — 命令と同じ名の dir も、置き場の根も False。置き場の無い path も False。
  (<- root str (ContractRoot))
  (val named-dir (+ root "/sh"))
  (<- (MakeDirectory named-dir))
  (<- root-seen bool (ExecutableAt :path root))
  (<- named-dir-seen bool (ExecutableAt :path named-dir))
  (<- missing-seen bool (ExecutableAt :path (+ root "/none")))
  (assert (= #(root-seen named-dir-seen missing-seen) #(False False False))
          (.format "ExecutableAt の答え(置き場の根・命令と同じ名の dir・無い path){}" #(root-seen named-dir-seen missing-seen))))


(deftest test-the-own-working-directory-is-an-existing-directory
  {:interpreters ["subprocess" "scripted-process"]}
  (<- here str (WorkingDirectory))
  (<- seen PathStat (StatPath here))
  (assert (os.path.isabs here) here)
  (assert (= seen.kind PathKind.DIRECTORY) (.format "作業 dir {} の種類 {}" here seen.kind)))
