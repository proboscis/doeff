;;; defclass のカード(agora-redesign #910 U17・v9)のテストの見本 — decorator の札・欄の型・縦の表・used by
;;; (arg of・returns・field of・made in)。
(require doeff-hy.macros [defk <-])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])


(defclass [(dataclass :frozen True)] PlacedVersion []
  "本文の行の版 1 つ: ref = 入力の id・text = 文。"
  (#^ str ref)
  (#^ str text)
  (#^ (| str None) request-id)
  (#^ int sent-at)
  (#^ int at))


(defclass [dataclass] Pair []
  "短い組。"
  (#^ str left)
  (#^ int right))


(defrecord InputVersions
  "置き場の入力の版の列の組。"
  (#^ (get tuple #(PlacedVersion ...)) placed))


(defk latest-by-ref [versions ref]
  {:pre [(: versions (get tuple #(PlacedVersion ...))) (: ref str)] :post [(: % (| PlacedVersion None))]}
  "ref の最新の版を返すため。"
  (next (gfor v (reversed versions) :if (= v.ref ref) v) None))


(defk placed-version [ref text]
  {:pre [(: ref str) (: text str)] :post [(: % PlacedVersion)]}
  "版を 1 つ作るため。"
  (PlacedVersion ref text None 0 0))
