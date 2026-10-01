;;; 系(defsystem の関数)から coordinator に渡す宣言を出す(ADR-DOE-CLUSTER-001)。
;;;
;;;   hy -m doeff_cluster.shared.entry.declare <module>:<系の関数> --foundation <module>:<土台の関数> --revision <commit>
;;;       [--only 'job,…'] [--environ FILE] [--apply URL --actor <送り手>] [--replicas 0|1]
;;;
;;; 系の関数に土台の関数を渡して System の値を作り、job ごとに Program を詰める(service_model.system-declaration)。
;;; 宣言の前に 2 つを検め、外れれば理由つきで終了 2(argparse の error と同じ — 計画 2.2 の E・9 節の P):
;;;   - 系の関数の module の在る git の checkout が汚れておらず push 済みで、HEAD が --revision と同じ commit(詰める Program が参照する
;;;     code と、実行先が --revision で展開する code を一致させる — runtime_env.checked-declaring-checkout。checkout の読みは effect で、
;;;     答えるのは runtime_env の翻訳の handler checkout-reads と汎用の子 process の handler)
;;;   - 土台の関数の頭の :needs(__doeff_needs__)が各 job の :needs の一部(service_model.foundation-needs-refusal。土台が :needs を
;;;     名乗らなければ検めない)
;;; 付けなければ宣言の行(と job ごとの describe = 呼んだ関数と引数)を印字するだけ。
;;; --apply を付けると、先に詰めた Program を PUT /programs/<sha> で置き(改訂 1 の F)、次に Service ごとに資源の口で書く:
;;;   無ければ POST /resources/Service で作る(所有者 = --actor)。在れば GET で読んだ resourceVersion を付けて PUT する
;;;   (読んでから書くまでに誰かが書いていれば 409 で止まる — 他の作業係の変更を消さない)。所有者と replicas はいまの値を保つ
;;;   (replicas は Rollout が持つ。--replicas を付けた時だけ変える)。一覧に無い Service には触らない。
;;; 旧い引数(--config・--pin)と、System の値を直に指す旧い形は受け付けない。
;;;
;;; 置き場(agora-redesign #2346): CLI と apply はここ(shared/entry・役 main)・要求の本文の形は doeff_cluster.shared.protocol.declaration_requests・
;;; 宣言してよいかの判断は doeff_cluster.shared.core.declaring。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
(import argparse)
(import json)
(import sys)
(import urllib.parse [quote :as url-quote])
(import doeff [run with_handlers])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.foundation.process_versions [current-versions])
(import doeff_cluster.shared.protocol.checkout_reads [checkout-reads])
(import doeff_cluster.shared.protocol.declaration_requests [spec-for-update create-body])
(import doeff_cluster.shared.core.declaring [declaring-refusal])
(import doeff_cluster.shared.intent.service_model [resolve resolve-value system-declaration environ-overlay-refusal System Declaration])


(defn #^ None apply-declaration [#^ str url #^ Declaration declaration #^ str actor #^ (| int None) [replicas None]]  ; defk にできない: CLI の入口の HTTP の I/O
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


(defn #^ None main []  ; defk にできない: CLI の入口
  "宣言の CLI。旧い引数は理由つきで断る。"
  (setv parser (argparse.ArgumentParser :description "系(defsystem の関数)→ coordinator の宣言"))
  (.add-argument parser "system" :help "module:attr(defsystem の関数の名)")
  (.add-argument parser "--foundation" :required True :help "module:attr(土台の関数 — 本体の Program を受けて自分の handler の下で走らせる module の最上位の関数)")
  (.add-argument parser "--revision" :required True)
  (.add-argument parser "--only" :default "" :help "この job だけ(`,` で並べる)")
  (.add-argument parser "--apply" "--put" :dest "apply")
  (.add-argument parser "--actor" :help "送り手(依頼の主体の id・作業係の名)。--apply に要る")
  (.add-argument parser "--replicas" :type int :choices [0 1])
  (.add-argument parser "--environ" :default None :help "job ごとの environ の上書きの JSON の file({job: {名: 文字列}} — 宣言の :environ に書いた名の値だけを変える。配る先ごとの口の URL など)")
  (.add-argument parser "--config" :default None :help "受け付けない(旧い形 — 設定は Program の中の Ask と :environ で読む)")
  (.add-argument parser "--pin" :default None :help "受け付けない(旧い形 — Program を詰めた commit と別の commit で解くことになる)")
  (setv args (.parse-args parser))
  (when (is-not args.config None)
    (.error parser "--config は受け付けない — 設定は Program の中の Ask と、宣言の :environ で読む(ADR-DOE-CLUSTER-001 R4)"))
  (when (is-not args.pin None)
    (.error parser "--pin は受け付けない — Program の job は宣言した commit でだけ解く"))
  ;; System の値を指す旧い形は、関数として呼ぶ前に見て、理由つきで断る(名を解くのは resolve の 1 か所 — #1692)。
  (when (and (in ":" args.system) (isinstance (resolve-value args.system) System))
    (.error parser "系の値ではなく、defsystem の関数(土台を受けて系を返す)を指す"))
  (setv build (resolve args.system))
  (setv system (build (resolve args.foundation)))
  ;; 宣言の前の検め(頭の註)。checkout の読みの effect は汎用の子 process(git)へ訳して本物の git で答える。
  (match (run (scheduled (with_handlers [subprocess-handler checkout-reads]
                           (declaring-refusal build (resolve args.foundation) system args.revision))))
    None None
    reason (.error parser reason))
  (setv only (sfor n (.split args.only ",") :if n n)
        overlay (if args.environ (with [f (open args.environ :encoding "utf-8")] (json.load f)) {})
        refusal (environ-overlay-refusal system overlay)
        _ (when (is-not refusal None) (.error parser refusal))
        declaration (system-declaration system args.revision :versions (current-versions) :environ overlay)
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
