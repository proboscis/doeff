;;; Immutable table: a table from string keys to values that never changes after it is built (Table), and its write (TableWrite).
;;;
;;; Why: a reader that rebuilds a table (key -> value) every tick (the projection workspace of agora's screen) has these needs:
;;;   (1) After a write, a reader still holding the old table (a processing loop that keeps answering on another thread) must keep seeing the old rows, so the table cannot be updated in place.
;;;   (2) Only a few dozen rows change per tick, so we do not want to copy the whole table every tick (copying 140,000 rows costs about 2 ms per tick).
;;;   (3) The reader layer does not pass mappings (dict, Mapping) around; it looks up key -> value through functions (agora's boundary 2 (b)).
;;; Table satisfies all three: inside it has two layers, "base + delta", and the base is never modified after it is built. with-writes copies only the delta and returns a new
;;; Table (only the delta is copied). When the delta exceeds 1/COMPACT-RATIO of the base, a new base is built from the base and delta combined (only that write is O(n);
;;; amortized it is small). A read looks up the delta first, then the base. Table is not a Mapping (it is not passed around as a mapping); the only accessors are row, keys, rows, items, and
;;; size.
;;; A small table whose base has fewer than MIN-DELTA rows has no delta layer and copies the base on every write. The copy is small, and the cost of a delta layer (copying the whole delta dict on every write
;;; plus set operations) is higher (copying one dict key takes 0.3-0.7 us, whereas a write through the delta layer takes about 4.7 us).
;;;
;;; A writer that rereads rows it wrote within one tick accumulates its writes in a draft (TableDraft, in the section below) and applies with-writes just once at the end of the tick.
;;;
;;; Difference from FrozenMap (frozen.hy): FrozenMap freezes a mapping over an open set of keys and copies the whole thing on every write. Table is for a large table that
;;; changes a little at a time every tick, and it is not a mapping.
;;;
;;; Lineage (_Line): so that changed-keys (the keys that differ between the table previously handed to a reader and the current one) and equality (__eq__) can be answered in time proportional to the number of writes between the two tables.
;;; Comparing two tables that share a base reads the entire delta layer, so while the delta grew up to 1/COMPACT-RATIO of the base,
;;; the per-tick comparison cost grew in proportion to the number of rows (for the table of agora's screen conversation 5,554, 0.1-0.8 ms per comparison, and 11 ms over all rows on a tick that rebuilds the base).
;;; A child table made by with-writes (and by the draft's freeze) joins its parent's lineage if the parent has no child yet, and appends one list of written keys to the lineage record.
;;; Two tables in the same lineage are in an ancestor/descendant relation, so the only keys that can differ are the keys recorded between the two positions. A second child, a child whose record exceeds the larger of its row count and MIN-DELTA,
;;; and tables made by table-of or by copying start a new lineage (comparison then takes the older path: the delta if they share a base, otherwise all keys). The record holds only keys (no rows).
;;;
;;; What small tables hold: agora's screen holds 450,000 small per-row tables (reply lists, card relations and tags), and each table's empty delta dict, empty
;;; frozenset, and lineage (holding a dict and a Lock) took about 0.9 GB. The empty delta and the removed keys share one empty value per module
;;; (_NO-DELTA, _NO-REMOVED), and the lineage is created when the first child is made (a table that never makes a child has no lineage: _line is None).
(import dataclasses [dataclass])
(import collections.abc [Mapping])
(import threading [Lock])
(import typing [Generic TypeVar])

(setv V (TypeVar "V"))

;; Rebuild the base when the delta exceeds 1/COMPACT-RATIO of the base.
(setv COMPACT-RATIO 8)
;; While the base is small, do not lower the delta limit too far (do not rebuild every time on a small table).
(setv MIN-DELTA 64)
;; Messages for refusing truthiness and length reads (the same text for Table and TableDraft; they name the fix).
(setv TABLE-TRUTH-REFUSAL "Table / TableDraft is not read as a boolean; check emptiness with (= (.size t) 0) or (> (.size t) 0)")
(setv TABLE-LEN-REFUSAL "Table / TableDraft is not counted with len; read the row count with (.size t)")

;; The empty delta and empty removed keys (see "What small tables hold" in the header comment), shared by all tables. A delta is never modified (a write copies it into a new dict),
;; so nobody writes to the shared empty dict either.
(setv _NO-DELTA {})
(setv _NO-REMOVED (frozenset))
;; A lock that makes the decision to create a lineage for a table without one a single decision (even if two threads make the first child of the same parent, the parent has one lineage).
(setv _LINE-BIRTH (Lock))

(setv TABLE-MAPPING-EQ-REFUSAL "Do not compare a Table with a dict / Mapping using ==; it silently turns into false. To compare, convert to a dict such as (dict (zip (.keys t) (.rows t))) first (card E0)")


(defclass [(dataclass :frozen True)] TableWrite [(get Generic V)]
  "One write to a table: set the row at key to value (value = None deletes the row)."
  (#^ str key)
  (#^ (| V None) value))


(defclass _Line []
  "One lineage (see the header comment): log = position -> sequence of written keys (the entry at position i holds the keys of the write that made the child at position i + 1 from the table at position i; entries are only appended), keys = the number of
   keys recorded, lock = a lock that makes the decision to attach a child and the appending of the record a single step (even if two threads make a child of the same parent on free-threaded Python, only one is attached)."
  (setv __slots__ #("log" "keys" "lock"))

  (defn #^ None __init__ [self]
    (setv self.log {}
          self.keys 0
          self.lock (Lock))))


(defclass Table [(get Generic V)]
  "An immutable table (see the header comment). Built by table-of. Read with row, keys, rows, items, size; written with with-writes (returns a new Table)."
  (setv __slots__ #("_base" "_delta" "_removed" "_size" "_flat" "_line" "_at"))
  (#^ (get dict #(str V)) _base)
  (#^ (get dict #(str V)) _delta)
  (#^ (get frozenset str) _removed)
  (#^ int _size)
  ;; A copy of all rows (a dict combining the base and delta), built only once, the first time all rows are read (_flat-rows below). For a base-only table it is the base itself.
  (#^ (| (get dict #(str V)) None) _flat)
  ;; The lineage and the position within its record (see the header comment). A newly built table has no lineage (None) and position 0. The lineage is created (at position 0) when the first child is made,
  ;; and _on-line attaches a child to its parent's lineage.
  (#^ (| _Line None) _line)
  (#^ int _at)

  (defn #^ None __init__ [self #^ (get dict #(str V)) base #^ (get dict #(str V)) delta #^ (get frozenset str) removed]
    (object.__setattr__ self "_base" base)
    ;; Replace the empty delta and removed keys with the shared empty values (see "What small tables hold" in the header comment; the table does not keep the caller's empty values).
    (object.__setattr__ self "_delta" (if delta delta _NO-DELTA))
    (object.__setattr__ self "_removed" (if removed removed _NO-REMOVED))
    (object.__setattr__ self "_flat" None)
    (object.__setattr__ self "_line" None)
    (object.__setattr__ self "_at" 0)
    ;; Row count = base rows - removed base rows + delta rows not in the base. Counted in proportion to the delta and removed keys (does not iterate all base keys; `&` iterates the smaller side.
    ;; `(- (.keys delta) (.keys base))`, which iterates all base keys, was 65-96% of the cost of a one-row write; card E0).
    (object.__setattr__ self "_size" (+ (- (len base) (len (& removed (.keys base))))
                                        (- (len delta) (len (& (.keys delta) (.keys base)))))))

  (defn #^ (get dict #(str V)) _flat-rows [self]
    "A dict of all rows (base and delta combined, excluding removed keys), built only once per Table; the source for keys, rows, and items. Read only, never modified."
    (when (is-not self._flat None)
      (return self._flat))
    (setv flat (if (or self._delta self._removed)
                   (| self._base self._delta)
                   self._base))
    (when (and self._removed (is flat self._base))
      (setv flat (dict flat)))
    (for [key self._removed]
      (.pop flat key None))
    (object.__setattr__ self "_flat" flat)
    flat)

  (defn #^ (| V None) row [self #^ str key]
    "The row at key (None if absent)."
    (cond (in key self._delta) (get self._delta key)
          (in key self._removed) None
          True (.get self._base key)))

  (defn #^ (get tuple #(str ...)) keys [self]
    "The keys (the order is the same for keys, rows, and items)."
    (tuple (._flat-rows self)))

  (defn #^ (get tuple #(V ...)) rows [self]
    "The row values (in the same order as keys). For readers that read all rows (counting, listing)."
    (tuple (.values (._flat-rows self))))

  (defn #^ (get tuple #((get tuple #(str V)) ...)) items [self]
    "The (key, row) pairs (in the same order as keys and rows). For readers that read all rows of a table whose keys cannot be derived from the values, keys included (the persistence spelling)."
    (tuple (.items (._flat-rows self))))

  (defn #^ int size [self]
    "The number of rows."
    self._size)

  (defn #^ "Table[V]" with-writes [self #^ (get tuple #((get TableWrite V) ...)) writes]
    "A new Table with the sequence of writes applied (this Table is unchanged). If the same key is written twice in the sequence, the later one wins."
    ;; If the writes are empty, or every write is identical (by identity) to the current row, return this table as is (do not build a new table).
    (when (all (gfor write writes (is (.row self write.key) write.value)))
      (return self))
    ;; For a small table, apply the writes directly to the copy (do not build an intermediate key -> row dict; same result as the small-table path of _with-pending).
    (when (and (< (len self._base) MIN-DELTA) (not self._delta) (not self._removed))
      (setv written (dict self._base))
      (for [write writes]
        (if (is write.value None)
            (.pop written write.key None)
            (setv (get written write.key) write.value)))
      (return (_on-line self (_base-only-table written) (tuple (gfor write writes write.key)))))
    (_with-pending self (dfor write writes write.key write.value)))

  (defn #^ str __repr__ [self]
    (.format "Table(size={})" self._size))

  ;; Refuse truthiness and length reads (I0b of #2708). Without these two, Python always reads a table as true, and `(not table)` is
  ;; false even for an empty table (an early return on emptiness silently has no effect). Refusing finds such oversights as exceptions. Read size instead.
  (defn #^ bool __bool__ [self]
    "Refuse to be read as a boolean (see the comment above). Check emptiness with (= (.size t) 0) / (> (.size t) 0)."
    (raise (TypeError TABLE-TRUTH-REFUSAL)))

  (defn #^ int __len__ [self]
    "Refuse to be counted with len (see the comment above). Read the row count with (.size t)."
    (raise (TypeError TABLE-LEN-REFUSAL)))

  (defn #^ bool __eq__ [self #^ object other]
    "Compare by row contents (the same set of keys, and equal rows per key). A table is a value, so a copy and the original are equal if their contents are the same (how the content is split
     between base and delta does not matter). For the equality of frozen records that hold a table in a field (comparing the cache before and after a fold)."
    ;; The same table is equal without reading its contents (on a tick with no writes the fields before and after stay the same object, making the comparison O(1)).
    (when (is self other)
      (return True))
    ;; Refuse a comparison with a mapping instead of silently returning false (a Table is not a mapping; to compare row contents, convert with something like dict(zip(keys, rows)); card E0).
    (when (isinstance other Mapping)
      (raise (TypeError TABLE-MAPPING-EQ-REFUSAL)))
    (when (not (isinstance other (type self)))
      (return False))
    ;; Two tables that share a base (with-writes copies only the delta, so they point at the same base until the delta exceeds 1/COMPACT-RATIO of the base and the base is rebuilt) have
    ;; identical base rows. The keys whose presence or rows can differ are only those in either delta or removed keys. Comparing only those keys is enough (cost is proportional to the number of writes, not rows;
    ;; this is the per-tick comparison of a table that gets a few writes per tick. Comparing copies of the board in agora's screen used to read all rows of 2,088 cards).
    ;; Rows are checked by identity first, and only different objects are compared by value (same as changed-keys; for a table made by copying one side's delta and adding writes, the copied rows are the same object).
    ;; Comparing by value alone made the number of rows read vary with the order of iterating the key set (which changes per process because of string hash randomization).
    ;; Tables in the same lineage (one is a descendant of the other; see the header comment) only need the keys written between the two to be compared (proportional to the number of writes).
    (setv span (_line-span self other))
    (when (is-not span None)
      (return (and (= self._size other._size)
                   (all (gfor key span
                              :setv mine (.row self key)
                              :setv theirs (.row other key)
                              (or (is mine theirs) (= mine theirs)))))))
    (when (is self._base other._base)
      (return (all (gfor key (| (.keys self._delta) self._removed (.keys other._delta) other._removed)
                         :setv mine (.row self key)
                         :setv theirs (.row other key)
                         (or (is mine theirs) (= mine theirs))))))
    (and (= self._size (.size other))
         (all (gfor #(key value) (zip (.keys self) (.rows self)) (= (.row other key) value)))))

  (defn #^ (get tuple #(str ...)) changed-keys [self #^ "Table[V]" other]
    "The keys whose rows differ (including keys present in only one table; in ascending key order). This reports to the reader only the keys of rows that changed between the table previously handed to it and the current one (agora's
     screen puts the keys of changed board cards into its signal). The read path is the same as __eq__: the same table reads no rows, two tables sharing a base compare only the rows of
     the delta and removed keys (proportional to the number of writes, not rows), and otherwise all keys of both are compared. Rows are checked by identity first, and only different objects
     are compared by value (a key whose row was rewritten with an equal value is not reported; for tables that __eq__ reads as equal, the result is empty)."
    (when (is self other)
      (return #()))
    ;; For tables in the same lineage, only the keys written between the two (see the header comment; does not read the whole delta layer).
    (setv span (_line-span self other))
    (setv candidates (cond (is-not span None) span
                           (is self._base other._base)
                           (| (set (.keys self._delta)) self._removed (set (.keys other._delta)) other._removed)
                           True (| (set (.keys self)) (set (.keys other)))))
    (tuple (sorted (gfor key candidates
                         :setv mine (.row self key)
                         :setv theirs (.row other key)
                         :if (not (or (is mine theirs) (= mine theirs)))
                         key))))

  ;; A table compared by contents is not made a dictionary key (even though it is immutable, comparing reads all rows). Python sets __hash__ to None for a class that defines only __eq__.

  (defn #^ (get tuple #((get type "Table[V]") (get tuple #((get dict #(str V)) (get dict #(str V)) (get frozenset str))))) __reduce__ [self]
    "For copying (copy.copy, copy.deepcopy) and pickle: rebuild a table that refuses field writes through the constructor (__init__)."
    #(Table #(self._base self._delta self._removed)))

  (defn #^ None __setattr__ [self #^ str name #^ object value]
    (raise (AttributeError (.format "Table cannot be modified (tried to write field {!r})" name)))))


(defn #^ (get Table V) _base-only-table [#^ (get dict #(str V)) base]
  "Builds a table without a delta layer (base only) without recounting rows via set operations. This is the write path for small tables (row count = number of base rows).
   base accepts only a copy made inside this module (it is never modified after being built)."
  (setv table (object.__new__ Table))
  (object.__setattr__ table "_base" base)
  (object.__setattr__ table "_delta" _NO-DELTA)
  (object.__setattr__ table "_removed" _NO-REMOVED)
  (object.__setattr__ table "_size" (len base))
  (object.__setattr__ table "_flat" None)
  (object.__setattr__ table "_line" None)
  (object.__setattr__ table "_at" 0)
  table)


(defn #^ (get Table V) _on-line [#^ (get Table V) parent #^ (get Table V) child #^ (get tuple #(str ...)) keys]
  "Attaches the child table just made by a write to the parent's lineage (see the header comment). Only when the parent has no child yet (the parent's position is the end of the record) and the record
   does not exceed the larger of the child's row count and MIN-DELTA (a lineage record never grows beyond one table's worth), the written keys are appended to the record and the child is attached. A child that is not attached keeps the new lineage it was created with.
   child accepts only a table just made inside this module (not yet handed to anyone). Returns child.
   For a parent without a lineage (its first child), the lineage is created here (the parent stays at position 0, the same as a new lineage at creation)."
  (setv line parent._line)
  (when (is line None)
    (with [_LINE-BIRTH]
      (when (is parent._line None)
        (object.__setattr__ parent "_line" (_Line)))
      (setv line parent._line)))
  (with [line.lock]
    (setv at (len line.log))
    (when (and (= parent._at at)
               (<= (+ line.keys (len keys)) (max MIN-DELTA child._size)))
      (setv (get line.log at) keys)
      (setv line.keys (+ line.keys (len keys)))
      (object.__setattr__ child "_line" line)
      (object.__setattr__ child "_at" (+ at 1))))
  child)


(defn #^ (| (get frozenset str) None) _line-span [#^ (get Table V) left #^ (get Table V) right]
  "Answers the set of keys written between two tables in the same lineage (all keys that can differ; see the header comment). None if the lineages differ (the caller compares by the older path).
   The record is only appended to, so the entries between the two positions have not changed since the two tables were made. None also for a table without a lineage (it has made no child and is nobody's child in a lineage)."
  (when (or (is left._line None) (is-not left._line right._line))
    (return None))
  (setv low (min left._at right._at)
        high (max left._at right._at))
  (setv log left._line.log)
  (frozenset (gfor at (range low high) key (get log at) key)))


(defn #^ (get Table V) _with-pending [#^ (get Table V) table #^ (get dict #(str (| V None))) pending]
  "A new Table with writes of key -> row (None deletes the row) applied all at once (neither table nor pending changes). This is the single point
   that Table.with-writes and TableDraft.freeze pass through (internal to this module; it does not expose an entry point that passes mappings around). Building a TableWrite per write and applying it in a Hy loop
   made the draft's freeze (tens of thousands of writes) about 4 times heavier than copying a dict, so writes are applied with bulk dict operations (comprehensions, set
   difference and union, and | merging)."
  ;; If every write is identical to the current row (by identity; a delete write is for a key with no row), return the table as is.
  (when (all (gfor #(key value) (.items pending) (is (.row table key) value)))
    (return table))
  ;; A small table (no delta layer, base under MIN-DELTA rows) copies the base and writes to it; the cost of a delta layer is higher than the copy (see the header comment).
  ;; There are few writes (a few per tick), so apply them to the copy one at a time without building comprehensions and set operations.
  (when (and (< (len table._base) MIN-DELTA) (not table._delta) (not table._removed))
    (setv written (dict table._base))
    (for [#(key value) (.items pending)]
      (if (is value None)
          (.pop written key None)
          (setv (get written key) value)))
    (return (_on-line table (_base-only-table written) (tuple pending))))
  (setv puts (dfor #(key value) (.items pending) :if (is-not value None) key value)
        drops (frozenset (gfor #(key value) (.items pending) :if (is value None) key))
        delta (| (dfor #(key value) (.items table._delta) :if (not-in key drops) key value) puts)
        removed (| (- table._removed (.keys puts)) (& drops (.keys table._base))))
  ;; The child joins the parent's lineage (including a child that rebuilt the base; a lineage holds only the keys written between two tables, regardless of the base).
  (_on-line table
            (if (> (+ (len delta) (len removed)) (max MIN-DELTA (// (len table._base) COMPACT-RATIO)))
                (Table (| (dfor #(key value) (.items table._base) :if (not-in key removed) key value) delta) _NO-DELTA _NO-REMOVED)
                (Table table._base delta (frozenset removed)))
            (tuple pending)))


(defn #^ (get Table V) table-of [#^ (get tuple #((get TableWrite V) ...)) rows]
  "Builds a Table from a sequence of rows (rows with value = None are not included)."
  (Table (dfor write rows :if (is-not write.value None) write.key write.value) _NO-DELTA _NO-REMOVED))


;;; Draft (TableDraft): for a writer that rereads, within the same tick, rows it wrote in that tick.
;;;
;;; Why: the fold (the projection workspace of agora's screen) rereads each row within the same tick every time it writes one.
;;;   Applying with-writes one write at a time copies the delta (up to 1/COMPACT-RATIO of the base) for every write, so on a listing tick (tens of thousands of writes)
;;;   that comes to hundreds of millions of copies. A draft accumulates writes locally (O(1) per write), reads look up the draft first and then the original table, and at the end of the tick freeze applies
;;;   with-writes just once and returns a new Table.
;;; A draft is mutable, so it is not passed outside the writer that made it (what is handed out is the Table that freeze returns). The original table is not changed by the draft's writes.

(defclass TableDraft [(get Generic V)]
  "A draft of writes to a Table (see the comment above). Built by draft-of. Read with row, keys, rows; written with put, remove; freeze at the end of the tick."
  (setv __slots__ #("_table" "_writes"))
  (#^ (get Table V) _table)
  (#^ (get dict #(str (| V None))) _writes)

  (defn #^ None __init__ [self #^ (get Table V) table]
    (setv self._table table
          self._writes {}))

  (defn #^ (| V None) row [self #^ str key]
    "The row at key (this draft's writes take precedence; None if absent)."
    (if (in key self._writes)
        (get self._writes key)
        (.row self._table key)))

  (defn #^ (get tuple #(str ...)) keys [self]
    "The keys (after applying this draft's writes; the order is unspecified)."
    (+ (tuple (gfor key (.keys self._table) :if (not-in key self._writes) key))
       (tuple (gfor #(key value) (.items self._writes) :if (is-not value None) key))))

  (defn #^ (get tuple #(V ...)) rows [self]
    "The row values (after applying this draft's writes; the order is unspecified, but it is the same as keys)."
    (+ (tuple (gfor #(key value) (zip (.keys self._table) (.rows self._table)) :if (not-in key self._writes) value))
       (tuple (gfor value (.values self._writes) :if (is-not value None) value))))

  (defn #^ None put [self #^ str key #^ V value]
    "Set the row at key to value."
    (setv (get self._writes key) value))

  (defn #^ None remove [self #^ str key]
    "Delete the row at key (the key may be absent)."
    (setv (get self._writes key) None))

  (defn #^ (get Table V) freeze [self]
    "A new Table with the writes applied (the original table itself if there are no writes). May be called any number of times; neither the draft nor the original table changes."
    ;; A subclass of Table that keeps a value derived from its rows (it advances that value in its with-writes) receives the writes through
    ;; its with-writes, so the frozen table keeps the derived value; a plain Table takes the bulk path.
    (cond (not self._writes) self._table
          (is (type self._table) Table) (_with-pending self._table self._writes)
          True (.with-writes self._table (tuple (gfor #(key value) (.items self._writes) (TableWrite key value))))))

  (defn #^ int size [self]
    "The number of rows (after applying this draft's writes), so that the emptiness of a draft is read by count rather than truthiness."
    (len (.keys self)))

  (defn #^ bool __bool__ [self]
    "Refuse to be read as a boolean (same as Table; check emptiness with (= (.size d) 0))."
    (raise (TypeError TABLE-TRUTH-REFUSAL)))

  (defn #^ int __len__ [self]
    "Refuse to be counted with len (same as Table; the row count is (.size d))."
    (raise (TypeError TABLE-LEN-REFUSAL)))

  (defn #^ str __repr__ [self]
    (.format "TableDraft(base={!r}, writes={})" self._table (len self._writes))))


(defn #^ (get TableDraft V) draft-of [#^ (get Table V) table]
  "Makes a draft of writes to the table (the original table is unchanged)."
  (TableDraft table))
