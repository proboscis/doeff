;;; 宿(job の Program を走らせる所)が Program に提供する物の閉じた契約(ADR-DOE-CLUSTER-001・改訂 1 の H)。
;;;
;;; 宿は 2 つ: 本番の worker の子 process(job_entry)と、手元の sim-cluster の偽の宿。どちらも Program に提供するのは次の 3 つだけで、
;;; それ以外(scheduler・時計・記録係・業務の handler)は Program が自分の with-handlers で並べる(runner は handler を足さない — R2)。
;;;
;;;   1. run-context  = Ask HOST-CONTRACT.run-context-key の答え(shared/intent/run_context の RunContext — coordinator の URL・worker・job・世代)
;;;   2. environ      = 宣言の :environ(子の環境変数)。Program は名の Ask で読み、値は字面どおりの文字列(下の os-environ-reader・environ-table-reader)
;;;   3. program-path = Ask HOST-CONTRACT.program-key の答え(この job の詰めた Program の file の path — 記録係が header に載せる)
;;;   4. 退きの知らせ = AwaitRetirement(worker/intent/retirement_model — #3672)の答え。本番の宿は worker の shim が job へ知らせの pipe の
;;;      読み口を継がせ、その fd の番号を環境変数 HOST-CONTRACT.notice-env で渡す(worker が shim の標準入力へ書いた行を shim が中継する)。
;;;      答え手は入口の側の土台の handler pipe-retirement-notices(worker/entry/retirement_notices — 業務の側が土台の組に並べる)。sim の偽の宿は
;;;      世界の受け手で答える(local.hy の process-notices)。
;;;
;;; 本番では、入口の側の土台の handler host-reader(shared/entry/host_reader — #2981 でここから移した)が os.environ から 1 と 3 に答え、
;;; os-environ-reader が 2 に答える(業務の側が土台の組に並べる)。host-reader は session val を使うので、その外側に状態の handler
;;; (doeff_core_effects.handlers の state)が要る — 土台の組の中で host-reader より外に置く。
;;; sim の偽の宿は同じ鍵に同じ型で答える。job_entry の文書・host-reader・sim の宿は、この値を参照する(写しを作らない)。
;;;
;;; environ の読みは答え方の同じ 2 つの答え手で、名で実 I/O の有無が決まる(#1536): 本番の土台 = os-environ-reader(子の process の
;;; os.environ — 実 I/O)・sim の偽の宿(local.hy の run-fenced)= environ-table-reader(子の spec.environ の値の表だけ)。2 つが同じ値を
;;; 返す事は宿の読みの契約テスト(tests/test_host_reads_contract.hy)が保つ。値は字面どおり返す —doeff_core_effects.handlers.env_var_ask は { で始まり
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
;;; 期限つきの待ち WaitWithin は通す — 期限は時計の handler が持ち(模擬の時計は列の 1 項・task を起こさない)、待つのは Program の
;;; future だけ(#2618)。
;;; 柵は Program の Spawn を包み直して(process の中の task として覚える — process の終わりで一緒に止める)外へ送り、その包みが出す
;;; 登録(local.hy の KeepChild — sim の仕組みの effect)だけは表の外でも通す。
(require doeff-hy.macros [defhandler defk val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import os)
(import collections.abc [Mapping])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.scheduler [Spawn TaskCompleted Gather Wait Race Cancel CreatePromise CompletePromise FailPromise
                                      CreateExternalPromise CreateSemaphore AcquireSemaphore ReleaseSemaphore])
(import doeff_time [DelayEffect GetTimeEffect GetMonotonicEffect WaitUntilEffect WaitWithinEffect])


(defrecord HostContract
  "宿が提供する物の鍵。run-context-key / program-key / versions-key = Ask の鍵・program-env = 子の process に Program の path を渡す
   環境変数の名・notice-env = 子の process に退きの知らせの pipe の読み口の fd の番号を渡す環境変数の名(shim が置く — #3672)。
   versions-key の答え = この process の版の識別(foundation/process_versions.process-versions の dict — 記録係が
   header に載せる・送り手の client が blob に添える。protocol の層は自分で読まない — #2345)。"
  {:tags {:context "doeff-cluster" :role "foundation"}}
  (#^ str run-context-key)
  (#^ str program-key)
  (#^ str versions-key)
  (#^ str program-env)
  (#^ str notice-env))


(val HOST-CONTRACT (HostContract :run-context-key "doeff.cluster.run-context"
                                 :program-key "doeff.cluster.program"
                                 :versions-key "doeff.cluster.versions"
                                 :program-env "DOEFF_WORKER_PROGRAM"
                                 :notice-env "DOEFF_WORKER_NOTICE_FD"))


;; sim の柵が外へ通す effect の型(頭の註)。scheduler の effect と doeff-time の時計の effect だけ。
(val SIM-PASSABLE #(Spawn TaskCompleted Gather Wait Race Cancel CreatePromise CompletePromise FailPromise CreateExternalPromise
                    CreateSemaphore AcquireSemaphore ReleaseSemaphore
                    DelayEffect GetTimeEffect GetMonotonicEffect WaitUntilEffect WaitWithinEffect))


(defk this-program-path []
  {:pre [] :post [(: % str)] :tags {:context "doeff-cluster" :role "foundation"}}
  "この job の子 process が走らせる詰めた Program の file の path を、worker が渡した環境変数(HOST-CONTRACT.program-env)から読むため
   (無ければ空)。宿の答え手 shared/entry/host_reader が program-key の Ask に答える時に呼ぶ — 環境変数の読みは foundation に置き、
   入口の層は直に読まない(DOEFF106)。"
  (.get os.environ HOST-CONTRACT.program-env ""))


(defhandler environ-table-reader [#^ (get Mapping #(str str)) environ]
  {:needs #{} :tags {:context "doeff-cluster" :role "foundation"}}
  ;; 渡された値の表(sim の宿 = 子の spec.environ・検の表)だけから答える — この process の os.environ を読まない(実 I/O を持たない)。
  ;; 宣言の :environ の名の Ask に、値を字面どおり(文字列のまま — {module.path} を解かない・JSON を parse しない)答える。表に無い名と
  ;; 文字列でない鍵(型を鍵にする Ask など)は外側の handler へ通す。答え方は os-environ-reader と同じで、同じに保つのは宿の読みの契約
  ;; テスト(tests/test_host_reads_contract.hy — 本物と sim の宿が同じ deftest を通る)。
  (Ask [key]
    :when (and (isinstance key str) (in key environ))
    (resume (get environ key))))


(defhandler os-environ-reader []
  {:needs #{} :tags {:context "doeff-cluster" :role "foundation"}}
  ;; この process の os.environ から答える実 I/O の答え手(本番の子の土台が並べる — doeff-linter の目録 world_handlers.json の env)。
  ;; 名で実 I/O の有無が決まるように、値の表の答え手 environ-table-reader と名を分けた(#1536 — 以前は 1 つの名が既定の引数の時だけ
  ;; os.environ を読んでいた)。答え方は environ-table-reader と同じ(os.environ は文字列でない鍵の問いで TypeError を投げるので、鍵の型を
  ;; 先に見る)。
  ;; [] を書く理由: 送る Program(remote・detached の task)が handler の値を捕まえると送れない(ADR-DOE-CLUSTER-001 R3b・
  ;; remote_model.StrictPickler)ので、Program の本文の with-handlers の中で (os-environ-reader) と呼んでその場で作る。
  (Ask [key]
    :when (and (isinstance key str) (in key os.environ))
    (resume (get os.environ key))))
