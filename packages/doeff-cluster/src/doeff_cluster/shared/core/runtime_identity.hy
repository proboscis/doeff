;;; 入口の検め — この process が「宣言した実行環境(RuntimeEnv)の root から、宣言の commit の code を import している」ことを確かめる。
;;;
;;; 何のため: task を送る service が宣言と違う版で動いたまま task を送ると、送った task は宣言の版で走り、送り手と task の版が黙って
;;; 食い違う。起動の時にこの検めが一致を答えない限り、送り手は起きない(worker の入口の検め〔job_entry の probe〕が起こす前に止めるのと
;;; 同じ役を、起きた後の process の側で持つ)。宣言と完成の印の形式はこの package の物なので、判断もこの package に置く(使い手は
;;; 確かめる module の一覧だけを渡す)。
;;;
;;; 一致の根拠(2 段):
;;;   1. キーの 3 点が揃う — 渡された宣言(DOEFF_RUNTIME_ENV)と root の完成の印(env_prepare が最後に置く `.doeff-env-ready.json`)の
;;;      platform から env-key で計算し直したキー・渡されたキー(DOEFF_RUNTIME_ENV_KEY)・印のキー。キーの材料は root の中身を決める欄
;;;      (repo と commit・project・import の根)だけなので、env-vars や tools だけ違う宣言が同じ root を使い回しても不一致にしない
;;;      (丸ごとの等しさで比べない)。印は「この root の木はキーの宣言を展開した物」を言う。
;;;   2. 確かめる module ごとに、import した file がその root の中の宣言の repo の dir(`<root>/<repo の名>/`)の下に在り、project の
;;;      venv(`.venv` — 第三者の package の置き場)の中ではない — image の venv・別の checkout・venv に残った古い wheel から
;;;      import していない(env_handlers の PROBE-PROGRAM と同じ規則)。
;;;
;;; 失敗の kind(IdentityFailureKind):
;;;   undeclared           宣言が無い(worker の env の root の外 — 例: image の venv で起こした)
;;;   root-unmarked        venv の上に完成の印を持つ root が無い
;;;   marker-mismatch      印の形式を読めない・キーの 3 点が揃わない
;;;   module-outside-root  module の file が root の中の宣言の repo の下に無い・venv の中・import できない
;;; 宣言そのものが読めない(DOEFF_RUNTIME_ENV が壊れた JSON・宣言の型の検査で断られる)のは worker の誤りで、kind ではなく
;;; 例外(RuntimeEnvInvalid・JSONDecodeError)で落ちる(黙って続けないことは同じ)。
;;;
;;; I/O は ReadRuntimeFacts 1 つ(答えは文字列のまま)。handler = shared/protocol/runtime_facts.hy の process-runtime-facts(この process の
;;; 環境変数・sys.prefix・印の file・module の置き場・pid を読む)と、下の given-runtime-facts(渡した材料をそのまま答える — 検と模擬)。
;;; 宣言の型への読み戻しと判断(judge-identity)は Program の側。印の形式の読み戻しはここ(書き手 = env_prepare の env-marker->json)。
;;; 形式の版(ENV-MARKER-FORMAT)が違えば読まずに marker-mismatch で名乗る。
;;;
;;; 置き場(agora-redesign #2344): 判断の Program はここ(shared/core)・型と effect は doeff_cluster.shared.intent.runtime_identity_model・
;;; 渡した材料で答える handler given-runtime-facts は doeff_cluster.shared.protocol.runtime_facts。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "program"})
(import json)
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv runtime-env-of-json env-key])
(import doeff_cluster.shared.intent.runtime_identity_model [IdentityFailureKind ModuleOrigin RootMarker RuntimeFacts RepoCommit
                                                           RuntimeIdentity RuntimeIdentityMismatch ProcessFacts ReadRuntimeFacts])
(import doeff_cluster.env_prepare [ENV-MARKER-FORMAT project-dir])


(defk outside-origins [declared root origins]
  {:pre [(: declared RuntimeEnv) (: root str) (: origins tuple)] :post [(: % tuple)]}
  "宣言の repo の dir の下に無い・project の venv の中・import できない module の置き場(判断 2 段目)。"
  (<- pdir str (project-dir declared root))
  (val venv (+ (.rstrip pdir "/") "/.venv/"))
  (val bases (tuple (gfor r declared.repos (+ (.rstrip root "/") "/" r.name "/"))))
  (tuple (gfor o origins
               :if (not (and o.file
                             (any (gfor b bases (.startswith o.file b)))
                             (not (.startswith o.file venv))))
               o)))


(defk judge-identity [facts pid]
  {:pre [(: facts RuntimeFacts) (: pid int)] :post [(: % (| RuntimeIdentity RuntimeIdentityMismatch))]}
  "材料から一致か不一致かを決める(I/O なし)。pid は答えに載せるだけ。"
  (cond
    (is facts.declared None)
    (RuntimeIdentityMismatch :kind IdentityFailureKind.UNDECLARED :pid pid
                             :detail "実行環境の宣言(DOEFF_RUNTIME_ENV)が無い — 宣言した root の外で起きている")
    (or (not facts.root) (is facts.marker None))
    (RuntimeIdentityMismatch :kind IdentityFailureKind.ROOT-UNMARKED :pid pid
                             :detail "venv の上に完成の印を持つ root が無い")
    (!= facts.marker.format ENV-MARKER-FORMAT)
    (RuntimeIdentityMismatch :kind IdentityFailureKind.MARKER-MISMATCH :pid pid
                             :detail (.format "root {} の印の形式 {} を読めない(読める形式 = {})"
                                              facts.root facts.marker.format ENV-MARKER-FORMAT))
    True
    (do
      (<- computed str (env-key facts.declared facts.marker.platform))
      (cond
        (!= computed facts.key)
        (RuntimeIdentityMismatch :kind IdentityFailureKind.MARKER-MISMATCH :pid pid
                                 :detail (.format "渡されたキー {} が、宣言から計算したキー {} と違う" facts.key computed))
        (!= facts.marker.key computed)
        (RuntimeIdentityMismatch :kind IdentityFailureKind.MARKER-MISMATCH :pid pid
                                 :detail (.format "root {} の印のキー {} が、宣言から計算したキー {} と違う"
                                                  facts.root facts.marker.key computed))
        True
        (do
          (<- outside tuple (outside-origins facts.declared facts.root facts.origins))
          (if outside
              (RuntimeIdentityMismatch :kind IdentityFailureKind.MODULE-OUTSIDE-ROOT :pid pid
                                       :detail (.join "・" (gfor o outside
                                                                 (.format "{} は {}" o.module (or o.file "import できない")))))
              (RuntimeIdentity :key facts.key :root facts.root :pid pid
                               :commits (tuple (gfor r facts.declared.repos
                                                     (RepoCommit :name r.name :commit r.commit))))))))))


(defk decode-env [text]
  {:pre [(: text str)] :post [(: % (| RuntimeEnv None))]}
  "宣言の JSON の文字列 → 宣言(空なら None)。"
  (if text
      (do (<- env RuntimeEnv (runtime-env-of-json (json.loads text))) env)
      None))


(defk decode-marker [text]
  {:pre [(: text str)] :post [(: % (| RootMarker None))]}
  "完成の印の file の中身 → 印(空なら None)。形式の版が違えば宣言は読まない。"
  (if text
      (do (val raw (json.loads text))
          (val version (.get raw "format" 0))
          (if (= version ENV-MARKER-FORMAT)
              (do (<- env RuntimeEnv (runtime-env-of-json (get raw "env")))
                  (RootMarker :format version :key (str (get raw "key")) :platform (str (get raw "platform")) :env env))
              (RootMarker :format (if (isinstance version int) version 0) :key "" :platform "" :env None)))
      None))


(defk check-runtime-identity [modules]
  {:pre [(: modules tuple)] :post [(: % (| RuntimeIdentity RuntimeIdentityMismatch))]}
  "この process の宣言と、modules を import した先の commit の一致を確かめる。modules は宣言の repo ごとに 1 つ以上を含める
   — 含めない repo の module は確かめない。"
  (<- read ProcessFacts (ReadRuntimeFacts modules))
  (<- declared (| RuntimeEnv None) (decode-env read.declared-json))
  (<- marker (| RootMarker None) (decode-marker read.marker-json))
  (val facts (RuntimeFacts :declared declared :key read.key :root read.root :marker marker :origins read.origins))
  (<- verdict (| RuntimeIdentity RuntimeIdentityMismatch) (judge-identity facts read.pid))
  verdict)
