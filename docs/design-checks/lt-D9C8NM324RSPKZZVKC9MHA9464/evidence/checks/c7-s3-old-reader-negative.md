# 確かめ c7-s3-old-reader-negative

- シナリオ: S3
- 種類: negative
- 期待: 前の版(4ca9fcd50)の行の判じは、印の記録を自分の記録より緩く扱わない — 完了した歩みとの組は古い(STALE)、import の途中の歩みとの組は自分の記録と同じく file の行で比べる(BY_FILES)。盲検の前の主張「前の版も古いと判じる」はこの後半で偽(S3 の実際に記した)
- 観測: 前の版の _row_check の本文そのものを走らせ: 印・完了 → STALE、印・import の途中 → BY_FILES、16 進どうし同じ → SAME

| 証拠の file(記録の dir からの path) | sha256 |
| --- | --- |
| evidence/revision/s3-old-reader.txt | 342f18860b819c7d83bb24d1eb712baacd12cb628477559d3245023d60154998 |

各 file の版と命令は design.md の「証拠の索引」。
