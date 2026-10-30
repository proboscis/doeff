;;; 置き場の手入れ — 保持の期限を過ぎた行と出来事の回収、古い変更の列の刈り取り(記録の service の中で回す Program)。
;;;
;;; どれも公開 effect ではない(業務の Program は撃たない)。記録の service の composition root が手入れの係を 1 つ立て、
;;; maintenance-loop を回す。handler の組(memory・PostgreSQL)はどれも下の 5 つに答える。
;;;
;;;   SweepExpired   期限を過ぎた行を消して変更の列に RowRemoved を積み、期限を過ぎた出来事を捨てる。回収はこの effect の時だけ
;;;                  走る — 読みは回収せず期限を自分で見て(#3561)、書き(PutRow・PutRows・AppendEvent)は自分が触る行と出来事だけを
;;;                  片付ける(#3605 の D)。触られない期限を過ぎた行の RowRemoved は、係が撃つこの回収が積む。同じ回収が、覚え方
;;;                  (StreamDecl.key-memory)の長さを過ぎた鍵の覚えを外す(下の「鍵の覚えを外す」)
;;;   PruneChanges  keep-seconds より古い変更を変更の列から消し、floor(これより前の位置は Reset)を上げる。変更の列が際限なく
;;;                  伸びないようにする。floor より前の位置で WatchChanges を頼んだ読み手は Reset を受けて一覧から読み直す
;;;                  (keep-seconds は読み手の遅れの許容 — これより遅れた読み手だけが読み直す)。upto-seq を持てば、消すのはその番号まで
;;;                  (送りの答え — 下)
;;;   ReadChangeLog  変更の列を、after より後から番号の順に limit 件まで、表と保持の期限で絞らずに読む(送りの関数の読み — 下)。
;;;                  WatchChanges は頼んだ表の、読み手に見える変更だけを答えるので、送りがそれで読むと、置き場の今の宣言に無い
;;;                  (退役した)表の変更と期限を過ぎた終端の行の変わりを送らずに刈る(card ki-e4ca6fc87109 — 2026-10-11 の使い手の本番で、
;;;                  退役した 4 表の変更 約 22,450 行が送り先に無いまま刈られた)
;;;
;;; 消す前に送る(card ki-c32e43651f2e・親 ki-e26367fd8fcb — 2026-10-11 11:21 に使い手の記録の Postgres の disk が 100% に
;;; なった。古い変更を消さずに階層つきの保管へ送ってから外す): 手入れの計画が送りの関数 archive(keep-seconds → Program)を持てば、
;;; 刈りの前にそれを走らせ、答え ArchivedThrough の番号までだけを刈る(送りが Unreachable の回は刈らない)。送りは書きの錠の外で走る
;;; (送り先の I/O で書き手を止めない)。送り先とその効果は使い手の composition が決める(この package は送り先を知らない)。
;;;
;;; 出来事を送ってから外す(card ki-707aad0f88e8): 宣言 StreamDecl.archive-before-retire の列の、保持の期限を過ぎた出来事は、回収
;;; (SweepExpired)も書き(追記が触る鍵・組の片付け)も置き場から外さない — 書きは冪等キーを覚えるだけ(読みと冪等の答えは前と同じ)。
;;; 外すのは手入れの計画の出来事の送りの関数 archive-events(出来事の頁 → 答えが EventsArchived | Unreachable の Program)が
;;; 送り先に在ると確かめた出来事だけ。手入れの回は刻 T を 1 度読み、その境で期限を過ぎた出来事を頁で読み(ReadExpiredEvents)、
;;; 頁ごとに送り、確かめた番号のうち頁に在った物だけを同じ刻 T の境で外す(RetireArchivedEvents — 読みと外しの間に新しく期限を
;;; 過ぎた出来事は頁に無く、境の読み直しでも外れない)。送りが Unreachable の頁で回を終える(その頁と残りは次の回)。
;;;
;;; 鍵の覚えを外す(card ki-4ebaefac6668): 置き場を去った出来事の冪等キーの覚え(values.RetiredKey)は、前は外さず、列に積んだ出来事の数
;;; だけ際限なく育った。列の宣言の覚え方 StreamDecl.key-memory(_values.hy の KeyMemory の註)が RememberKeysFor の列は、覚えた刻から
;;; 宣言の長さを過ぎた覚えを SweepExpired が外し(答え Swept.forgotten)、RememberNoKeys の列は出来事が置き場を去る時に覚えを残さない。
;;; 送ってから外す列の覚えは、出来事が送り先に在ると確かめて外れるまで外さない — 送り先に届かない間は出来事も覚えも置き場に残り、
;;; 送り直しの答えは変わらない。外れた覚えの中身(鍵・番号・本文)は送り先の出来事の行が持つので、覚えの行は送らずに外す(送り先の無い
;;; 列の覚えは、出来事を送らずに捨てた列の物で、送る先が無い)。外した後の同じ鍵の追記は新しい出来事(置き場がその鍵を知らない)。
;;;
;;;   ReadExpiredEvents    印の列の、刻 at の境で期限を過ぎた出来事(書きが覚えた鍵の出来事を含む)を、列の名の順・列の中は
;;;                        積んだ刻と番号の順に limit 件まで読む(1 頁 = 1 つの列)。after = 前の頁の続きの位置(None = 頭から)
;;;   RetireArchivedEvents 列 stream の番号 sequences の出来事のうち、刻 at の境で今も期限を過ぎた物だけを外し、冪等キーを覚える
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "records" :role "program"})
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import doeff [EffectBase])
(import doeff_time [Delay GetTime])
(import doeff_records._values [Event RowChanged RowRemoved Unreachable])
(import datetime [datetime])

;; 出来事の送りの 1 頁の出来事の数の上限(送りの関数に渡す 1 回の量 — 外しの書きの錠の中で触る行もこの数で縛る)。
(val ARCHIVE-PAGE 500)


;; 答えの class は答える effect より前に置く(effect が基底 EffectBase[答え] で答えの型を名乗る — ADR-DOE-API-001 R2)。
(defclass [(dataclass :frozen True)] Swept []
  "回収の結果: rows = 消した行の数(変更の列に RowRemoved を積んだ数)/ forgotten = 外した鍵の覚えの数(覚え方の長さを過ぎた覚え —
   _values.hy の KeyMemory の註)/ write-keys = 外した行の書きの冪等キーの覚えの数(覚えた刻から values.WRITE-KEY-MEMORY-SECONDS を過ぎた
   覚え — card ki-78c6d663491f)。"
  (#^ int rows)
  (setv #^ int forgotten 0)
  (setv #^ int write-keys 0))


(defclass [(dataclass :frozen True)] Pruned []
  "刈り取りの結果: floor = 刈った後の floor(この位置以上の WatchChanges は続けられる)/ removed = 消した変更の数。"
  (#^ int floor)
  (#^ int removed))


(defclass [(dataclass :frozen True)] ChangeLog []
  "変更の列の生の読みの答え: changes = 番号の昇順の変更(空 = after より後に無い)/ epoch = 置き場の版。"
  (#^ (get tuple #((| RowChanged RowRemoved) ...)) changes)
  (#^ int epoch))


(defclass [(dataclass :frozen True)] SweepExpired [(get EffectBase (| Swept Unreachable))]
  "保持の期限を過ぎた行と出来事を回収する。答え = Swept | Unreachable。")


(defclass [(dataclass :frozen True)] PruneChanges [(get EffectBase (| Pruned Unreachable))]
  "keep-seconds 秒より古い変更を変更の列から消す。upto-seq = 消してよい番号の上限(送って確かめた番号 — None = 上限なし・送りを持たない
   計画)。答え = Pruned | Unreachable。"
  (#^ float keep-seconds)
  (setv #^ (| int None) upto-seq None)
  (defn #^ None __post_init__ [self]
    (when (or (isinstance self.keep-seconds bool) (not (isinstance self.keep-seconds #(int float))) (< self.keep-seconds 0))
      (raise (ValueError (.format "PruneChanges.keep_seconds は 0 以上の秒: {!r}" self.keep-seconds))))
    (when (and (is-not self.upto-seq None) (or (isinstance self.upto-seq bool) (not (isinstance self.upto-seq int)) (< self.upto-seq 0)))
      (raise (ValueError (.format "PruneChanges.upto_seq は 0 以上の番号か None: {!r}" self.upto-seq))))))


(defclass [(dataclass :frozen True)] ReadChangeLog [(get EffectBase (| ChangeLog Unreachable))]
  "変更の列の、番号 after より後の変更を番号の順に limit 件まで、表と保持の期限で絞らずに読む。答え = ChangeLog | Unreachable。"
  (#^ int after)
  (#^ int limit)
  (defn #^ None __post_init__ [self]
    (when (or (isinstance self.after bool) (not (isinstance self.after int)) (< self.after 0))
      (raise (ValueError (.format "ReadChangeLog.after は 0 以上の番号: {!r}" self.after))))
    (when (or (isinstance self.limit bool) (not (isinstance self.limit int)) (< self.limit 1))
      (raise (ValueError (.format "ReadChangeLog.limit は 1 以上の数: {!r}" self.limit))))))


(defclass [(dataclass :frozen True)] ArchivedThrough []
  "送りの答え: seq = 送り先に在ると確かめた変更の番号の上限(この番号までは刈ってよい — 0 = 1 つも確かめていない)。"
  (#^ int seq))


(defclass [(dataclass :frozen True)] ExpiredCursor []
  "ReadExpiredEvents の続きの位置: stream = 列の名 / at・sequence = その列で前の頁の最後の出来事の刻と番号(None = 列の頭から)。"
  (#^ str stream)
  (setv #^ (| int None) at None)
  (setv #^ (| int None) sequence None)
  (defn #^ None __post_init__ [self]
    (when (!= (is self.at None) (is self.sequence None))
      (raise (ValueError (.format "ExpiredCursor の at と sequence は両方在るか両方 None: {!r}" self))))))


(defclass [(dataclass :frozen True)] ExpiredEvents []
  "ReadExpiredEvents の答え: items = 1 つの列の出来事(積んだ刻と番号の順 — 空もある)/ next = 続きの位置(None = 印の列の全部を
   読み終えた)。"
  (#^ (get tuple #(Event ...)) items)
  (#^ (| ExpiredCursor None) next))


(defclass [(dataclass :frozen True)] ReadExpiredEvents [(get EffectBase (| ExpiredEvents Unreachable))]
  "印(StreamDecl.archive-before-retire)の列の、刻 at(GetTime の答え — 境の秒は handler が epoch ミリ秒にする)の境で保持の期限を過ぎた出来事を、after の位置から limit 件まで
   読む(頭の註)。答え = ExpiredEvents | Unreachable。"
  (#^ datetime at)
  (setv #^ (| ExpiredCursor None) after None)
  (setv #^ int limit ARCHIVE-PAGE)
  (defn #^ None __post_init__ [self]
    (when (not (isinstance self.at datetime))
      (raise (TypeError (.format "ReadExpiredEvents.at は datetime: {!r}" self.at))))
    (when (not (or (is self.after None) (isinstance self.after ExpiredCursor)))
      (raise (TypeError (.format "ReadExpiredEvents.after は ExpiredCursor | None: {!r}" self.after))))
    (when (or (isinstance self.limit bool) (not (isinstance self.limit int)) (< self.limit 1))
      (raise (ValueError (.format "ReadExpiredEvents.limit は 1 以上の数: {!r}" self.limit))))))


(defclass [(dataclass :frozen True)] RetiredEvents []
  "RetireArchivedEvents の答え: count = 外した出来事の数。"
  (#^ int count))


(defclass [(dataclass :frozen True)] RetireArchivedEvents [(get EffectBase (| RetiredEvents Unreachable))]
  "列 stream の番号 sequences(送り先に在ると確かめた出来事)のうち、刻 at の境で今も保持の期限を過ぎた出来事だけを置き場から外し、
   冪等キーを覚える。答え = RetiredEvents | Unreachable。"
  (#^ str stream)
  (#^ datetime at)
  (#^ (get tuple #(int ...)) sequences)
  (defn #^ None __post_init__ [self]
    (when (not (isinstance self.at datetime))
      (raise (TypeError (.format "RetireArchivedEvents.at は datetime: {!r}" self.at))))
    (when (not (and (isinstance self.sequences tuple)
                    (all (gfor n self.sequences (and (isinstance n int) (not (isinstance n bool)) (> n 0))))))
      (raise (ValueError (.format "RetireArchivedEvents.sequences は正の番号の tuple: {!r}" self.sequences))))))


(defclass [(dataclass :frozen True)] EventsArchived []
  "出来事の送りの関数の答え: sequences = 渡された頁のうち送り先に在ると確かめた出来事の番号(空 = 1 つも確かめていない)。"
  (#^ (get tuple #(int ...)) sequences))


(defclass [(dataclass :frozen True)] EventsArchiveReport []
  "手入れ 1 回の出来事の送りの結果(係の記録と計器のため): sent = 送りの関数に渡した出来事の数 / confirmed = そのうち送り先に在ると
   確かめた数 / unreached = 送りが届かなかった(Unreachable の)頁の出来事の数 / retired = 置き場から外した数 / halted = 回を途中で
   終えた訳(送り先か置き場に届かなかった Unreachable・None = 期限を過ぎた出来事を読み終えた)。"
  (#^ int sent)
  (#^ int confirmed)
  (#^ int unreached)
  (#^ int retired)
  (setv #^ (| Unreachable None) halted None))


(defclass [(dataclass :frozen True)] MaintenanceReport []
  "手入れ 1 回の結果(係の記録と計器のため)。置き場に届かなかった手入れは Unreachable(次の回で撃ち直す)。archived = 送りの答え
   (送りを持たない計画は None・送り先に届かなかった回は Unreachable で、その回は刈らない — pruned も同じ Unreachable)。
   events = 出来事の送りの結果(出来事の送りの関数を持たない計画は None)。"
  (#^ (| Swept Unreachable) swept)
  (#^ (| Pruned Unreachable) pruned)
  (setv #^ (| ArchivedThrough Unreachable None) archived None)
  (setv #^ (| EventsArchiveReport None) events None))


(defk prune-after-archive [keep-seconds archive]
  {:pre [(: keep-seconds (| int float)) (: archive (| Callable None))] :post [(: % MaintenanceReport)]}
  "古い変更を、送りの関数 archive が在れば送って確かめた番号までだけ刈るため(頭の註の「消す前に送る」)。答えの swept は呼び手が埋める。"
  (when (is archive None)
    (<- pruned (PruneChanges keep-seconds))
    (return (MaintenanceReport (Swept 0) pruned)))
  (<- archived (archive keep-seconds))
  (match archived
    (ArchivedThrough :seq seq) (do (<- bounded (PruneChanges keep-seconds :upto-seq seq))
                                   (MaintenanceReport (Swept 0) bounded archived))
    (Unreachable) (MaintenanceReport (Swept 0) archived archived)
    _ (raise (TypeError (.format "手入れの送りの答えが ArchivedThrough でも Unreachable でもない: {!r}" archived)))))


(defk archived-page [archive-events at page]
  {:pre [(: archive-events Callable) (: at datetime) (: page ExpiredEvents)] :post [(: % EventsArchiveReport)]}
  "期限を過ぎた出来事の頁 1 つを送りの関数で送り、送り先に在ると確かめた番号のうち頁に在った物だけを、読みと同じ刻 at の境で外すため
   (頭の註の「出来事を送ってから外す」— 頁に無い番号を答えても外さない)。送りが届かなければ外さず、halted に Unreachable を置く。"
  (<- answer (archive-events page.items))
  (match answer
    (Unreachable) (EventsArchiveReport :sent (len page.items) :confirmed 0 :unreached (len page.items) :retired 0 :halted answer)
    (EventsArchived :sequences sequences)
      (do (val read (frozenset (gfor event page.items event.sequence)))
          (val confirmed (tuple (sorted (frozenset (gfor n sequences :if (in n read) n)))))
          (when (not confirmed)
            (return (EventsArchiveReport :sent (len page.items) :confirmed 0 :unreached 0 :retired 0)))
          (<- retired (RetireArchivedEvents (. (get page.items 0) stream) at confirmed))
          (match retired
            (RetiredEvents :count count) (EventsArchiveReport :sent (len page.items) :confirmed (len confirmed) :unreached 0 :retired count)
            (Unreachable) (EventsArchiveReport :sent (len page.items) :confirmed (len confirmed) :unreached 0 :retired 0 :halted retired)
            _ (raise (TypeError (.format "RetireArchivedEvents の答えが RetiredEvents でも Unreachable でもない: {!r}" retired)))))
    _ (raise (TypeError (.format "出来事の送りの答えが EventsArchived でも Unreachable でもない: {!r}" answer)))))


(defk archive-expired-events [archive-events]
  {:pre [(: archive-events Callable)] :post [(: % EventsArchiveReport)]}
  "印の列の期限を過ぎた出来事を、刻 T を 1 度読んで、その境で頁ごとに送って確かめた物だけ外すため(頭の註の「出来事を送ってから外す」)。
   置き場か送り先に届かない頁で回を終える(残りは次の回 — 外していないので、次の回の読みにまた出る)。"
  (<- now (GetTime))
  (var after None)
  (var total (EventsArchiveReport :sent 0 :confirmed 0 :unreached 0 :retired 0))
  (while True
    (<- page (ReadExpiredEvents now :after after))
    (when (isinstance page Unreachable)
      (return (EventsArchiveReport :sent total.sent :confirmed total.confirmed :unreached total.unreached :retired total.retired
                                   :halted page)))
    (when page.items
      (<- done (archived-page archive-events now page))
      (:= total (EventsArchiveReport :sent (+ total.sent done.sent) :confirmed (+ total.confirmed done.confirmed)
                                     :unreached (+ total.unreached done.unreached) :retired (+ total.retired done.retired)
                                     :halted done.halted))
      (when (is-not done.halted None)
        (return total)))
    (when (is page.next None)
      (return total))
    (:= after page.next)))


(defk maintenance-tick [keep-seconds archive [archive-events None]]
  {:pre [(: keep-seconds (| int float)) (: archive (| Callable None)) (: archive-events (| Callable None))] :post [(: % MaintenanceReport)]}
  "手入れを 1 回行う: 期限切れを回収し、出来事の送りの関数が在れば印の列の期限を過ぎた出来事を送って確かめた物だけ外してから、古い変更を
   (送りが在れば送って確かめた所まで)刈る(回収が積んだ RowRemoved は新しいので刈られない)。"
  (<- swept (SweepExpired))
  (var events None)
  (when (is-not archive-events None)
    (<- sent (archive-expired-events archive-events))
    (:= events sent))
  (<- report (prune-after-archive keep-seconds archive))
  (MaintenanceReport swept report.pruned report.archived events))


(defk maintenance-loop [interval-seconds keep-seconds archive ticks [archive-events None]]
  {:pre [(: interval-seconds (| int float)) (: keep-seconds (| int float)) (: archive (| Callable None)) (: ticks (| int None))
         (: archive-events (| Callable None))]
   :post [(: % list)]}
  "記録の service の手入れの係の本体: interval-seconds ごとに maintenance-tick を撃つ。archive = 送りの関数(keep-seconds → 答えが
   ArchivedThrough | Unreachable の Program・None = 送らない)。ticks = 回数(None = 止めるまで)。archive-events = 出来事の送りの関数
   (期限を過ぎた出来事の頁 → 答えが EventsArchived | Unreachable の Program・None = 送らない — 頭の註)。答え = 手入れの結果の列(ticks が
   None の時は返らない)。"
  (setv reports [] count 0)
  (while (or (is ticks None) (< count ticks))
    (<- report (maintenance-tick keep-seconds archive archive-events))
    ;; 止めるまで回す係は結果を溜めない(溜めると際限なく伸びる)。
    (when (is-not ticks None) (.append reports report))
    (+= count 1)
    (when (or (is ticks None) (< count ticks))
      (<- (Delay interval-seconds))))
  reports)
