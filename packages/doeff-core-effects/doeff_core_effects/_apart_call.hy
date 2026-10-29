;;; program を撃った task の scheduler の thread の外で回す仕組み run-apart(card ki-3b942d3af8da)— program の計算(VM の歩と、program の
;;; 中の素の関数)は別の thread の新しい VM で回し、program が出す effect は撃った task へ送り返して、撃った所の handler で答える。
;;;
;;; 何のためか: scheduler は 1 本の thread で全部の task を回し、task を替えるのは scheduler の effect(待ち・立て・合図)の所だけ。effect を
;;; 出さずに続く計算(行ごとの読み解き・sha256 の計り直し・tuple の組み立て)は、終わるまで scheduler の thread を占め、同じ run の他の task
;;; (待ち受けの要求)を全部その後ろに並ばせる。2026-10-27 01:26〜01:44 JST と 02:08〜02:16 JST、記録の service の手入れの係(使い手が渡す
;;; 送りの関数)が ClickHouse から読み戻した 19 万行を 1 行ずつ照らす計算に 4 分ずつ 2 回 scheduler の thread を占め、本体と twin の要求が
;;; 全部 数分 待った(DB の接続は全部 idle・要求の待ちは task が始まる前の queue の区間)。Compute(compute_effects.hy)は純粋な program しか
;;; 回せず(handler を 1 つも付けない VM)、送りの関数のように effect(SQL・計器・時計)を出しながら計算する program は外へ出せなかった。
;;;
;;; 形:
;;;   - run-apart = program に当てる handler の値(ADR-DOE-API-001 R3・R16 — (run-apart program) で当て、with-handlers の列にも置ける)。
;;;     当てた program の task の中で、daemon の thread 1 本に新しい VM(PyVM)を起こし、program を送り返しの handler(forwarding-handler)
;;;     だけの下で回す。program が出す effect は全部この handler に届き、handler は effect を通い路(ApartLane)へ置いて答えを待つ(別の
;;;     thread なので待つのはその thread だけ)。撃った task は通い路に外から完了させる promise を掛けて待ち(Wait — scheduler の他の task は
;;;     回り続ける)、届いた effect を自分で撃って(撃った所の handler の全部が答える — 置き場・SQL・計器・時計・scheduler の effect も)、
;;;     答えか例外を返す。program の答えは run-apart の答え、program の例外は run-apart の例外。
;;;   - 1 本の program の effect は 1 つずつ(VM が答えを待つ間は次を出さない)なので、撃った task は届いた順に 1 つずつ答える。
;;;   - 計算は別の thread で回り、free-threaded の Python では撃った run の scheduler の thread と並んで走る。effect の往復は thread の受け渡し
;;;     1 回(外から完了させる promise と Future)ぶん遅い — effect の少ない重い計算に使う(要求ごとの短い program には使わない)。
;;;   - Try は別の thread の VM の中で答える(VM に try-handler を置く — Try の program は別の thread に留まり、内側の handler も取り直す)。
;;;   - 撃った所で答える effect が program を値に持つ物(Spawn・Gather・Race の program、SqlTransaction の program)は、その program を
;;;     撃った run が回す。中の計算は撃った run の scheduler の thread に戻り、中の program は別の thread の VM の中で program が被せた
;;;     handler(run-apart の内側の handler)も持たない — 撃った所の handler だけが答える。重い計算と内側の handler の要る effect は
;;;     その外に置く。
;;;   - 取り消し: 撃った task が取り消されたら通い路を閉じ、答えを待つ effect と、閉じた後に program が出す effect は ApartClosed を
;;;     program へ投げる(program の finally は回るが、finally の中の effect も ApartClosed になる — 後始末に effect を使う program には
;;;     使えない)。effect を出さない計算は終わるまで止まらない(thread は daemon — process の終わりを止めない)。閉じた後の program の
;;;     答えは捨てる。撃った側は、program を待たなくなった時に run-apart の task を取り消す(daemon の task は run の終わりに取り消されない)。
;;;   - 選び方: 入口が見える 1 つの純粋な計算 → Compute(compute_effects.hy — pool と計器を持つ)。driver の塞ぐ呼び → offloaded
;;;     (offloaded_call.hy)。使い手が決める、effect を出しながら長く計算する program → run-apart(1 回 1 本の thread・上限も計器も無い —
;;;     ThreadPerCall と同じ形なので、要求ごとに使わない)。
;;; 仕組みの在りか(先例): 外から完了させる promise で待つのは offloaded_call.hy の offloaded と同じ作法・新しい VM を起こすのは
;;; offloaded_call.hy の run-detached と同じ。Compute(純粋な program だけ)と違い、program は effect を出してよい。
(require doeff-hy.macros [defk deff <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import threading)
(import functools [partial])
(import concurrent.futures [Future])
(import doeff [Program Transfer TransferThrow with-handlers])
(import doeff.program [handler :as raw-handler])
(import doeff [PyVM])
(import collections.abc [Callable])
(import doeff_core_effects._scheduler [CreateExternalPromise ExternalPromise Wait TaskCancelledError])
(import doeff_core_effects._handlers [try-handler])

(val MODULE-TAGS {:context "scheduler" :role "foundation"})


(defclass ApartClosed [Exception]
  "撃った task が取り消され、通い路が閉じた後に、別の thread の program が出した effect に投げる例外(頭の註の取り消し)。")


(defrecord ApartAsked
  "別の thread の program が出した effect 1 つ: effect = 出した effect・reply = 答えを返す Future(撃った task が値か例外で終える)。"
  (#^ object effect)
  (#^ Future reply))


(defrecord ApartDone
  "別の thread の program が答えた: value = program の答え。"
  (#^ object value))


(defrecord ApartFailed
  "別の thread の program が落ちた: error = 上げた例外。"
  (#^ BaseException error))


(defclass ApartLane []
  "撃った task と別の thread の VM の通い路(頭の註)。資源なので値の型ではない。armed = 撃った task が掛けた、次の知らせを受ける外から
   完了させる promise(None = 掛かっていない)・asked = 渡したが答えのまだ無い effect の Future(閉じる時に ApartClosed で終える —
   渡した promise が取り消しと行き違うと、別の thread が答えを永久に待つ)・closed = 撃った task が去った印。錠は thread の間の Condition。"

  (deff __init__ [self]  ; defk にできない: 資源の class の初期化
    {:pre [(: self ApartLane)] :post [(: % None)]}
    (setv self.condition (threading.Condition)
          self.armed None
          self.asked None
          self.closed False))

  (deff arm [self promise]  ; defk にできない: 撃った task(scheduler の thread)が知らせの受け口を掛ける同期の口
    {:pre [(: self ApartLane) (: promise ExternalPromise)] :post [(: % None)]}
    "次の知らせを受ける promise を掛け、知らせを待つ別の thread を起こすため。"
    (with [_ self.condition]
      (setv self.armed promise)
      (.notify-all self.condition)))

  (deff deliver [self message]  ; defk にできない: 別の thread の VM が知らせを置く同期の口(VM の外)
    {:pre [(: self ApartLane) (: message (| ApartAsked ApartDone ApartFailed))] :post [(: % bool)]}
    "知らせ 1 つを、撃った task が掛けた promise で渡すため(掛かるまで待つ)。閉じていれば渡さずに False。"
    (with [_ self.condition]
      (while (and (is self.armed None) (not self.closed))
        (.wait self.condition))
      (when self.closed
        (return False))
      (setv promise self.armed
            self.armed None)
      (when (isinstance message ApartAsked)
        (setv self.asked message.reply)))
    (.complete promise message)
    True)

  (deff close [self]  ; defk にできない: 撃った task が去る時の同期の口(取り消しの後始末)
    {:pre [(: self ApartLane)] :post [(: % None)]}
    "通い路を閉じるため(知らせを待つ別の thread を起こし、以後の知らせを渡さず、答えのまだ無い effect を ApartClosed で終える)。"
    (with [_ self.condition]
      (setv self.closed True
            self.armed None
            asked self.asked
            self.asked None)
      (.notify-all self.condition))
    (when (and (is-not asked None) (not (.done asked)))
      (.set-exception asked (ApartClosed "撃った task が去った — 通い路は閉じている")))))


(deff forwarding-handler [lane]  ; defk にできない: 別の thread の VM に入れる答え手を作る(VM の外の Future を待つ)
  {:pre [(: lane ApartLane)] :post [(: % Callable)]}
  "別の thread の VM で、program が出す effect を全部 通い路へ送り、撃った task が返した答え(か例外)で再開する答え手を作るため。"
  (defn forwarded [effect k]
    ;; effect 1 つを撃った task へ渡し、答えを待って再開する(待つのはこの thread だけ)。
    (setv reply (Future))
    (if (.deliver lane (ApartAsked :effect effect :reply reply))
        (try
          (setv value (.result reply))
          (except [error BaseException]
            (return (yield (TransferThrow k error))))
          (else
            (return (yield (Transfer k value)))))
        (return (yield (TransferThrow k (ApartClosed "撃った task が去った — 通い路は閉じている"))))))
  (raw-handler forwarded))


(deff apart-thread [lane program]  ; defk にできない: daemon の thread の本体(VM の外から新しい VM を起こす)
  {:pre [(: lane ApartLane) (: program Program)] :post [(: % None)]}
  "別の thread で program を新しい VM で回し、答えか例外を通い路へ置くため(Try はこの VM の中で答える — 頭の註)。"
  (try
    (setv value (.run (PyVM) (with-handlers [(forwarding-handler lane) try-handler] program)))
    (except [error BaseException]
      (.deliver lane (ApartFailed :error error))
      None)
    (else
      (.deliver lane (ApartDone :value value))
      None)))


(defk answered-here [asked]
  {:pre [(: asked ApartAsked)] :post [(: % None)] :tags {:context "scheduler" :role "foundation"}}
  "別の thread の program が出した effect を、撃った task の handler で答え、答えか例外を Future へ返すため。撃った task の取り消しは
   上げる(答えのまだ無い Future は run-apart の通い路の閉じが ApartClosed で終える)。"
  (try
    (<- value asked.effect)
    (except [error TaskCancelledError]
      (raise))
    (except [error Exception]
      (.set-exception asked.reply error))
    (else
      (.set-result asked.reply value)))
  None)


(defk next-message [lane]
  {:pre [(: lane ApartLane)] :post [(: % (| ApartAsked ApartDone ApartFailed))] :tags {:context "scheduler" :role "foundation"}}
  "通い路に外から完了させる promise を掛けて、別の thread の次の知らせを待つため(待つのは撃った task だけ)。"
  (<- promise (CreateExternalPromise))
  (.arm lane promise)
  (<- message (Wait promise.future))
  message)


(defk _apart [program]
  {:tp [T] :pre [(: program (get Program #(T object)))] :post [(: % T)] :tags {:context "scheduler" :role "foundation"}}
  "program を別の thread の新しい VM で回し、program が出す effect をこの task の handler で答えて、program の答えを返すため(頭の註)。
   program の例外はそのまま上げる。この task が取り消されたら通い路を閉じる(program へは ApartClosed — 頭の註の取り消し)。
   外側に scheduled が要る(CreateExternalPromise と Wait)。"
  (val lane (ApartLane))
  (.start (threading.Thread :target (fn [] (apart-thread lane program)) :name "doeff-apart" :daemon True))
  (try
    (while True
      (<- message (next-message lane))
      (match message
        (ApartDone :value value) (return value)
        (ApartFailed :error error) (raise error)
        (ApartAsked) (<- (answered-here message))))
    (finally
      (.close lane))))


(defclass _ApartHandler [partial]
  "run-apart の型: 包む program を _apart に渡す partial に、handler の印 _doeff_is_handler_fn を持たせるため(印が無いと with-handlers が
   effect の答え手として包み直す・doeff_records の BodyWrapper と同じ付け方)。"
  (setv _doeff_is_handler_fn True))


;; program を撃った task の scheduler の thread の外で回す handler(頭の註): (run-apart program) は、program を別の thread の新しい VM で
;; 回し、program が出す effect を当てた所の handler で答えて、program の答えを返す program。program の例外はそのまま上げる。外側に
;; scheduled が要る(CreateExternalPromise と Wait)。設定も呼びごとの状態も持たないので、工場でなく印の付いた handler の値
;; (ADR-DOE-API-001 R3・R16・ADR-DOE-API-002 R1 — maybe・result と同じ)。
(setv run-apart (_ApartHandler _apart))

;; 何にも答えず(__doeff_handles__ = ())、program の effect を当てた所で出し直し、その周りで通い路の待ち(CreateExternalPromise・Wait)を
;; 出す、という宣言(doeff-effect-analyzer の body wrapper の読み — doeff_records の records-signal-handler と同じ)。
(setv run-apart.__doeff_handles__ #()
      run-apart.__doeff_effects__ #(CreateExternalPromise Wait))
