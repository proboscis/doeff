;;; 系(defsystem の関数)から coordinator に渡す宣言を出す(ADR-DOE-CLUSTER-001)。
;;;
;;;   hy -m doeff_cluster.declare <module>:<系の関数> --foundation <module>:<土台の関数> --revision <commit>
;;;       [--only 'job,…'] [--apply URL --actor <送り手>] [--replicas 0|1]
;;;
;;; 系の関数に土台の関数を渡して System の値を作り、job ごとに Program を詰める(service_model.system-declaration)。
;;; 付けなければ宣言の行(と job ごとの describe = 呼んだ関数と引数)を印字するだけ。
;;; --apply を付けると、先に詰めた Program を PUT /programs/<sha> で置き(改訂 1 の F)、次に Service ごとに資源の口で書く:
;;;   無ければ POST /resources/Service で作る(所有者 = --actor)。在れば GET で読んだ resourceVersion を付けて PUT する
;;;   (読んでから書くまでに誰かが書いていれば 409 で止まる — 他の作業係の変更を消さない)。所有者と replicas はいまの値を保つ
;;;   (replicas は Rollout が持つ。--replicas を付けた時だけ変える)。一覧に無い Service には触らない。
;;; 旧い引数(--config・--pin)と、System の値を直に指す旧い形は受け付けない。
(require doeff-hy.macros [deff])
(import argparse)
(import importlib)
(import json)
(import sys)
(import urllib.parse [quote :as url-quote])
(import .service_model [resolve system-declaration System Declaration])


(defn #^ dict spec-for-update [#^ dict row #^ dict current [replicas None]]  ; defk にできない: CLI の入口(Program の外)が呼ぶ純粋な判断
  "宣言の行 → PUT の spec。所有者と replicas と readiness の無い行の readiness はいまの資源の値を保つ。"
  (setv spec (dfor #(k v) (.items row) :if (!= k "name") k v))
  (| {"readiness" (.get current "readiness")}
     spec
     {"owner" (.get current "owner")
      "replicas" (if (is replicas None) (.get current "replicas" 1) replicas)}))


(deff create-body [#^ dict row #^ (| int None) replicas]  ; defk にできない: CLI の入口(Program の外)と sim-cluster の宣言が同じ形を作る純粋な判断
  {:pre [(: row dict) (: replicas (| int None))] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "まだ無い Service を作る POST /resources/Service の本文を作るため(declare の CLI と手元の sim-cluster で同じ形)。replicas を付けなければ 1。"
  {"name" (get row "name")
   "spec" (| (dfor #(k v) (.items row) :if (!= k "name") k v)
             {"replicas" (if (is replicas None) 1 replicas)})})


(defn apply-declaration [#^ str url #^ Declaration declaration #^ str actor [replicas None]]  ; defk にできない: CLI の入口の HTTP の I/O
  "詰めた Program を置いてから、宣言の行を資源の口で書く(どれかが失敗したら 1 で終わる)。"
  (import httpx)
  (setv client (httpx.Client :base-url (.rstrip url "/") :headers {"X-Actor" actor} :trust-env False :timeout 30))
  (setv failed False)
  (for [#(sha blob) (sorted (.items declaration.programs))]
    (setv response (.put client (+ "/programs/" sha)
                         :json {"blob" blob
                                "versions" (get (get (get declaration.rows 0) "run") "versions")}))
    (print (.format "program {}: {}" (cut sha 0 12) response.status-code))
    (when (>= response.status-code 300) (setv failed True)))
  (when failed (sys.exit 1))
  (for [row declaration.rows]
    (setv name (get row "name") path (+ "/resources/Service/" (url-quote name :safe "")))
    (setv current (.get client path))
    (cond
      (= current.status-code 404)
        (setv response (.post client "/resources/Service" :json (create-body row replicas)))
      True
        (do (.raise-for-status current)
            (setv body (.json current))
            (setv response (.put client path :json {"resourceVersion" (get body "resourceVersion")
                                                    "spec" (spec-for-update row (get body "spec") replicas)}))))
    (print (.format "{}: {} {}" name response.status-code (cut response.text 0 300)))
    (when (>= response.status-code 300) (setv failed True)))
  (when failed (sys.exit 1)))


(defn main []  ; defk にできない: CLI の入口
  "宣言の CLI。旧い引数は理由つきで断る。"
  (setv parser (argparse.ArgumentParser :description "系(defsystem の関数)→ coordinator の宣言"))
  (.add-argument parser "system" :help "module:attr(defsystem の関数の名)")
  (.add-argument parser "--foundation" :required True :help "module:attr(土台の関数 — 本体の Program を受けて自分の handler の下で走らせる module の最上位の関数)")
  (.add-argument parser "--revision" :required True)
  (.add-argument parser "--only" :default "" :help "この job だけ(`,` で並べる)")
  (.add-argument parser "--apply" "--put" :dest "apply")
  (.add-argument parser "--actor" :help "送り手(依頼の主体の id・作業係の名)。--apply に要る")
  (.add-argument parser "--replicas" :type int :choices [0 1])
  (.add-argument parser "--config" :default None :help "受け付けない(旧い形 — 設定は Program の中の Ask と :environ で読む)")
  (.add-argument parser "--pin" :default None :help "受け付けない(旧い形 — Program を詰めた commit と別の commit で解くことになる)")
  (setv args (.parse-args parser))
  (when (is-not args.config None)
    (.error parser "--config は受け付けない — 設定は Program の中の Ask と、宣言の :environ で読む(ADR-DOE-CLUSTER-001 R4)"))
  (when (is-not args.pin None)
    (.error parser "--pin は受け付けない — Program の job は宣言した commit でだけ解く"))
  ;; System の値を指す旧い形は、関数に解く(resolve の契約 = Callable)前に見て、理由つきで断る。
  (setv #(module _ attr) (.partition args.system ":"))
  (when (and attr (isinstance (getattr (importlib.import-module module) attr None) System))
    (.error parser "系の値ではなく、defsystem の関数(土台を受けて系を返す)を指す"))
  (setv build (resolve args.system))
  (setv system (build (resolve args.foundation))
        only (sfor n (.split args.only ",") :if n n)
        declaration (system-declaration system args.revision)
        rows (lfor row declaration.rows :if (or (not only) (in (get row "name") only)) row)
        declaration (Declaration :rows rows
                                 :programs (dfor row rows :setv sha (get (get row "run") "program")
                                                 sha (get declaration.programs sha))))
  (cond
    args.apply
      (do (when (not args.actor) (.error parser "--apply には --actor(送り手)が要る"))
          (apply-declaration args.apply declaration args.actor args.replicas))
    True (do (for [row rows]
               (print (.format "{}: {}" (get row "name") (get (get row "run") "describe")) :file sys.stderr))
             (print (json.dumps {"jobs" rows} :ensure-ascii False :indent 1)))))


(when (= __name__ "__main__")
  (main))
