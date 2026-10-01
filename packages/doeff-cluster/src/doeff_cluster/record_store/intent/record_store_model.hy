;;; effect の記録の置き場(record-store)の effect — 置き場の Program(record_store.core.program の store-loop)が出し、file の I/O の
;;; 言い換え(record_store.protocol.record_files の record-files)が答える(record_store.hy から分けた・#2030)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass [(dataclass :frozen True)] AppendRecordLines [EffectBase]
  "結果は追記した行の数。fsync してから返る。"
  (#^ str service)
  (#^ str run)
  (#^ int chunk)
  (#^ list lines))

(defclass [(dataclass :frozen True)] ListRecordRuns [EffectBase]
  "結果は run の dict の list。service = None なら全部。"
  (#^ (| str None) service))

(defclass [(dataclass :frozen True)] ReadRecordRun [EffectBase]
  "結果は JSONL の text(区切りの順)。無ければ None。"
  (#^ str service)
  (#^ str run)
  (#^ (| int None) from-chunk)
  (#^ (| int None) to-chunk))

(defclass [(dataclass :frozen True)] CompactRecords [EffectBase]
  "結果は gzip にした区切りの数。idle-ms 書かれていない .jsonl を圧縮する。"
  (#^ int now-ms)
  (#^ int idle-ms))

(defclass [(dataclass :frozen True)] PruneRecords [EffectBase]
  "結果は消した run の #(service run) の list。最後の書きが now-ms - retention-ms より古い run を消す。"
  (#^ int now-ms)
  (#^ int retention-ms))


;; --- 純粋な判断 --------------------------------------------------------------------------------
