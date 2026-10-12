(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass])
(defclass Outer [] (defrecord Span {:check [(>= start 0)]} #^ int start) (defwire W {:names :camel} #^ int start-at))
