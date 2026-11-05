//! Validated form of the raw side-effect settings — allowed layers, inventory additions, and the allow-list and boundary parts from architecture.hy (DOEFF106).
//! Built by the settings assembly (`settings`) and read by the rule and the explanation (`explain`) (split out of settings.rs).

use std::collections::{BTreeMap, BTreeSet};

use super::layers::LayerId;

/// Raw side-effect settings (after validation).
#[derive(Debug, Clone)]
pub struct RawSettingsSpec {
    pub allowed: BTreeSet<LayerId>,
    pub catalog_extra: Option<String>,
    /// Modules (mangled dotted spelling) allowed raw side effects, set when the architecture.hy allow-list (:world-handlers) is written.
    /// When Some, the layer's `allowed` is not used and only this module's file is allowed.
    pub world_modules: Option<BTreeSet<String>>,
    /// Boundary-part module (mangled dotted spelling) -> allowed targets it may touch (architecture.hy :boundary-parts).
    /// Inside these modules, raw side-effect evidence whose target is in the list is not reported as a DOEFF106 hit.
    pub boundary: BTreeMap<String, Vec<super::architecture::WorldTouch>>,
}
