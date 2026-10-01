//! 生の副作用の設定の検めた形 — 許す層・目録の足し・architecture.hy の許可名簿と境目の部品(DOEFF106)。
//! 設定の組み立て(`settings`)が作り、規則と説明(`explain`)が読む(agora-redesign #2123 で settings.rs から分けた)。

use std::collections::{BTreeMap, BTreeSet};

use super::layers::LayerId;

/// 生の副作用の設定(検めた後)。
#[derive(Debug, Clone)]
pub struct RawSettingsSpec {
    pub allowed: BTreeSet<LayerId>,
    pub catalog_extra: Option<String>,
    /// architecture.hy の許可名簿(:world-handlers)を書いた時、生の副作用を許す module(mangle した dotted の綴り)。
    /// Some なら層の `allowed` は使わず、この module の file だけに許す(agora-redesign #1140)。
    pub world_modules: Option<BTreeSet<String>>,
    /// 境目の部品の module(mangle した dotted の綴り)→ 許す触れる先(architecture.hy の :boundary-parts・agora-redesign #1797)。
    /// ここに在る module の中では、触れる先が一覧に入る生の副作用の証拠を DOEFF106 で当たりにしない。
    pub boundary: BTreeMap<String, Vec<super::architecture::WorldTouch>>,
}
