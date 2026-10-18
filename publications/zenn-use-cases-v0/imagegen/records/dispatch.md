# 配る判断の記事の図の制作記録

## 3枚をSVGで描く（2026-10-18）

- 11本目からの候補2の下書き（doeff-dispatch.md）は、概念図・処理図に、配った枚数と急ぎの待ちを比べる図を足して3枚にした。
- 正本と作り方は[dispatch-svg-source.txt](../dispatch-svg-source.txt)に書いた。`tool`は5本目の開発の記録と同じ。
- 3枚目はcaptionsの`extra_kinds`に書き、`verify_publication.py`が台帳と本文を照らす。
- 比べの図の最初の版は高さ900で、下の段の目盛りと注が重なった。高さを1000にし、下の段を25px下げて描き直した。
- 概念図の最初の版は、右の列の矢印の字（`ReadReady・ReadFree`・`Dispatch(…)`）が右の端からはみ出した。字を矢印の左へ移して描き直した。
