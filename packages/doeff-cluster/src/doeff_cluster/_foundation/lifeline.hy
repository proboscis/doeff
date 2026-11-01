;;; 生存の合図の thread— scheduler の外の OS の thread で、使い手の渡す 1 周の判断(turn — 送るなら
;;; #(URL 本文の bytes) を返す Program)を間を置いて回し、送る周は自分の接続の pool で POST して、答えを使い手の書き(settle — status と
;;; 理由を受ける Program)へ渡す汎用の I/O。何を・いつ送るかは使い手(worker/protocol/coordinator_link の lifeline-turn・lifeline-settled)が
;;; 決め、この module は送るだけ。
;;;
;;; 使い手の調整ループの scheduler も、HTTP の asyncio の橋(await-handler の 1 つの loop の thread)も、file の効果の thread も通らない:
;;; HTTP は自分の同期の httpx.Client、時計の読みは 1 周ごとの新しい VM に並べる答え手(LifelineSettings.handlers — 本番 =
;;; sync-time-handler)。ループが同期の処理で scheduler を塞ぐ間や、外の完了が返らない間も、この thread は送れる(zeus 2026-10-24
;;; 05:42〜05:46 JST — 拍の 250 秒の間、拍の外の背景の task も外の完了を待ったまま送れず、coordinator が lease の切れで機体の task を
;;; 全部 lost にした)。同じ process の中なので、process 全体が止まる間(GIL を放さない C の呼び・process の凍結)は守れない。
;;;
;;; 1 周の間の待ち(LifelineSettings.pause)も使い手が渡す(本番 = 止めの合図の Event を秒つきで待つ event-pause — 止めればすぐ抜ける・
;;; 検 = 段で進む仮想の時計)。この thread は時の経過そのものを見張る係なので、出来事でなく間で起きる。思わぬ例外でも thread は抜けない:
;;; traceback を stderr に出し、届かなかった送りとして settle に渡して(使い手が log に出す)次の周へ。
;;;
;;; 送りの後の書き(LifelineSettings.aside — 使い手の書き 1 つ・無ければ None): settle が真を答えた送りの後に、もう 1 つの thread
;;; (doeff-lifeline-aside)で走らせる。送りの thread は書きの終わりを待たない — 詰まった disk の上の書きが次の送りを遅らせて生存の窓を
;;; 切らさない。書きは 1 本ずつで、走っている間に来た求めは 1 つに畳む(合図は Event 1 つ)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import sys)
(import threading)
(import traceback)
(import typing [Callable])
(import httpx)
(import doeff [Program with-handlers])
(import doeff_core_effects._offloaded_call [run-detached])


(defrecord LifelineSettings
  "生存の合図の thread の設定。turn = 1 周の判断を作る関数(引数なし → 送るなら #(URL 本文の bytes)・送らないなら None を答える
   Program)・settle = 送り 1 本の答えの書きを作る関数(status = HTTP の status か None = 届かなかった・detail = 理由・text = 返事の
   本文の全部(届かなかった時は \"\")→ 送りの後の書き aside を走らせるかの真偽を答える Program)・aside = 送りの後の書きを作る関数
   (引数なし → Program・None = 書かない — 頭の註)・handlers = turn と settle と aside の Program を走らせる答え手の組(外側が先 —
   本番 = 時計の答え手と file の答え手)・period-seconds = 1 周の間・
   reply-seconds = 送り 1 本の返事の上限(秒)・connect-seconds = 接続の段だけの上限(秒)・headers = 送りに付ける header・
   pause = 1 周の間の待ちを作る関数(秒・止めの合図 → Program)・transport = 検が渡す偽の相手(None = 本物の HTTP)。"
  (#^ (get Callable #([] Program)) turn)
  (#^ (get Callable #([(| int None) str str] Program)) settle)
  (#^ (| (get Callable #([] Program)) None) aside)
  (#^ list handlers)
  (#^ float period-seconds)
  (#^ float reply-seconds)
  (#^ float connect-seconds)
  (#^ (get dict #(str str)) headers)
  (#^ (get Callable #([float threading.Event] Program)) pause)
  (#^ (| httpx.BaseTransport None) transport))


(defrecord PostAnswer
  "送り 1 本の答え: status = HTTP の status(None = 届かなかった)・detail = 理由(届かない時は例外の repr・返事が在る時は本文の先頭
   200 字)・text = 返事の本文の全部(届かなかった時は \"\" — 使い手が要る欄だけを読む)。"
  (#^ (| int None) status)
  (#^ str detail)
  (#^ str text))


(defk event-pause [seconds stopped]
  {:pre [(: seconds float) (: stopped threading.Event)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "本番の 1 周の間の待ち: 止めの合図が立つか seconds が過ぎるまで待つため(止めればすぐ抜ける)。"
  (.wait stopped seconds)
  None)


(defk posted [client planned settings]
  {:pre [(: client httpx.Client) (: planned tuple) (: settings LifelineSettings)] :post [(: % PostAnswer)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "送る周の POST 1 本を送り、答えを PostAnswer にするため。planned = 使い手の 1 周の判断の答え #(URL 本文の bytes)(使い手の層と
   この層が共に読める型が無いので組で受ける)。"
  (val url (get planned 0))
  (val body (get planned 1))
  (try
    (val response (.post client url :content body :headers (| {"Content-Type" "application/json"} settings.headers)
                         :timeout (httpx.Timeout settings.reply-seconds :connect settings.connect-seconds)))
    (PostAnswer :status response.status-code :detail (cut response.text 0 200) :text response.text)
    (except [error httpx.HTTPError]
      (PostAnswer :status None :detail (repr error) :text ""))))


(defk lifeline-run [settings stopped asked client]
  {:pre [(: settings LifelineSettings) (: stopped threading.Event) (: asked threading.Event) (: client httpx.Client)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "止められるまで、1 周の判断と送りと答えの書きを period-seconds ごとに回すため(daemon の thread の本体)。settle が真を答えた送りの
   後は、送りの後の書きの合図 asked を立てる(書きは待たない — 頭の註)。思わぬ例外でも抜けず、traceback を stderr に出し、
   届かなかった送りとして settle に渡す(その答えでは書きを求めない)。抜けるのは止められた時だけで、その時に自分の client を閉じる。"
  (try
    (while (not (.is-set stopped))
      (try
        (<- planned (| tuple None) (with-handlers settings.handlers (settings.turn)))
        (when (is-not planned None)
          (<- answer PostAnswer (posted client planned settings))
          (<- wanted bool (with-handlers settings.handlers (settings.settle answer.status answer.detail answer.text)))
          (when wanted
            (.set asked)))
        (except [error Exception]
          (traceback.print-exc :file sys.stderr)
          (try
            (<- _wanted bool (with-handlers settings.handlers (settings.settle None (repr error) "")))
            (except [_ Exception]
              (traceback.print-exc :file sys.stderr)))))
      (<- (settings.pause settings.period-seconds stopped)))
    (finally
      (.close client)))
  None)


(defk aside-run [settings stopped asked]
  {:pre [(: settings LifelineSettings) (: stopped threading.Event) (: asked threading.Event)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "送りの後の書きの thread の本体: 合図 asked が立つたびに、合図を下ろしてから書き(settings.aside)を 1 本走らせるため(書きの間に
   立った合図は次の 1 本に畳む)。思わぬ例外でも抜けず、traceback を stderr に出して次の合図を待つ。抜けるのは止められた時だけ。"
  (while (not (.is-set stopped))
    (.wait asked)
    (.clear asked)
    (when (not (.is-set stopped))
      (try
        (<- (with-handlers settings.handlers (settings.aside)))
        (except [_ Exception]
          (traceback.print-exc :file sys.stderr)))))
  None)


(defk start-lifeline [settings]
  {:pre [(: settings LifelineSettings)] :post [(: % (get Callable #([] None)))] :tags {:context "doeff-cluster" :role "foundation"}}
  "生存の合図の thread(名 doeff-lifeline)と、送りの後の書きが在れば書きの thread(名 doeff-lifeline-aside)を daemon で起こし、
   止める関数を返すため。接続の pool は送りの thread だけが使う(本体の HTTP の client と分けない pool は、free-threaded の Python で
   thread の間の錠の外の書きを読んで止まった — kube_client の頭の註)。"
  (val stopped (threading.Event))
  (val asked (threading.Event))
  (val client (httpx.Client :transport settings.transport))
  ;; thread の target と止める関数は外の library が呼ぶ素の関数 — 中身は defk を新しい VM で走らせる。
  (.start (threading.Thread :target (fn [] (run-detached (lifeline-run settings stopped asked client))) :name "doeff-lifeline" :daemon True))
  (when (is-not settings.aside None)
    (.start (threading.Thread :target (fn [] (run-detached (aside-run settings stopped asked))) :name "doeff-lifeline-aside" :daemon True)))
  ;; 止めの合図の後に書きの合図も立て、書きの合図を待っている thread を起こして抜けさせる。
  (fn [] (.set stopped) (.set asked)))
