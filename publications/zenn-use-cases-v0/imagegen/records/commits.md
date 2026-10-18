# 開発の記録「ライブラリのcommitの68%を、Claude Codeと書いた1年」の図の制作記録

## 3枚をSVGで描く（2026-10-18）

- 11本目からの候補6（doeff-commits.md）は、概念図・処理図に、月ごとのcommitの数の図を足して3枚にした。5本目（doeff-fleet.md）と同じ作り方。
- 正本と作り方は[commits-svg-source.txt](../commits-svg-source.txt)に書いた。`tool`は5本目と同じ。
- 3枚目は`../../visuals/captions/commits.json`の`extra_kinds`に書き、`verify_publication.py`が台帳と本文を照らす。
- 最初の表は、作業の機体に時刻帯の表が無く、`TZ=Asia/Tokyo`がUTCのまま月を分けていた（10月が2,075個になった）。author dateの秒をUTC+9に直して数え直し、表と図と本文を2,114個に揃えた。
- 概念図では、4つの網をmainの前の門として描かない。網は毎日のテスト・CI・PyPIからの入れ直しで走り、赤くなれば直す仕事を会話へ頼む。
