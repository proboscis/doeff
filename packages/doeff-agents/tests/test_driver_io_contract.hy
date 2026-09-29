;;; driver 層の I/O の契約テスト — 同じ効果(doeff_agents.io_effects)に答える本物(driver-io-handler)と fake(fake-driver-io-handler)が、
;;; 同じ deftest を通る。解釈器の組み立てと契約の世界は driver_io_contract_handlers.hy。本物は一時 dir の中の file と、そこで聞く実の
;;; unix socket と、短い子 process(/bin/sh -c 'exit 0')だけを使う。
;;;
;;;   * ReadText: 不在は None・書いた text(utf-8・複数行)がそのまま読める・WriteText は上書き
;;;   * AppendText: 無い file は作って足す・足した順につながる
;;;   * 親 dir の無い所への WriteText・AppendText・TouchFile・CopyFile は FileNotFoundError(黙って親を作らない)
;;;   * MakeDirs: 親ごと作る・在っても成功・PathExists は file・dir・不在を分ける
;;;   * TouchFile: 無ければ空の file・在れば中身を変えない
;;;   * CopyFile: source 不在は False(target は作らない)・在れば True で同じ中身
;;;   * ListDir: 不在は空・直下の file と dir を絶対 path の昇順で・pattern で絞る・孫は並べない
;;;   * ExecutableAt: 実行の bit の在る mode で書いた file だけが True・dir と無い path は False
;;;   * EnvValue: 在る名は値・無い名は None。WhichExecutable: 無い名は None
;;;   * HomePath・TempRoot は在る dir の絶対 path・ProcessId は正の整数
;;;   * MonotonicTime は戻らず、Sleep の秒以上進む
;;;   * UnixConnectProbe: 聞く socket は accepting・不在と socket でない file は refused
;;;   * UnixLineRequest: 聞く socket は 1 行を答える・不在は FileNotFoundError・socket でない file は ConnectionRefusedError
;;;   * RunProcess: 無い命令は起こせない答え(exit-code 127・started False・OSError の文)
;;;   * SpawnDetached: 正の pid を返し、log-path を(親 dir ごと)用意する
;;; 本物だけの性質(子の出力を utf-8 で読む)は test_session_backend.py、fake だけの性質(実 file を作らない・台本の命令と which の名簿から答える)は
;;; ADR-DOE-AGENTS-013 の検に残す。
(require doeff-hy.macros [defk deftest <- val var])
(import os)
(import doeff [EffectBase])
(import doeff_agents.io_effects [
  AppendText
  CopyFile
  EnvValue
  ExecutableAt
  HomePath
  ListDir
  MakeDirs
  MonotonicTime
  PathExists
  ProcessId
  ProcessOutcome
  ReadText
  RunProcess
  Sleep
  SpawnDetached
  TempRoot
  TouchFile
  UnixConnectProbe
  UnixLineRequest
  WhichExecutable
  WriteText])
(import driver_io_contract_handlers [ContractRoot LIVE-SOCKET SET-NAME UNSET-NAME])

(val MISSING-COMMAND "/nonexistent/doeff-driver-command")


(defk under-root [#* parts]
  {:pre [(: parts tuple)] :post [(: % str)] :tags {:context "driver-io-test" :role "program"}}
  "契約の根の下の path。"
  (<- root str (ContractRoot))
  (os.path.join root #* parts))


(defk raised-by [request]
  {:pre [(: request EffectBase)] :post [(: % (| tuple None))] :tags {:context "driver-io-test" :role "program"}}
  "要求 request が上げた OSError の型と文の組(上げなければ None)。"
  (var raised None)
  (try
    (<- request)
    (except [error OSError]
      (:= raised #((type error) (str error)))))
  raised)


(defk refused [detail]
  {:pre [(: detail str)] :post [(: % ProcessOutcome)] :tags {:context "driver-io-test" :role "judgment"}}
  "起こせない子の答えの期待(契約の側で独りで書く — 答え手の関数を借りない)。"
  (ProcessOutcome :exit-code 127 :stdout "" :stderr "" :started False :start-error detail))


(deftest test-text-is-read-back-as-written
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- path str (under-root "note.txt"))
  (<- before (ReadText :path path))
  (<- (WriteText :path path :text "一行目\n二行目\n"))
  (<- written (ReadText :path path))
  (<- (WriteText :path path :text "上書き"))
  (<- overwritten (ReadText :path path))
  (assert (is before None) (.format "書く前の答え {!r}" before))
  (assert (= written "一行目\n二行目\n") (.format "書いた後の答え {!r}" written))
  (assert (= overwritten "上書き") (.format "上書きした後の答え {!r}" overwritten)))


(deftest test-appended-text-is-joined-in-order
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- path str (under-root "log.jsonl"))
  (<- (AppendText :path path :text "{\"n\":1}\n"))
  (<- (AppendText :path path :text "{\"n\":2}\n"))
  (<- logged (ReadText :path path))
  (assert (= logged "{\"n\":1}\n{\"n\":2}\n") (.format "足した後の答え {!r}" logged)))


(deftest test-writing-under-a-missing-directory-raises
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- source str (under-root "source.txt"))
  (<- (WriteText :path source :text "中身"))
  (<- target str (under-root "none" "file.txt"))
  (<- write-error (raised-by (WriteText :path target :text "x")))
  (<- append-error (raised-by (AppendText :path target :text "x")))
  (<- touch-error (raised-by (TouchFile :path target)))
  (<- copy-error (raised-by (CopyFile :source source :target target)))
  (<- parent-exists (PathExists :path (os.path.dirname target)))
  (val absent #(FileNotFoundError (.format "[Errno 2] No such file or directory: {!r}" target)))
  (assert (= #(write-error append-error touch-error copy-error) #(absent absent absent absent))
          (.format "親 dir の無い所への書き(Write・Append・Touch・Copy)で上がった例外 {}"
                   #(write-error append-error touch-error copy-error)))
  (assert (not parent-exists) "書きが断られた後に親 dir が在る"))


(deftest test-directories-are-made-with-their-parents
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- nested str (under-root "a" "b"))
  (<- (MakeDirs :path nested))
  (<- (MakeDirs :path nested))
  (<- (WriteText :path (os.path.join nested "f.txt") :text "x"))
  (<- parent-seen (PathExists :path (os.path.dirname nested)))
  (<- nested-seen (PathExists :path nested))
  (<- file-seen (PathExists :path (os.path.join nested "f.txt")))
  (<- missing-seen (PathExists :path (os.path.join nested "none")))
  (assert (= #(parent-seen nested-seen file-seen missing-seen) #(True True True False))
          (.format "PathExists の答え(親・作った dir・file・不在){}" #(parent-seen nested-seen file-seen missing-seen))))


(deftest test-touch-keeps-existing-content
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- fresh str (under-root "fresh"))
  (<- kept str (under-root "kept"))
  (<- (WriteText :path kept :text "残る"))
  (<- (TouchFile :path fresh))
  (<- (TouchFile :path kept))
  (<- fresh-text (ReadText :path fresh))
  (<- kept-text (ReadText :path kept))
  (assert (= #(fresh-text kept-text) #("" "残る")) (.format "TouchFile の後の中身(新しい file・在った file){}" #(fresh-text kept-text))))


(deftest test-copy-answers-whether-the-source-was-there
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- source str (under-root "source.txt"))
  (<- target str (under-root "target.txt"))
  (<- absent-target str (under-root "absent-target.txt"))
  (<- (WriteText :path source :text "複製"))
  (<- copied (CopyFile :source source :target target))
  (<- missing (CopyFile :source (os.path.join (os.path.dirname source) "none.txt") :target absent-target))
  (<- target-text (ReadText :path target))
  (<- absent-seen (PathExists :path absent-target))
  (assert (= #(copied missing) #(True False)) (.format "CopyFile の答え(在る source・無い source){}" #(copied missing)))
  (assert (= target-text "複製") (.format "複製した中身 {!r}" target-text))
  (assert (not absent-seen) "無い source の複製で target が作られた"))


(deftest test-listing-shows-direct-entries-in-order
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- root str (ContractRoot))
  (val base (os.path.join root "listed"))
  (<- (MakeDirs :path (os.path.join base "sub")))
  (<- (WriteText :path (os.path.join base "b.snapshot.json") :text "{}"))
  (<- (WriteText :path (os.path.join base "a.snapshot.json") :text "{}"))
  (<- (WriteText :path (os.path.join base "other.txt") :text ""))
  (<- (WriteText :path (os.path.join base "sub" "c.snapshot.json") :text "{}"))
  (<- everything (ListDir :path base))
  (<- snapshots (ListDir :path base :pattern "*.snapshot.json"))
  (<- missing (ListDir :path (os.path.join root "none")))
  (assert (= everything (tuple (gfor name ["a.snapshot.json" "b.snapshot.json" "other.txt" "sub"] (os.path.join base name))))
          (.format "ListDir の答え {}" everything))
  (assert (= snapshots (tuple (gfor name ["a.snapshot.json" "b.snapshot.json"] (os.path.join base name))))
          (.format "pattern で絞った答え {}" snapshots))
  (assert (= missing #()) (.format "不在の dir の答え {}" missing)))


(deftest test-only-files-written-with-an-execute-bit-are-executable
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- runnable str (under-root "runnable"))
  (<- plain str (under-root "plain"))
  (<- (WriteText :path runnable :text "#!/bin/sh\n" :mode 0o755))
  (<- (WriteText :path plain :text "#!/bin/sh\n" :mode 0o644))
  (<- runnable-seen (ExecutableAt :path runnable))
  (<- plain-seen (ExecutableAt :path plain))
  (<- missing-seen (ExecutableAt :path MISSING-COMMAND))
  (assert (= #(runnable-seen plain-seen missing-seen) #(True False False))
          (.format "ExecutableAt の答え(0o755・0o644・不在){}" #(runnable-seen plain-seen missing-seen))))


(deftest test-a-directory-or-a-missing-path-is-not-executable
  {:interpreters ["driver-io" "fake-driver-io"]}
  ;; dir は実行の bit があっても起こせる file ではない。在る dir の下の無い path も False。
  (<- folder str (under-root "folder"))
  (<- missing str (under-root "none"))
  (<- (MakeDirs :path folder))
  (<- folder-seen (ExecutableAt :path folder))
  (<- missing-seen (ExecutableAt :path missing))
  (assert (= #(folder-seen missing-seen) #(False False))
          (.format "ExecutableAt の答え(dir・無い path){}" #(folder-seen missing-seen))))


(deftest test-environment-values-are-read-by-name
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- present (EnvValue :name SET-NAME))
  (<- absent (EnvValue :name UNSET-NAME))
  (<- no-executable (WhichExecutable :name "doeff-driver-io-no-such-executable"))
  (assert (= #(present absent) #("在る値" None)) (.format "EnvValue の答え(在る名・無い名){}" #(present absent)))
  (assert (is no-executable None) (.format "無い実行ファイルの答え {!r}" no-executable)))


(deftest test-the-own-places-are-existing-directories
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- home (HomePath))
  (<- temp (TempRoot))
  (<- pid (ProcessId))
  (<- home-seen (PathExists :path home))
  (<- temp-seen (PathExists :path temp))
  (assert (and (os.path.isabs home) home-seen) (.format "HomePath {!r} が在る dir の絶対 path でない" home))
  (assert (and (os.path.isabs temp) temp-seen) (.format "TempRoot {!r} が在る dir の絶対 path でない" temp))
  (assert (and (isinstance pid int) (> pid 0)) (.format "ProcessId の答え {!r}" pid)))


(deftest test-the-monotonic-clock-advances-by-the-sleep
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- before (MonotonicTime))
  (<- again (MonotonicTime))
  (<- (Sleep :seconds 0.02))
  (<- after (MonotonicTime))
  (assert (<= before again) (.format "単調時計が戻った {} → {}" before again))
  (assert (>= (- after again) 0.02) (.format "Sleep 0.02 の間に進んだ秒 {}" (- after again))))


(deftest test-the-connect-probe-tells-a-listener-from-absence
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- live str (under-root LIVE-SOCKET))
  (<- absent str (under-root "absent.sock"))
  (<- stale str (under-root "stale.sock"))
  (<- (WriteText :path stale :text ""))
  (<- live-present (PathExists :path live))
  (<- live-seen (UnixConnectProbe :socket-path live :timeout 1.0))
  (<- absent-seen (UnixConnectProbe :socket-path absent :timeout 1.0))
  (<- stale-seen (UnixConnectProbe :socket-path stale :timeout 1.0))
  (assert (= #(live-seen absent-seen stale-seen) #("accepting" "refused" "refused"))
          (.format "UnixConnectProbe の答え(聞く socket・不在・socket でない file){}" #(live-seen absent-seen stale-seen)))
  (assert live-present (.format "聞く socket の path {} が在ると見えない" live)))


(deftest test-a-line-request-is-answered-or-refused
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- live str (under-root LIVE-SOCKET))
  (<- absent str (under-root "absent.sock"))
  (<- stale str (under-root "stale.sock"))
  (<- (WriteText :path stale :text ""))
  (<- answered (UnixLineRequest :socket-path live :payload "ping\n" :timeout 5.0))
  (<- absent-error (raised-by (UnixLineRequest :socket-path absent :payload "ping\n" :timeout 1.0)))
  (<- stale-error (raised-by (UnixLineRequest :socket-path stale :payload "ping\n" :timeout 1.0)))
  (assert (= answered "got:ping\n") (.format "聞く socket の答え {!r}" answered))
  (assert (= #(absent-error stale-error) #(#(FileNotFoundError "[Errno 2] No such file or directory")
                                           #(ConnectionRefusedError "[Errno 111] Connection refused")))
          (.format "UnixLineRequest が上げた例外(不在・socket でない file){}" #(absent-error stale-error))))


(deftest test-a-missing-command-cannot-start
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- outcome ProcessOutcome (RunProcess :argv #(MISSING-COMMAND)))
  (<- expected ProcessOutcome (refused (.format "[Errno 2] No such file or directory: {!r}" MISSING-COMMAND)))
  (assert (= outcome expected) (.format "無い命令の答え {}" outcome)))


(deftest test-a-detached-child-gets-its-log-prepared
  {:interpreters ["driver-io" "fake-driver-io"]}
  (<- log-path str (under-root "logs" "daemon.log"))
  (<- pid (SpawnDetached :argv #("/bin/sh" "-c" "exit 0") :log-path log-path))
  (<- log-seen (PathExists :path log-path))
  (assert (and (isinstance pid int) (> pid 0)) (.format "SpawnDetached の答え {!r}" pid))
  (assert log-seen (.format "log-path {} が用意されていない" log-path)))
