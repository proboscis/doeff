# 第21章: 他の言語との比較

## この章で学ぶこと

- 代数的エフェクトを持つ言語
- 各言語のアプローチの違い
- doeff の位置づけ

---

## 21.1 OCaml 5

### 特徴

- 2022年にリリースされた産業用言語初の組み込みエフェクト
- 言語レベルでのサポート
- Shallow handlers

```ocaml
effect Get : int
effect Set : int -> unit

let handler () =
  let state = ref 0 in
  try_with f ()
    { effc = fun (type a) (eff : a Effect.t) ->
      match eff with
      | Get -> Some (fun k -> continue k !state)
      | Set x -> Some (fun k -> state := x; continue k ())
      | _ -> None }
```

### doeff との違い

| OCaml 5 | doeff |
|---------|-------|
| 言語組み込み | ライブラリ |
| 静的型によるエフェクト追跡 | 動的（実行時） |
| Shallow handlers | ジェネレータベース |
| 高性能 | Python 速度 |

---

## 21.2 Koka

### 特徴

- 研究言語として設計
- エフェクト型システム
- 行多相エフェクト

```koka
effect state<s>
  fun get() : s
  fun set(x : s) : ()

fun counter() : state<int> int
  val i = get()
  set(i + 1)
  i
```

### doeff との違い

| Koka | doeff |
|------|-------|
| エフェクト型 | 型なし（動的） |
| 研究用 | 実用指向 |
| 小さなエコシステム | Python エコシステム活用 |

---

## 21.3 Eff

### 特徴

- 代数的エフェクトの研究のための言語
- Andrej Bauer と Matija Pretnar が設計
- エフェクトの理論的基盤

```eff
effect Emit : int -> unit

let example () =
  Emit 1;
  Emit 2;
  Emit 3

handle example () with
| Emit x k -> k ()
| return _ -> ()
```

---

## 21.4 SimPy

### 特徴

- Python の離散イベントシミュレータ
- ジェネレータベース
- エフェクト抽象化なし

```python
def car(env):
    while True:
        yield env.timeout(parking_duration)
        yield resource.request()
        yield env.timeout(trip_duration)
        resource.release(request)
```

### doeff との違い

| SimPy | doeff |
|-------|-------|
| シミュレーション専用 | 汎用 |
| 時間のみ | Reader/State/Writer など |
| ステップ実行あり | ステップ実行あり |

---

## 21.5 Effect-TS

### 特徴

- TypeScript のエフェクトシステム
- 強力な型システム
- 複雑な型定義

```typescript
const program = Effect.gen(function* () {
  const config = yield* Config.string("API_KEY")
  const data = yield* fetchData(config)
  return data
})
```

### doeff との違い

| Effect-TS | doeff |
|-----------|-------|
| 静的型 | 動的型 |
| ボイラープレート多め | シンプル |
| TypeScript エコシステム | Python エコシステム |

---

## 21.6 比較表

| 言語/ライブラリ | 型システム | エコシステム | 学習曲線 |
|---------------|-----------|-------------|---------|
| OCaml 5 | 静的、強力 | 中規模 | 高 |
| Koka | エフェクト型 | 小規模 | 高 |
| Eff | 研究用 | 最小 | 高 |
| Effect-TS | 静的（複雑） | TypeScript | 中〜高 |
| SimPy | 動的 | Python | 低 |
| **doeff** | 動的 | Python | **低〜中** |

---

## 21.7 doeff の位置づけ

doeff は「実用的な代数的エフェクト」を Python で実現する。

### 強み

- Python の豊富なエコシステム
- 低い学習曲線
- 既存コードとの統合が容易
- テストが簡単

### 制約

- 静的型によるエフェクト追跡なし
- 継続のシリアライズ不可
- 純粋な関数型言語ほどの表現力はない

### ターゲット

- Python 開発者
- 関数型プログラミングに興味がある人
- テスト容易性を重視する人
- モナドで挫折した人

---

## まとめ

| 選択肢 | 向いている人 |
|--------|-------------|
| OCaml 5 | 型安全性を最重視、パフォーマンス重視 |
| Koka | エフェクト型を研究したい |
| Effect-TS | TypeScript で型安全なエフェクトが必要 |
| **doeff** | **Python で実用的にエフェクトを使いたい** |

次の章では、応用領域を見ていく。
