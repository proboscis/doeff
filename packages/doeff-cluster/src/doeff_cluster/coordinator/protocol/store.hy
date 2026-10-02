;;; coordinator の保存の effect に答える handler。
;;;   durable-states = SaveState(調停の前後の状態)を KV の差分(durable_kv の durable-delta)に綴り、変化があれば Persist を出す(#2446)。
;;;   wal-store      = Persist を耐久の置き場(foundation/wal_store の WalStore・memory の MemoryWalStore — 層 protocol は foundation を
;;;                    読めないので、置き場は下の DurableStore の形で受け、組み立ては entry が持つ)へ書く。
;;;   memory-store   = まとまりごとの差分(キー → 新しい値)を list に積むテストの置き場。
;;; Persist は KV の書きの組を耐久の置き場へ書く手前の effect(答え手 = wal-store・模擬の宿 — sim/local の coordinator の handler・テストの台本)。
;;; 書きは doeff-hy の表の書き TableWrite(key = キー・value = 新しい値・消えたキーは None — #2722)。層 protocol と foundation の両方が読める
;;; 型で、写像を effect の欄に持たない。置き場の口(DurableStore.persist)と置き場の 1 行の形は #2446 の前と同じ(キー → 新しい値の差分)なので、
;;; wal-store が書きの組から差分に戻して渡し、移しの前の置き場はそのまま読める。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import dataclasses [dataclass])
(import pathlib [Path])
(import abc [ABC])
(import types [NotImplementedType])
(import doeff [EffectBase])
(import doeff_hy.table [TableWrite])
(import doeff_cluster.coordinator.intent.cluster_model [SaveState])
(import doeff_cluster.coordinator.protocol.durable_kv [durable-delta])
(import doeff_cluster.coordinator.protocol.wal_format [DroppedTail LogScan SnapshotRead apply-delta encode-line encode-snapshot read-snapshot scan-log writes-of])


(defclass [(dataclass :frozen True)] Persist [EffectBase]
  "1 まとまりの変化を耐久の場所へ書き、fsync が終わってから戻る。writes = 変わったキーごとの書き(TableWrite — value は durable_kv.hy が
   綴った新しい値・消えたキーは None)。書けなければ例外(返事をせずに落ちる)。出すのは durable-states(SaveState の答え手)だけ。
   欄の名は以前の delta(キー → 値の dict)から替えた — 旧い名で読む答え手は (. effect delta) の読みで名指して落ちる(#2722)。"
  (#^ (get tuple #(TableWrite ...)) writes))


;; --- 置き場の 2 つの形と、それを切り分けて読み書きする口(#2785)------------------------------------------------
;; 置き場は 2 つの形のどちらか(閉じた和 DurableStore):
;;   ByteLog    = file の置き場(foundation/wal_store の WalStore)— byte の I/O だけを持ち、行と写しの形(wal_format)はこの口が綴り・検める。
;;   DeltaStore = memory の置き場(entry の MemoryWalStore と、それを継いだ模擬の壊れた置き場)— file を持たないので形を綴らず、
;;                差分と表をそのまま受け渡す。
;; 層 protocol は foundation も entry も読めず、foundation も protocol を読めないので、名前のある共通の基底は置けない。形は method の名で
;; 見分ける: 実行時は ABC の __subclasshook__ が型ごとに 1 度だけ判じ、ABC が答えを覚える(typing.Protocol の isinstance は欄ごとに
;; inspect.getattr_static を撃ち、Persist ごとの契約と match で 1 回 50 µs ほど — Persist の CPU の 6 割 — かかった・#2785 の測り)。
;; 静的な形(使い手の型検査が読む)は store.pyi の Protocol。どちらの置き場も method table / replace-table / recovery を持つ(table =
;; 耐久になった全部のキーの表・recovery = 読み直しで捨てた最後の行の記録)— 写像を欄に持たない(DOEFF172)。

(defclass MethodShape [ABC]
  "method の名(METHODS)を全部持つ型をその形と見なす基底 — ByteLog と DeltaStore が 1 つの判じ方を共有するため。"
  (setv #^ (get tuple #(str ...)) METHODS #())
  (defn [classmethod] #^ (| bool NotImplementedType) __subclasshook__ [cls #^ type other]
    (if (and cls.METHODS (all (gfor name cls.METHODS (callable (getattr other name None))))) True NotImplemented)))


(defclass ByteLog [MethodShape]
  "file の置き場の形(foundation/wal_store の WalStore)— 読み書きの口が写しと log の byte を受け渡す相手。"
  (#^ int seq)
  (#^ int max-log-bytes)
  (#^ Path snapshot)
  (#^ Path log)
  (setv METHODS #("exists" "check_place" "read_snapshot_bytes" "read_log_lines" "drop_tail" "append_line" "write_snapshot"
                  "table" "replace_table" "recovery"))
  ;; 表と読み直しの記録は欄に持たず method で受け渡す(写像を欄に持たない — DOEFF172)。
  (defn #^ (get dict #(str object)) table [self] (raise NotImplementedError))
  (defn #^ None replace-table [self #^ (get dict #(str object)) kv] (raise NotImplementedError))
  (defn #^ (| (get dict #(str object)) None) recovery [self] (raise NotImplementedError))
  (defn #^ bool exists [self] (raise NotImplementedError))
  (defn #^ None check-place [self] (raise NotImplementedError))
  (defn #^ (| bytes None) read-snapshot-bytes [self] (raise NotImplementedError))
  (defn #^ (get list bytes) read-log-lines [self] (raise NotImplementedError))
  (defn #^ None drop-tail [self #^ int kept #^ int size #^ str reason] (raise NotImplementedError))
  (defn #^ int append-line [self #^ bytes line] (raise NotImplementedError))
  (defn #^ None write-snapshot [self #^ bytes data] (raise NotImplementedError)))


(defclass DeltaStore [MethodShape]
  "memory の置き場の形(entry の MemoryWalStore)— 読み書きの口が差分と表をそのまま受け渡す相手。"
  (setv METHODS #("exists" "load" "persist" "checkpoint" "table" "replace_table" "recovery"))
  ;; 表と読み直しの記録は欄に持たず method で受け渡す(写像を欄に持たない — DOEFF172)。
  (defn #^ (get dict #(str object)) table [self] (raise NotImplementedError))
  (defn #^ None replace-table [self #^ (get dict #(str object)) kv] (raise NotImplementedError))
  (defn #^ (| (get dict #(str object)) None) recovery [self] (raise NotImplementedError))
  (defn #^ bool exists [self] (raise NotImplementedError))
  (defn #^ (get dict #(str object)) load [self] (raise NotImplementedError))
  (defn #^ None persist [self #^ (get dict #(str object)) delta] (raise NotImplementedError))
  (defn #^ None checkpoint [self] (raise NotImplementedError)))


(val DurableStore (| ByteLog DeltaStore))


(defk durable-exists [store]
  {:pre [(: store DurableStore)] :post [(: % bool)] :tags {:context "coordinator" :role "protocol"}}
  "置き場に耐久の中身が在るか — 起動が置き場から読み直すか、以前の形の file から移すかを決めるため。"
  (.exists store))


(defk durable-load [store]
  {:pre [(: store DurableStore)] :post [(: % (get dict #(str object)))] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "耐久の中身を読み直し、全部のキーの表を返す(置き場の表と seq も進める)— 起動が返事を済ませた書きを 1 つも失わずに状態を作るため。
   file の置き場は写し → log の行を検めて当て、読めない最後の 1 行だけを捨てて切り詰める。途中の破損は WalCorrupted で何も書き換えない。"
  (match store
    (ByteLog)
      (do (.check-place store)
          (val snapshot (.read-snapshot-bytes store))
          ;; 写しが無ければ空の表・番号 0 から当てる。
          (val base (if (is snapshot None)
                        (SnapshotRead :seq 0 :rows #())
                        (! (read-snapshot snapshot (str store.snapshot)))))
          ;; 写しの書きを表に組み、log の行をその表の上へその場で当てる。
          (val table (dfor row base.rows row.key row.value))
          (<- scan LogScan (scan-log (.read-log-lines store) base.seq table (str store.log)))
          (match scan.dropped
            (DroppedTail :kept kept :size size :reason reason) (.drop-tail store kept size reason)
            None None)
          (.replace-table store table)
          (setv store.seq scan.seq)
          table)
    (DeltaStore) (.load store)))


(defk durable-persist [store delta]
  {:pre [(: store DurableStore) (: delta (get dict #(str object)))] :post [(: % None)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "1 まとまりの差分(キー → 新しい値・消えたキーは None)を耐久にして(fsync 済み)から戻る — Persist の答え手と起動の書きが同じ
   書き方をするため。file の置き場は log に checksum つきの 1 行を追記し、log が上限を超えたら写しにまとめ直す。空の差分は書かない。"
  (match store
    (ByteLog)
      (when delta
        (setv store.seq (+ store.seq 1))
        (<- line bytes (encode-line store.seq delta))
        (val size (.append-line store line))
        (<- writes tuple (writes-of delta))
        (<- (apply-delta (.table store) writes))
        (when (> size store.max-log-bytes)
          (<- (durable-checkpoint store))))
    (DeltaStore) (.persist store delta))
  None)


(defk durable-checkpoint [store]
  {:pre [(: store DurableStore)] :post [(: % None)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "全部のキーを写しにまとめ直し(fsync 済み)、その後で log を空にする — log を上限の内に保ち、以前の形から移した状態を 1 つの写しに
   置くため。"
  (match store
    (ByteLog)
      (do (<- data bytes (encode-snapshot store.seq (.table store)))
          (.write-snapshot store data))
    (DeltaStore) (.checkpoint store))
  None)


(defhandler durable-states
  ;; 引数なし: 前後の状態だけから差分を綴る(置き場は外側の Persist の答え手が持つ)。
  (SaveState [before after]
    (<- delta (durable-delta before after))
    (when delta
      (<- (Persist (tuple (gfor #(key value) (.items delta) (TableWrite key value))))))
    (resume None)))


(defhandler wal-store [#^ DurableStore store]
  ;; 引数に残す理由: 置き場(開いた log の file と seq)は composition root が起動の時に 1 つ作って渡す
  ;; 置き場の口は差分(キー → 新しい値)のまま — 書きの組から戻して、置き場の形を切り分ける口 durable-persist に渡す。
  (Persist [writes]
    (<- (durable-persist store (dfor w writes w.key w.value)))
    (resume None)))


(defhandler memory-store [#^ list log]
  ;; テスト: まとまりごとの差分(キー → 新しい値)を list に積む(耐久の置き場の代わり)。
  ;; 引数に残す理由: 積んだ差分の列は検が持って読む
  (Persist [writes]
    (.append log (dfor w writes w.key w.value))
    (resume None)))
