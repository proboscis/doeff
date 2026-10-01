;;; 宿(job の Program を走らせる所)が Program に提供する物の閉じた契約(ADR-DOE-CLUSTER-001・改訂 1 の H)。
;;;
;;; 宿は 2 つ: 本番の worker の子 process(job_entry)と、手元の sim-cluster の偽の宿。どちらも Program に提供するのは次の 3 つだけで、
;;; それ以外(scheduler・時計・記録係・業務の handler)は Program が自分の with-handlers で並べる(runner は handler を足さない — R2)。
;;;
;;;   1. run-context  = Ask HOST-CONTRACT.run-context-key の答え(job_context.RunContext — coordinator の URL・worker・job・世代)
;;;   2. environ      = 宣言の :environ(子の環境変数)。Program は名の Ask で読み、値は字面どおりの文字列(下の environ-reader)
;;;   3. program-path = Ask HOST-CONTRACT.program-key の答え(この job の詰めた Program の file の path — 記録係が header に載せる)
;;;
;;; 本番では、この module の土台の handler host-reader が os.environ から 1 と 3 に答え、(environ-reader) が 2 に答える(業務の側が
;;; 土台の組に並べる)。host-reader は session val を使うので、その外側に状態の handler(doeff_core_effects.handlers の state)が要る —
;;; 土台の組の中で host-reader より外に置く。
;;; sim の偽の宿は同じ鍵に同じ型で答える。job_entry の文書・host-reader・sim の宿は、この値を参照する(写しを作らない)。
;;;
;;; environ の読みの定義は environ-reader の 1 つ(名 → 値の置き場を引数に取る): 本番の土台 = 引数なし(子の process の os.environ)・
;;; sim の偽の宿(local.hy の run-fenced)= 子の spec.environ。値は字面どおり返す — doeff_core_effects.handlers.env_var_ask は { で始まり
;;; } で終わる値を {module.path} の import として解くので、宣言の :environ に JSON の object を置いた設定が本番の子でだけ落ちていた
;;; (sim の宿は字面どおり返していた — job API の計画の決定 4 の戻し方「専用の handler を足して置き換える」)。env_var_ask の
;;; {module.path} の機能はそのまま(宿の契約の読みとしては使わない)。
;;;
;;; SIM-PASSABLE = sim の偽の宿の柵(local.hy の fence)が Program の外へ通す effect の型の表(改訂 1 の B)。本番の子 process では
;;; Program の土台が scheduler と時計を含むが、sim の土台は含まない(含めると service の中に 2 つ目の scheduler ができ、Delay が外の
;;; scheduler を塞ぐ)ので、この 2 種類だけは sim の外側(scheduler と、doeff-time の仮想の sim-time-handler か壁の async-time-handler)が
;;; 答える。Await は通さない(本番の子と同じく、本物の I/O を持つ Program は土台に await-handler を並べる — local.hy の頭の註)。表の外の effect は、
;;; Program と宿の答え(HOST-CONTRACT の 3 つとクラスタの約束の effect)のどちらも答えなければ、本番の子と同じ未処理の例外
;;; (doeff.UnhandledEffect)で process を落とす — sim の外側(検の handler・sim の世界)が本番には無い答えを黙って返さないため。
;;; 時計のうち SetTime(仮想の時計を系ごと動かす)と ScheduleAt(時計の handler が外側で Spawn する = 柵の外で走る)は通さない。
;;; 柵は Program の Spawn を包み直して(process の中の task として覚える — process の終わりで一緒に止める)外へ送り、その包みが出す
;;; 登録(local.hy の KeepChild — sim の仕組みの effect)だけは表の外でも通す。
(require doeff-hy.macros [defhandler val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import os)
(import collections.abc [Mapping])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.scheduler [Spawn TaskCompleted Gather Wait Race Cancel CreatePromise CompletePromise FailPromise
                                      CreateExternalPromise CreateSemaphore AcquireSemaphore ReleaseSemaphore])
(import doeff_time [DelayEffect GetTimeEffect GetMonotonicEffect WaitUntilEffect])
(import doeff_cluster.job_context [RunContext context-from-env])


(defrecord HostContract
  "宿が提供する物の鍵。run-context-key / program-key = Ask の鍵・program-env = 子の process に Program の path を渡す環境変数の名。"
  (#^ str run-context-key)
  (#^ str program-key)
  (#^ str program-env))


(val HOST-CONTRACT (HostContract :run-context-key "doeff.cluster.run-context"
                                 :program-key "doeff.cluster.program"
                                 :program-env "DOEFF_WORKER_PROGRAM"))


;; sim の柵が外へ通す effect の型(頭の註)。scheduler の effect と doeff-time の時計の effect だけ。
(val SIM-PASSABLE #(Spawn TaskCompleted Gather Wait Race Cancel CreatePromise CompletePromise FailPromise CreateExternalPromise
                    CreateSemaphore AcquireSemaphore ReleaseSemaphore
                    DelayEffect GetTimeEffect GetMonotonicEffect WaitUntilEffect))


(defhandler host-reader
  {:needs #{} :tags {:context "doeff-cluster" :role "foundation"}}
  ;; 本番の宿の答え(worker が子へ渡した環境変数を読む)。土台の handler なので os.environ を直に読む(ADR-DOE-CLUSTER-001 R5b —
  ;; 記録係の下に置く)。環境変数は process の間で変わらないので session で 1 回だけ読む。
  (session val context (context-from-env))
  (session val program-path (os.environ.get HOST-CONTRACT.program-env ""))
  (Ask [key]
    :when (in key #(HOST-CONTRACT.run-context-key HOST-CONTRACT.program-key))
    (resume (if (= key HOST-CONTRACT.run-context-key) context program-path))))


(defhandler environ-reader [#^ Mapping [environ os.environ]]
  {:needs #{} :tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 名 → 値の置き場が宿ごとに違う(本番 = 子の process の os.environ — 既定・sim = 子の spec.environ)。読みの定義を
  ;; この 1 つにして、本番の子と sim の子が同じ :environ に同じ値を返す(頭の註)。本番の土台は引数なしの (environ-reader) を土台の
  ;; with-handlers の中でその場で呼ぶ(handler の値は Program に詰められない — ADR-DOE-CLUSTER-001 R3b・remote_model.StrictPickler)。
  ;; 宣言の :environ の名の Ask に、値を字面どおり(文字列のまま — {module.path} を解かない・JSON を parse しない)答える。置き場に無い名と
  ;; 文字列でない鍵(型を鍵にする Ask など — os.environ は文字列でない鍵の問いで TypeError を投げる)は外側の handler へ通す。
  (Ask [key]
    :when (and (isinstance key str) (in key environ))
    (resume (get environ key))))
