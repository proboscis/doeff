# 型の穴の記事「公開APIの型の穴をテストで塞ぎ、黙って開かせない」の図の制作記録

## 2枚をSVGで描く（2026-10-18）

- 11本目からの候補7（doeff-types.md）の2枚（types-concept・types-flow）を、手で書いたSVGから作った。10本目（doeff-adr.md）と同じ作り方。
- 正本と作り方は[types-svg-source.txt](../types-svg-source.txt)に書いた。`tool`は10本目と同じ。
- types-conceptの「1 表のテスト」の副題は、はじめ「開発の環境のpytest」だった。表のテストはCIのPython Compatibility（pushとpull requestのたび）でも走ると確かめ、「pytest・変更のたびにCIでも走る」に直した。
- types-flowの点線の67個は、commit 78c7c3811の前後の木（9fe8c330b・78c7c3811）でverifytypesを数え、塞いだ名を照らした（開発の環境はPython 3.14で、dataclassの`__replace__`が1個多く数えられ68個になる。図と本文はPython 3.12の数）。
