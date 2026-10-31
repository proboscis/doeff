;;; 塞ぐ呼び(driver の I/O など)を scheduler の thread の外で回し、撃った task だけが外から完了させる promise で待つ仕組み。
;;;PostgreSQL の答え手 2 つ — postgres-sql-handler(postgres_sql.hy)と pooled-postgres-sql-handler
;;; (pooled_postgres_sql.hy — #880 U2 で書いた仕組みをここへ移した)— が共に使う。
;;;   - offloaded = call を Executor の thread で回し、CreateExternalPromise + Wait で待つ(thread_pool_compute.hy と同じ作法)。待つのは撃った
;;;     task だけで、scheduler の他の task は回り続ける — scheduled の下で使う。待ちが取り消されたら Handoff が後始末をする: 始まっていない
;;;     仕事は外し、取り消しの前後に出来た答えは abandon(Executor で回す後始末 — 借りた接続を返す等)へ 1 度だけ回す。もう走っていて答えの
;;;     まだ無い仕事には interrupt(Executor で回す止め方 — 走っている子 process を止める等)を 1 度だけ回す。止め方を
;;;     持たない仕事(既定の keep-going)は走り切り、答えは abandon へ回る。
;;;   - 答えは 1 枠の箱 Delivered に入れて promise を完了させ、待ち手へ渡す時に箱を空ける。scheduler は完了した promise の答えを、handle が
;;;     全部消えたと掃除(HANDLE_SWEEP_INTERVAL の id ごと — doeff #502)が見るまで表に持つ。答えそのもので完了させると、待ち手が手放した
;;;     答え(ClickHouse の読みの行・file の中身)が掃除まで残り、Rust の版の表は gc からも見えない(読みの口
;;;     の job が 3.5 時間で RssAnon 440→2464 MB になり、残る型は SqlRows だった)。await_handler の _AwaitedValue と同じ作法。
;;;   - ThreadPerCall = 呼び 1 つに thread 1 本を起こす Executor(持ち主も後始末も要らない既定)。thread の数に上限を置かないのは、呼びが
;;;     thread の中で別の資源(接続の許可)を待っても、その資源を持つ側の次の呼びが thread の空きを待って詰まらないため。同時の数の上限は
;;;     呼びが待つ資源の側(PostgresConnections の許可)が持つ。thread は daemon — 止まらない呼び(長い文)が process の終わりを止めない。
;;;   - ReusedThreads = 呼びを空いた thread へ渡し、空きが無い時だけ thread を 1 本起こす Executor。ThreadPerCall
;;;     と同じく上限を置かず daemon で、違いは呼びの後の thread を _IDLE-SECONDS の間待たせて次の呼びに使う事だけ。呼びごとに thread を起こすと
;;;     起こす待ち(呼び手の thread で数 ms)が文の数に比例し、預かり所の普段の CPU の 2 割を占めた。同時に走る thread の数は ThreadPerCall と
;;;     同じ(呼びの数の山)で、空きのまま _IDLE-SECONDS を過ぎた thread は終わる。thread は呼びを回し終えたら呼びと答えへの参照を捨ててから
;;;     次を待つ(待つ間の thread が答えを持ち続けない — 上の Delivered と同じ訳)。
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "sql" :role "foundation"})
(import collections.abc [Callable])
(import concurrent.futures [Executor Future])
(import queue [Empty SimpleQueue])
(import threading)
(import doeff [PyVM])
(import doeff_core_effects._scheduler [CreateExternalPromise Wait TaskCancelledError])

;; まだ答えを渡していない印(答えが None の仕事と見分ける)。
(val NOT-DELIVERED (object))
;; ReusedThreads の空いた thread が次の呼びを待つ秒(過ぎたら終わる — 頭の註)。
(val _IDLE-SECONDS 60.0)


(defclass Delivered []
  "仕事の答え 1 つを promise の表から待ち手(または後始末)へ 1 度だけ渡す 1 枠の箱(頭の註)。表に残るのは空の箱で、答えは受け手が
   持つ間だけ生きる。資源の係なので値の型ではない。"
  (setv __slots__ #("_mut_value"))

  (deff __init__ [self value]  ; defk にできない: 資源の class の初期化
    {:pre [(: self Delivered) (: value "仕事の答え")] :post [(: % "None")]}
    "答えを持って始めるため。"
    (setv self._mut-value value))

  (deff take [self]  ; defk にできない: scheduler の thread と取り消しの callback が触る箱の口(VM の外の資源)
    {:pre [(: self Delivered)] :post [(: % "仕事の答え")]}
    "答えを 1 度渡し、箱の参照を外すため(2 度目は None)。"
    (setv value self._mut-value)
    (setv self._mut-value None)
    value))


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

  (deff __init__ [self [on-start None]]  ; defk にできない: 資源の class の初期化
    {:pre [(: self ThreadPerCall) (: on-start (| None (get Callable #([] None))))] :post [(: % "None")]}
    "thread が呼びを回す前に 1 度呼ぶ手(on-start — thread の IO 優先度を下げる等)を持つため(None = 何もしない)。"
    (setv self.on-start on-start))

  (deff submit [self call #* args]  ; defk にできない: Executor の口(concurrent.futures の約束 — VM の外)
    {:pre [(: self ThreadPerCall) (: call "(引数) → 値 の callable") (: args tuple)] :post [(: % Future)]}
    "call を新しい thread で回し、その終わりを Future で渡すため(始まる前に取り消された Future は回さない・on-start は call の前に thread の中で呼ぶ)。"
    (setv future (Future))
    (.start (threading.Thread :target (fn []
                                        (when (.set-running-or-notify-cancel future)
                                          (try
                                            (when (is-not self.on-start None)
                                              (.on-start self))
                                            (.set-result future (call #* args))
                                            (except [error BaseException]
                                              (.set-exception future error)))))
                              :name "doeff-offloaded-call"
                              :daemon True))
    future))


(defclass ReusedThreads [Executor]
  "呼びを空いた daemon の thread へ渡し、空きが無い時だけ thread を 1 本起こす Executor(頭の註)。資源の係なので値の型ではない。
   idle = 呼びを待っている thread のうち、まだ呼びを割り当てていない数。started = 起こした thread の数(検が読む)。"

  (deff __init__ [self [idle-seconds _IDLE-SECONDS]]  ; defk にできない: 資源の class の初期化
    {:pre [(: self ReusedThreads) (: idle-seconds float)] :post [(: % "None")]}
    "空いた thread が次の呼びを待つ秒を持ち、呼びの列と数えを空で始めるため。"
    (setv self.idle-seconds idle-seconds
          self.idle-lock (threading.Lock)
          self.jobs (SimpleQueue)
          self.idle 0
          self.started 0))

  (deff submit [self call #* args]  ; defk にできない: Executor の口(concurrent.futures の約束 — VM の外)
    {:pre [(: self ReusedThreads) (: call "(引数) → 値 の callable") (: args tuple)] :post [(: % Future)]}
    "call を列に置き、空いた thread が無ければ 1 本起こして、その終わりを Future で渡すため(始まる前に取り消された Future は回さない)。
     thread を起こすのは錠の外 — 起こす待ちの間に他の呼びと空いた thread の数えを止めない。"
    (setv future (Future))
    (with [_ self.idle-lock]
      (.put self.jobs #(future call args))
      (setv fresh (= self.idle 0))
      (if fresh
          (setv self.started (+ self.started 1))
          (setv self.idle (- self.idle 1))))
    (when fresh
      (.start (threading.Thread :target self.serve :name "doeff-offloaded-call" :daemon True)))
    future)

  (deff serve [self]  ; defk にできない: thread の本体(VM の外 — 列から呼びを取って回す)
    {:pre [(: self ReusedThreads)] :post [(: % "None")]}
    "列の呼びを 1 つずつ回し、空きのまま idle-seconds を過ぎたら終わるため。待ちが切れた時は錠の下で列をもう一度見る — 切れたのと同時に
     この thread へ割り当てられた呼び(submit が idle を減らして置いた物)を落とさない。空きに数えるのは答えを Future へ書く前 — 答えを
     受けた呼び手がすぐ次の呼びを出しても、この thread が空きに数えられていて新しい thread を起こさない。"
    (while True
      (setv job
        (try
          (.get self.jobs :timeout self.idle-seconds)
          (except [Empty]
            (with [_ self.idle-lock]
              (try
                (.get-nowait self.jobs)
                (except [Empty]
                  (setv self.idle (- self.idle 1))
                  None))))))
      (when (is job None)
        (return None))
      (setv #(future call args) job
            job None
            value None
            error None
            ;; 始まる前に取り消された Future は回さない。
            ran (.set-running-or-notify-cancel future))
      (when ran
        (try
          (setv value (call #* args))
          (except [raised BaseException]
            (setv error raised))))
      (setv call None
            args None)
      (with [_ self.idle-lock]
        (setv self.idle (+ self.idle 1)))
      (cond
        (not ran) None
        (is-not error None) (.set-exception future error)
        True (.set-result future value))
      ;; 待つ間に呼びと答えを持たない(頭の註)。
      (setv future None
            value None
            error None))))


(defclass Handoff []
  "Executor の仕事 1 つの答えを promise へ渡す係(scheduler の thread と仕事の thread の間で錠を取って触る)。待ち手が取り消されたら、始まって
   いない仕事は外し、取り消しの前後に出来た答えは abandon(Executor で回す後始末 — 借りた接続を返す等)へ、走っていて答えのまだ無い仕事
   は interrupt(Executor で回す止め方)へ、どちらか 1 度だけ回す。"

  (deff __init__ [self pool abandon interrupt]  ; defk にできない: 資源の class の初期化
    {:pre [(: self Handoff) (: pool Executor) (: abandon (get Callable #([object] None))) (: interrupt (get Callable #([] None)))]
     :post [(: % "None")]}
    ;; 錠は同じ thread から入り直せる RLock: give-up が錠を持ったまま始まっていない仕事を外すと、Future.cancel が同じ thread で完了の
    ;; callback(deliver)を呼ぶ。入り直せない錠だと scheduler の thread がそこで止まり、run 全体が固まった(発見)。
    ;; 仕事の Future は持たない: Future の完了の callback がこの係と promise を持つので、係が Future を持つと循環の参照になり、promise と
    ;; abandon が抱える物(呼び鈴など)の handle が循環の回収(gc)まで消えず、scheduler の掃除がその行を消せなかった。
    ;; Future は give-up の引数で受ける(取り消しの callback と待ちの後始末が持つ — どちらも promise が終われば手放される)。
    (setv self.pool pool
          self.abandon abandon
          self.interrupt interrupt
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
        True (do (setv self.delivered (Delivered (.result future)))
                 (.complete promise self.delivered)))))

  (deff give-up [self job]  ; defk にできない: scheduler の取り消しの callback(on_cancel — VM の外)と、待ちが取り消しで抜けた時の後始末
    {:pre [(: self Handoff) (: job Future)] :post [(: % "None")]}
    "待ち手が取り消されたら、始まっていない仕事を外し、もう渡した答えを後始末へ回し、走っていて答えのまだ無い仕事を止め方へ回すため
     (何度呼んでも、後始末か止め方のどちらかを 1 度)。止め方を Executor で回すのは、止めるのに待ち(猶予)が要っても scheduler の thread を
     塞がないため。走り終えて答えを渡す直前の仕事にも止め方は回るので、止め方は止める物が無ければ何もしない形にする。"
    (with [_ self.lock]
      (when (not self.abandoned)
        (setv self.abandoned True)
        (setv removed (.cancel job))
        (cond
          removed None
          (is-not self.delivered NOT-DELIVERED) (.submit self.pool self.abandon (.take self.delivered))
          True (.submit self.pool self.interrupt))))))


(defk offloaded [pool call abandon [interrupt keep-going]]
  {:pre [(: pool Executor) (: call (get Callable #([] object))) (: abandon (get Callable #([object] None)))
         (: interrupt (get Callable #([] None)))]
   :post [(: % "call の答え")]
   :tags {:context "sql" :role "foundation"}}
  "call を pool の thread で回し、この task だけが外から完了させる promise で待つため(scheduler の他の task は回り続ける)。待ちが取り消され
   たら、始まっていない仕事は外し、届かなかった答えは abandon で後始末する(完了の値が届く前の取り消しも、届いた後で再開の前の取り消しも)。
   走っている仕事は interrupt で止める(既定 keep-going = 止めずに走り切らせる — 頭の註)。答えは箱 Delivered から取り出して返し、
   scheduler の promise の表には空の箱だけを残す(頭の註)。"
  (<- promise (CreateExternalPromise))
  (val handoff (Handoff pool abandon interrupt))
  (val job (.submit pool call))
  (.on-cancel promise (fn [] (.give-up handoff job)))
  (.add-done-callback job (fn [done] (.deliver handoff promise done)))
  (try
    (<- delivered (Wait promise.future))
    (except [TaskCancelledError]
      (.give-up handoff job)
      (raise)))
  (.take delivered))
