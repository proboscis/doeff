# 開発の記録の記事の図の制作記録

記事ごとの制作記録。はじめ[imagegen/README.md](../README.md)の末尾へ書き足していた節を、2026-10-17に文を変えずにこのファイルへ移した。文中の`manifest.json`・`captions.json`は、今は記事ごとの`../manifest/fleet.json`・`../../visuals/captions/fleet.json`である。

## 開発の記録の3枚をSVGで描く（2026-10-17追記）

- 5週目の開発の記録（doeff-fleet.md）は、概念図・処理図に、35日のcommitの数の図を足して3枚にした。
- 正本と作り方は[fleet-svg-source.txt](../fleet-svg-source.txt)に書いた。`tool`は時間の記事と同じ。
- 3枚目はcaptions.jsonの`extra_kinds`に書き、`verify_publication.py`が台帳と本文を照らす。
