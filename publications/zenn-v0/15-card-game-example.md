# 第15章: card_game_2026 での実例

## この章で学ぶこと

- 実際のゲームでの doeff 適用
- 複雑なドメインでの設計
- ステップ実行とリプレイ

---

## 15.1 card_game_2026 とは

card_game_2026 は、代数的エフェクトと Rust VM を活用したカードゲームエンジン。

特徴:
- Hy（Lisp方言）で実装
- Rust VM によるステップ実行
- 型安全なドメイン制約

---

## 15.2 エフェクトによるゲームアクション

ゲームのアクションはエフェクトとして表現される。

```python
# ゲームエフェクトの例
@dataclass(frozen=True)
class DrawCard(EffectBase):
    player_id: str
    count: int = 1

@dataclass(frozen=True)
class PlayCard(EffectBase):
    player_id: str
    card_id: str
    target: str | None = None

@dataclass(frozen=True)
class DealDamage(EffectBase):
    source_id: str
    target_id: str
    amount: int
```

---

## 15.3 ゲームロジックの実装

```python
@do
def player_turn(player_id):
    yield Log(f"Player {player_id}'s turn")
    
    # ドローフェーズ
    yield DrawCard(player_id, count=1)
    
    # プレイヤーの選択を待つ
    action = yield AskPlayer(player_id, "Choose action")
    
    match action:
        case {"type": "play_card", "card_id": card_id, "target": target}:
            yield play_card(player_id, card_id, target)
        case {"type": "end_turn"}:
            yield Log(f"Player {player_id} ends turn")
        case _:
            raise ValueError(f"Invalid action: {action}")

@do
def play_card(player_id, card_id, target):
    card = yield Get("cards", card_id)
    
    # カードの効果を実行
    for effect in card["effects"]:
        yield execute_effect(effect, player_id, target)
    
    # カードを捨て札に
    yield MoveCard(card_id, "hand", "discard")
```

---

## 15.4 ステップ実行

Rust VM により、ゲームを1ステップずつ実行できる。

```python
# ゲームの1ステップを実行
def step(game_state):
    # 現在の継続から次のエフェクトを取得
    effect = get_next_effect(game_state)
    
    # エフェクトを処理
    result = handle_effect(effect, game_state)
    
    # 新しい状態を返す
    return apply_result(game_state, result)

# ゲームループ
while not is_game_over(state):
    state = step(state)
    
    # 各ステップ後にUI更新やログ出力が可能
    render(state)
```

---

## 15.5 リプレイ機能

構造化ログにより、ゲームをリプレイできる。

```python
@do
def replay_game(log):
    """ログからゲームをリプレイ"""
    for entry in log:
        if entry["type"] == "player_action":
            yield replay_action(entry)
        
        # 状態を検証
        expected = entry.get("expected_state")
        if expected:
            actual = yield get_current_state()
            assert actual == expected, "State mismatch!"
```

---

## 15.6 テスト戦略

### 単位テスト: Pure Core

```python
def test_damage_calculation():
    # 純粋な計算をテスト
    damage = calculate_damage(attack=10, defense=3)
    assert damage == 7

def test_card_effect():
    # カード効果のロジックをテスト
    result = apply_card_effect(
        card={"damage": 5, "heal": 2},
        source_stats={"attack": 10},
        target_stats={"defense": 3}
    )
    assert result["damage_dealt"] == 12
```

### 統合テスト: doeff Program

```python
async def test_player_turn():
    runtime = AsyncRuntime()
    
    result = await runtime.run(
        player_turn("player1"),
        store=initial_game_state
    )
    
    assert result.is_ok()
    # 状態の変化を検証
```

### シナリオテスト

```python
async def test_full_game_scenario():
    """特定のシナリオをテスト"""
    scenario = [
        {"player": "p1", "action": "play_card", "card": "fireball"},
        {"player": "p2", "action": "play_card", "card": "shield"},
        {"player": "p1", "action": "end_turn"},
    ]
    
    state = initial_state
    for step in scenario:
        result = await execute_action(state, step)
        state = result.state
    
    assert state["winner"] is None  # ゲームは続く
```

---

## 15.7 学んだこと

card_game_2026 の開発から得られた教訓:

1. **エフェクトでドメインを表現**: ゲームアクションをエフェクトとして定義することで、ロジックが明確になる

2. **ステップ実行の価値**: デバッグ、リプレイ、AI学習に有用

3. **型安全性の重要性**: 複雑なドメインでは型によるガードが不可欠

4. **テスト戦略の階層化**: Pure Core → Program → シナリオの順でテスト

---

## まとめ

| 概念 | card_game_2026 での実装 |
|------|------------------------|
| エフェクト | ゲームアクション |
| ハンドラ | ゲームルールの実行 |
| 状態 | ゲームステート |
| ログ | リプレイ用データ |

次の章から、産業利用パターンを見ていく。
