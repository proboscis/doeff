;;; adopted 行 reconciler(ADR-DOE-AGENTS-007 R8/R9 — 幽霊 running 行の根治)。
;;;
;;; 台帳(adopted 非終端行)と substrate の生存実態(TmuxListSessions —
;;; herdr agent.list / tmux list-panes)を突合し、終端 or 改名追随を記帳する
;;; 単一の書き手。経路ごとの命令的な掃きは存在しない — end-state を観測して
;;; 収束させる宣言的 1 本(monitor-cycle の単一掃き取りと同じ骨)。
;;;
;;; 安全の 3 本柱(設計申告 design-sessionhost-adopted-reconciler-2026-08-17):
;;;   1. 一過性の不在を終端に潰さない(D566 の pin): 終端は複数回の観測
;;;      (min-checks)+ 時間窓(window-seconds)の両方を要求し、前提条件は
;;;      store の guarded UPDATE(SQL WHERE)に彫られている。判定できない間の
;;;      倒れ先 = 非終端のまま(読み手の導出は unknown/dormant 側)。
;;;   2. 改名復活(R55 鋳造名化)は「終端」でなく後継への紐づけ
;;;      (superseded + successor_session_id)。同一性照合は会話 ID 第一鍵
;;;      (stamp-never-crosses-identities と同軸)。
;;;   3. 会話の「終了」はどの記帳からも導出しない(R9): 行の終端印は宿り
;;;      (1 回の実行体)の終端であって会話の ended ではない — ended の唯一の
;;;      源は agora の終端簿(D567)。
;;;
;;; 供給断(integration-lead 条件①): 生存一覧の取得失敗・空一覧の周期は
;;; 不在を 1 回も記帳せず周期ごと skip — 観測の不成立 ≠ 不在(波1席の
;;; unknown/dormant 弁別と同語)。一覧は必ず最初の effect — 供給断の周期に
;;; 書き込みが 1 つも起きないことは構造。
;;;
;;; 起動猶予(integration-lead 条件②): allow-vanish は host 側が「条件①を
;;; 通った成功周期を 1 回以上経たか」で決める — daemon 停止中に窓が経過した
;;; 行が再起動後の最初の周期で即 vanish になる形を塞ぐ。
;;;
;;; substrate への変異(kill・キー送出・FS 書き・配送)はこのモジュールでは
;;; semgrep doeff-agents-reconcile-must-not-mutate-substrate が構造的に禁止
;;; する。store への書き込みは guarded UPDATE effect のみ(session-store-
;;; upsert 禁止 — semgrep doeff-agents-reconcile-terminal-only-via-guarded-
;;; updates)。

(require doeff-hy.macros [defk deff <-])

(import datetime [timedelta])

(import doeff_agents.sessionhost.effects [
  SessionRow
  clock-now
  session-store-list-active
  session-store-record-event
  session-store-reconcile-clear-absence
  session-store-reconcile-follow-rename
  session-store-reconcile-mark-absent
  session-store-reconcile-supersede
  session-store-reconcile-vanish
  tmux-list-sessions])
(import doeff_agents.sessionhost.policy [iso-format])


;; 既定 knob(host は env を use-site で読む — 他の watchdog knob と同じ流儀)。
(setv DEFAULT-RECONCILE-INTERVAL-SECONDS 60)
(setv DEFAULT-RECONCILE-ABSENT-WINDOW-SECONDS 900)
(setv DEFAULT-RECONCILE-ABSENT-CHECKS 3)
;; 60s 周期なら checks=3 は 3 分で満ちるため、実質の下限は窓 900s が支配する
;; (= 「15 分 + 供給断でない周期 3 回」— integration-lead 検分 (a) の読み)。


;; ---------------------------------------------------------------------------
;; 純関数面(分類・後継解決)
;; ---------------------------------------------------------------------------

(deff build-live-index [live]
  {:pre [(: live list)]
   :post [(: % dict)]}
  "生存一覧 → 照合 index。by_conv は会話 ID → entry のリスト(複数 = 同一
   会話の多重生存 = 異常として保留に使う)、by_pane は pane → entry
   (herdr は 1 pane = 1 agent)、names は生存名の集合。"
  (setv by-conv {})
  (setv by-pane {})
  (setv names (set))
  (for [entry live]
    (setv conv (.get entry "conversation_id"))
    (when (isinstance conv str)
      (.setdefault by-conv conv [])
      (.append (get by-conv conv) entry))
    (setv (get by-pane (get entry "pane_id")) entry)
    (.add names (get entry "session_name")))
  {"by_conv" by-conv "by_pane" by-pane "names" names})

(deff classify-adopted-row [row index]
  {:pre [(: row SessionRow) (: index dict)]
   :post [(: % tuple)]}
  "adopted 非終端行 1 つの同一性照合(stamp-never-crosses-identities と
   同軸: 会話 ID 第一鍵・名前は素の縮退鍵)。戻り値 = #(分類 現在名):
     alive       — 同一性互換の生存が同じ pane に在る(現在名を運ぶ —
                   呼び手は名前が変わっていれば改名追随する)
     alive_moved — 同一会話が別 pane に生存(終端禁止・後継行があれば
                   supersede)
     hold        — 同一性が判定できない(倒れ先 = 不明側・何も書かない)
     absent      — 同一性互換の生存の痕跡なし(不在の記帳へ)"
  (setv by-conv (get index "by_conv"))
  (setv by-pane (get index "by_pane"))
  (setv names (get index "names"))
  (setv conv (if (is row.conversation None)
                 None
                 (.get row.conversation "session_id")))
  (when (isinstance conv str)
    (setv matches (.get by-conv conv []))
    (when (> (len matches) 1)
      ;; 同一会話の多重生存 = 異常(同一性判定不能)。
      (return #("hold" None)))
    (when (= (len matches) 1)
      (setv entry (get matches 0))
      (if (= (get entry "pane_id") row.pane-id)
          (return #("alive" (get entry "session_name")))
          (return #("alive_moved" None))))
    ;; 会話の生存痕跡なし。pane に別の何かが生きていれば同一性不確か
    ;; (下地再利用 / agent session の終了残骸)— 不在とは断じない。
    (if (in row.pane-id by-pane)
        (return #("hold" None))
        (return #("absent" None))))
  ;; 会話なし世代(7/21 一括登記等): name + pane の縮退照合。
  (setv entry (.get by-pane row.pane-id))
  (when (is-not entry None)
    ;; pane が宿りの物理 — 同 pane なら名前違いは改名(R55)として追随。
    (return #("alive" (get entry "session_name"))))
  (if (in row.session-name names)
      ;; 名前だけ別 pane に生存 — 同名再利用と pane 移動を弁別できない。
      (return #("hold" None))
      (return #("absent" None))))

(deff find-successor-id [row rows]
  {:pre [(: row SessionRow) (: rows list)]
   :post [(: % (| str None))]}
  "後継行の解決: 同一会話の非終端行のうち最新(started_at DESC, session_id
   ASC — session.list / db-session-by-conversation と同じ全順序)。自分が
   最新なら None(後継は常に前方 — 紐づけは新しい行へだけ向く)。"
  (setv conv (if (is row.conversation None)
                 None
                 (.get row.conversation "session_id")))
  (when (not (isinstance conv str))
    (return None))
  (setv same-conv (lfor r rows
                        :if (and (is-not r.conversation None)
                                 (= (.get r.conversation "session_id") conv))
                        r))
  (when (< (len same-conv) 2)
    (return None))
  (setv ordered (sorted same-conv :key (fn [r] r.session-id)))
  (setv ordered (sorted ordered :key (fn [r] (or r.started-at ""))
                        :reverse True))
  (setv newest (get ordered 0))
  (if (= newest.session-id row.session-id)
      None
      newest.session-id))


;; ---------------------------------------------------------------------------
;; program 面(宣言的 1 pass)
;; ---------------------------------------------------------------------------

(defk reconcile-row-once [row index rows allow-vanish observed-at cutoff-iso
                          min-checks]
  {:pre [(: row SessionRow) (: index dict) (: rows list)
         (: allow-vanish bool) (: observed-at str) (: cutoff-iso str)
         (: min-checks int)]
   :post [(: % str)]}
  "1 行の突合と記帳。戻り値 = 分類結果(summary の集計鍵)。終端の前提条件は
   guarded UPDATE(SQL)側が最終防衛する — ここの分岐はその前段の観測。"
  (setv #(kind current-name) (classify-adopted-row row index))
  (when (= kind "alive")
    (<- _ (session-store-reconcile-clear-absence row.session-id))
    (when (and (is-not current-name None) (!= current-name row.session-name))
      (<- n (session-store-reconcile-follow-rename row.session-id current-name))
      (when (> n 0)
        (<- _ (session-store-record-event row.session-id "session_renamed" row))
        (return "renamed")))
    (return "alive"))
  (when (= kind "alive_moved")
    ;; 会話は生きている(観測成立)— 不在ではない。後継行があれば旧宿りを
    ;; 紐づけ終端、無ければ保留(後継行の供給は登録の定期化 = 波1 S2 便)。
    (<- _ (session-store-reconcile-clear-absence row.session-id))
    (setv successor (find-successor-id row rows))
    (when (is successor None)
      (return "hold"))
    (<- n (session-store-reconcile-supersede row.session-id successor
                                             observed-at))
    (when (> n 0)
      (<- _ (session-store-record-event row.session-id "session_superseded" row))
      (return "superseded"))
    (return "hold"))
  (when (= kind "hold")
    ;; 同一性不確か — 何も書かない(不在にも数えない・presence にもしない)。
    (return "hold"))
  ;; kind = absent。台帳内に後継行が既にあれば紐づけ終端が先(消滅と
  ;; 誤裁定しない — vanish 側の NOT EXISTS guard と対)。
  (setv successor (find-successor-id row rows))
  (when (is-not successor None)
    (<- n (session-store-reconcile-supersede row.session-id successor
                                             observed-at))
    (when (> n 0)
      (<- _ (session-store-record-event row.session-id "session_superseded" row))
      (return "superseded"))
    (return "hold"))
  (<- _ (session-store-reconcile-mark-absent row.session-id observed-at))
  (when (not allow-vanish)
    (return "absent"))
  (<- n (session-store-reconcile-vanish row.session-id observed-at cutoff-iso
                                        min-checks))
  (when (> n 0)
    (<- _ (session-store-record-event row.session-id "session_vanished" row))
    (return "vanished"))
  "absent")

(defk reconcile-cycle [allow-vanish window-seconds min-checks]
  {:pre [(: allow-vanish bool) (: window-seconds int) (> window-seconds 0)
         (: min-checks int) (> min-checks 0)]
   :post [(: % dict)]}
  "adopted 行 reconciler の 1 pass(ADR-DOE-AGENTS-007 R8)。生存一覧は
   **必ず最初の effect**(供給断の周期に書き込みが 1 つも起きないことの
   構造)で、周期あたり 1 回だけ観測する(行ごとの probe 連打をしない)。
   空一覧 = 供給断として周期ごと skip(健全な substrate が 0 席を返す
   ことは無い — 88 席実勢・integration-lead 条件①)。per-row 隔離は
   monitor-cycle と同じ(1 行の失敗で残りを見捨てない)。"
  (setv summary {"skipped" None "alive" 0 "renamed" 0 "absent" 0 "hold" 0
                 "vanished" 0 "superseded" 0 "errors" 0})
  (<- live (tmux-list-sessions))
  (when (= (len live) 0)
    (setv (get summary "skipped") "supply_cut")
    (return summary))
  (<- now (clock-now))
  (setv observed-at (iso-format now))
  (setv cutoff-iso (iso-format (- now (timedelta :seconds window-seconds))))
  (<- rows (session-store-list-active))
  (setv adopted-rows (lfor r rows :if r.adopted r))
  (setv index (build-live-index live))
  (for [row (sorted adopted-rows :key (fn [r] r.session-id))]
    (try
      (<- outcome (reconcile-row-once row index rows allow-vanish observed-at
                                      cutoff-iso min-checks))
      (setv (get summary outcome) (+ (get summary outcome) 1))
      (except [e Exception]
        (setv (get summary "errors") (+ (get summary "errors") 1)))))
  summary)
