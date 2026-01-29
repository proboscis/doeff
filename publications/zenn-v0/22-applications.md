# 第22章: 応用領域

## この章で学ぶこと

- doeff が活きる領域
- シミュレーション
- バックテスト
- ゲーム開発

---

## 22.1 シミュレーション

### なぜ doeff が適しているか

- ステップ実行が可能
- 時間のシミュレーション
- 決定論的な再現
- 状態の追跡

### 例: 待ち行列シミュレーション

```python
@do
def customer_process(customer_id: int, service_time: float):
    arrival_time = yield GetTime()
    yield Log(f"Customer {customer_id} arrived at {arrival_time}")
    
    # サービスを要求
    yield request_service(customer_id)
    
    service_start = yield GetTime()
    yield Log(f"Customer {customer_id} service started")
    
    yield Delay(service_time)
    
    service_end = yield GetTime()
    yield Log(f"Customer {customer_id} service completed")
    
    return {
        "customer_id": customer_id,
        "arrival_time": arrival_time,
        "wait_time": service_start - arrival_time,
        "service_time": service_time
    }
```

---

## 22.2 バックテスト

### 金融のバックテスト

```python
@do
def trading_strategy(prices: list[float]):
    position = 0
    cash = 100000
    
    for i, price in enumerate(prices):
        yield StructuredLog(
            event="tick",
            day=i,
            price=price,
            position=position,
            cash=cash
        )
        
        signal = yield calculate_signal(prices[:i+1])
        
        if signal > 0 and position == 0:
            # 買い
            shares = int(cash / price)
            position = shares
            cash -= shares * price
            yield Log(f"BUY {shares} @ {price}")
        
        elif signal < 0 and position > 0:
            # 売り
            cash += position * price
            yield Log(f"SELL {position} @ {price}")
            position = 0
    
    # 最終評価
    total_value = cash + position * prices[-1]
    return {"final_value": total_value, "return": total_value / 100000 - 1}
```

### テストの容易さ

```python
async def test_strategy():
    test_prices = [100, 105, 103, 110, 108, 115]
    
    runtime = SimulationRuntime()
    result = runtime.run(trading_strategy(test_prices))
    
    assert result.value["final_value"] > 0
```

---

## 22.3 ゲーム開発

### ターンベースゲーム

```python
@do
def game_loop():
    yield Put("turn", 0)
    yield Put("players", initialize_players())
    
    while True:
        turn = yield Get("turn")
        players = yield Get("players")
        
        # ゲーム終了判定
        winner = check_winner(players)
        if winner:
            yield Log(f"Game over! Winner: {winner}")
            return winner
        
        # 各プレイヤーのターン
        for player in players:
            action = yield get_player_action(player)
            yield execute_action(player, action)
            yield Log(f"Player {player.id} performed {action}")
        
        yield Modify("turn", lambda t: t + 1)
```

### リプレイ機能

構造化ログからゲームをリプレイできる。

```python
@do
def replay_game(log: list[dict]):
    for entry in log:
        if entry["event"] == "action":
            yield execute_action(
                entry["player_id"],
                entry["action"]
            )
            yield Delay(0.5)  # アニメーション用の遅延
```

---

## 22.4 データパイプライン

### ETL パイプライン

```python
@do
def etl_pipeline(source: str, destination: str):
    yield Log("Starting ETL pipeline")
    
    # Extract
    raw_data = yield extract_data(source)
    yield StructuredLog(event="extracted", row_count=len(raw_data))
    
    # Transform
    transformed = yield transform_data(raw_data)
    yield StructuredLog(event="transformed", row_count=len(transformed))
    
    # Load
    yield load_data(transformed, destination)
    yield StructuredLog(event="loaded", destination=destination)
    
    return {"rows_processed": len(transformed)}
```

---

## 22.5 テスト自動化

### E2E テストの記述

```python
@do
def e2e_test_user_registration():
    yield Log("Starting user registration test")
    
    # ユーザー作成
    user = yield create_user({
        "email": "test@example.com",
        "password": "password123"
    })
    yield Assert(user["id"] is not None, "User should have ID")
    
    # ログイン
    session = yield login("test@example.com", "password123")
    yield Assert(session["token"] is not None, "Should have token")
    
    # プロファイル更新
    yield update_profile(session, {"name": "Test User"})
    
    # 確認
    profile = yield get_profile(session)
    yield Assert(profile["name"] == "Test User", "Name should be updated")
    
    yield Log("Test passed!")
```

---

## 22.6 機械学習パイプライン

```python
@do
def ml_pipeline(data_path: Path, model_name: str):
    yield Log(f"Starting ML pipeline: {model_name}")
    
    # データ読み込み
    data = yield load_dataset(data_path)
    yield StructuredLog(event="data_loaded", samples=len(data))
    
    # 前処理
    preprocessed = yield preprocess(data)
    
    # 学習
    train_data, test_data = split_data(preprocessed)
    model = yield train_model(train_data, model_name)
    yield StructuredLog(event="model_trained", model=model_name)
    
    # 評価
    metrics = yield evaluate_model(model, test_data)
    yield StructuredLog(event="evaluated", **metrics)
    
    return {"model": model, "metrics": metrics}
```

---

## まとめ

| 領域 | doeff の利点 |
|------|-------------|
| シミュレーション | ステップ実行、時間制御 |
| バックテスト | 決定論的、再現可能 |
| ゲーム | 状態管理、リプレイ |
| ETL | パイプライン構築、ログ |
| テスト | 依存注入、モック不要 |
| ML | 実験追跡、再現性 |

次の章では、今後の展望を見ていく。
