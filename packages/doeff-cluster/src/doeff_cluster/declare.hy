;;; 系の宣言(System の値)から coordinator に渡す宣言(b)を出す。
;;;
;;;   hy -m doeff_cluster.declare <module>:<System の値の名> --revision <commit> [--pin 'service=commit,…']
;;;       [--config '{"service": {"鍵": 値}}'] [--only 'service,…'] [--apply URL --actor <送り手>] [--replicas 0|1]
;;;
;;; --pin は service ごとに commit を差し替える(1 つだけ新しい commit にする時に使う。他の行は同じ形のまま = 止まらない)。
;;; --apply を付けると、Service ごとに資源の口で書く(2026-09-24 から。旧い --put は同じ意味の別名):
;;;   無ければ POST /resources/Service で作る(所有者 = --actor)。在れば GET で読んだ resourceVersion を付けて PUT する
;;;   (読んでから書くまでに誰かが書いていれば 409 で止まる — 他の作業係の変更を消さない)。所有者と replicas はいまの値を保つ
;;;   (replicas は Rollout が持つ。--replicas を付けた時だけ変える)。一覧に無い Service には触らない。
;;; 付けなければ JSON を印字するだけ。
(import argparse)
(import json)
(import sys)
(import urllib.parse [quote :as url-quote])
(import .service_model [resolve system-declaration])


(defn #^ list pin-revisions [#^ list jobs #^ dict pins]
  "純粋: 名指した service の行だけ commit を差し替える。"
  (lfor job jobs (if (in (get job "name") pins) (| job {"revision" (get pins (get job "name"))}) job)))


(defn #^ dict spec-for-update [#^ dict row #^ dict current [replicas None]]
  "純粋: 宣言の行 → PUT の spec。所有者と replicas と readiness の無い行の readiness はいまの資源の値を保つ。
   base(土台の commit)は追随の係が持つ欄なので、baseFrom を持つ行ではいまの値を保つ(coordinator の側でも保つ — 二重の守り)。"
  (setv spec (dfor #(k v) (.items row) :if (!= k "name") k v))
  (| {"readiness" (.get current "readiness")}
     (if (and (.get spec "baseFrom") (.get current "base")) {"base" (get current "base")} {})
     spec
     {"owner" (get current "owner")
      "replicas" (if (is replicas None) (.get current "replicas" 1) replicas)}))


(defn apply-declaration [#^ str url #^ list jobs #^ str actor [replicas None]]
  (import httpx)
  (setv client (httpx.Client :base-url (.rstrip url "/") :headers {"X-Actor" actor} :trust-env False :timeout 10))
  (setv failed False)
  (for [row jobs]
    (setv name (get row "name") path (+ "/resources/Service/" (url-quote name :safe "")))
    (setv current (.get client path))
    (cond
      (= current.status-code 404)
        (setv response (.post client "/resources/Service"
                              :json {"name" name
                                     "spec" (| (dfor #(k v) (.items row) :if (!= k "name") k v)
                                               {"replicas" (if (is replicas None) 1 replicas)})}))
      True
        (do (.raise-for-status current)
            (setv body (.json current))
            (setv response (.put client path :json {"resourceVersion" (get body "resourceVersion")
                                                    "spec" (spec-for-update row (get body "spec") replicas)}))))
    (print (.format "{}: {} {}" name response.status-code (cut response.text 0 300)))
    (when (>= response.status-code 300) (setv failed True)))
  (when failed (sys.exit 1)))


(defn main []
  (setv parser (argparse.ArgumentParser :description "系の宣言(System の値)→ coordinator の宣言"))
  (.add-argument parser "system" :help "module:attr(System の値の名)")
  (.add-argument parser "--revision" :required True)
  (.add-argument parser "--pin" :default "")
  (.add-argument parser "--config" :default "{}")
  (.add-argument parser "--only" :default "" :help "この service だけ(`,` で並べる)")
  (.add-argument parser "--apply" "--put" :dest "apply")
  (.add-argument parser "--actor" :help "送り手(依頼の主体の id・作業係の名)。--apply に要る")
  (.add-argument parser "--replicas" :type int :choices [0 1])
  (setv args (.parse-args parser))
  (setv pins (dict (gfor kv (.split args.pin ",") :if kv (.split kv "=" 1)))
        only (sfor n (.split args.only ",") :if n n))
  (setv jobs (lfor job (pin-revisions (system-declaration (resolve args.system) args.revision (json.loads args.config)) pins)
                   :if (or (not only) (in (get job "name") only))
                   job))
  (cond
    args.apply
      (do (when (not args.actor) (.error parser "--apply には --actor(送り手)が要る"))
          (apply-declaration args.apply jobs args.actor args.replicas))
    True (print (json.dumps {"jobs" jobs} :ensure-ascii False :indent 1))))


(when (= __name__ "__main__")
  (main))
