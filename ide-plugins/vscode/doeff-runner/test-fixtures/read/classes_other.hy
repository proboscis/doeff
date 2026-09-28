;;; 別の module の同名の定義(placed-version)— 帯と used by の名に file 名を添えて見分けるテストの見本(#910 V11 で見つけたずれ)。
(require doeff-hy.macros [defk])
(import pkg.classes [PlacedVersion])


(defk placed-version [ref]
  {:pre [(: ref str)] :post [(: % PlacedVersion)]}
  "空の文の版を作るため。"
  (PlacedVersion ref "" None 0 0))
