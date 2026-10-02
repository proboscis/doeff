;;; worker の入口の検め(probe)の判断 — 検めの本体の program・束の並べ方・束の結果の読みと spec ごとの行き先・検めの子の起こし方
;;; (handlers.hy の ProbeStore から分けた・#2465)。I/O は呼び手(ProbeStore — 後に worker/protocol の言い換え)が行う。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])
(import enum [Enum])
(import json)
(import doeff_core_effects.process_effects [EnvEntry EnvMode])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeLayout])
(import doeff_cluster.worker.core.launch [JobLaunch child-environment env-project-dir])


;; 入口の検め 1 回(同じ木の束 1 本)の時間の上限(2026-09-27 に 60 → 300)。上限は import の速さを測る物ではなく、import の途中で
;; 固まった入口(module の直下の待ち等)を止めるための物。bytecode の無い冷えた root では、込んでいない Pod でも doeff と業務の Hy の
;; compile に壁時計 60 秒前後かかる(本番の実測 = CPU 60 秒)ので、60 秒は冷えた root で必ず切れて撃ち直しを繰り返した。検めは木ごとに
;; 1 本(ProbeStore)・時間切れは process group ごと止める(孫を残さない)ので、上限を広げても重なって積み上がらない。
(setv PROBE-SECONDS 300)


(setv PROBE-DETAIL-CHARS 480)


;; 検めの process を包む shim の猶予(秒)。worker が消えた時(kill -9 を含む)に shim が検めの group を止める — job の子と同じ仕組み。
(setv PROBE-STOP-GRACE "5")


(defk probe-reason [code stderr]
  {:pre [(: code int) (: stderr str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "検めの process の終了から、状態に出す理由の 1 行を決めるため(stderr の最後の空でない行。無ければ終了の番号)。"
  (val lines (lfor line (.splitlines stderr) :if (.strip line) (.strip line)))
  (cut (if lines (get lines -1) f"入口の検めが終了 {code} で終わった(理由の出力なし)") 0 PROBE-DETAIL-CHARS))


;; 検めの本体(木の中の道具に依らない — 木の job_entry に probe の口が無い古い commit も同じく検められる)。引数 = 検める import path の列
;; (「module」か「module:attr」)。各々を import し attr の在否を見る(同じ木の束の対象を 1 つの process で — 2026-09-27)。
;; 出力 = 対象ごとに、終わった時点で 1 行 `<PROBE-MARK>["対象", 読み込めない理由 | null]` を flush で出す(時間切れで止めても、それまでに
;; 終わった対象の結果が残る — 固まった入口 1 つが束の全部を道連れにしない)。行の前に改行を 1 つ置く(import した module が改行なしで
;; 標準出力に書いても行が壊れない)。どれが読めなくても 0 で終わる(理由は対象ごとの行が運ぶ)。
(setv PROBE-MARK "doeff-probe-result: ")


(setv PROBE-PROGRAM (.join "\n" [
  "(import importlib json sys)"
  "(for [path (cut sys.argv 1 None)]"
  "  (setv #(module _ attr) (.partition path \":\"))"
  "  (setv reason None)"
  "  (try (setv m (importlib.import-module module)) (when attr (getattr m attr))"
  "    (except [e Exception]"
  "      (setv reason (.format \"{} を読み込めない: {}: {}\" path (. (type e) __name__) (.join \" \" (.split (str e)))))))"
  (+ "  (.write sys.stdout (+ \"\\n\" \"" PROBE-MARK "\" (json.dumps [path reason] :ensure-ascii False) \"\\n\"))")
  "  (.flush sys.stdout))"]))


(defn #^ list probe-targets [#^ JobSpec spec]
  "検める import path の列: 入口の module(spec.entry)だけ(Program の job は関数の参照を持たない — 版と復元は起こした子が検める)。"
  [spec.entry])


(defk probe-launches [waiting busy]
  {:pre [(: waiting list) (: busy frozenset)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "今起こす検めの束を選ぶため。待っている検めの束の鍵(#(木 実行環境の宣言の JSON か None 単独の spec-hash か None) — 来た順)と、検めの process が走っている木
   → 今起こす束の鍵(木ごとに 1 つ・来た順)。同じ木(root)の検めは import の閉包の大半が同じなので、並べると同じ compile を本数ぶん
   撃つ(2026-09-27 の本番: 7 本が並んで CPU の上限 4 の Pod を締め付けた)— 1 本ずつにし、同じ拍に来た物は 1 本の束にまとめる。
   単独の鍵(3 つ目が spec-hash)= 直前に時間切れになった spec(束に混ぜず 1 本で検める — 固まる入口が次の束を道連れにしない)。"
  ;; 木ごとに来た順で最初の鍵(後ろから入れて、前の鍵が上書きで残る)。
  (val first-of-tree (dfor key (reversed waiting) (get key 0) key))
  (tuple (gfor key waiting :if (and (not-in (get key 0) busy) (= (get first-of-tree (get key 0)) key)) key)))


(defclass ProbeSettle [Enum]
  ;; 束が終わった時の spec 1 つの行き先: 結果どおりに決まる・固まった対象を持つので時間切れ・結果が出る前に束が止まったので待ちへ戻す。
  (setv DECIDED "decided" TIMED-OUT "timed-out" REQUEUE "requeue"))


(defrecord ProbeSettled
  "spec 1 つの行き先と、決まった時の理由(DECIDED で None = 読み込めた)。"
  (#^ ProbeSettle kind)
  (setv #^ (| str None) reason None))


(defn #^ (| str None) probe-verdict [#^ list targets #^ dict results]  ; defk にできない: ProbeStore(Program の外の I/O の道具)が呼ぶ純粋な判断
  "純粋: spec の検める対象と、束の結果(対象 → 読み込めない理由か None)→ 最初に読み込めなかった対象の理由(全部読めたら None)。
   結果に無い対象は、検めの本体が答えなかった物として読み込めない扱いにする。"
  (for [target targets]
    (cond
      (not-in target results) (return (.format "{} の検めの答えが無い" target))
      (is-not (get results target) None) (return (cut (str (get results target)) 0 PROBE-DETAIL-CHARS))))
  None)


(defn #^ ProbeSettled probe-settle [#^ list targets #^ dict results #^ bool timed-out #^ (| str None) stuck #^ (| str None) crash]  ; defk にできない: ProbeStore(Program の外の I/O の道具)が呼ぶ純粋な判断
  "純粋: 束が終わった後の spec 1 つの行き先。targets = spec の対象・results = 束が出した対象ごとの結果・timed-out = 時間切れで止めた・
   stuck = 時間切れの時に進んでいた対象(束の順で結果の無い最初の物)・crash = 本体が 0 以外で終わった時の理由(0 で終わったら None)。
   対象の結果が全部出ていれば結果どおり。時間切れなら、進んでいた対象を持つ spec だけ時間切れ、ほかは待ちへ戻す。0 以外で終わった束の
   結果の欠けた spec はその理由で失敗。"
  (cond
    (all (gfor t targets (in t results))) (ProbeSettled :kind ProbeSettle.DECIDED :reason (probe-verdict targets results))
    (and timed-out (in stuck targets)) (ProbeSettled :kind ProbeSettle.TIMED-OUT)
    timed-out (ProbeSettled :kind ProbeSettle.REQUEUE)
    (is-not crash None) (ProbeSettled :kind ProbeSettle.DECIDED :reason crash)
    True (ProbeSettled :kind ProbeSettle.DECIDED :reason (probe-verdict targets results))))


(defk probe-results [stdout]
  {:pre [(: stdout str)] :post [(: % dict)] :tags {:context "worker" :role "judgment"}}
  "検めの process の標準出力から対象ごとの結果を読むため(PROBE-MARK で始まる行・途中で止めた束は、それまでに終わった対象だけ)。"
  (var results {})
  (for [line (.splitlines stdout)]
    (val body (.strip line))
    (when (.startswith body PROBE-MARK)
      ;; 読めない行(途中で切れた JSON)は飛ばす。
      (val pair (try (json.loads (cut body (len PROBE-MARK) None)) (except [ValueError] None)))
      (when (and (isinstance pair list) (= (len pair) 2) (isinstance (get pair 0) str))
        (:= results (| results {(get pair 0) (get pair 1)})))))
  results)


(defk probe-command [code-path runtime-env targets * hy-command uv layout allowed-env probe-dir]
  {:pre [(: code-path str) (: runtime-env (| str None)) (: targets tuple) (: hy-command str) (: uv str) (: layout CodeLayout)
         (: allowed-env dict) (: probe-dir str)]
   :post [(: % JobLaunch)] :tags {:context "worker" :role "judgment"}}
  "検めの子(shim の下で起こす本体)の起こし方を、渡された値だけから決めるため。実行環境の job は子と同じ root の venv の
   `uv run --no-sync --frozen --project <root の project> hy -c …`・環境変数は子と同じ許可表(allowed-env = worker の環境のうち許可表の名と
   LC_* の分)と宣言の env-vars・PYTHONPATH を置かない・cwd = probe-dir(REPLACE)。それ以外は worker の hy と木の PYTHONPATH を worker の環境の
   上に足す(EXTEND)・cwd = 木。shim の命令の並びは呼び手が前に足す。"
  (if runtime-env
      (do (val declared (json.loads runtime-env))
          (<- child-env dict (child-environment allowed-env {} (dfor v (.get declared "envVars" []) (get v "name") (get v "value")) {}))
          (<- project-dir str (env-project-dir code-path declared))
          (JobLaunch :argv (+ #(uv "run" "--no-sync" "--frozen" "--project" project-dir "hy" "-c" PROBE-PROGRAM) targets)
                     :cwd probe-dir
                     :env (tuple (gfor k (sorted child-env) (EnvEntry :name k :value (get child-env k))))
                     :env-mode EnvMode.REPLACE :work-dir None :last-used None))
      (JobLaunch :argv (+ #(hy-command "-c" PROBE-PROGRAM) targets)
                 :cwd code-path
                 :env #((EnvEntry :name "PYTHONPATH" :value (.pythonpath layout code-path)))
                 :env-mode EnvMode.EXTEND :work-dir None :last-used None)))
