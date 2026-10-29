;;; Serialization protocol for Program transfer.
;;; cloudpickle by default, injectable via Ask("serializer").

(import dataclasses [dataclass])
(import typing [Any Protocol])
(import cloudpickle)


(defclass Serializer [Protocol]
  (defn dumps [self obj] ...)
  (defn loads [self data] ...))


(defclass [(dataclass :frozen True)] CloudpickleSerializer []
  "Default serializer using cloudpickle."
  (defn dumps [self obj]
    (cloudpickle.dumps obj))
  (defn loads [self data]
    (cloudpickle.loads data)))


(setv default-serializer (CloudpickleSerializer))
