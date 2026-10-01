;;; 汎用の子 process の effect(agora-redesign #802 便 1・消費者 = #796 日次の全体検証・#795 webapp の組み立て・doeff-agents の driver 層)。
;;; 業務の語を持たない土台の語彙で、HttpRequest(http_effects.hy)と同じ段。答え手は仕組みごとに差し替える:
;;;   subprocess-handler            本物の子 process と os.environ(os_process.hy)。doeff-agents の driver-io-handler も同じ実装を呼ぶ
;;;   offloaded-subprocess-handler  本物と同じ実装を、呼び 1 つに thread 1 本で回す(os_process.hy・agora-redesign #2184)— 子を待つ間も
;;;                                 scheduler の他の task が回る(並べた RunProcess を Spawn / Gather で同時に走らせる・子の間も拍を回す)。
;;;                                 外側に scheduled が要る
;;;   scripted-process-handler      I/O なし — 命令の名ごとの台本と、決めた環境変数・job ごとの作業 dir(scripted_process.hy)
;;;
;;; RunProcess・ExecutableAt・ProcessOutcome は doeff-agents の io_effects から移した(定義は 1 つ — io_effects は同じ型を re-export する)。移す時に
;;; 足した欄は全部既定値つきで、今の使い手の振る舞いは変わらない:
;;;   RunProcess の env(None = 呼び手の環境を継ぐ・EnvEntry の tuple = env-mode の通り)・output-path(None = 追記しない・path = 子の出力を
;;;   その file の末尾へ足す — 答えの stdout / stderr も持つ)。
;;;   RunProcess の env-mode(agora-redesign #822)= env の tuple の扱いの閉じた型 EnvMode:
;;;     REPLACE(既定)  tuple が子の環境変数の全部(前からの振る舞い)
;;;     EXTEND         呼び手の環境を継いだ上で tuple を足す(同じ名は tuple が勝つ)
;;;   env が None の時は env-mode を読まない(呼び手の環境を全部継ぐ)。
;;;   RunProcess の env-drop(agora-redesign #831)= EXTEND で継ぐ呼び手の環境から外す名の型(fnmatch — `UV_*` のように)の tuple。既定 #()
;;;   = 外さない。REPLACE と env None の時は読まない(REPLACE は継がないので外す物が無い)。子に呼び手の venv や道具の設定を持ち込ませない時に使う。
;;;   ProcessOutcome の started(False = 起こせなかった — OSError を値で)・start-error(その理由)。exit-code は子の returncode を丸めずに
;;;   持つ(負の値 = signal・137 など)。時間切れは timed-out True(exit-code 124)、起こせない時は exit-code 127。
;;;   RunProcess の process-group・stop-grace・stream-output(agora-redesign #2184 — 着地の列の窓の門の命令の走らせ方):
;;;     process-group  True = 子を新しい session(自分の process group)で走らせる。時間切れでは group へ SIGTERM → stop-grace 秒待つ →
;;;                    SIGKILL(子が起こした孫も止まる)。時間内に終わった後も、group に残った子(背景に回った孫)へ SIGTERM を送る。
;;;                    既定 False = 前からの振る舞い(時間切れで子だけを止める)
;;;     stop-grace     process-group の止め方の猶予の秒(既定 10.0)
;;;     stream-output  True = 子の出力を、届いた順に output-path へ書きながら走らせる(時間切れで止めた子の出力も file に残る — 走者の log が
;;;                    指す赤の証拠)。既定 False = 子が終わってから stdout・stderr の順に足す(前からの振る舞い)。output-path が None なら読まない
;;;   待ち方はどれも subprocess の communicate と同じ = 子の終了に加えて出力の EOF(背景の孫が出力を抱えたままなら期限まで待つ)。
;;;   ProcessAlive(agora-redesign #2184)= pid の process が生きているか。0 以下の pid は生きていない(group への signal にしない)。
;;;
;;; 文字列と bytes の約束(agora-redesign #2160): RunProcess の stdin と ProcessOutcome の stdout / stderr は、子の bytes を utf-8 と
;;; surrogateescape で読み書きした文字列(可逆 — Python の os.fsdecode と同じ作法)。有効な utf-8 はふつうの文字列のまま、壊れた bytes は
;;; surrogate の文字(U+DC80〜U+DCFF)で表す。bytes が要る使い手(git の diff を patch-id へ渡す等)は `.encode "utf-8" "surrogateescape"`
;;; で元の bytes に戻し、bytes を渡す時は同じ作法で文字列にして stdin に置く。表に出す時(JSON・log)は呼び手が置き換えて読む。
;;;
;;;   RunProcess        子 process を 1 回走らせて終わりを待つ。答え = ProcessOutcome。
;;;   ExecutableAt      その path に実行できる file が在るか。答え = bool。symlink は辿った先で判じ、dir・無い path・file でない物は実行の bit が
;;;                     あっても False(判断は executable-file-answer の 1 か所 — 本物と I/O なしの答え手が同じ関数を呼ぶ)。
;;;   ReadEnvironment   自分の process の環境変数のうち names の分。答え = 在る分だけの EnvEntry の tuple(names の順)。
;;;   WorkingDirectory  自分の process の作業 dir(絶対 path)。
;;;   ProcessAlive      pid の process が生きているか。答え = bool(本物 = signal 0 を送れるか・送る権限が無いだけの process は生きている)。
;;;
;;; 立てたらすぐ返す子(agora-redesign #2223 — 消費者 = merge-queue の controller の配りの腕。拍は子を待たない):
;;;   StartProcess      子を立てて、終わりを待たずに返す。答え = ProcessStarted(pid)か ProcessNotStarted(理由の文 — 出力の file が開けない・
;;;                     cwd が無い・命令が無い。RunProcess の起こせない形と同じ文で、出力の file を先に確かめる)。子の標準入力は無し、標準出力と
;;;                     標準エラーは stdout-path・stderr-path の file の末尾へ(None = 捨てる)。pipe にしない — 読まずにおくと約 64 KB で子が
;;;                     止まる。env・env-mode・env-drop・cwd・process-group は RunProcess と同じ。
;;;   PollProcess       立てた子を待たずに 1 度だけ問う。答え = ProcessRunning・ProcessExited(終了 code — 答えた時に回収し、その pid を忘れる)・
;;;                     ProcessNotChild(この答え手が立てた子でない pid)。
;;;   StopProcess       立てた子を止めて回収する: process-group なら group へ、そうでなければ子へ SIGTERM → stop-grace 秒待つ → SIGKILL。
;;;                     答え = ProcessExited か ProcessNotChild(他人の process には signal を送らない)。終わっていた子はそのまま回収する。
;;;   本物の答え手は、立てた子の表を process に 1 つ持つ(子は OS の process ごとの資源 — 答え手を積み直しても同じ子を問える)。
;;;
;;; 時間切れと起こせない形の答え(timed-out-outcome・not-started-outcome)と、起こせない理由の文(start-refusal — OSError の文と同じ形)は
;;; ここで 1 度だけ作る。本物(os_process.hy)と I/O なし(scripted_process.hy)の答え手は同じ関数を呼ぶ(同じ形で答える — 契約テスト
;;; tests/test_process_contract.hy)。
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord defenum])
(import os)
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff [EffectBase])
(import doeff_core_effects.file_effects [PathKind])

;; 時間切れの時の exit-code(coreutils の timeout と同じ)と、起こせない時の exit-code(shell と同じ)。
(val TIMED-OUT-CODE 124)
(val NOT-STARTED-CODE 127)


;; RunProcess の env の tuple の扱い(頭の註): REPLACE = 子の環境変数の全部・EXTEND = 呼び手の環境を継いで足す。
(defenum EnvMode REPLACE EXTEND)


(defrecord EnvEntry
  "環境変数 1 つ(名と値)。"
  (#^ str name)
  (#^ str value))


(defclass [(dataclass :frozen True :kw-only True)] ProcessOutcome []
  "子 process 1 回の結果。判断(成否の解釈)は呼び手が持つので raise しない(頭の註)。stdout / stderr は子の bytes の可逆の文字列
   (utf-8 と surrogateescape — 頭の註の文字列と bytes の約束)。"
  #^ int exit-code
  #^ str stdout
  #^ str stderr
  #^ bool timed-out
  (setv timed-out False)
  #^ bool started
  (setv started True)
  #^ str start-error
  (setv start-error ""))


(defclass [(dataclass :frozen True :kw-only True)] RunProcess [EffectBase]
  "子 process を 1 回走らせて終わりを待つ(頭の註)。終了 code は値で返る。"
  #^ tuple argv
  #^ (| str None) stdin
  (setv stdin None)
  #^ (| float None) timeout
  (setv timeout None)
  #^ (| str None) cwd
  (setv cwd None)
  #^ (| tuple None) env
  (setv env None)
  #^ EnvMode env-mode
  (setv env-mode EnvMode.REPLACE)
  #^ (get tuple #(str ...)) env-drop
  (setv env-drop #())
  #^ (| str None) output-path
  (setv output-path None)
  #^ bool process-group
  (setv process-group False)
  #^ float stop-grace
  (setv stop-grace 10.0)
  #^ bool stream-output
  (setv stream-output False))


(defclass [(dataclass :frozen True :kw-only True)] ExecutableAt [EffectBase]
  "その path に実行できる file が在るか。"
  #^ str path)


(defclass [(dataclass :frozen True)] ReadEnvironment [EffectBase]
  "自分の process の環境変数のうち names の分を読む(頭の註)。"
  (#^ (get tuple #(str ...)) names))


(defclass [(dataclass :frozen True)] WorkingDirectory [EffectBase]
  "自分の process の作業 dir(頭の註)。")


(defclass [(dataclass :frozen True)] ProcessAlive [EffectBase]
  "pid の process が生きているか(頭の註)。"
  (#^ int pid))


(defclass [(dataclass :frozen True :kw-only True)] StartProcess [EffectBase]
  "子 process を立てて、終わりを待たずに返す(頭の註)。答え = ProcessStarted か ProcessNotStarted。"
  #^ tuple argv
  #^ (| str None) cwd
  (setv cwd None)
  #^ (| tuple None) env
  (setv env None)
  #^ EnvMode env-mode
  (setv env-mode EnvMode.REPLACE)
  #^ (get tuple #(str ...)) env-drop
  (setv env-drop #())
  #^ (| str None) stdout-path
  (setv stdout-path None)
  #^ (| str None) stderr-path
  (setv stderr-path None)
  #^ bool process-group
  (setv process-group False))


(defclass [(dataclass :frozen True)] PollProcess [EffectBase]
  "StartProcess で立てた子の様子を、待たずに 1 度だけ問う(頭の註)。答え = ProcessRunning か ProcessExited か ProcessNotChild。"
  (#^ int pid))


(defclass [(dataclass :frozen True :kw-only True)] StopProcess [EffectBase]
  "StartProcess で立てた子を止めて回収する(頭の註)。答え = ProcessExited か ProcessNotChild。"
  #^ int pid
  #^ float stop-grace
  (setv stop-grace 10.0))


(defrecord ProcessStarted
  "子を立てた(pid = PollProcess と StopProcess で問う子の印)。"
  (#^ int pid))


(defrecord ProcessNotStarted
  "子を立てられなかった(detail = OSError の文と同じ形の理由 — 出力の file が開けない・cwd が無い・命令が無い)。"
  (#^ str detail))


(defrecord ProcessRunning
  "子はまだ走っている。"
  (#^ int pid))


(defrecord ProcessExited
  "子は終わり、回収した(exit-code = returncode を丸めない — 負の値 = signal)。この後その pid は ProcessNotChild。"
  (#^ int pid)
  (#^ int exit-code))


(defrecord ProcessNotChild
  "この答え手が立てた子でない pid(立てていない・既に終わりを答えて回収した)— 他人の process に signal を送らないため。"
  (#^ int pid))


(defk timed-out-outcome [stdout stderr]
  {:pre [(: stdout str) (: stderr str)] :post [(: % ProcessOutcome)] :tags {:context "process" :role "judgment"}}
  "時間切れの答え(それまでの出力 stdout / stderr を持つ・exit-code 124・timed-out True)を作るため。"
  (ProcessOutcome :exit-code TIMED-OUT-CODE :stdout stdout :stderr stderr :timed-out True :started True :start-error ""))


(defk not-started-outcome [detail]
  {:pre [(: detail str)] :post [(: % ProcessOutcome)] :tags {:context "process" :role "judgment"}}
  "起こせない形の答え(exit-code 127・started False・理由 detail)を作るため。"
  (ProcessOutcome :exit-code NOT-STARTED-CODE :stdout "" :stderr "" :timed-out False :started False :start-error detail))


(defk start-refusal [error-number path]
  {:pre [(: error-number int) (: path str)] :post [(: % str)] :tags {:context "process" :role "judgment"}}
  "起こせない理由の文を、本物の subprocess が上げる OSError の文と同じ形(\"[Errno 2] No such file or directory: '/x'\")で作るため。"
  (str (OSError error-number (os.strerror error-number) path)))


(defk executable-file-answer [kind runnable]
  {:pre [(: kind PathKind) (: runnable bool)] :post [(: % bool)] :tags {:context "process" :role "judgment"}}
  "ExecutableAt の答えを決めるため: 辿った先の種類 kind が file で、かつ実行を許されている(runnable)時だけ True。dir・無い path・その他の
   種類は runnable でも False(dir の実行の bit は「中へ入れる」の意味で、起こせる file ではない)。"
  (and (= kind PathKind.FILE) runnable))
