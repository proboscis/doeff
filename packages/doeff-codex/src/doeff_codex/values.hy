;;; 公開 effect の欄の値 — codex の会話(app-server の thread)の宣言・始まり方・ターンの参照・出来事の 1 件。
;;;
;;; 会話の単位は codex の thread(thread の id は codex が thread/start の答えで決める — 呼び手は選べない)。新しい会話は FreshThread で
;;; 頼み、答え TurnStarted の turn.thread-id を覚えて、続きを ResumeThread で頼む。資格・PATH・HOME・CODEX_HOME は CodexHome.env で
;;; composition root が渡す(handler は os.environ を読まない・秘密を運ぶ env の鍵は上の層の決まりで断る)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex" :role "type"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff_codex.rpc [ApprovalPolicy SandboxMode])
(import doeff_codex.lines [CodexLine])


(defrecord CodexHome
  "codex の process の環境: env = 子の process の環境変数の全部(PATH・HOME・CODEX_HOME — handler は os.environ を読まない)。
   env が写像なのは、子の process に渡す環境変数の名 → 値そのものだから(欄の集合が決まった構造ではない — doeff-claude-code の
   ClaudeHome.env と同じ)。"
  {:tags {:context "codex" :role "type"}}
  (#^ (get dict #(str str)) env))

(defrecord CodexSessionSpec
  "会話の宣言: home = 環境 / cwd = 作業の dir / model = model の名(None なら codex の既定)/ approval-policy・sandbox = 許可の方針と
   sandbox(None なら codex の既定 — 答え手の無い許可の問いで止めたくない時は ApprovalPolicy.NEVER を名乗る)。同じ宣言の続きは
   生きた process を使い回し、違えば起こし直す。"
  {:tags {:context "codex" :role "type"}}
  (#^ CodexHome home)
  (#^ str cwd)
  (setv #^ (| str None) model None)
  (setv #^ (| ApprovalPolicy None) approval-policy None)
  (setv #^ (| SandboxMode None) sandbox None))

(defrecord FreshThread
  "新しい会話で始める(thread の id は codex が決め、TurnStarted が名乗る)。"
  {:tags {:context "codex" :role "type"}})

(defrecord ResumeThread
  "前の会話(thread)を続ける。生きた process が在ればそれを使い、無ければ新しい process で thread/resume する。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id))

(defrecord CodexTurn
  "ターンの参照: thread-id = 会話 / turn-id = codex が turn/start の答えで決めたターンの id。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id))

(defrecord CodexEvent
  "ターンの出来事 1 件: seq = 会話の中で単調に増える番号(ReadTurnEvents の after-seq に渡す)/ record = 分けた行(lines.hy の記録)。"
  {:tags {:context "codex" :role "type"}}
  (#^ int seq)
  (#^ CodexLine record))
