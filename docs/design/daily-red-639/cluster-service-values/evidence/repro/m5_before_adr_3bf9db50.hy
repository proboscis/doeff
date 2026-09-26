;;; doeff worker の業務コード(service と task)は file・lock・socket を直に触らず、cluster を意識した effect だけで読み書きする。
;;; 実行(repo の root で): pytest controllers/worker/adr/defadr_worker_business_code_touches_io_only_through_effects.hy -q
;;; 規則の本体 = repo の root の .semgrep.yaml(worker-… の 4 規則)と、service の本体の検査(下の service-bodies-in — Hy の reader で読む)。
;;; defsemgrep は規則ごとに悪い例が赤・良い例が緑を確かめ、deftest は (1) 悪い例 1 つずつが狙いの規則に当たること(1 回の semgrep で
;;; まとめて)、(2) 実際の検体のコードに違反が 0 件なこと、(3) service の本体の検査が本物の形の本体に当たり、repo の本体に 0 件なことを確かめる。
(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest defk <-])
(import doeff-adr.macros [fact interpretation counterexample])
(import dataclasses [dataclass])
(import json)
(import re)
(import shutil)
(import subprocess)
(import tempfile)
(import pathlib [Path])
(import hy)
(import hy.models [Expression Sequence String Symbol])

(setv REPO-ROOT (get (. (.resolve (Path __file__)) parents) 3))
(setv CONFIG (/ REPO-ROOT ".semgrep.yaml"))
(setv RULE-PREFIX "worker-")


(defn semgrep-findings [root targets]
  "root の下の targets に repo の規則をかけ、[(path 規則 id)] を返す(worker- の規則だけ)。"
  (setv semgrep (shutil.which "semgrep"))
  (assert (is-not semgrep None) "semgrep が PATH に無い")
  (setv proc (subprocess.run [semgrep "--metrics=off" "--disable-version-check" "--quiet" "--json"
                              "--project-root" (str root) "--config" (str CONFIG) #* targets]
                             :cwd (str root) :capture-output True :text True))
  (assert (in proc.returncode #(0 1)) (+ "semgrep が走らなかった: " proc.stderr))
  (setv payload (json.loads proc.stdout))
  (lfor r (get payload "results")
        :setv rule (get (.split (get r "check_id") ".") -1)
        :if (.startswith rule RULE-PREFIX)
        #((get r "path") rule)))


;; 悪い例: 1 file = 1 つの違反の形。期待の規則(接頭辞 worker-business-code- / worker- を外した名)に当たらねばならない。
(setv BAD-EXAMPLES
  [#("controllers/worker/lab/bad_open.hy" "touches-files-only-through-effects"
     "(defk keep [x]\n  {:pre [(: x str)] :post [(: % int)]}\n  (with [f (open \"/tmp/x\" \"w\")] (.write f x)))\n")
   #("controllers/worker/lab/bad_path_method.hy" "touches-files-only-through-effects"
     "(defn load [p] (.read-text p))\n")
   #("controllers/worker/lab/bad_import_os.hy" "touches-files-only-through-effects"
     "(import os)\n(defn gone [p] (os.remove p))\n")
   #("controllers/worker/lab/bad_python_file.py" "touches-files-only-through-effects"
     "from pathlib import Path\n")
   #("controllers/worker/lab/bad_local_text.hy" "touches-files-only-through-effects"
     "(import controllers.transport.file_model [ReadLocalText])\n")
   #("controllers/worker/lab/bad_env.hy" "touches-files-only-through-effects"
     "(defn where [] (os.environ.get \"HOST\"))\n")
   #("controllers/worker/lab/bad_threading.hy" "locks-only-through-scheduler-semaphore"
     "(import threading [Lock])\n")
   #("controllers/worker/lab/bad_lock_call.hy" "locks-only-through-scheduler-semaphore"
     "(setv guard (Lock))\n")
   #("controllers/worker/lab/bad_raw_semaphore.hy" "locks-only-through-scheduler-semaphore"
     "(defn handle [] (Semaphore 7))\n")
   #("controllers/worker/lab/bad_python_lock.py" "locks-only-through-scheduler-semaphore"
     "from multiprocessing import Lock\n")
   #("controllers/worker/lab/bad_fcntl.hy" "locks-only-through-scheduler-semaphore"
     "(import json fcntl)\n")
   #("controllers/worker/lab/bad_socket.hy" "talks-to-the-network-only-through-effects"
     "(import socket)\n")
   #("controllers/worker/lab/bad_httpx.hy" "talks-to-the-network-only-through-effects"
     "(import httpx)\n")
   #("controllers/worker/lab/bad_python_subprocess.py" "talks-to-the-network-only-through-effects"
     "import subprocess\n")
   #("controllers/worker/lab/bad_handler_import.hy" "talks-to-the-network-only-through-effects"
     "(import doeff_cluster.shared_handlers [shared-http])\n")
   #("controllers/worker/lab/bad_client.hy" "talks-to-the-network-only-through-effects"
     "(defn make [url] (SharedClient url))\n")
   #("controllers/worker/lab/bad_dunder_import.hy" "does-not-bypass-the-import-rules"
     "(defn sock [] ((. (__import__ \"socket\") socket)))\n")
   #("controllers/worker/lab/bad_hy_inline.hy" "does-not-bypass-the-import-rules"
     "(defn sock [] (hy.I.socket.socket))\n")])

;; 良い例: どの worker- の規則にも当たってはならない(業務コードの effect・handler と composition root の実 I/O・範囲外)。
(setv GOOD-EXAMPLES
  [#("controllers/worker/lab/good_service.hy"
     (+ "(require doeff-hy.macros [defk <-])\n"
        "(import doeff_cluster.service_model [service])\n"
        "(import doeff_cluster.shared_model [ReadShared WriteShared])\n"
        "(import doeff_cluster.semaphore_model [CreateNamedSemaphore])\n"
        "(import doeff_core_effects.scheduler [AcquireSemaphore ReleaseSemaphore])\n"
        "(import acp_client.runtime.effects [OpenListing])\n"
        "(defk keeper-program []\n"
        "  {:pre [] :post [(: % int)]}\n"
        "  (<- sem (CreateNamedSemaphore \"turn-lock\"))\n"
        "  (<- (AcquireSemaphore sem))\n"
        "  (<- rows dict (ReadShared \"turn/\"))\n"
        "  (<- ok bool (WriteShared \"turn/c1/0\" {\"state\" \"queued\"} :expect None))\n"
        "  (<- listing str (OpenListing \"profile\" \"default\"))\n"
        "  (<- (ReleaseSemaphore sem))\n"
        "  (len rows))\n"
        "(setv keeper (service \"keeper\" keeper-program :env \"controllers.worker.lab.envs:board_env\"))\n"))
   #("controllers/worker/lab/good_names.hy"
     ;; open- や Lock を含むだけの別の名前は当たらない。
     "(defn open-listing [x] x)\n(setv ClusterSemaphoreName \"x\" block-size 3)\n(defn opened [] (.replace \"a\" \"a\" \"b\"))\n")
   #("controllers/worker/lab/store_handlers.hy"
     "(import httpx)\n(import threading)\n(defn read [p] (with [f (open p)] (.read f)))\n")
   #("controllers/worker/lab/envs.hy"
     "(import os)\n(import doeff_cluster.shared_handlers [shared-http SharedClient])\n(defn env [ctx] [(shared-http (SharedClient ctx.url))])\n")
   #("controllers/worker/lab/probe_main.hy"
     "(import socket)\n(import tempfile)\n(defn main [] (open \"/tmp/x\") (socket.socket) (__import__ \"os\"))\n")
   #("controllers/worker/handlers.hy"
     "(import subprocess)\n(defn start [argv] (subprocess.Popen argv))\n")
   #("controllers/other/io.hy"
     ;; lab の外の file 操作は semgrep の規則の範囲外(service の本体は下の service の本体の検査が見る)。
     (+ "(defn read [p] (open p))\n"
        "(defk keeper-program [interval]\n"
        "  {:pre [(: interval float)] :post [(: % int)]}\n"
        "  (<- rows dict (ReadShared \"k\"))\n"
        "  0)\n"
        "(setv keeper (service \"keeper\" keeper-program :env \"m:e\"))\n"))])


(defn write-tree [root examples]
  (for [example examples]
    (setv path (/ root (get example 0)))
    (.mkdir path.parent :parents True :exist-ok True)
    (.write-text path (get example -1) :encoding "utf-8")))


;; service の本体 = service の宣言 (service "名" <関数> …) が名指す、同じ module の最上位の defk。断る語は .semgrep.yaml の
;; worker-business-code-* と同じで、dir を問わずに当てる。semgrep の generic mode は複数行の Lisp の form の終わりを切れず、
;; 契約の map の `(: …)` の閉じ括弧で form が終わったと読んで本体に届かない(2026-09-26 実測 — 以前の (defservice ...) の
;; 規則は契約を持つ本物の service の本体を 1 本も見ていなかった・agora-redesign #639)ので、form の範囲は Hy の reader で取る。
(setv SERVICE-BODY-IO-RE
  (re.compile r"(?x)
    \(open[\s)]
    | \(\.(open|read-text|write-text|read-bytes|write-bytes|unlink|mkdir|rmdir|touch|rename)[\s)]
    | \b(os|pathlib|shutil|tempfile|sqlite3|socket|ssl|select|http|urllib|requests|httpx|aiohttp|subprocess|threading|_thread|multiprocessing|asyncio|queue|fcntl)\.\w
    | \((threading\.|multiprocessing\.|asyncio\.)?(Lock|RLock|Semaphore|BoundedSemaphore|Condition|Barrier)[\s)]
    | \bReadLocalText\b | \b__import__\b | \bimportlib\b | \bhy\.I\."))
(setv SERVICE-DECLARATION-RE (re.compile r"\(service\s+\""))

;; 悪い例は本物の service と同じく契約の map を持つ(以前の規則が取り逃した形)。良い例は service の外の file 操作と、effect だけの本体。
(setv BAD-SERVICE
  (+ "(defk keeper-program [interval]\n"
     "  {:pre [(: interval float)] :post [(: % int)]}\n"
     "  (setv f (open \"/tmp/x\"))\n"
     "  0)\n"
     "(setv keeper (service \"keeper\" keeper-program :env \"m:e\"))\n"))
(setv GOOD-SERVICE
  (+ "(defn read [p] (open p))\n"
     "(defk keeper-program [interval]\n"
     "  {:pre [(: interval float)] :post [(: % int)]}\n"
     "  (<- rows dict (ReadShared \"k\"))\n"
     "  0)\n"
     "(setv keeper (service \"keeper\" keeper-program :env \"m:e\"))\n"))


(defclass [(dataclass :frozen True)] ServiceBodyReport []
  "service の本体の検査の結果。bodies = 読めた本体の数(読み手が壊れて 0 本しか読めない空の検査を見分けるため)。"
  (#^ int bodies)
  (#^ tuple violations))  ; #(#(path 関数の名 語) …)


(defk service-bodies-in [text]
  {:pre [(: text str)] :post [(: % list)]}
  ;; module の本文から service の本体を [#(関数の名 註の行を除いた定義の本文)] で返す — 語の検査が読む範囲を form の境で切るため。
  (setv forms (list (hy.read-many text))
        lines (.splitlines text)
        programs #{}
        todo (list forms))
  (while todo
    (setv form (.pop todo))
    (when (and (isinstance form Expression) (>= (len form) 3) (= (get form 0) (Symbol "service"))
               (isinstance (get form 1) String) (isinstance (get form 2) Symbol))
      (.add programs (str (get form 2))))
    (when (isinstance form Sequence)
      (.extend todo form)))
  (lfor form forms
        :if (and (isinstance form Expression) (> (len form) 1) (= (get form 0) (Symbol "defk"))
                 (in (str (get form 1)) programs))
        #((str (get form 1))
          (.join "\n" (gfor line (cut lines (- form.start-line 1) form.end-line)
                            :if (not (.startswith (.lstrip line) ";"))
                            line)))))


(defk service-body-report [root]
  {:pre [(: root Path)] :post [(: % ServiceBodyReport)]}
  ;; repo の controllers・services(adr の dir を除く)の service の本体に、file・lock・socket の直の使用が無いかを数える。
  (setv bodies 0 violations [])
  (for [base ["controllers" "services"]]
    (when (.is-dir (/ root base))
      (for [p (sorted (.rglob (/ root base) "*.hy"))]
        (when (in "adr" (. (.relative-to p root) parts)) (continue))
        (setv text (.read-text p :encoding "utf-8"))
        ;; Hy の reader は遅いので、service の宣言の綴りを持つ file だけを読む(持たない file に service の本体は無い)。
        (when (is (.search SERVICE-DECLARATION-RE text) None) (continue))
        (<- found list (service-bodies-in text))
        (+= bodies (len found))
        (for [#(name body) found]
          (for [m (.finditer SERVICE-BODY-IO-RE body)]
            (.append violations #((str (.relative-to p root)) name (.group m 0))))))))
  (ServiceBodyReport bodies (tuple violations)))


(defadr ADR-WORKER-BUSINESS-CODE-IO-THROUGH-EFFECTS
  :title "doeff worker の業務コードは file・lock・socket を effect を通してだけ触り、読み書きは cluster を意識した effect に限る"
  :status "accepted"
  :scope ["controllers/worker/lab" "controllers/worker/services" ".semgrep.yaml"]
  :problem
    [(fact "RemoteJob は Program を cloudpickle して別の worker へ送る。lock・socket を捕まえた Program は送れず、file を捕まえた Program は読みの file が中身の写しへ黙って替わり、書きの file は受け側で復元できなかった"
           :evidence "controllers/worker/docs/experiment-log.md 限界の表(2026-09-23 実測)")
     (fact "service は worker の障害で別の worker へ移り、Program を最初から走らせ直す。worker の手元の file や process の中の lock は移った先から見えない"
           :evidence "controllers/worker/docs/experiment-log.md 結果の表(kill -9 の移し替え)")
     (fact "operator 指示 2026-09-23 逐語: lock,socket,file must not be touched directly, they must be touched via effect. defsemgrep is needed to forbid that and only allow cluster aware read write must be done")
     (fact "operator 指示 2026-09-23 逐語: if such lock system is needed, we do need to have lock abstracted over effects, and i believe we have it at least for scheduled handler")]
  :context
    [(interpretation "業務コード = service(service の宣言 (service \"名\" <関数> …) が名指す関数の本体)と task(RemoteJob に渡す Program)を書く module。controllers/worker/services/(本番)と lab/(実験)の .hy / .py のうち *handlers.hy・envs.hy・*_main.hy を除いた物")
     (interpretation "cluster を意識した effect = どの worker で走っても同じ意味になる effect。共有の保存(ReadShared / WriteShared の compare-and-set)・scheduler の Semaphore(名前付きなら cluster-semaphore の handler で cluster 全体の lease)・宛先ごとの外への通信の effect(ACP の OpenListing 等)。node の手元の file を読む ReadLocalText は含まない")]
  :decision
    [(rule R1 "業務コードは file(open・pathlib・os・shutil・tempfile・sqlite3 …)を直に触らず、共有の保存の effect で読み書きする。環境変数も worker ごとに違うので Ask で受ける")
     (rule R2 "lock は doeff の scheduler の Semaphore の effect(CreateSemaphore / AcquireSemaphore / ReleaseSemaphore)で扱う。新しい lock の effect の族は作らない。cluster で同じ名前 = 同じ lock にする時だけ、名前の欄を足した CreateSemaphore の子 class(CreateNamedSemaphore)を使い、composition root が cluster-semaphore の handler を scheduled の内側に被せる")
     (rule R3 "外への通信(socket・HTTP・子 process)は宛先ごとの effect で出し、HTTP の client は handler の中に閉じる。業務コードは handler の module も client の class も import しない")
     (rule R4 "実 I/O を書いてよいのは handler の module(*handlers.hy)・composition root(envs.hy・*_main.hy)・worker と coordinator の基盤(controllers/worker 直下)・shim.py だけ")
     (rule R5 "service の本体(service の宣言が名指す、同じ module の最上位の defk)は、どの dir に書かれても同じ語を断る。form の範囲は Hy の reader で取る(generic の semgrep は複数行の form の終わりを切れない)")]
  :laws
    [(law business-code-io-goes-through-effects
       :statement "module ∈ 業務コード ⇒ module に file・lock・socket・子 process・動的な import の直の使用が 0 件"
       :counterexamples [(counterexample "service の本体が (open …) で worker の手元の file に状態を書く — 移った先の worker から見えない")
                         (counterexample "service が threading.Lock で排他する — 別の worker の同じ service とは排他にならず、RemoteJob でも送れない")
                         (counterexample "service が httpx.Client を作って coordinator へ直に書く — handler を差し替えてテストできない")])
     (law service-body-is-io-free-anywhere
       :statement "f = service の宣言 (service \"名\" f …) が名指す最上位の defk ⇒ f の定義の中に file・lock・socket の直の使用が 0 件(dir を問わない)"
       :counterexamples [(counterexample "lab の外の dir に書いた service の本体が (open …) する")
                         (counterexample "契約の map を持つ本体を generic の semgrep の (defservice ...) で囲んで検める — `(: …)` の閉じ括弧で form が終わったと読み、本体に 1 度も届かない空の検査(2026-09-26 実測・agora-redesign #639)")])]
  :enforcement
    [(defsemgrep worker-files-rule
       "worker-business-code-touches-files-only-through-effects"
       [{"relative-path" "controllers/worker/lab/keeper.hy"
         "source" "(defk keep [x]\n  {:pre [(: x str)] :post [(: % int)]}\n  (with [f (open \"/tmp/x\" \"w\")] (.write f x)))\n"}]
       [{"relative-path" "controllers/worker/lab/keeper.hy"
         "source" "(defk keep [x]\n  {:pre [(: x str)] :post [(: % bool)]}\n  (<- ok bool (WriteShared \"k\" x :expect None))\n  ok)\n"}
        {"relative-path" "controllers/worker/lab/store_handlers.hy"
         "source" "(defn read [p] (with [f (open p)] (.read f)))\n"}])
     (defsemgrep worker-locks-rule
       "worker-business-code-locks-only-through-scheduler-semaphore"
       [{"relative-path" "controllers/worker/lab/runner.hy"
         "source" "(import threading)\n(setv guard (threading.Lock))\n"}]
       [{"relative-path" "controllers/worker/lab/runner.hy"
         "source" "(<- sem (CreateNamedSemaphore \"turn-lock\"))\n(<- (AcquireSemaphore sem))\n(<- (ReleaseSemaphore sem))\n"}
        {"relative-path" "controllers/worker/lab/envs.hy"
         "source" "(import threading)\n"}])
     (defsemgrep worker-network-rule
       "worker-business-code-talks-to-the-network-only-through-effects"
       [{"relative-path" "controllers/worker/lab/placer.hy"
         "source" "(import httpx)\n(defn put [url row] (.put (httpx.Client) url :json row))\n"}]
       [{"relative-path" "controllers/worker/lab/placer.hy"
         "source" "(import doeff_cluster.shared_model [WriteShared])\n(defk put [key row]\n  {:pre [(: key str) (: row dict)] :post [(: % bool)]}\n  (<- ok bool (WriteShared key row :expect None))\n  ok)\n"}
        {"relative-path" "controllers/worker/lab/placer_main.hy"
         "source" "(import httpx)\n(import doeff_cluster.shared_handlers [shared-http SharedClient])\n"}])
     (defsemgrep worker-bypass-rule
       "worker-business-code-does-not-bypass-the-import-rules"
       [{"relative-path" "controllers/worker/lab/sneaky.hy"
         "source" "(defn sock [] ((. (__import__ \"socket\") socket)))\n"}]
       [{"relative-path" "controllers/worker/lab/sneaky.hy"
         "source" "(import doeff_cluster.shared_model [ReadShared])\n"}])
     (deftest test-a-service-body-that-touches-a-file-is-red
       ;; 契約の map を持つ本物の形の本体に当たり、service の外の file 操作と effect だけの本体には当たらない。
       (<- bad list (service-bodies-in BAD-SERVICE))
       (assert (= (lfor #(name body) bad m (.finditer SERVICE-BODY-IO-RE body) #(name (.group m 0)))
                  [#("keeper-program" "(open ")]))
       (<- good list (service-bodies-in GOOD-SERVICE))
       (assert (= (lfor #(name _) good name) ["keeper-program"]) "良い例の service の本体を読めていない")
       (assert (= (lfor #(name body) good m (.finditer SERVICE-BODY-IO-RE body) (.group m 0)) [])))
     (deftest test-the-service-bodies-in-this-repo-touch-no-lock-socket-file
       ;; 実際の検体(controllers・services の全体)の service の本体に違反が 0 件。読めた本体が 0 本なら読み手が壊れている。
       (<- report ServiceBodyReport (service-body-report REPO-ROOT))
       (assert (> report.bodies 0) "service の本体を 1 本も読めていない(読み手が壊れた)")
       (assert (= report.violations #()) (+ "service の本体の違反: " (repr report.violations))))
     (deftest test-each-bad-example-hits-its-own-rule-and-no-good-example-hits-any
       ;; 悪い例を 1 file ずつ置き、1 回の semgrep で「どの file もその狙いの規則に当たる」「良い例はどれにも当たらない」を確かめる。
       (with [tmp (tempfile.TemporaryDirectory :prefix "worker-io-rules-")]
         (setv root (Path tmp))
         (write-tree root (+ BAD-EXAMPLES GOOD-EXAMPLES))
         (setv findings (semgrep-findings root ["controllers"])))
       (setv missed (lfor #(path expected _) BAD-EXAMPLES
                          :if (not (any (gfor #(p rule) findings (and (= p path) (.endswith rule expected)))))
                          #(path expected)))
       (assert (= missed []) (+ "悪い例が狙いの規則に当たらない: " (repr missed)))
       (setv good-paths (sfor example GOOD-EXAMPLES (get example 0)))
       (setv false-hits (lfor #(p rule) findings :if (in p good-paths) #(p rule)))
       (assert (= false-hits []) (+ "良い例が規則に当たった: " (repr false-hits))))
     (deftest test-the-worker-code-in-this-repo-has-no-violation
       ;; 実際の検体(controllers/worker と、service を宣言しうる controllers・services の全体)に違反が 0 件。
       (setv findings (semgrep-findings REPO-ROOT ["controllers" "services"]))
       (assert (= findings []) (+ "業務コードの違反: " (repr findings))))]
  :plans ["controllers/worker/README.md"])
