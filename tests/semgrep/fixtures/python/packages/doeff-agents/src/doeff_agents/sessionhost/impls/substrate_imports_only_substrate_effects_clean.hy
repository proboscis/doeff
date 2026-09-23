;;; semgrep clean fixture: doeff-agents-substrate-does-not-import-acp
;;; (card acp:kanban-issue:ki-554e364641e8)— 器が持ってよい import と綴りの形。発火しない。

;; OK: substrate の effect と kind の共通部品(acp ではない)
(import doeff_agents.sessionhost.effects [FsWriteTextAtomic FsRemoveFile LogLine])
(import doeff_agents.sessionhost.impls.markers [ready-marker-of])

;; OK: wire の欄の綴りは器の側の写しとして局所の定数で持つ(検が effects.py と突き合わせる)
(setv CLAUDE-MEMORY-FILES-KEY "memory_files")

;; OK: 註で綴りの家を path として名指すのは import ではない
;; 綴りの家は sessionhost/acp/effects.py の CHARTER_MEMORY_FILES_KEY で、ここはその写し。
