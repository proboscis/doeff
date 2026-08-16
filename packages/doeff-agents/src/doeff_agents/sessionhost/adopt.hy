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
;;;
;;; 波 1-S1 改訂(ADR-DOE-AGENTS-007 R2): 任意の conversation_id を受け、
;;; conversation_json({"session_id" …} — ADR-006 union の identity 成分)へ
;;; 書く。冪等は会話を考慮した 3 分岐 — 同会話 = 既存行 / 会話未登記 = 補充
;;; (同一宿りの登記の完成・store の COALESCE first-write-wins が最終防衛)/
;;; 別会話 = 新行の鋳造(下地再利用 = 新会話の新宿り。既存行の会話 identity は
;;; 決して書き換えない — 行は会話の 1 回の宿りの記録)。

(require doeff-hy.macros [defk deff <-])

(import dataclasses [replace])
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
  "既に生きている席の事後登記。順序義務: 実在確認 → 登記。冪等は会話を
   考慮した 3 分岐(ADR-007 R2 波 1-S1 改訂): 同一 substrate.ref の非終端行に
   (a) 同じ会話が登記済み → 既存行をそのまま返す(書かない)
   (b) 会話未登記(NULL)→ 会話を補充して返す(同一宿りの登記の完成)
   (c) 別の会話 → 新行を鋳造(下地再利用 = 新会話の新宿り)。
   conversation_id を運ばない adopt は従来どおり最新の非終端行を返す。"
  (setv session-name (get params "session_name"))
  (setv substrate-ref (get params "substrate_ref"))
  (setv backend-kind (get params "backend_kind"))
  (setv conversation-id (.get params "conversation_id"))
  (setv conversation (if (isinstance conversation-id str)
                         {"session_id" conversation-id}
                         None))

  ;; --- 実在確認(観測のみ)。失敗はここで typed に止まり、行は作られない。
  (<- alive (tmux-has-session session-name))
  (when (not alive)
    (raise (AdoptTargetNotFound
             (+ f"adopt target not found: no live {backend-kind} session "
                f"named '{session-name}' (substrate.ref {substrate-ref !r})"))))

  ;; --- 冪等 3 分岐: 同一 substrate.ref の非終端行を新しい順に見る
  ;; (started_at DESC, session_id ASC — session.list と同じ全順序)。
  (<- active-rows (session-store-list-active))
  (setv pane-rows (lfor r active-rows :if (= r.pane-id substrate-ref) r))
  (setv pane-rows (sorted pane-rows :key (fn [r] r.session-id)))
  (setv pane-rows (sorted pane-rows :key (fn [r] (or r.started-at ""))
                          :reverse True))
  ;; 会話を運ばない adopt: 従来の冪等(最新の非終端行を返す)。
  (when (and pane-rows (is conversation None))
    (return (get pane-rows 0)))
  ;; (a) 同会話の行が既にある → そのまま返す(observation-only)。
  ;; (b) の候補 = 会話未登記(NULL)の最新行、も同じ走査で拾う。
  (setv null-row None)
  (for [r pane-rows]
    (when (and (is-not r.conversation None)
               (= (.get r.conversation "session_id") conversation-id))
      (return r))
    (when (and (is null-row None) (is r.conversation None))
      (setv null-row r)))
  ;; (b) 会話未登記の最新行 → 補充(同一宿りの登記の完成)。merge upsert は
  ;; actor 内の read-modify-write・conversation_json は COALESCE
  ;; first-write-wins なので、競合しても identity が書き換わることは
  ;; 構造的に無い。event は既存語彙 session_conversation_discovered。
  (when (is-not null-row None)
    (setv filled (replace null-row :conversation conversation))
    (<- _ (session-store-upsert filled))
    (<- _ (session-store-record-event
            filled.session-id "session_conversation_discovered" filled))
    (return filled))
  ;; (c) pane-rows が全行別会話 = 下地の再利用(新会話の新宿り)、または
  ;; pane-rows 空 = 初回登記 → どちらも新行の鋳造へ。

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
              ;; 波 1-S1: 会話 identity(呼び手が運んだ時のみ・identity 成分)。
              :conversation conversation
              :work-dir (or (.get params "work_dir") "")
              :backend-kind backend-kind
              :backend-ref backend-ref))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event row.session-id "session_adopted" row))
  row)
