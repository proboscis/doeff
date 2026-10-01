;;; coordinator の保存の effect に答える handler。
;;;   durable-states = SaveState(調停の前後の状態)を KV の差分(durable_kv の durable-delta)に綴り、変化があれば Persist を出す(#2446)。
;;;   wal-store      = Persist を耐久の置き場(foundation/wal_store の WalStore・memory の MemoryWalStore — 層 protocol は foundation を
;;;                    読めないので、置き場は下の DurableStore の形で受け、組み立ては entry が持つ)へ書く。
;;;   memory-store   = まとまりごとの delta を list に積むテストの置き場。
;;; Persist は KV の差分を書く手前の effect(書き手 = wal-store・模擬の宿 — sim/local の coordinator の handler・テストの台本)。
;;; 差分の形は #2446 の前と同じ(キー → 新しい値・消えたキーは None)なので、書き手と移しの前の記録はそのまま読める。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import dataclasses [dataclass])
(import typing [Protocol])
(import doeff [EffectBase])
(import doeff_cluster.coordinator.intent.cluster_model [SaveState])
(import doeff_cluster.coordinator.protocol.durable_kv [durable-delta])


(defclass [(dataclass :frozen True)] Persist [EffectBase]
  "1 まとまりの変化(キー → 新しい値・消えたキーは None — durable_kv.hy)を耐久の場所へ書き、fsync が終わってから戻る。
   書けなければ例外(返事をせずに落ちる)。出すのは durable-states(SaveState の答え手)だけ。"
  (#^ dict delta))


(defclass DurableStore [Protocol]
  "wal-store が書く置き場の形(foundation/wal_store の WalStore と entry の MemoryWalStore がこの形を持つ)。"
  (defn #^ None persist [self #^ dict delta] (raise NotImplementedError)))


(defhandler durable-states
  ;; 引数なし: 前後の状態だけから差分を綴る(置き場は外側の Persist の答え手が持つ)。
  (SaveState [before after]
    (val delta (durable-delta before after))
    (when delta
      (<- (Persist delta)))
    (resume None)))


(defhandler wal-store [#^ DurableStore store]
  ;; 引数に残す理由: 置き場(開いた log の file と seq)は composition root が起動の時に 1 つ作って渡す
  (Persist [delta]
    (.persist store delta)
    (resume None)))


(defhandler memory-store [#^ list log]
  ;; テスト: まとまりごとの delta を list に積む(耐久の置き場の代わり)。
  ;; 引数に残す理由: 積んだ delta の列は検が持って読む
  (Persist [delta]
    (.append log delta)
    (resume None)))
