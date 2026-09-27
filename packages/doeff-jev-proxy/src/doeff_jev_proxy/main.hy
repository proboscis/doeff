;;; Jev の呼び出しを覚える代理の composition root — env と Secret の file を読み、handler の組と HTTP の口を組んで立て、止めの合図まで答える。
;;; 判断はここに無い(流れ = service.hy・鍵 = key.hy・答え手 = handlers.hy)。
;;;
;;;   hy -m doeff_jev_proxy.main
;;;
;;; 受け取る env(宣言はここ 1 点):
;;;   JEV_PROXY_DB                      覚えた答えの置き場(SQLite の file の path・必須 — Pod では永続の volume の上)
;;;   JEV_PROXY_ROSTER_FILE             身元の名簿(必須・{version: 1, principals: [{name, tokenSha256}]} — token そのものは持たない)
;;;   JEV_PROXY_ADMINS                  答えを消せる名簿の名(, 区切り・既定は空 = 誰も消せない)
;;;   JEV_PROXY_HOST / JEV_PROXY_PORT   HTTP の口(既定 0.0.0.0 / 8878)
;;;   JEV_PROXY_UPSTREAM_TIMEOUT_SECONDS  本物の Jev の時間切れ(既定 60)
;;;   本物の Jev の宛先とキー          doeff-jev の決め方のまま(JEV_BASE_URL・JEV_MODEL・JEV_API_KEY_FILE … — 既定は TypeSafe 直)。
;;;                                     キーは Secret の mount の file を JEV_API_KEY_FILE で指す(env にキーの値を載せない)
(require doeff-hy.macros [val])
(import collections.abc [Callable])
(import os)
(import signal)
(import threading)
(import doeff [run with_handlers])
(import doeff_core_effects [await_handler try_handler])
(import doeff_core_effects.http_handlers [http_production_handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_records.principals [decode-roster])
(import doeff_jev.target [key_required target_from_process_environment])
(import doeff_jev_proxy.values [ProxyRequest ProxyReply])
(import doeff_jev_proxy.service [respond])
(import doeff_jev_proxy.effects [PrepareStore])
(import doeff_jev_proxy.handlers [new-flights sqlite-store-handler jev-upstream-handler roster-handler
                                  single-flight-handler])
(import doeff_jev_proxy.http_server [ProxyServerConfig start-proxy-server stop-server])

(val ENV-DB "JEV_PROXY_DB")
(val ENV-ROSTER-FILE "JEV_PROXY_ROSTER_FILE")
(val ENV-ADMINS "JEV_PROXY_ADMINS")
(val ENV-HOST "JEV_PROXY_HOST")
(val ENV-PORT "JEV_PROXY_PORT")
(val ENV-UPSTREAM-TIMEOUT "JEV_PROXY_UPSTREAM_TIMEOUT_SECONDS")
(val DEFAULT-HOST "0.0.0.0")
(val DEFAULT-PORT 8878)
(val DEFAULT-UPSTREAM-TIMEOUT 60.0)


(defn #^ Callable proxy-runner [#^ list handlers]  ; defk にできない: HTTP の口が要求ごとに Program を走らせる関数(Program の外の入口)
  "handler の組(外側が先 — 一番内側は single-flight-handler)を被せて respond を走らせる関数を作る。"
  (fn [#^ ProxyRequest request]
    (run (scheduled (with_handlers (+ [(await_handler) try_handler] handlers) (respond request))))))


(defn #^ None serve []  ; defk にできない: process の入口
  "代理を起動し、SIGTERM / SIGINT まで答え続ける。"
  (setv target (target_from_process_environment))
  (when (and (key_required target) (not target.api-key))
    (raise (SystemExit (.format "Jev の代理: 本物の Jev({})のキーが無い — JEV_API_KEY_FILE に Secret の mount の path を置く" target.host))))
  (setv missing (lfor name #(ENV-DB ENV-ROSTER-FILE) :if (not (.get os.environ name)) name))
  (when missing (raise (SystemExit (.format "Jev の代理: env {} が要る" (.join "・" missing)))))
  (setv path (get os.environ ENV-DB)
        roster (run (decode-roster (with [handle (open (get os.environ ENV-ROSTER-FILE) :encoding "utf-8")] (.read handle))))
        admins (frozenset (gfor name (.split (.get os.environ ENV-ADMINS "") ",") :if (.strip name) (.strip name)))
        timeout (float (.get os.environ ENV-UPSTREAM-TIMEOUT DEFAULT-UPSTREAM-TIMEOUT))
        flights (run (new-flights)))
  (setv handlers [(http_production_handler)
                  (sqlite-store-handler path)
                  (jev-upstream-handler target timeout)
                  (roster-handler roster admins)
                  (single-flight-handler flights)]
        runner (proxy-runner handlers))
  ;; 表の用意は要求を受ける前に 1 度(同じ組の上で)。
  (run (scheduled (with_handlers (+ [(await_handler) try_handler] handlers) (PrepareStore))))
  (setv running (start-proxy-server (ProxyServerConfig :runner runner
                                                       :host (.get os.environ ENV-HOST DEFAULT-HOST)
                                                       :port (int (.get os.environ ENV-PORT DEFAULT-PORT))))
        stopping (threading.Event))
  (for [sig #(signal.SIGTERM signal.SIGINT)]
    (signal.signal sig (fn [#* _] (.set stopping))))
  (print (.format "jev-proxy: {} で待ち受け(本物の Jev = {}・model 既定 {}・名簿 {} 名・管理者 {})"
                  running.url target.base-url target.model (len roster.digests) (sorted admins))
         :flush True)
  (.wait stopping)
  (stop-server running))


(when (= __name__ "__main__")
  (serve))
