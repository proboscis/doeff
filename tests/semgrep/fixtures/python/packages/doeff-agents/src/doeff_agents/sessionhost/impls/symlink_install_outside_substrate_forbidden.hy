;; 陽性対照(発火する 2 行)— 据え付けの物理を 1 点の外で組み直す綴り。
(os.symlink target link)
(.symlink_to (Path link) target)
;; 弁別(発火しない)— 1 点へ委ねる形・読みだけの syscall・張り替えの動詞そのもの。
(fs-ensure-symlink link target)
(os.readlink link)
(ensure-symlink-outcome link target)
