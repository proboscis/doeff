# 専用pingの送信先を通常idle回収から保護する

通常ターンの終了から600秒でsessionを片付ける従来の処理では、1時間の
キャッシュを失効5分前に更新する専用pingが、その送信先を失っていた。

通常ターンの終了時刻は変更しない。sessionhostは稼働中のClaude/headless/
multi_turn sessionに対し、`cache_retained_until_ms`を読み出し時に導出する。
初回は通常ターンの終了から1時間、以後はキャッシュ利用を観測した成功pingの
応答時刻から1時間まで、そのsessionをidle回収から保護する。

1時間は対応するキャッシュTTLの最大値に基づく**送信先の保持予算**であり、
キャッシュの有効期限や有効性の証明ではない。controllerの送信判断は引き続き
応答観測、リクエスト開始時刻の下限、TTL、機体・profile・sessionの同一性を使う。
短いTTLやTTL不明の場合にも、保持しているという理由でpingを許可しない。

成功receiptは従来どおりSQLiteに永続化する。保持期限の読取りはsession/stateの
indexを使い、対象sessionの成功receiptだけを集計する。再起動でも同じ期限を
復元でき、旧DBに保存済みのreceiptも利用できる。要求中、失敗、応答不明の
操作は保持を更新しない。キャッシュ利用量が0の応答でも更新しない。

明示的なcancel/cleanup、異なるprofileへの切替、nodeの廃止は通常どおり処理する。
通常の実行枠は取得せず、`turn_ended_at`、通常ターンの結果、イベントも変更しない。
pingが止まれば保持期限は有限で尽き、従来のidle条件も満たしたsessionを片付ける。

## 検証

- `test_sessionhost_cache_residency.py`: 修正前に初回・2回目の保持が失敗することを確認。
  実sessionhost、CLI替え玉、SQLiteを使い、600秒の回収、55分の初回ping、
  110分の次回ping、失敗時の期限切れ、明示cancel、Codexの従来の回収を検証する。
  SQLite接続を開き直す試験で永続記録からの復元も検証する。
- `sessionhost_cache_maintenance_deftests.hy`の
  `test-clock-swapped-idle-cleanup-ping-and-next-cycle`: 時計、ファイル、receiptの
  handlerを差し替え、実際の`cache-host-probe`、保持期限導出、`sessions-to-retire`
  を組み合わせて55分・110分の成功と、その後に応答がない場合の回収を検証する。
- 既存の専用ping試験: 通常sendとの排他、通常sessionの状態を変えないこと、
  親processの死亡からの回復、通常枠0での実行を維持する。

本番の自動送信による結果は別途確認する。仮想時計の成功を本番実測とは扱わない。
