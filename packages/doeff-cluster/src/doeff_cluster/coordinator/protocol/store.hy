;;; coordinator の保存の effect に答える handler。
;;;   durable-states = SaveState(調停の前後の状態)を KV の差分(durable_kv の durable-delta)に綴り、変化があれば Persist を出す(#2446)。
;;;   wal-store      = Persist を耐久の置き場(foundation/wal_store の WalStore・memory の MemoryWalStore — 層 protocol は foundation を
;;;                    読めないので、置き場は下の DurableStore の形で受け、組み立ては entry が持つ)へ書く。
;;;   memory-store   = まとまりごとの差分(キー → 新しい値)を list に積むテストの置き場。
;;; Persist は KV の書きの組を耐久の置き場へ書く手前の effect(答え手 = wal-store・模擬の宿 — sim/local の coordinator の handler・テストの台本)。
;;; 書きは doeff-hy の表の書き TableWrite(key = キー・value = 新しい値・消えたキーは None — #2722)。層 protocol と foundation の両方が読める
;;; 型で、写像を effect の欄に持たない。置き場の口(DurableStore.persist)と置き場の 1 行の形は #2446 の前と同じ(キー → 新しい値の差分)なので、
;;; wal-store が書きの組から差分に戻して渡し、移しの前の置き場はそのまま読める。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import dataclasses [dataclass])
(import typing [Protocol])
(import doeff [EffectBase])
(import doeff_hy.table [TableWrite])
(import doeff_cluster.coordinator.intent.cluster_model [SaveState])
(import doeff_cluster.coordinator.protocol.durable_kv [durable-delta])


(defclass [(dataclass :frozen True)] Persist [EffectBase]
  "1 まとまりの変化を耐久の場所へ書き、fsync が終わってから戻る。writes = 変わったキーごとの書き(TableWrite — value は durable_kv.hy が
   綴った新しい値・消えたキーは None)。書けなければ例外(返事をせずに落ちる)。出すのは durable-states(SaveState の答え手)だけ。
   欄の名は以前の delta(キー → 値の dict)から替えた — 旧い名で読む答え手は (. effect delta) の読みで名指して落ちる(#2722)。"
  (#^ (get tuple #(TableWrite ...)) writes))


(defclass DurableStore [Protocol]
  "wal-store が書く置き場の形(foundation/wal_store の WalStore と entry の MemoryWalStore がこの形を持つ)。"
  (defn #^ None persist [self #^ dict delta] (raise NotImplementedError)))


(defhandler durable-states
  ;; 引数なし: 前後の状態だけから差分を綴る(置き場は外側の Persist の答え手が持つ)。
  (SaveState [before after]
    (val delta (durable-delta before after))
    (when delta
      (<- (Persist (tuple (gfor #(key value) (.items delta) (TableWrite key value))))))
    (resume None)))


(defhandler wal-store [#^ DurableStore store]
  ;; 引数に残す理由: 置き場(開いた log の file と seq)は composition root が起動の時に 1 つ作って渡す
  ;; 置き場の口は差分(キー → 新しい値)のまま — 書きの組から戻して渡す。
  (Persist [writes]
    (.persist store (dfor w writes w.key w.value))
    (resume None)))


(defhandler memory-store [#^ list log]
  ;; テスト: まとまりごとの差分(キー → 新しい値)を list に積む(耐久の置き場の代わり)。
  ;; 引数に残す理由: 積んだ差分の列は検が持って読む
  (Persist [writes]
    (.append log (dfor w writes w.key w.value))
    (resume None)))
