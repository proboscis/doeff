//! DOEFF137(handler の縁の検)の当たりの種類。説明(explain.rs)と判定(mod.rs)が読む — mod.rs を読み戻さないよう自分の module に
//! 置く(agora-redesign #2121)。

/// DOEFF137 の当たりの種類。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContractTestBreach {
    /// 縁の検を求める handler に、届く縁の検が無い。
    NoEdgeTest,
    /// 理由の無い `:contract-test none`。
    NoneWithoutReason,
}
