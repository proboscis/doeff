;;; 1 回きりの print の本番の handler — ClaudePrintOnce に、CLI の print mode の子 process を 1 本起こして答える。
;;;
;;; 宣言 1 つの手順:
;;;   1. 資格の置き場: 宣言が借りた資格を持てば、家の設定 dir の下にその process だけの設定 dir を作って .credentials.json に置く
;;;      (credential_home.hy — 会話の process と同じ部品)。持たなければ家の設定 dir のまま。
;;;   2. 作業 dir: 使い捨ての空の dir(呼び手の作業 dir を CLI に見せない — 道具なしでも CLI は作業 dir を読む)。
;;;   3. 起こす: argv.hy の print-once-argv・print-once-env。本文は標準入力。process は自分の組(新しい session)で起こし、待ちの上限
;;;      (宣言の timeout-seconds)を越えたら組ごと SIGKILL して PrintTimedOut。
;;;   4. 片づけ: 降りたら(止めた時も、起こせなかった時も)作業 dir と資格の置き場を消す。
;;;   5. 読む: stdout(stream-json)の JSON の object の行を lines.hy の分類で読み、答えの和にする(print-outcome — 純関数。答えは最後の
;;;      result の行・口座の利用枠の尽きは前の行の拒まれた限度の行と限度の答え)。
;;; 1〜4 は塞ぐ I/O なので PRINT-THREADS の thread で回す(offloaded — 撃った task だけが待ち、scheduler の他の task は回り続ける)。
;;; 待ちが取り消されたら、走っている process の組を止める。外側に doeff の scheduler が要る。
(require doeff-hy.macros [defhandler defk deff <- val])
(require doeff-hy.record [defrecord])
(import contextlib)
(import functools)
(import dataclasses [dataclass field])
(import os)
(import shutil)
(import signal)
(import subprocess)
(import tempfile)
(import threading)
(import time)
(import doeff_core_effects.offloaded_call [ThreadPerCall offloaded run-detached keep-nothing])
(import doeff_claude_code.effects [ClaudePrintOnce PrintAnswered PrintFailed PrintLimited PrintTimedOut])
(import doeff_claude_code.lines [AccountLimitHit TurnResult parse-record classify-record stderr-tail-within])
(import doeff_claude_code.dialogue [limit-hit-after])
(import doeff_claude_code.argv [print-once-argv print-once-env])
(import doeff_claude_code.credential_home [open-credential-home close-credential-home])

;; 1 回きりの print の process を待つ thread(呼び 1 つに 1 本 — 同時の数は呼び手の判定の数が決める)。
(val PRINT-THREADS (ThreadPerCall))
;; PrintFailed の detail に写す CLI の result の文の字数の上限(1 行の訳 — 長い誤りの文を記録へ丸ごと運ばない)。
(val DETAIL-CHARS 400)
(val WORK-DIR-PREFIX "claude-print-")


(defrecord RanPrint
  "thread で回した 1 回きりの print の生の結末(handler の内側の値 — 答えの和へは print-outcome が読む)。exit-code = 終了 code(起こせ
   なかった・止めた時は None)/ stdout・stderr = 出力の全部 / elapsed-ms = 起こしてから降りるまで / timed-out = 待ちの上限で止めたか /
   launch-error = 起こせなかった訳(起こせたら None)。"
  (#^ (| int None) exit-code)
  (#^ str stdout)
  (#^ str stderr)
  (#^ int elapsed-ms)
  (#^ bool timed-out)
  (#^ (| str None) launch-error))


(defclass PrintProcess []
  "走っている 1 回きりの print の process の持ち手 — 待ちの上限と、待ちの取り消し(offloaded の interrupt — 別の thread)の両方から
   同じ process の組を止めるため。止めた後に起こそうとしたら起こさない。資源の係なので値の型ではない。"

  (defn __init__ [self]
    (setv #^ threading.Lock self.lock (threading.Lock))
    (setv #^ (| subprocess.Popen None) self.process None)
    (setv #^ bool self.stopped False))

  (defn #^ subprocess.Popen start [self #^ list argv #^ str cwd #^ dict env]
    "process を自分の組で起こす(止めた後なら OSError — 起こせなかった時と同じ道)。"
    (with [self.lock]
      (when self.stopped
        (raise (OSError "起こす前に止められた")))
      (setv self.process (subprocess.Popen argv :cwd cwd :env env :stdin subprocess.PIPE :stdout subprocess.PIPE
                                           :stderr subprocess.PIPE :text True :encoding "utf-8" :errors "replace"
                                           :start-new-session True))
      self.process))

  (defn #^ None stop [self]
    "process の組を SIGKILL で止める(CLI が起こした子も一緒に — 冪等・もう降りていれば何もしない)。"
    (with [self.lock]
      (setv self.stopped True)
      (when (and (is-not self.process None) (is (.poll self.process) None))
        (with [(contextlib.suppress ProcessLookupError PermissionError)]
          (os.killpg self.process.pid signal.SIGKILL))))))


(deff elapsed-ms-since [#^ float started]  ; defk にできない: thread の中の塞ぐ手順(run-print-once)が値として読む
  {:pre [(: started float)] :post [(: % int)]}
  "time.monotonic の起点から今までを、RanPrint の elapsed-ms のミリ秒の整数にするため。"
  (int (* (- (time.monotonic) started) 1000)))

(deff run-print-once [#^ tuple command #^ ClaudePrintOnce request #^ PrintProcess holder]  ; defk にできない: Executor の thread で回す入口(塞ぐ I/O と try/finally の後始末)
  {:pre [(: command tuple) (: request ClaudePrintOnce) (: holder PrintProcess)] :post [(: % RanPrint)]}
  "宣言 1 つの CLI を起こして降りるまで待ち、生の結末を返すため(頭の註の 1〜4)。作業 dir と資格の置き場は、どの終わり方でも消す。"
  (setv home (if (is request.credential None)
                 None
                 (run-detached (open-credential-home request.home.config-dir request.credential)))
        work (tempfile.mkdtemp :prefix WORK-DIR-PREFIX)
        started (time.monotonic))
  (try
    (try
      (setv process (.start holder (print-once-argv command request) work
                            (print-once-env request (if (is home None) request.home.config-dir home))))
      (except [error OSError]
        (return (RanPrint :exit-code None :stdout "" :stderr "" :elapsed-ms (elapsed-ms-since started) :timed-out False
                          :launch-error (str error)))))
    (try
      (setv #(stdout stderr) (.communicate process :input request.input :timeout request.timeout-seconds))
      (return (RanPrint :exit-code process.returncode :stdout stdout :stderr stderr :elapsed-ms (elapsed-ms-since started)
                        :timed-out False :launch-error None))
      (except [subprocess.TimeoutExpired]
        (.stop holder)
        (setv #(stdout stderr) (.communicate process))
        (return (RanPrint :exit-code None :stdout stdout :stderr stderr :elapsed-ms (elapsed-ms-since started)
                          :timed-out True :launch-error None))))
    (finally
      (shutil.rmtree work :ignore-errors True)
      (run-detached (close-credential-home home)))))


(defk failed-detail [#^ TurnResult result]
  {:pre [(: result TurnResult)] :post [(: % str)] :tags {:context "claude-code" :role "foundation"}}
  "誤りを名乗った result の行を、PrintFailed の detail の 1 行にするため(CLI の文 — 無ければ subtype と終わり方の名)。"
  (if result.result-text
      (cut result.result-text 0 DETAIL-CHARS)
      (.format "CLI が誤りを名乗った(subtype {}・terminal_reason {})" result.subtype result.terminal-reason)))

(defk limit-in-stream [#^ str stdout]
  {:pre [(: stdout str)] :post [(: % (| AccountLimitHit None))] :tags {:context "claude-code" :role "judgment"}}
  "stream-json の stdout の行から、口座の利用枠が尽きた事実を読むため(純関数 — card ki-eb4f8e1db64d): 規則は会話の手番と同じ 1 点
   dialogue.hy の limit-hit-after(拒まれた rate_limit_event の行 = 枠の種類と戻る刻・本体の error rate_limit の答えの行 = 限度の文)。
   名乗る行が無ければ None。"
  (val kinds (gfor line (.splitlines stdout) :setv record (parse-record line) :if (is-not record None) (classify-record record)))
  (functools.reduce limit-hit-after kinds None))

(defk print-outcome [exit-code #^ str stdout #^ str stderr #^ int elapsed-ms #^ bool timed-out #^ float seconds]
  {:pre [(: exit-code (| int None)) (: stdout str) (: stderr str) (: elapsed-ms int) (: timed-out bool) (: seconds float)]
   :post [(: % (| PrintAnswered PrintFailed PrintLimited PrintTimedOut))] :tags {:context "claude-code" :role "judgment"}}
  "1 回きりの print の CLI の生の結末を答えの和にするため(純関数): 止めた = PrintTimedOut・答えずに降りた print の行が口座の利用枠の
   尽きを名乗った = PrintLimited(limit-in-stream)・result の行が無い・誤りを名乗った・終了 code が 0 でない・構造化の答えが無い =
   PrintFailed・ほか = PrintAnswered(答えの値は CLI の値のまま — 拒まれた限度の行が在っても答えた print は答え)。result の行は stdout
   (stream-json)の空でない最後の行(lines.hy の分類で読む — JSON の境界はそこ 1 か所)。"
  (<- tail (stderr-tail-within stderr))
  (when timed-out
    (return (PrintTimedOut :seconds seconds :stderr-tail tail)))
  (val last-line (next (gfor line (reversed (.splitlines stdout)) :if (.strip line) line) ""))
  (val record (parse-record last-line))
  (val result (if (is record None) None (classify-record record)))
  (val answered (and (isinstance result TurnResult) (not result.is-error) (= exit-code 0)
                     (is-not result.structured-output None)))
  (when (not answered)
    (<- limit (| AccountLimitHit None) (limit-in-stream stdout))
    (when (is-not limit None)
      (return (PrintLimited :limit limit :exit-code exit-code :stderr-tail tail))))
  (when (not (isinstance result TurnResult))
    (return (PrintFailed :exit-code exit-code :api-status None :stderr-tail tail
                         :detail (.format "CLI が答えの行(type result の JSON)を出さずに降りた(終了 code {})" exit-code))))
  (when (or result.is-error (!= exit-code 0))
    (<- detail (failed-detail result))
    (return (PrintFailed :exit-code exit-code :api-status result.api-error-status :detail detail :stderr-tail tail)))
  (when (is result.structured-output None)
    (return (PrintFailed :exit-code exit-code :api-status None :stderr-tail tail
                         :detail (.format "構造化の答え(structured_output)が無い: {}" (cut result.result-text 0 DETAIL-CHARS)))))
  (PrintAnswered :structured-output result.structured-output :usage result.usage :cost-usd result.cost-usd
                 :models (tuple (gfor window result.model-windows window.model)) :elapsed-ms elapsed-ms
                 :api-ms result.timing.duration-api-ms))

(defk print-once [#^ tuple command #^ ClaudePrintOnce request]
  {:pre [(: command tuple) (bool command) (: request ClaudePrintOnce)] :post [(: % (| PrintAnswered PrintFailed PrintLimited PrintTimedOut))]
   :tags {:context "claude-code" :role "process"}}
  "宣言 1 つに CLI を 1 回走らせて答えるため: 塞ぐ手順(run-print-once)を PRINT-THREADS の thread で回して待ち(取り消されたら process の
   組を止める)、生の結末を答えの和にする。起こせなかった CLI は PrintFailed(終了 code なし)。"
  (val holder (PrintProcess))
  (<- ran (offloaded PRINT-THREADS (fn [] (run-print-once command request holder)) keep-nothing (fn [] (.stop holder))))
  (when (is-not ran.launch-error None)
    (return (PrintFailed :exit-code None :api-status None :stderr-tail ""
                         :detail (.format "CLI を起こせなかった: {}" ran.launch-error))))
  (<- outcome (print-outcome ran.exit-code ran.stdout ran.stderr ran.elapsed-ms ran.timed-out request.timeout-seconds))
  outcome)


;; 引数に残す理由: command(起こす実行ファイル)は composition root が決める物で、会話の handler(handler.hy の ClaudeCodeHost の
;; command)と同じく宣言の外から渡す — 検は同じ handler を替え玉の CLI で組む。
(defhandler claude-print-handler [command]
  "本番の 1 回きりの print の handler。command = 実行ファイルと前置きの引数(例 #(\"claude\"))。"
  (ClaudePrintOnce [home model]
    (<- outcome (print-once command effect))
    (resume outcome)))
