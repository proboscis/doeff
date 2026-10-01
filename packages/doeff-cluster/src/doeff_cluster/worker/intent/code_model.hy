;;; 展開したコードの木の bytecode の準備の effect — 走査・引き継ぎ・焼き・import の閉包・完成の印・経過の注記(焼きの Program
;;; worker/core/code_prepare が出し、言い換え worker/protocol/tree_files の tree-files が汎用の file の効果へ出し直す — #2468)。code_prepare.hy から分けた(#2027)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import doeff [EffectBase])

;; 子 process の import の根の既定(木の根そのもの)— effect の欄の既定値と、純粋な判断の既定の引数が同じ値を読む。
(setv DEFAULT-IMPORT-ROOTS #("."))


(defclass [(dataclass :frozen True)] ScanTree [EffectBase]
  "結果 = #(source の相対 path の列 .pyc の相対 path の列)(隠し file と __pycache__ の外の .pyc は除く)。"
  (#^ str tree))


(defclass [(dataclass :frozen True)] LinkPycs [EffectBase]
  "old の .pyc を new の同じ相対 path へ hardlink する。結果 = 張った数。"
  (#^ str old)
  (#^ str new)
  (#^ tuple pycs))


(defclass [(dataclass :frozen True)] CompileSources [EffectBase]
  "結果 = 焼けなかった物の #(相対 path 理由) の列。"
  (#^ str tree)
  (#^ tuple items)
  (#^ int jobs)
  (setv #^ tuple roots DEFAULT-IMPORT-ROOTS))


(defclass [(dataclass :frozen True)] ImportClosure [EffectBase]
  "結果 = entries(module 名)の import の閉包に入る source の相対 path の frozenset(焼く範囲を絞るため)。"
  (#^ str tree)
  (#^ tuple sources)
  (#^ tuple entries)
  (#^ tuple roots))


(defclass [(dataclass :frozen True)] WriteMarker [EffectBase]
  "完成の印を木の根へ置く(別の file へ書いて置き換える)。"
  (#^ str tree)
  (#^ dict content))


(defclass [(dataclass :frozen True)] Note [EffectBase]
  (#^ str line))
