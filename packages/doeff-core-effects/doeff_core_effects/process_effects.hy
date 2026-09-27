;;; 汎用の子 process の effect(agora-redesign #802 便 1・消費者 = #796 日次の全体検証・#795 webapp の組み立て・doeff-agents の driver 層)。
;;; 業務の語を持たない土台の語彙で、HttpRequest(http_effects.hy)と同じ段。答え手は仕組みごとに差し替える:
;;;   subprocess-handler         本物の子 process と os.environ(os_process.hy)。doeff-agents の driver-io-handler も同じ実装を呼ぶ
;;;   scripted-process-handler   I/O なし — 命令の名ごとの台本と、決めた環境変数・job ごとの作業 dir(scripted_process.hy)
;;;
;;; RunProcess・ExecutableAt・ProcessOutcome は doeff-agents の io_effects から移した(定義は 1 つ — io_effects は同じ型を re-export する)。移す時に
;;; 足した欄は全部既定値つきで、今の使い手の振る舞いは変わらない:
;;;   RunProcess の env(None = 呼び手の環境を継ぐ・EnvEntry の tuple = env-mode の通り)・output-path(None = 追記しない・path = 子の出力を
;;;   その file の末尾へ足す — 答えの stdout / stderr も持つ)。
;;;   RunProcess の env-mode(agora-redesign #822)= env の tuple の扱いの閉じた型 EnvMode:
;;;     REPLACE(既定)  tuple が子の環境変数の全部(前からの振る舞い)
;;;     EXTEND         呼び手の環境を継いだ上で tuple を足す(同じ名は tuple が勝つ)
;;;   env が None の時は env-mode を読まない(呼び手の環境を全部継ぐ)。
;;;   ProcessOutcome の started(False = 起こせなかった — OSError を値で)・start-error(その理由)。exit-code は子の returncode を丸めずに
;;;   持つ(負の値 = signal・137 など)。時間切れは timed-out True(exit-code 124)、起こせない時は exit-code 127。
;;;
;;;   RunProcess        子 process を 1 回走らせて終わりを待つ。答え = ProcessOutcome。
;;;   ExecutableAt      その path に実行できる file が在るか。答え = bool。
;;;   ReadEnvironment   自分の process の環境変数のうち names の分。答え = 在る分だけの EnvEntry の tuple(names の順)。
;;;   WorkingDirectory  自分の process の作業 dir(絶対 path)。
(require doeff-hy.record [defrecord defenum])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff [EffectBase])


;; RunProcess の env の tuple の扱い(頭の註): REPLACE = 子の環境変数の全部・EXTEND = 呼び手の環境を継いで足す。
(defenum EnvMode REPLACE EXTEND)


(defrecord EnvEntry
  "環境変数 1 つ(名と値)。"
  (#^ str name)
  (#^ str value))


(defclass [(dataclass :frozen True :kw-only True)] ProcessOutcome []
  "子 process 1 回の結果。判断(成否の解釈)は呼び手が持つので raise しない(頭の註)。"
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
  #^ (| str None) output-path
  (setv output-path None))


(defclass [(dataclass :frozen True :kw-only True)] ExecutableAt [EffectBase]
  "その path に実行できる file が在るか。"
  #^ str path)


(defclass [(dataclass :frozen True)] ReadEnvironment [EffectBase]
  "自分の process の環境変数のうち names の分を読む(頭の註)。"
  (#^ (get tuple #(str ...)) names))


(defclass [(dataclass :frozen True)] WorkingDirectory [EffectBase]
  "自分の process の作業 dir(頭の註)。")
