;;; session.adopt program(koine session surface v0 stage 1 — ADR-DOE-AGENTS-007)。
;;;
;;; 既に生きている席(pane)の事後登記。observation-only(koine 条項 2):
;;; substrate へ許される接触は実在確認(TmuxHasSession — herdr backend では
;;; substrate_herdr の agent.get に解決される substrate 中立 probe)だけ。
;;; 変異 effect — キー送出・session 作成/破棄・FS 書き・配送 — はこの
;;; モジュールでは semgrep doeff-agents-adopt-must-not-mutate-substrate が
;;; 構造的に禁止する。
;;;
;;; 順序義務(semantics-v0 operations): 実在確認 → 成功時のみ登記。
;;; 失敗した adopt は行を残さない(幻 turn-open の再発防止)。

(require doeff-hy.macros [defk deff <-])

(import uuid)

(import doeff_agents.sessionhost.effects [
  SessionRow
  clock-now
  session-store-list-active
  session-store-record-event
  session-store-upsert
  tmux-has-session])
(import doeff_agents.sessionhost.policy [iso-format])


(defclass AdoptTargetNotFound [Exception]
  "adopt の実在確認が失敗した(typed — host が wire error_code
   \"adopt_target_not_found\" へ写像する)。この例外の時点で行は作られて
   いない(順序義務: 実在確認が登記より先)。")


(deff mint-adopted-session-id []
  {:pre [True]
   :post [(: % str)]}
  "sessionhost 採番の不透明 id(S24)。呼び手の名を埋め込まない — id の
   parse から呼び手規約を復元できないことが契約(semantics-v0 resource 表)。"
  (str (uuid.uuid4)))


(defk adopt-program [params]
  {:pre [(: params dict)
         (: (.get params "session_name") str)
         (: (.get params "substrate_ref") str)
         (: (.get params "agent_kind") str)
         (: (.get params "lifecycle") str)
         (: (.get params "backend_kind") str)]
   :post [(: % SessionRow)]}
  "既に生きている席の事後登記。順序義務: 実在確認 → 登記。冪等: 同一
   substrate.ref(pane_id 列)の非終端行があれば新規作成せず既存行を返す
   (再登記しない — 台帳の二重住民を作らない)。"
  (setv session-name (get params "session_name"))
  (setv substrate-ref (get params "substrate_ref"))
  (setv backend-kind (get params "backend_kind"))

  ;; --- 実在確認(観測のみ)。失敗はここで typed に止まり、行は作られない。
  (<- alive (tmux-has-session session-name))
  (when (not alive)
    (raise (AdoptTargetNotFound
             (+ f"adopt target not found: no live {backend-kind} session "
                f"named '{session-name}' (substrate.ref {substrate-ref !r})"))))

  ;; --- 冪等: 同一 substrate.ref の非終端行は既存行をそのまま返す
  ;; (observation-only — 既存行を書き換えもしない)。
  (<- active-rows (session-store-list-active))
  (for [existing active-rows]
    (when (= existing.pane-id substrate-ref)
      (return existing)))

  ;; --- 登記(実在確認の成功後にのみ到達する)。
  (<- now (clock-now))
  (setv backend-ref {"kind" backend-kind
                     "ref" substrate-ref
                     "session_name" session-name})
  (setv display-name (.get params "name"))
  (when (isinstance display-name str)
    (setv (get backend-ref "name") display-name))
  (setv row (SessionRow
              :session-id (mint-adopted-session-id)
              :session-name session-name
              :pane-id substrate-ref
              :agent-type (get params "agent_kind")
              :lifecycle (get params "lifecycle")
              :status "running"
              :started-at (iso-format now)
              ;; adopt は観測のみ — startup marker(observed_active_at)を
              ;; 決して立てない。この形(running + observed_active_at None)が
              ;; launch timeout の発火条件そのものなので、刈り取り免除
              ;; (adopted=1)が同じ変更セットに必須(S26)。
              :adopted True
              :work-dir (or (.get params "work_dir") "")
              :backend-kind backend-kind
              :backend-ref backend-ref))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event row.session-id "session_adopted" row))
  row)
