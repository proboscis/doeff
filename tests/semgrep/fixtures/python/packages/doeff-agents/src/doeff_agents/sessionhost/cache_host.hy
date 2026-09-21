;; ruleid: cache-maintenance-never-becomes-normal-turn
(<- (SessionSend session "ping"))
;; ruleid: cache-maintenance-never-becomes-normal-turn
(<- (session-store-upsert normal-row))
;; ok: cache-maintenance-never-becomes-normal-turn
(<- (HostCacheWrite dedicated-receipt))
;; ok: cache-maintenance-never-becomes-normal-turn
(<- (SessionCachePing operation same-identity-env))
