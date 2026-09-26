;;; session host の共有の語彙(sessionhost/effects.hy・sessionhost/policy.hy)は、退役する headless の backend の module を import しない
;;; (agora-redesign #624)。
;;;
;;; 理由: doeff_agents.shell(headless の adapter と session.py が読む)は境界の env の語彙を policy.hy から借り、policy.hy は effects.hy を
;;; 読む。この 2 つが headless の器(headless_protocol.py の状態機械ほか)を import すると、agent の手番を doeff-claude-code で回す経路が、
;;; 削除計画 (b) の手順 5-1(#668)で消える器に依存する。Headless* の effect の語彙の家は sessionhost/headless_effects.hy。
;;; 経路の全体の閉包は agora-controllers の scripts/check_headless_route_imports.hy が見る — ここは code の在る repo の側の見張り。
(require doeff-hy.macros [deftest])

(import pathlib [Path])
(import hy)
(import hy.models [Expression Symbol])

(setv SESSIONHOST (/ (. (Path __file__) (resolve) parent parent) "src" "doeff_agents" "sessionhost"))
(setv SHARED #("effects.hy" "policy.hy"))
;; 退役する headless の backend の module(最後の名で照らす — 絶対の名も相対の名も同じ)。
(setv HEADLESS-BACKEND #{"headless" "substrate_headless" "headless_process" "headless_protocol" "headless_events" "headless_effects"
                          "headless_argv"})


(defn #^ list imported-names [#^ Path path]
  "file の import の式が名指す module の名の最後の部分を、実行せずに reader から拾うため。"
  (setv found [] stack (list (hy.read-many (.read-text path :encoding "utf-8") :filename (str path))))
  (while stack
    (setv form (.pop stack))
    (when (isinstance form Expression)
      (.extend stack form)
      (when (and form (= (get form 0) (Symbol "import")))
        (for [arg (cut form 1 None)]
          ;; `a.b` は `(. a b)`・`.a` は `(. None a)` と読まれる — 式の最後の記号が module の名の最後の部分。
          (setv last (if (isinstance arg Expression) (get arg -1) arg))
          (when (isinstance last Symbol)
            (.append found (get (.split (str last) ".") -1)))))))
  found)


(deftest test-the-shared-vocabulary-does-not-import-the-headless-backend []
  (for [name SHARED]
    (setv hits (lfor m (imported-names (/ SESSIONHOST name)) :if (in m HEADLESS-BACKEND) m))
    (assert (= hits []) f"sessionhost/{name} が退役する headless の backend を import する: {hits}")))

(deftest test-the-reader-sees-a-headless-import-when-there-is-one [tmp-path]
  ;; 反例: 移す前の effects.hy の形(headless_protocol を module の直下で import)は拾われる。
  (setv bad (/ tmp-path "effects.hy"))
  (.write-text bad "(import doeff_agents.sessionhost.headless_protocol [BackendLiveness])\n(import .headless_effects [x])\n")
  (assert (= (sorted (lfor m (imported-names bad) :if (in m HEADLESS-BACKEND) m)) ["headless_effects" "headless_protocol"])))
