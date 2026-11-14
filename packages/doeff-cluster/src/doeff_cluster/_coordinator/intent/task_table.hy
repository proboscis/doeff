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
  "形 1 つの測った山(measured_room の頭の註の測った山で数える要り — task_claims.shape-peaks-of): mib = 形を名乗り山を測った行のうち、
   まだ終わっていない行と、近い回の終わった行(終わった刻の新しい順に RECENT-PEAK-RUNS 本 — 保持の間の全部ではない)の山の最大・
   settled = その近い回に終わり(finished)まで走った行が在るか(真 = 山は終わりまでの測り — 名乗りを要りの下限にしない)。"
  {:check [(>= mib 0)]}
  (#^ int mib)
  (#^ bool settled))


;; 形の山に数える近い回の終わった行の数(ADR-DOE-CLUSTER-005 R4 の改め 2026-11-15)。以前は保持の間(約 24 時間)の終わった行の全部の
;; 最大で数え、外れ値の 1 本が保持の間ずっと形の申告を縛った(日次の回の申告 20,804 MiB — 同じ形の近い回の山は 614・3,751・5,610・
;; 7,025 MiB)。要りの越えで止められた回も終わった行(lost・failed)としてこの数に入るので、止められた直後の置き直しは止められた回の山で
;; 数える(同じ小さい申告で止めと置き直しを繰り返さない)。
(setv RECENT-PEAK-RUNS 5)


(defrecord ShapeTally
  "形 1 つの測った山の数え(TaskIndex の中だけの値 — 族の山を数える family-peaks-of も読む): rows = 形を名乗る行の数(山の無い行を含む —
   形の族の行を、形の行が無くなった時に外すため)・live = まだ終わっていない行の山の値 → その山を持つ行の数(行が消えても最大を求め直せる
   よう値ごとに数える)・ended = 終わった行(ENDED-PHASES)のうち山を測った行の id → #(終わった刻 山 finished か)・recent = ended のうち
   終わった刻の新しい順(同じ刻は id の大きい順)に RECENT-PEAK-RUNS 本の #(終わった刻 id 山 finished か)。"
  {:check [(>= rows 0)]}
  (#^ int rows)
  (#^ dict live)
  (#^ dict ended)
  (#^ tuple recent))


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


(defn #^ tuple recent-runs [#^ (get tuple #(tuple ...)) runs]
  "終わった行の列 runs(#(終わった刻 id 山 finished か) — 順は問わない)の、終わった刻の新しい順(同じ刻は id の大きい順)に RECENT-PEAK-RUNS 本。"
  (tuple (cut (sorted runs :key (fn [r] #((get r 0) (get r 1))) :reverse True) 0 RECENT-PEAK-RUNS)))


(defn #^ (| ShapePeak None) peak-of-runs [#^ tuple live-peaks #^ tuple recent]
  "まだ終わっていない行の山の列 live-peaks と近い回の終わった行 recent(recent-runs の答え)から ShapePeak(山が 1 つも無ければ None)。"
  (setv values (+ (tuple live-peaks) (tuple (gfor r recent (get r 2)))))
  (if values
      (ShapePeak :mib (max values) :settled (any (gfor r recent (get r 3))))
      None))


(defn #^ (| ShapePeak None) peak-of-tally [#^ ShapeTally tally]
  "形 1 つの数えから ShapePeak(数える山が無ければ None — 形を peaks から外す)。"
  (peak-of-runs (tuple tally.live) tally.recent))


(defn #^ (| ShapeTally None) tally-of [#^ list parts]
  "形 1 つの数えの部品 parts([rows live ended])から ShapeTally(形を名乗る行が無ければ None — 形を tallies から外す)。recent は ended から
   求める。"
  (setv [rows running ended] parts)
  (if (> rows 0)
      (ShapeTally :rows rows :live running :ended ended
                  :recent (recent-runs (tuple (gfor #(id #(at peak done)) (.items ended) #(at id peak done)))))
      None))


(defn #^ None tally-count [#^ list parts #^ object row #^ int sign]
  "形の数えの部品 parts([rows live ended] — 呼び手の写し)に行 row を sign(1 = 足す・−1 = 引く)で数える。"
  (setv (get parts 0) (+ (get parts 0) sign))
  (when (is-not row.peak-mib None)
    (if (in row.phase ENDED-PHASES)
        (if (> sign 0)
            (setv (get (get parts 2) row.id) #((or row.finished-ms 0) row.peak-mib (= row.phase "finished")))
            (.pop (get parts 2) row.id None))
        (do (setv running (get parts 1)
                  count (+ (.get running row.peak-mib 0) sign))
            (if (> count 0)
                (setv (get running row.peak-mib) count)
                (.pop running row.peak-mib None))))))


(defn #^ TaskIndex index-of-rows [#^ tuple rows]
  "全部の行から作った索引(最初の読み・照らしの test の正解)。"
  (setv live {} held {} parts {} programs {})
  (for [task rows]
    (when task.program
      (setv (get programs task.program) (+ (.get programs task.program 0) 1)))
    (if (held-row? task)
        (do (setv lapse (held-lapse task))
            (when (is-not lapse None)
              (setv (get held task.id) lapse)))
        (setv (get live task.id) task))
    (when task.shape
      (when (not-in task.shape parts)
        (setv (get parts task.shape) [0 {} {}]))
      (tally-count (get parts task.shape) task 1)))
  (setv tallies (dfor #(shape part) (.items parts) shape (tally-of part)))
  (TaskIndex :live live :held held :earliest (if held (min (.values held)) None) :tallies tallies
             :peaks (dfor #(shape tally) (.items tallies) :setv peak (peak-of-tally tally) :if (is-not peak None) shape peak)
             :programs programs))


(defn #^ TaskIndex index-after [#^ TaskIndex index #^ dict changes]
  "index に書き changes(task の id → #(前の行か None 書いた後の行か None))を当てた次の索引(index は変えない)。費用は書いた行の数と、
   変わった dict の写し(live は走査の対象の行の数・held は保持の行の数 — どちらも書きの在った時だけ・C の写し)に比例し、保持の行を回らない。"
  (setv live index.live held index.held earliest index.earliest tallies index.tallies programs index.programs
        live-copied False held-copied False programs-copied False touched {})
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
    ;; 形ごとの数え(形を名乗る行 — 山の無い行は行の数だけ)。形ごとの部品は、この呼びの中で初めて触る時だけ写す(前の索引の値を書き換えない)。
    (for [#(row sign) #(#(old -1) #(new 1))]
      (when (and (is-not row None) row.shape)
        (when (not-in row.shape touched)
          (setv before (.get tallies row.shape))
          (setv (get touched row.shape) (if (is before None) [0 {} {}] [before.rows (dict before.live) (dict before.ended)])))
        (tally-count (get touched row.shape) row sign))))
  ;; 最小の期限は、最小の行が消えた・延びた時だけ求め直す(足した行は比べるだけ)。
  (when held-copied
    (setv earliest (if held (min (.values held)) None)))
  (setv peaks index.peaks)
  (when touched
    (setv tallies (dict tallies) peaks (dict peaks))
    (for [#(shape part) (.items touched)]
      (setv tally (tally-of part))
      (if (is tally None)
          (.pop tallies shape None)
          (setv (get tallies shape) tally))
      (setv peak (if (is tally None) None (peak-of-tally tally)))
      (if (is peak None)
          (.pop peaks shape None)
          (setv (get peaks shape) peak))))
  (TaskIndex :live live :held held :earliest earliest :tallies tallies :peaks peaks :programs programs))


(defclass TaskTable [Table]
  "task の id → TaskRecord の表(頭の註)。Table と同じに読み書きし、加えて索引を task-index で引く。with-writes は書いた行の前と後で索引を
   進めた新しい TaskTable を返す(索引をまだ作っていない表は、子も作らずに最初の読みを待つ)。"
  (setv __slots__ #("_index" "_families"))

  (defn #^ None __init__ [self #^ dict base #^ dict delta #^ frozenset removed]
    (.__init__ (super) base delta removed)
    (object.__setattr__ self "_index" None)
    (object.__setattr__ self "_families" None))

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
  (object.__setattr__ made "_families" None)
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


(defn #^ dict family-peaks-of [#^ TaskIndex index #^ Table families]
  "形ごとの測った山を、形の族の近い回の山で補った値(形 → ShapePeak — ADR-DOE-CLUSTER-005 R4 の改め 2026-11-15)を全部の行から知るため:
   families = 形 → 族の関係(ClusterState.shape-families — 行は shape と family を持つ)。形そのものに近い回の finished の行が在れば
   形の山のまま。無い形のうち族を持つ形は、族の山(族の全部の形のまだ終わっていない行の山と、族の全部の形の終わった行の近い
   RECENT-PEAK-RUNS 本 — peak-of-runs)と形の山の大きい方を山にし、settled は族の近い回に finished が在るか。族の山も無ければ形の山のまま。"
  (when (= (.size families) 0)
    (return index.peaks))
  (setv members {})
  (for [row (.rows families)]
    (.append (.setdefault members row.family []) row.shape))
  (setv by-family {})
  (for [#(family shapes) (.items members)]
    (setv running [] runs [])
    (for [shape shapes]
      (setv tally (.get index.tallies shape))
      (when (is-not tally None)
        (.extend running tally.live)
        (.extend runs tally.recent)))
    (setv peak (peak-of-runs (tuple running) (recent-runs (tuple runs))))
    (when (is-not peak None)
      (setv (get by-family family) peak)))
  (setv peaks (dict index.peaks))
  (for [row (.rows families)]
    (setv own (.get peaks row.shape) family (.get by-family row.family))
    (when (and (is-not family None) (or (is own None) (not own.settled)))
      (setv (get peaks row.shape) (ShapePeak :mib (max family.mib (if (is own None) 0 own.mib)) :settled family.settled))))
  peaks)


(defn #^ dict shape-family-peaks [#^ Table tasks #^ Table families]
  "task の表 tasks と形 → 族の関係 families から family-peaks-of の値を知るため。TaskTable は、同じ families の object に対する答えを
   表に覚える(表も families も書きのたびに新しい object になるので、覚えた値は同じ 2 つの object の間でだけ使う — 置く判断は 1 歩に
   何度もこの値を読む)。素の Table は覚えずに毎回求める。"
  (setv index (task-index tasks))
  (when (not (isinstance tasks TaskTable))
    (return (family-peaks-of index families)))
  (setv kept tasks._families)
  (when (and (is-not kept None) (is (get kept 0) families))
    (return (get kept 1)))
  (setv peaks (family-peaks-of index families))
  (object.__setattr__ tasks "_families" #(families peaks))
  peaks)
