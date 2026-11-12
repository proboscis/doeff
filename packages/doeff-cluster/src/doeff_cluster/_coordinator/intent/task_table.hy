;;; task の表(TaskTable)と、表の書きのたびに差分で持ち続ける派生の値(TaskIndex)— 2026-11-12。
;;;
;;; なぜ: coordinator は要求 1 件ごとに調停(settle → reconcile → place-tasks・task-due)を回し、その中で task の表の全部の行から
;;; 派生の値(走査の対象の行・保持の行の最小期限・形ごとの測った山・置いている task の名乗り)を何度も作り直していた。表は書きのたびに
;;; 新しい object になるので、同じ object の間の覚えでは要求をまたいで使い回せず、1 要求の費用が保持の間の終わった行を含む行の総数に
;;; 比例した(約 1,800 行で主 thread の CPU 98〜99%)。
;;; TaskTable は Table の子 class で、with-writes(と Table の下書きの freeze — どちらも with-writes を通る)のたびに、書いた行の前と後だけを
;;; 読んで索引を次の値へ進め、新しい表と一緒に返す。読み手(cluster_policy の scan-rows・task-split・shape-peaks-of・placed-claims と
;;; program_policy.program-refs — 2026-11-13)は
;;; 索引を引くだけで、全部の行を回らない。保持の間の終わった行は走査の対象の索引に入らず、形ごとの山へは行が書かれた時に 1 度だけ足す。
;;; 索引は最初に読まれた時に全部の行から 1 度だけ作り(読み直しの後・table-of で作った表)、その後は書きの差分だけで進む。
;;; 索引と全部の行から作り直した値が同じである事は tests/test_task_index.hy が任意の書きの列で照らす。
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_hy._table [Table TableWrite table-of])


;; 終わった task の phase(旧い形の保存の行を読む時に、まだ終わっていない行だけを断る — task-record-from-json)。切り離した task の
;; 終わりの行(この phase の切り離した行)は結果を保持しているだけの行で、走査の対象に入らない(held-row?)。
(setv ENDED-PHASES (frozenset #("finished" "code-failed" "failed" "version-mismatch" "lost" "cancelled" "env-failed")))


(defrecord ShapePeak
  "形 1 つの測った山(measured_room の頭の註の測った山で数える要り — cluster_policy.shape-peaks-of): mib = 形を名乗り山を測った行(保持の間の
   終わった行を含む)の山の最大・settled = そのうち終わり(finished)まで走った行が在るか(真 = 山は終わりまでの測り — 名乗りを要りの下限に
   しない)。"
  {:check [(>= mib 0)]}
  (#^ int mib)
  (#^ bool settled))


(defrecord ShapeTally
  "形 1 つの測った山の数え(TaskIndex の中だけの値): peaks = 山の値 → その山を持つ行の数(行が消えても最大を求め直せるよう値ごとに数える)・
   finished = そのうち finished の行の数。"
  {:check [(>= finished 0)]}
  (#^ dict peaks)
  (#^ int finished))


(defrecord TaskIndex
  "task の表の派生の値(頭の註): live = 走査の対象の行(held-row? でない行 — task の id → 行。保持の間の終わった行を含まない)・held = 保持の
   行のうち終わった刻を持つ行の期限(task の id → finished-ms + retain-ms + 1 — cluster_policy.task-lapse-at と同じ式)・earliest = held の
   最小(無ければ None)・tallies = 形 → ShapeTally・peaks = 形 → ShapePeak(tallies から求めた値 — 読み手が引く)・programs = 詰めた
   Program の置き場のキー → そのキーを参照する行の数(保持の間の終わった行を含む全部の行 — program_policy.program-refs が置き場の掃除で
   残す物を決める。0 になったキーは入れない)。どの dict も作った後に書き換えない(古い表を持つ読み手が古い値を読み続ける)。"
  (#^ dict live)
  (#^ dict held)
  (#^ (| int None) earliest)
  (#^ dict tallies)
  (#^ dict peaks)
  (#^ dict programs))


(defn #^ bool held-row? [#^ object task]
  "切り離した終わりの行(結果を保持しているだけ)か。切り離していない終わった行は含まない(lease を延ばされ走査の対象のまま)。"
  (and task.detached (in task.phase ENDED-PHASES)))


(defn #^ (| int None) held-lapse [#^ object task]
  "保持の行の期限(finished-ms + retain-ms + 1)。終わった刻を持たない保持の行は毎拍 now から数えて動くので None(期限の列に入れない)。"
  (if (is task.finished-ms None) None (+ task.finished-ms task.retain-ms 1)))


(defn #^ (| ShapePeak None) peak-of-tally [#^ ShapeTally tally]
  "形 1 つの数えから ShapePeak(数える行が無ければ None — 形を peaks から外す)。"
  (if tally.peaks
      (ShapePeak :mib (max tally.peaks) :settled (> tally.finished 0))
      None))


(defn #^ TaskIndex index-of-rows [#^ tuple rows]
  "全部の行から作った索引(最初の読み・照らしの test の正解)。"
  (setv live {} held {} tallies {} programs {})
  (for [task rows]
    (when task.program
      (setv (get programs task.program) (+ (.get programs task.program 0) 1)))
    (if (held-row? task)
        (do (setv lapse (held-lapse task))
            (when (is-not lapse None)
              (setv (get held task.id) lapse)))
        (setv (get live task.id) task))
    (when (and task.shape (is-not task.peak-mib None))
      (setv before (.get tallies task.shape))
      (setv peaks (if (is before None) {} before.peaks))
      (setv (get peaks task.peak-mib) (+ (.get peaks task.peak-mib 0) 1))
      (setv (get tallies task.shape) (ShapeTally :peaks peaks
                                                 :finished (+ (if (is before None) 0 before.finished)
                                                              (if (= task.phase "finished") 1 0))))))
  (TaskIndex :live live :held held :earliest (if held (min (.values held)) None) :tallies tallies
             :peaks (dfor #(shape tally) (.items tallies) shape (peak-of-tally tally)) :programs programs))


(defn #^ TaskIndex index-after [#^ TaskIndex index #^ dict changes]
  "index に書き changes(task の id → #(前の行か None 書いた後の行か None))を当てた次の索引(index は変えない)。費用は書いた行の数と、
   変わった dict の写し(live は走査の対象の行の数・held は保持の行の数 — どちらも書きの在った時だけ・C の写し)に比例し、保持の行を回らない。"
  (setv live index.live held index.held earliest index.earliest tallies index.tallies programs index.programs
        live-copied False held-copied False tallies-copied False programs-copied False touched (set))
  (for [#(id #(old new)) (.items changes)]
    ;; 走査の対象の行。
    (setv was-live (and (is-not old None) (not (held-row? old))))
    (setv is-live (and (is-not new None) (not (held-row? new))))
    (when (or was-live is-live)
      (when (not live-copied)
        (setv live (dict live) live-copied True))
      (if is-live
          (setv (get live id) new)
          (.pop live id None)))
    ;; 保持の行の期限。
    (setv old-lapse (if (and (is-not old None) (held-row? old)) (held-lapse old) None))
    (setv new-lapse (if (and (is-not new None) (held-row? new)) (held-lapse new) None))
    (when (!= old-lapse new-lapse)
      (when (not held-copied)
        (setv held (dict held) held-copied True))
      (if (is new-lapse None)
          (.pop held id None)
          (setv (get held id) new-lapse)))
    ;; Program の参照の数え(前の行の参照を引き、後の行の参照を足す — 同じキーのままなら数えは変わらない)。
    (setv old-program (if (is old None) None old.program) new-program (if (is new None) None new.program))
    (when (!= old-program new-program)
      (when (not programs-copied)
        (setv programs (dict programs) programs-copied True))
      (when old-program
        (setv left (- (get programs old-program) 1))
        (if (> left 0)
            (setv (get programs old-program) left)
            (.pop programs old-program)))
      (when new-program
        (setv (get programs new-program) (+ (.get programs new-program 0) 1))))
    ;; 形ごとの山(形を名乗り山を測った行だけ)。
    (for [#(row sign) #(#(old -1) #(new 1))]
      (when (and (is-not row None) row.shape (is-not row.peak-mib None))
        (when (not tallies-copied)
          (setv tallies (dict tallies) tallies-copied True))
        (setv before (.get tallies row.shape))
        ;; 形ごとの山の数えは、この呼びの中で初めて触る時だけ写す(前の索引の値を書き換えない)。
        (setv peaks (if (is before None) {} (if (in row.shape touched) before.peaks (dict before.peaks))))
        (.add touched row.shape)
        (setv count (+ (.get peaks row.peak-mib 0) sign))
        (if (> count 0)
            (setv (get peaks row.peak-mib) count)
            (.pop peaks row.peak-mib None))
        (setv (get tallies row.shape)
              (ShapeTally :peaks peaks
                          :finished (+ (if (is before None) 0 before.finished) (if (= row.phase "finished") sign 0)))))))
  ;; 最小の期限は、最小の行が消えた・延びた時だけ求め直す(足した行は比べるだけ)。
  (when held-copied
    (setv earliest (if held (min (.values held)) None)))
  (setv peaks index.peaks)
  (when touched
    (setv peaks (dict peaks))
    (for [shape touched]
      (setv tally (get tallies shape))
      (setv peak (peak-of-tally tally))
      (if (is peak None)
          (do (.pop peaks shape None)
              (.pop tallies shape None))
          (setv (get peaks shape) peak))))
  (TaskIndex :live live :held held :earliest earliest :tallies tallies :peaks peaks :programs programs))


(defclass TaskTable [Table]
  "task の id → TaskRecord の表(頭の註)。Table と同じに読み書きし、加えて索引を task-index で引く。with-writes は書いた行の前と後で索引を
   進めた新しい TaskTable を返す(索引をまだ作っていない表は、子も作らずに最初の読みを待つ)。"
  (setv __slots__ #("_index"))

  (defn #^ None __init__ [self #^ dict base #^ dict delta #^ frozenset removed]
    (.__init__ (super) base delta removed)
    (object.__setattr__ self "_index" None))

  (defn #^ "TaskTable" with-writes [self #^ tuple writes]
    "書きの列を当てた新しい TaskTable(この表は変わらない)。何も変わらなければこの表そのもの。"
    (setv child (Table.with-writes self writes))
    (when (is child self)
      (return self))
    (setv index self._index)
    (_indexed child (if (is index None)
                        None
                        ;; 同じ鍵を 2 度書く列は後の書きが勝つ(Table と同じ)— 前の行はこの表の行。
                        (index-after index (dfor write writes write.key #((.row self write.key) write.value))))))

  (defn #^ bool __eq__ [self #^ object other]
    "Table と同じ中身の比べ(素の Table とも中身で比べる — Table の比べは相手を自分の型で問うので、素の Table の側から比べる)。"
    (if (and (isinstance other Table) (not (isinstance other TaskTable)))
        (Table.__eq__ other self)
        (Table.__eq__ self other)))

  (setv __hash__ None)

  (defn #^ tuple __reduce__ [self]
    "写し(copy・pickle)は TaskTable のまま作り直す(索引は最初の読みで作り直す)。"
    #(TaskTable #(self._base self._delta self._removed)))

  (defn #^ str __repr__ [self]
    (.format "TaskTable(size={})" self._size)))


(defn #^ TaskTable _indexed [#^ Table table #^ (| TaskIndex None) index]
  "Table.with-writes が作ったばかりの素の表 table を、同じ中身(基・差分・系譜の位置)の TaskTable にする(table は誰にも渡っていない)。"
  (setv made (object.__new__ TaskTable))
  (for [slot #("_base" "_delta" "_removed" "_size" "_flat" "_line" "_at")]
    (object.__setattr__ made slot (getattr table slot)))
  (object.__setattr__ made "_index" index)
  made)


(defn #^ TaskTable task-table-of [#^ tuple rows]
  "行の列(TableWrite の tuple — value = None の行は入れない)から TaskTable を作る(table-of の task の表の版)。"
  (_indexed (table-of rows) None))


(defn #^ TaskIndex task-index [#^ Table tasks]
  "task の表の索引。TaskTable は持っている索引(無ければ全部の行から 1 度だけ作って表に持たせる)を返す。素の Table(test が table-of で
   作った表)は覚えずに毎回全部の行から作る — 状態の task の表は TaskTable で持つ(ClusterState の既定・保存の読み直し)。"
  (if (isinstance tasks TaskTable)
      (do (when (is tasks._index None)
            (object.__setattr__ tasks "_index" (index-of-rows (.rows tasks))))
          tasks._index)
      (index-of-rows (.rows tasks))))
