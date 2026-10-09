;;; 待ちの子の効果(warm_effects.hy)の本物の答え手 os-warm-process-handler(agora-redesign #3646 の A3)。
;;;
;;;   ForkFromWarm     待ちの子の unix socket(AF_UNIX・SOCK_STREAM)に頼みの JSON 1 行を送り、答えの JSON 1 行を受ける。接続・送り・受けの
;;;                    全部で WARM-ANSWER-SECONDS の期限。socket の在否と mode は確かめない(作る側 = 待ちの子が mode で絞る — 相手の身元を
;;;                    確かめる分岐・名簿・合言葉は持たない)。断りの文は warm_effects.hy の関数で作り、env の値は含めない。
;;;   PollWarmChild    pid の process の state と start-ticks を機体ごとの読み proc-stat-of で読む(Linux = /proc/<pid>/stat の state〔3 番目の
;;;                    欄〕と starttime〔22 番目の欄〕・macOS = darwin_proc.hy の libproc の proc_bsdinfo)。同じ start-ticks で終わって
;;;                    いない(zombie でない)process が居れば WarmRunning、そうでなければ exit の file を読む(在れば WarmExited・無ければ WarmLost)。
;;;   SignalWarmChild  同じ start-ticks で終わっていない process が居る時だけ、その group(子 A は setsid した group の先頭)へ os.killpg で送る。
;;;   読みを知らない機体(Linux と macOS の外)・/proc の無い Linux では WarmProcUnavailable を上げる — 黙って Lost / Gone にしない。
;;;   待ちの子(doeff-cluster の warm_child.py)も、分けた子の start-ticks と自分の thread の本数を同じ読み(proc-stat-of・own-thread-count)
;;;   で読む — 頼み手と待ちの子が同じ start-ticks を照らす。
;;;
;;; 頼みと答えの形(待ちの子との約束 — 型は下の defwire・待ちの子の側も同じ関数で読み書きする):
;;;   頼み = WarmRequestWire {"entry", "args": [str], "cwd", "env": [{"name", "value"}], "logPath", "exitPath", "graceSeconds"}
;;;   答え = WarmForkedWire {"pid", "startTicks"} か WarmRefusedWire {"detail"}(知らない欄は断る — 2 つの形はこれで見分ける)
;;;   どれも JSON 1 行・utf-8・末尾に改行。warm-request-line(頼み手が送る)・warm-request-of(待ちの子が読む)・warm-answer-line
;;;   (待ちの子が答える)・warm-answer-of(頼み手が読む)。
;;; 本物との契約は tests/test_warm_process_contract.hy。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defwire])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import json)
(import os)
(import platform)
(import socket)
(import dataclasses [dataclass])
(import doeff_hy.wire [Malformed dump parse-json])
(import doeff_core_effects.process_effects [ProcessSignal])
(import doeff_core_effects.process_stat [ProcStat])
(import doeff_core_effects.darwin_proc [darwin-proc-stat darwin-thread-count])
(import doeff_core_effects.warm_effects [ForkFromWarm PollWarmChild SignalWarmChild WarmForked WarmRefused WarmRunning WarmExited
                                         WarmLost WarmSignaled WarmGone WARM-ANSWER-SECONDS warm-socket-missing warm-socket-refused
                                         warm-answer-late warm-answer-unreadable warm-lost warm-gone warm-exit-answer signal-number-of])

;; process の様子を読む置き場の根(Linux の procfs)。
(val PROC-ROOT "/proc")
;; 答えの 1 行の長さの上限(byte)— 約束の形を外れた相手が改行を送らずに流し続けても、受けを止めるため。
(val ANSWER-LIMIT 65536)


(defclass WarmProcUnavailable [RuntimeError]
  "この機体の process の様子の読みを知らない(Linux と macOS の外・/proc の無い Linux)ので、待ちの子の終わりを読めない・signal を送る相手を
   確かめられない。")


(defwire WarmEnvWire
  "頼みの env の 1 項(子の環境変数 1 つ)。"
  {:tags {:context "process" :role "type" :reads "json"} :names :camel :unknown :reject}
  (#^ str name)
  (#^ str value))


(defwire WarmRequestWire
  "待ちの子が受ける頼み 1 つ(ForkFromWarm の欄のうち socket-path を除いた全部 — 頭の註)。"
  {:tags {:context "process" :role "type" :reads "json"} :names :camel :unknown :reject}
  (#^ str entry)
  (#^ (get tuple #(str ...)) args)
  (#^ str cwd)
  (#^ (get tuple #(WarmEnvWire ...)) env)
  (#^ str log-path)
  (#^ str exit-path)
  (#^ float grace-seconds))


(defwire WarmForkedWire
  "待ちの子の答え: 仕事を受け、子 A を立てた。"
  {:tags {:context "process" :role "type" :reads "json"} :names :camel :unknown :reject}
  (#^ int pid)
  (#^ int start-ticks))


(defwire WarmRefusedWire
  "待ちの子の答え: 仕事を断った(detail = 理由 — env の値は含めない)。"
  {:tags {:context "process" :role "type" :reads "json"} :names :camel :unknown :reject}
  (#^ str detail))


(defk wire-line [wire]
  {:pre [(: wire (| WarmRequestWire WarmForkedWire WarmRefusedWire))] :post [(: % bytes)] :tags {:context "process" :role "foundation"}}
  "型のある値を、待ちの子との約束の 1 行(JSON・utf-8・末尾に改行)にして送れる形にするため。"
  (<- raw (dump wire))
  (.encode (+ (json.dumps raw :ensure-ascii False) "\n") "utf-8"))


(defk warm-request-line [request]
  {:pre [(: request ForkFromWarm)] :post [(: % bytes)] :tags {:context "process" :role "foundation"}}
  "頼み手が待ちの子へ送る 1 行を作るため(頭の註の頼みの形)。"
  (<- line bytes (wire-line (WarmRequestWire :entry request.entry :args request.args :cwd request.cwd
                                             :env (tuple (gfor e request.env (WarmEnvWire :name e.name :value e.value)))
                                             :log-path request.log-path :exit-path request.exit-path
                                             :grace-seconds request.grace-seconds)))
  line)


(defk warm-request-of [line]
  {:pre [(: line bytes)] :post [(: % (| WarmRequestWire Malformed))] :tags {:context "process" :role "foundation"}}
  "待ちの子が受けた 1 行を頼みに読むため。約束の形でなければ Malformed(待ちの子は断りを答える)。"
  (<- request (| WarmRequestWire Malformed) (parse-json WarmRequestWire line))
  request)


(defk warm-answer-line [answer]
  {:pre [(: answer (| WarmForked WarmRefused))] :post [(: % bytes)] :tags {:context "process" :role "foundation"}}
  "待ちの子が頼み手へ返す 1 行を作るため(頭の註の答えの形)。"
  (val wire (match answer
              (WarmForked :pid pid :start-ticks ticks) (WarmForkedWire :pid pid :start-ticks ticks)
              (WarmRefused :detail detail) (WarmRefusedWire :detail detail)))
  (<- line bytes (wire-line wire))
  line)


(defk warm-answer-of [line socket-path]
  {:pre [(: line bytes) (: socket-path str)] :post [(: % (| WarmForked WarmRefused))] :tags {:context "process" :role "foundation"}}
  "頼み手が受けた 1 行を答えに読むため。2 つの形のどちらでもなければ、読めない断り(warm-answer-unreadable)。"
  (<- forked (| WarmForkedWire Malformed) (parse-json WarmForkedWire line))
  (<- refused (| WarmRefusedWire Malformed) (parse-json WarmRefusedWire line))
  (<- unreadable str (warm-answer-unreadable socket-path))
  (match #(forked refused)
    #((WarmForkedWire :pid pid :start-ticks ticks) _) (WarmForked :pid pid :start-ticks ticks)
    #(_ (WarmRefusedWire :detail detail)) (WarmRefused :detail detail)
    _ (WarmRefused :detail unreadable)))


(defk received-line [connection]
  {:pre [(: connection socket.socket)] :post [(: % (| bytes None))] :tags {:context "process" :role "foundation"}}
  "接続から改行までの 1 行を受けるため(期限は接続の timeout)。改行の前に閉じられた・上限を超えた = None。"
  (var received b"")
  (var closed False)
  (while (not (or closed (in b"\n" received) (> (len received) ANSWER-LIMIT)))
    (val chunk (.recv connection 4096))
    (if chunk
        (:= received (+ received chunk))
        (:= closed True)))
  (if (in b"\n" received)
      (get (.split received b"\n" 1) 0)
      None))


(defk os-fork-from-warm [request]
  {:pre [(: request ForkFromWarm)] :post [(: % (| WarmForked WarmRefused))] :tags {:context "process" :role "foundation"}}
  "待ちの子に仕事を 1 つ頼むため(頭の註)。socket が無い・接続を断られた・期限が切れた・答えが読めない = 断り(理由の文に env の値は無い)。"
  (val path request.socket-path)
  (<- missing str (warm-socket-missing path))
  (<- refused str (warm-socket-refused path))
  (<- late str (warm-answer-late path))
  (<- unreadable str (warm-answer-unreadable path))
  (<- line bytes (warm-request-line request))
  (val received (with [connection (socket.socket socket.AF-UNIX socket.SOCK-STREAM)]
                  (.settimeout connection WARM-ANSWER-SECONDS)
                  (try
                    (do (.connect connection path)
                        (.sendall connection line)
                        (<- answered (| bytes None) (received-line connection))
                        (if (is answered None) unreadable answered))
                    (except [FileNotFoundError] missing)
                    (except [ConnectionRefusedError] refused)
                    (except [TimeoutError] late))))
  (if (isinstance received str)
      (WarmRefused :detail received)
      (do (<- answer (| WarmForked WarmRefused) (warm-answer-of received path))
          answer)))


(defk proc-stat-of [pid]
  {:pre [(: pid int)] :post [(: % (| ProcStat None))] :tags {:context "process" :role "foundation"}}
  "pid の process の state と start-ticks を、この機体の読みで読むため(頭の註)。居ない = None。読みを知らない機体は WarmProcUnavailable。"
  (val system (platform.system))
  (cond
    (= system "Linux") (do (<- seen (| ProcStat None) (linux-proc-stat pid)) seen)
    (= system "Darwin") (do (<- seen (| ProcStat None) (darwin-proc-stat pid)) seen)
    True (raise (WarmProcUnavailable (.format "process の様子の読みを知らない機体: {}" system)))))


(defk own-thread-count []
  {:pre [] :post [(: % int)] :tags {:context "process" :role "foundation"}}
  "この process の OS の thread の本数を、この機体の読みで読むため(待ちの子が fork の前に 1 本である事を確かめる — 頭の註)。"
  (val system (platform.system))
  (cond
    (= system "Linux") (len (os.listdir (os.path.join PROC-ROOT "self" "task")))
    (= system "Darwin") (do (<- threads int (darwin-thread-count)) threads)
    True (raise (WarmProcUnavailable (.format "thread の本数の読みを知らない機体: {}" system)))))


(defk linux-proc-stat [pid]
  {:pre [(: pid int)] :post [(: % (| ProcStat None))] :tags {:context "process" :role "foundation"}}
  "Linux で pid の process の state と start-ticks を /proc から読むため。居ない = None。/proc が無ければ WarmProcUnavailable。
   comm(2 番目の欄)は空白や括弧を含みうるので、最後の ')' の後ろを空白で割る(3 番目の欄が先頭・22 番目の欄はその 20 番目)。"
  (when (not (os.path.isdir PROC-ROOT))
    (raise (WarmProcUnavailable (.format "{} が無い機体では待ちの子の終わりを読めない" PROC-ROOT))))
  (when (<= pid 0)
    (return None))
  (val text (try (with [f (open (os.path.join PROC-ROOT (str pid) "stat") :encoding "utf-8" :errors "surrogateescape")] (.read f))
                 (except [[FileNotFoundError ProcessLookupError]] None)))
  (if (is text None)
      None
      (do (val fields (.split (get (.rsplit text ")" 1) 1)))
          (ProcStat :state (get fields 0) :start-ticks (int (get fields 19))))))


(defk same-child-running [pid start-ticks]
  {:pre [(: pid int) (: start-ticks int)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "pid が、頼んだ時と同じ start-ticks の process のままで、まだ終わっていない(zombie でない)かを確かめるため。"
  (<- seen (| ProcStat None) (proc-stat-of pid))
  (and (is-not seen None) (= seen.start-ticks start-ticks) (!= seen.state "Z")))


(defk os-poll-warm-child [pid start-ticks exit-path]
  {:pre [(: pid int) (: start-ticks int) (: exit-path str)] :post [(: % (| WarmRunning WarmExited WarmLost))]
   :tags {:context "process" :role "foundation"}}
  "頼んだ子の様子を 1 度だけ読むため(頭の註): 走っている = WarmRunning・そうでなければ exit の file の中身で答える。"
  (<- running bool (same-child-running pid start-ticks))
  (when running
    (return (WarmRunning :pid pid)))
  (val text (try (with [f (open exit-path :encoding "utf-8" :errors "surrogateescape")] (.read f))
                 (except [FileNotFoundError] None)))
  (if (is text None)
      (do (<- lost WarmLost (warm-lost pid exit-path))
          lost)
      (do (<- answer (| WarmExited WarmLost) (warm-exit-answer pid exit-path text))
          answer)))


(defk os-signal-warm-child [pid start-ticks sent]
  {:pre [(: pid int) (: start-ticks int) (: sent ProcessSignal)] :post [(: % (| WarmSignaled WarmGone))]
   :tags {:context "process" :role "foundation"}}
  "頼んだ子の group へ signal を送るため(頭の註)。同じ start-ticks で走っている時だけ送り、他人の process には送らない。"
  (<- running bool (same-child-running pid start-ticks))
  (<- gone WarmGone (warm-gone pid))
  (<- number int (signal-number-of sent))
  (if (not running)
      gone
      (try
        (do (os.killpg pid number) (WarmSignaled :pid pid))
        (except [ProcessLookupError] gone))))


(defhandler os-warm-process-handler
  ;; 本物の待ちの子と、機体ごとの process の様子の読み(頭の註)。
  (ForkFromWarm [socket-path entry args cwd env log-path exit-path grace-seconds]
    (<- answer (os-fork-from-warm (ForkFromWarm :socket-path socket-path :entry entry :args args :cwd cwd :env env
                                                :log-path log-path :exit-path exit-path :grace-seconds grace-seconds)))
    (resume answer))
  (PollWarmChild [pid start-ticks exit-path]
    (<- answer (os-poll-warm-child pid start-ticks exit-path))
    (resume answer))
  (SignalWarmChild [pid start-ticks signal]
    (<- answer (os-signal-warm-child pid start-ticks signal))
    (resume answer)))
