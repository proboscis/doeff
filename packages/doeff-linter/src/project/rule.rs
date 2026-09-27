//! 層の規則(repo をまたいで判じる規則)の閉じた一覧。ID・題・文・直し方の既定の 1 行はここだけに書く。

/// 層の規則の種類。Python の文ごとの規則(DOEFF001〜031)と違い、repo の module の一覧と設定を見て判じる。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ProjectRule {
    /// DOEFF101: 層の import の向き — 許された層の外の module を import しない。
    LayerImportDirection,
    /// DOEFF102: 層ごとに禁じた module(I/O の module など)を直に import しない。
    LayerForbiddenModule,
    /// DOEFF103: 型だけの層に関数と handler を定めない。
    LayerTypesOnly,
    /// DOEFF104: 層の module は文脈と役をタグで名乗る。
    ModuleDeclaresTags,
    /// DOEFF105: タグの role がその層で許される物。
    RoleMatchesLayer,
    /// DOEFF106: 生の副作用に直に触る定義は許された層にだけ置く。
    RawSideEffectDirect,
    /// DOEFF107: 呼ぶ定義を通して生の副作用に届く(事実の知らせ・info)。
    RawSideEffectVia,
    /// DOEFF108: 業務の file・handler・組み立ての関数の名に環境の語を付けない。
    EnvironmentName,
}

impl ProjectRule {
    /// 全部の層の規則(出力の一覧と `ALL` の展開のため)。
    pub const ALL: [ProjectRule; 8] = [
        ProjectRule::LayerImportDirection,
        ProjectRule::LayerForbiddenModule,
        ProjectRule::LayerTypesOnly,
        ProjectRule::ModuleDeclaresTags,
        ProjectRule::RoleMatchesLayer,
        ProjectRule::RawSideEffectDirect,
        ProjectRule::RawSideEffectVia,
        ProjectRule::EnvironmentName,
    ];

    /// 規則の ID。
    pub fn id(self) -> &'static str {
        match self {
            ProjectRule::LayerImportDirection => "DOEFF101",
            ProjectRule::LayerForbiddenModule => "DOEFF102",
            ProjectRule::LayerTypesOnly => "DOEFF103",
            ProjectRule::ModuleDeclaresTags => "DOEFF104",
            ProjectRule::RoleMatchesLayer => "DOEFF105",
            ProjectRule::RawSideEffectDirect => "DOEFF106",
            ProjectRule::RawSideEffectVia => "DOEFF107",
            ProjectRule::EnvironmentName => "DOEFF108",
        }
    }

    /// ID の綴り(大文字小文字は問わない)から規則を引く。層の規則でなければ None。
    pub fn parse(id: &str) -> Option<ProjectRule> {
        let upper = id.to_uppercase();
        ProjectRule::ALL.into_iter().find(|rule| rule.id() == upper)
    }

    /// 題(人が読む短い名)。
    pub fn title(self) -> &'static str {
        match self {
            ProjectRule::LayerImportDirection => "Layer Import Direction",
            ProjectRule::LayerForbiddenModule => "Layer Forbidden Module",
            ProjectRule::LayerTypesOnly => "Types-Only Layer",
            ProjectRule::ModuleDeclaresTags => "Module Declares Tags",
            ProjectRule::RoleMatchesLayer => "Role Matches Layer",
            ProjectRule::RawSideEffectDirect => "Raw Side Effect Placement",
            ProjectRule::RawSideEffectVia => "Raw Side Effect Via Call",
            ProjectRule::EnvironmentName => "Environment Name In Business Code",
        }
    }

    /// 規則の文(law が結びついていない時に一覧へ出す)。
    pub fn statement(self) -> &'static str {
        match self {
            ProjectRule::LayerImportDirection => "層の module は、設定で許した層の module だけを import する(repo の外の import は数えない)",
            ProjectRule::LayerForbiddenModule => "層の module は、その層に禁じた module(I/O の module など)を直に import しない",
            ProjectRule::LayerTypesOnly => "型だけの層の module は関数と handler を定めない",
            ProjectRule::ModuleDeclaresTags => "層の module の定義は、定義の :tags か module の頭のタグで文脈(context)と役(role)を名乗る",
            ProjectRule::RoleMatchesLayer => "タグの role は、その module の層で許された role の 1 つで、context も名乗る",
            ProjectRule::RawSideEffectDirect => "生の副作用(http・時刻・乱数・file・process・環境変数 …)に直に触る定義は、設定で許した層にだけ置く",
            ProjectRule::RawSideEffectVia => "呼ぶ定義を通して生の副作用に届く定義の知らせ(違反ではなく事実)",
            ProjectRule::EnvironmentName => "業務の file・handler・組み立ての関数の名に環境の語を付けない",
        }
    }

    /// 直し方の既定の 1 行。
    pub fn hint(self) -> &'static str {
        match self {
            ProjectRule::LayerImportDirection => "向きに反する import を外す — 要る値は許された層(intent の型など)へ移すか、effect を出して下の層の handler に答えさせる",
            ProjectRule::LayerForbiddenModule => "I/O は許された層(foundation など)の handler に置き、この層からは effect を出す",
            ProjectRule::LayerTypesOnly => "関数と handler は別の層(core・protocol)へ移し、この層には型だけを置く",
            ProjectRule::ModuleDeclaresTags => "契約の辞書に :tags {:context … :role …} を書くか、module の頭に MODULE-TAGS を置く",
            ProjectRule::RoleMatchesLayer => "role をこの層で許された物に直すか、module を role に合う層の dir へ移す",
            ProjectRule::RawSideEffectDirect => "effect を出して、許された層の handler に I/O をさせる",
            ProjectRule::RawSideEffectVia => "経路の先の定義が effect を出す形になっているかを確かめる",
            ProjectRule::EnvironmentName => "環境の違いは土台の handler の差し替えで表し、業務の名からは環境の語を外す",
        }
    }
}
