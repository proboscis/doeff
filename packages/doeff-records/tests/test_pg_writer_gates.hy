;; 同じ process の書きの並び(_pg.hy の writer-gates — card ki-0306f5a614b4)の、単位ごとの並びの表(session の値 gates)の検。
;; 起動の直後は表が空で、初めての単位の書きが並びを作る。前の版は単位の並びを 1 つ待つ(AcquireSemaphore で他の task に譲る)たびに
;; 次の単位を節の入口で読んだ古い表で調べて書き戻したので、待つ間に他の書きが足した単位を表から消し、その書きの ReleaseWriters が
;; KeyError で落ちた(記録の service の宣言し直しの 8 秒後の put-rows が 500 internal — card ki-2b1142ac9146・2026-10-23 21:35:54 UTC)。
;; 同じ単位に 2 つ目の並びを作って 1 本ずつの約束も破れた。PostgreSQL は使わない(並びは接続を借りる前の scheduler の semaphore だけ)。
(require doeff-hy.macros [defk deftest <- val])
(import doeff [run with_handlers])
(import doeff_core_effects [Get Put])
(import doeff_core_effects [state])
(import doeff_core_effects [scheduled Spawn Wait CreatePromise CompletePromise Promise])
(import doeff_records [AdmitWriters ReleaseWriters writer-gates])


(val LOG-KEY "writer-gates-test/log")


(defk noted [entry]
  {:pre [(: entry tuple)] :post [(: % None)]}
  "通った順と返した順の 1 つ entry を、状態の LOG-KEY の列の末に足す(Get と Put の間で他の task に譲らない)。"
  (<- seen (| tuple None) (Get LOG-KEY))
  (<- (Put LOG-KEY (+ (or seen #()) #(entry))))
  None)


(defk holding [units entered leave]
  {:pre [(: units tuple) (: entered Promise) (: leave Promise)] :post [(: % None)]}
  "units の並びを通り、通った事を entered に知らせ、leave が完了するまで持ってから返す(通った順と返した順を noted で積む)。"
  (<- (AdmitWriters units))
  (<- (noted #("admitted" units)))
  (<- (CompletePromise entered None))
  (<- (Wait leave.future))
  (<- (ReleaseWriters units))
  (<- (noted #("released" units)))
  None)


(defk racing-first-writes []
  {:pre [] :post [(: % tuple)]}
  "起動の直後の空の表で、初めての単位の書き 3 本を重ねる: A が u1 を持つ間に B(u1 と u2)が u1 で待ち、その間に C が u3 の並びを作る。
   A が u1 を返すと B が u1 を通って u2 へ進み、C は u3 を返す。答え = 通った順と返した順の列。"
  (<- a-in (CreatePromise))
  (<- a-out (CreatePromise))
  (<- b-in (CreatePromise))
  (<- b-out (CreatePromise))
  (<- c-in (CreatePromise))
  (<- c-out (CreatePromise))
  (<- a (Spawn (holding #("u1") a-in a-out)))
  (<- (Wait a-in.future))
  (<- b (Spawn (holding #("u1" "u2") b-in b-out)))
  (<- c (Spawn (holding #("u3") c-in c-out)))
  (<- (Wait c-in.future))
  (<- (CompletePromise a-out None))
  (<- (Wait a))
  (<- (Wait b-in.future))
  (<- (CompletePromise c-out None))
  (<- (Wait c))
  (<- (CompletePromise b-out None))
  (<- (Wait b))
  (<- log tuple (Get LOG-KEY))
  log)


(defk same-unit-after-a-wait []
  {:pre [] :post [(: % tuple)]}
  "B(u1 と u2)が u1 で待つ間に C が u2 の並びを作って持つ。B は u1 を通った後、C の u2 の並びの後ろで待つ(u2 に 2 つ目の並びを
   作って C と同時に持たない)。答え = 通った順と返した順の列。"
  (<- a-in (CreatePromise))
  (<- a-out (CreatePromise))
  (<- b-in (CreatePromise))
  (<- b-out (CreatePromise))
  (<- c-in (CreatePromise))
  (<- c-out (CreatePromise))
  (<- a (Spawn (holding #("u1") a-in a-out)))
  (<- (Wait a-in.future))
  (<- b (Spawn (holding #("u1" "u2") b-in b-out)))
  (<- c (Spawn (holding #("u2") c-in c-out)))
  (<- (Wait c-in.future))
  (<- (CompletePromise a-out None))
  (<- (Wait a))
  (<- (CompletePromise c-out None))
  (<- (Wait c))
  (<- (Wait b-in.future))
  (<- (CompletePromise b-out None))
  (<- (Wait b))
  (<- log tuple (Get LOG-KEY))
  log)


(defn gated [program]
  "program を本番と同じ並び(writer-gates)と session の値の置き場(state)の下で、scheduler 1 つで走らせる。"
  (run (scheduled (with_handlers [(state) writer-gates] program))))


(deftest test-a-first-write-waiting-on-one-unit-does-not-drop-units-other-writers-made
  (val log (gated (racing-first-writes)))
  (assert (= log #(#("admitted" #("u1")) #("admitted" #("u3")) #("released" #("u1")) #("admitted" #("u1" "u2"))
                  #("released" #("u3")) #("released" #("u1" "u2"))))
          log))


(deftest test-a-write-that-waited-queues-behind-the-gate-another-writer-made-meanwhile
  (val log (gated (same-unit-after-a-wait)))
  ;; B は C が u2 を返すまで通らない(同じ単位を 2 本が同時に持たない)。
  (assert (= log #(#("admitted" #("u1")) #("admitted" #("u2")) #("released" #("u1")) #("released" #("u2"))
                  #("admitted" #("u1" "u2")) #("released" #("u1" "u2"))))
          log))
