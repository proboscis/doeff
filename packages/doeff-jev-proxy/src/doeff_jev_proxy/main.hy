;;; Jev の呼び出しを覚える代理の composition root — env と Secret の file を読み、handler の組と HTTP の口を組んで立て、止めの合図まで答える。
;;; 判断はここに無い(流れ = service.hy・鍵 = key.hy・答え手 = handlers.hy)。
;;;
;;;   hy -m doeff_jev_proxy.main
;;;
;;; 組み立ては 2 つの段に分ける(検が main と同じ組み立てで口を開けるように):
;;;   assembled-from-env  env の写像 → 答え手の組の作り手・HTTP の口の組・起動の文(disk に書かない・口を開かない)
;;;   opened              組んだ物 → 置き場の表を用意して口を開く
;;; serve はこの 2 つを os.environ で同じ順に呼び、止めの合図を待つだけ。
;;;
;;; 受け取る env(宣言はここ 1 点):
;;;   JEV_PROXY_DB                      覚えた答えの置き場(SQLite の file の path・必須 — Pod では永続の volume の上)
;;;   JEV_PROXY_ROSTER_FILE             身元の名簿(必須・{version: 1, principals: [{name, tokenSha256}]} — token そのものは持たない)
;;;   JEV_PROXY_ADMINS                  答えを消せる名簿の名(, 区切り・既定は空 = 誰も消せない)
;;;   JEV_PROXY_HOST / JEV_PROXY_PORT   HTTP の口(既定 0.0.0.0 / 8878)
;;;   JEV_PROXY_UPSTREAM_TIMEOUT_SECONDS  本物の Jev の時間切れ(既定 60)
;;;   本物の Jev の宛先とキー          doeff-jev の決め方のまま(JEV_BASE_URL・JEV_MODEL・JEV_API_KEY_FILE … — 既定は TypeSafe 直)。
;;;                                     キーは Secret の mount の file を JEV_API_KEY_FILE で指す(env にキーの値を載せない)
;; 検は main と同じ組み立てを tests/test_entry.hy で呼ぶ(env の中身と、signal を待たずに口を閉じる所だけが違う)。
(require doeff-hy.macros [val defk deff <-])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable Mapping])
(import dataclasses [dataclass])
(import os)
(import types [NoneType])
(import signal)
(import threading)
(import doeff [run with_handlers])
(import doeff_core_effects [await_handler try_handler])
(import doeff_core_effects.http_handlers [http_production_handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_records.principals [Roster decode-roster])
(import doeff_jev.target [JevTarget key_required resolve_target read_text_from_disk])
(import doeff_jev_proxy.values [ProxyRequest ProxyReply])
(import doeff_jev_proxy.service [respond])
(import doeff_jev_proxy.effects [PrepareStore])
(import doeff_jev_proxy.handlers [Flights new-flights sqlite-store-handler jev-upstream-handler roster-handler
                                  single-flight-handler])
(import doeff_jev_proxy.http_server [ProxyServerConfig RunningServer start-proxy-server stop-server])

(val ENV-DB "JEV_PROXY_DB")
(val ENV-ROSTER-FILE "JEV_PROXY_ROSTER_FILE")
(val ENV-ADMINS "JEV_PROXY_ADMINS")
(val ENV-HOST "JEV_PROXY_HOST")
(val ENV-PORT "JEV_PROXY_PORT")
(val ENV-UPSTREAM-TIMEOUT "JEV_PROXY_UPSTREAM_TIMEOUT_SECONDS")
(val DEFAULT-HOST "0.0.0.0")
(val DEFAULT-PORT 8878)
(val DEFAULT-UPSTREAM-TIMEOUT 60.0)


(defrecord Assembled
  "env から組んだ代理 1 つ: handlers-for = 要求ごとに答え手の組を作る関数 / config = HTTP の口の組(runner・host・port)/
   path = 覚えた答えの置き場の file / banner = 起動の 1 行。"
  (#^ Callable handlers-for)
  (#^ ProxyServerConfig config)
  (#^ str path)
  (#^ str banner))


(defrecord Unassembled
  "env から組めなかった理由(入口が SystemExit の文にする)。"
  (#^ str reason))


(defk proxy-runner [handlers-for]
  {:pre [(: handlers-for Callable)] :post [(: % Callable)] :tags {:context "jev-proxy" :role "entry"}}
  "要求ごとに handler の組を handlers-for() で作り(外側が先 — 一番内側は single-flight-handler)、respond を走らせる関数を作るため。
   組は要求ごとに作り直す: http-production-handler の HTTP の client は、包んだ Program 1 つが終わると閉じる(組を使い回すと
   2 つ目の要求で「閉じた client」になる)。要求をまたいで共有する物(置き場の path・相乗りの表・名簿)は handlers-for が閉じ込めて渡す。
   返す関数は HTTP の口が要求ごとの thread で呼ぶ(Program の外の入口)。"
  (fn [#^ ProxyRequest request]
    (run (scheduled (with_handlers (+ [(await_handler) try_handler] (handlers-for)) (respond request))))))


(defk production-handlers [path target timeout roster admins flights]
  {:pre [(: path str) (: target JevTarget) (: timeout float) (: roster Roster) (: admins frozenset) (: flights Flights)]
   :post [(: % Callable)]
   :tags {:context "jev-proxy" :role "entry"}}
  "本番の答え手の組を要求ごとに作る関数を作るため(外側が先: HTTP の実体 → 置き場 → 本物の Jev への翻訳 → 名簿 → 相乗り)。"
  (fn [] [(http_production_handler)
          (sqlite-store-handler path)
          (jev-upstream-handler target timeout)
          (roster-handler roster admins)
          (single-flight-handler flights)]))


(defk assembled-from-env [environ]
  {:pre [(: environ Mapping)] :post [(: % (| Assembled Unassembled))] :tags {:context "jev-proxy" :role "entry"}}
  "env の写像(本番は os.environ)から代理を組むため — 本物の Jev の宛先とキー・名簿・管理者・時間切れ・口・相乗りの表・答え手の組。
   disk には書かず口も開かない(それは opened)。組めなければ理由を Unassembled で返す。"
  (val target (resolve_target environ read_text_from_disk))
  (when (and (key_required target) (not target.api-key))
    (return (Unassembled :reason (.format "Jev の代理: 本物の Jev({})のキーが無い — JEV_API_KEY_FILE に Secret の mount の path を置く"
                                  target.host))))
  (val missing (lfor name #(ENV-DB ENV-ROSTER-FILE) :if (not (.get environ name)) name))
  (when missing
    (return (Unassembled :reason (.format "Jev の代理: env {} が要る" (.join "・" missing)))))
  (val path (get environ ENV-DB))
  (val roster-text (with [handle (open (get environ ENV-ROSTER-FILE) :encoding "utf-8")] (.read handle)))
  (<- roster (decode-roster roster-text))
  (val admins (frozenset (gfor name (.split (.get environ ENV-ADMINS "") ",") :if (.strip name) (.strip name))))
  (val timeout (float (.get environ ENV-UPSTREAM-TIMEOUT DEFAULT-UPSTREAM-TIMEOUT)))
  (<- flights (new-flights))
  (<- handlers-for (production-handlers path target timeout roster admins flights))
  (<- runner (proxy-runner handlers-for))
  (val config (ProxyServerConfig :runner runner
                                 :host (.get environ ENV-HOST DEFAULT-HOST)
                                 :port (int (.get environ ENV-PORT DEFAULT-PORT))))
  (Assembled :handlers-for handlers-for :config config :path path
             :banner (.format "本物の Jev = {}・model 既定 {}・名簿 {} 名・管理者 {}"
                              target.base-url target.model (len roster.digests) (sorted admins))))


(defk opened [assembled]
  {:pre [(: assembled Assembled)] :post [(: % RunningServer)] :tags {:context "jev-proxy" :role "entry"}}
  "組んだ代理の置き場の表を要求の前に 1 度用意し(同じ組の上で)、HTTP の口を開くため。"
  (run (scheduled (with_handlers (+ [(await_handler) try_handler] (assembled.handlers-for)) (PrepareStore))))
  (start-proxy-server assembled.config))


(deff serve []  ; defk にできない: process の入口(signal は main の thread で受け、止めの合図まで待つ)
  {:pre [] :post [(: % NoneType)] :tags {:context "jev-proxy" :role "entry"}}
  "代理を os.environ から組んで口を開き、SIGTERM / SIGINT まで答え続ける。"
  (setv assembled (run (assembled-from-env os.environ)))
  (when (isinstance assembled Unassembled)
    (raise (SystemExit assembled.reason)))
  (setv running (run (opened assembled))
        stopping (threading.Event))
  (for [sig #(signal.SIGTERM signal.SIGINT)]
    (signal.signal sig (fn [#* _] (.set stopping))))
  (print (.format "jev-proxy: {} で待ち受け({})" running.url assembled.banner) :flush True)
  (.wait stopping)
  (stop-server running))


(when (= __name__ "__main__")
  (serve))
