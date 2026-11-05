;;; Generic channel effects (the first consumer = the intake of the processing loop of agora's screen -- reads, in arrival order from one channel, a socket message, a wake-up,
;;; a change in the record table, and the answers of Spawned tasks; btc-w1 #811). A foundation vocabulary with no business terms.
;;; There is only one responder, scheduler-channel-handler (scheduler_channel.hy) -- it has no I/O and waits on the same run's scheduler promise, so production and mocks use the
;;; same one (nothing to swap). Use it under scheduled.
;;;
;;;   CreateChannel   Create a new empty channel. Answer = Channel (the channel handle).
;;;   PutChannel      Put one item at the end of the channel. Does not wait (the channel has no limit). Answer = None.
;;;   TakeChannel     Take one item from the head of the channel. If empty, only the task that fired waits until something is put (other tasks keep running). Arrival order (FIFO).
;;;
;;; Only tasks of the same run can put (there is no entrance to put from an outside thread -- events of an outside thread are put by PutChannel from a task that waited on a promise
;;; that the responder completes from outside).
(require doeff-hy.record [defrecord])
(import collections [deque])
(import dataclasses [dataclass field])
(import doeff [EffectBase])
(import doeff_core_effects._scheduler [Promise])


(defclass [(dataclass :eq False)] Channel []
  "The handle of a channel (a resource, not a value -- the responder rewrites its items and waiters. Compared by identity). Touched only inside the scheduler's thread."
  #^ (get deque object) items
  (setv items (field :default-factory deque))
  #^ (get list (get Promise None)) waiters
  (setv waiters (field :default-factory list)))


(defclass [(dataclass :frozen True)] CreateChannel [EffectBase]
  "Create a new empty channel (see the header comment). Answer = Channel.")


(defclass [(dataclass :frozen True)] PutChannel [EffectBase]
  "Put one item at the end of the channel (see the header comment). Answer = None."
  (#^ Channel channel)
  (#^ object item))


(defclass [(dataclass :frozen True)] TakeChannel [EffectBase]
  "Take one item from the head of the channel -- if empty, wait until something is put (see the header comment). Answer = the value that was put."
  (#^ Channel channel))
