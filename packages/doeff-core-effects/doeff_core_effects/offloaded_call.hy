;;; 塞ぐ呼び(driver の I/O など)を scheduler の thread の外で回し、撃った task だけが外から完了させる promise で待つ仕組み
;;; (agora-redesign #1215)。PostgreSQL の答え手 2 つ — postgres-sql-handler(postgres_sql.hy)と pooled-postgres-sql-handler
;;; (pooled_postgres_sql.hy — #880 U2 で書いた仕組みをここへ移した)— が共に使う。
;;;   - offloaded = call を Executor の thread で回し、CreateExternalPromise + Wait で待つ(thread_pool_compute.hy と同じ作法)。待つのは撃った
;;;     task だけで、scheduler の他の task は回り続ける — scheduled の下で使う。待ちが取り消されたら Handoff が後始末をする: 始まっていない
;;;     仕事は外し、取り消しの前後に出来た答えは abandon(Executor で回す後始末 — 借りた接続を返す等)へ 1 度だけ回す。もう走っていて答えの
;;;     まだ無い仕事には interrupt(Executor で回す止め方 — 走っている子 process を止める等・agora-redesign #2847)を 1 度だけ回す。止め方を
;;;     持たない仕事(既定の keep-going)は走り切り、答えは abandon へ回る。
;;;   - ThreadPerCall = 呼び 1 つに thread 1 本を起こす Executor(持ち主も後始末も要らない既定)。thread の数に上限を置かないのは、呼びが
;;;     thread の中で別の資源(接続の許可)を待っても、その資源を持つ側の次の呼びが thread の空きを待って詰まらないため。同時の数の上限は
;;;     呼びが待つ資源の側(PostgresConnections の許可)が持つ。thread は daemon — 止まらない呼び(長い文)が process の終わりを止めない。
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "sql" :role "foundation"})
(import collections.abc [Callable])
(import concurrent.futures [Executor Future])
(import threading)
(import doeff_vm [PyVM])
(import doeff_core_effects.scheduler [CreateExternalPromise Wait TaskCancelledError])

;; まだ答えを渡していない印(答えが None の仕事と見分ける)。
(val NOT-DELIVERED (object))


(deff run-detached [program]  ; defk にできない: Executor の thread で回す入口(VM の外から新しい VM を起こす)
  {:pre [(: program "driver を呼ぶ Program")] :post [(: % "program の答え")]}
  "塞ぐ呼びを持つ Program(driver の文の手順)を Executor の thread の新しい VM で値にするため。"
  (.run (PyVM) program))


(deff keep-nothing [_value]  ; defk にできない: Executor の thread で回す後始末の入口(何も持たない答えの後始末)
  {:pre [(: _value "届かなかった答え")] :post [(: % "None")]}
  "取り消された待ち手に届かなかった答えが資源を持たない時の後始末(何もしない)。"
  None)


(deff keep-going []  ; defk にできない: Executor の thread で回す止め方の入口(止める物を持たない仕事の既定)
  {:pre [] :post [(: % "None")]}
  "待ち手が取り消された時に走っている仕事を止める手段が無い時の止め方(何もしない — 仕事は走り切り、答えは abandon へ回る)。"
  None)


(defclass ThreadPerCall [Executor]
  "呼び 1 つに daemon の thread 1 本を起こす Executor(頭の註)。資源の係なので値の型ではない。"

  (deff submit [self call #* args]  ; defk にできない: Executor の口(concurrent.futures の約束 — VM の外)
    {:pre [(: self ThreadPerCall) (: call "(引数) → 値 の callable") (: args tuple)] :post [(: % Future)]}
    "call を新しい thread で回し、その終わりを Future で渡すため(始まる前に取り消された Future は回さない)。"
    (setv future (Future))
    (.start (threading.Thread :target (fn []
                                        (when (.set-running-or-notify-cancel future)
                                          (try
                                            (.set-result future (call #* args))
                                            (except [error BaseException]
                                              (.set-exception future error)))))
                              :name "doeff-offloaded-call"
                              :daemon True))
    future))


(defclass Handoff []
  "Executor の仕事 1 つの答えを promise へ渡す係(scheduler の thread と仕事の thread の間で錠を取って触る)。待ち手が取り消されたら、始まって
   いない仕事は外し、取り消しの前後に出来た答えは abandon(Executor で回す後始末 — 借りた接続を返す等)へ、走っていて答えのまだ無い仕事
   は interrupt(Executor で回す止め方)へ、どちらか 1 度だけ回す。"

  (deff __init__ [self pool abandon interrupt job]  ; defk にできない: 資源の class の初期化
    {:pre [(: self Handoff) (: pool Executor) (: abandon (get Callable #([object] None))) (: interrupt (get Callable #([] None)))
           (: job Future)]
     :post [(: % "None")]}
    ;; 錠は同じ thread から入り直せる RLock: give-up が錠を持ったまま始まっていない仕事を外すと、Future.cancel が同じ thread で完了の
    ;; callback(deliver)を呼ぶ。入り直せない錠だと scheduler の thread がそこで止まり、run 全体が固まった(agora-redesign #2792 の検で発見)。
    (setv self.pool pool
          self.abandon abandon
          self.interrupt interrupt
          self.job job
          self.lock (threading.RLock)
          self.abandoned False
          self.delivered NOT-DELIVERED))

  (deff deliver [self promise future]  ; defk にできない: 仕事の thread から呼ばれる完了の callback(VM の外)
    {:pre [(: self Handoff) (: promise "scheduler の ExternalPromise") (: future Future)] :post [(: % "None")]}
    "仕事の終わりを promise へ渡すため(取り消された後に出来た答えは後始末へ回す)。"
    (with [_ self.lock]
      (cond
        (.cancelled future) None
        (is-not (.exception future) None) (when (not self.abandoned) (.fail promise (.exception future)))
        self.abandoned (.submit self.pool self.abandon (.result future))
        True (do (setv self.delivered (.result future))
                 (.complete promise self.delivered)))))

  (deff give-up [self]  ; defk にできない: scheduler の取り消しの callback(on_cancel — VM の外)と、待ちが取り消しで抜けた時の後始末
    {:pre [(: self Handoff)] :post [(: % "None")]}
    "待ち手が取り消されたら、始まっていない仕事を外し、もう渡した答えを後始末へ回し、走っていて答えのまだ無い仕事を止め方へ回すため
     (何度呼んでも、後始末か止め方のどちらかを 1 度)。止め方を Executor で回すのは、止めるのに待ち(猶予)が要っても scheduler の thread を
     塞がないため。走り終えて答えを渡す直前の仕事にも止め方は回るので、止め方は止める物が無ければ何もしない形にする。"
    (with [_ self.lock]
      (when (not self.abandoned)
        (setv self.abandoned True)
        (setv removed (.cancel self.job))
        (cond
          removed None
          (is-not self.delivered NOT-DELIVERED) (.submit self.pool self.abandon self.delivered)
          True (.submit self.pool self.interrupt))))))


(defk offloaded [pool call abandon [interrupt keep-going]]
  {:pre [(: pool Executor) (: call (get Callable #([] object))) (: abandon (get Callable #([object] None)))
         (: interrupt (get Callable #([] None)))]
   :post [(: % "call の答え")]
   :tags {:context "sql" :role "foundation"}}
  "call を pool の thread で回し、この task だけが外から完了させる promise で待つため(scheduler の他の task は回り続ける)。待ちが取り消され
   たら、始まっていない仕事は外し、届かなかった答えは abandon で後始末する(完了の値が届く前の取り消しも、届いた後で再開の前の取り消しも)。
   走っている仕事は interrupt で止める(既定 keep-going = 止めずに走り切らせる — 頭の註)。"
  (<- promise (CreateExternalPromise))
  (val handoff (Handoff pool abandon interrupt (.submit pool call)))
  (.on-cancel promise (fn [] (.give-up handoff)))
  (.add-done-callback handoff.job (fn [done] (.deliver handoff promise done)))
  (try
    (<- outcome (Wait promise.future))
    (except [TaskCancelledError]
      (.give-up handoff)
      (raise)))
  outcome)
