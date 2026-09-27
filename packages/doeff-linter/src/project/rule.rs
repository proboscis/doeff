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
    /// DOEFF109: service の境界 — ある service の判断と翻訳の層は、別の service の判断と翻訳の層を読まない。
    ServiceBoundary,
    /// DOEFF110: Hy の defn / defn/a を使わない(契約の辞書と :tags を書ける defk を使う)。
    DefnForbidden,
    /// DOEFF111: deff には defk にできない理由の註を付ける。
    DeffNeedsReason,
    /// DOEFF112: defk・deff・defp・defhandler・defeffect は :tags で必須の鍵を名乗る。
    DefinitionTagsRequired,
    /// DOEFF113: タグの :context と dir の service が食い違う(info)。
    ContextMatchesService,
}

impl ProjectRule {
    /// 全部の層の規則(出力の一覧と `ALL` の展開のため)。
    pub const ALL: [ProjectRule; 13] = [
        ProjectRule::LayerImportDirection,
        ProjectRule::LayerForbiddenModule,
        ProjectRule::LayerTypesOnly,
        ProjectRule::ModuleDeclaresTags,
        ProjectRule::RoleMatchesLayer,
        ProjectRule::RawSideEffectDirect,
        ProjectRule::RawSideEffectVia,
        ProjectRule::EnvironmentName,
        ProjectRule::ServiceBoundary,
        ProjectRule::DefnForbidden,
        ProjectRule::DeffNeedsReason,
        ProjectRule::DefinitionTagsRequired,
        ProjectRule::ContextMatchesService,
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
            ProjectRule::ServiceBoundary => "DOEFF109",
            ProjectRule::DefnForbidden => "DOEFF110",
            ProjectRule::DeffNeedsReason => "DOEFF111",
            ProjectRule::DefinitionTagsRequired => "DOEFF112",
            ProjectRule::ContextMatchesService => "DOEFF113",
        }
    }

    /// ID の綴り(大文字小文字は問わない)から規則を引く。層の規則でなければ None。
    pub fn parse(id: &str) -> Option<ProjectRule> {
        let upper = id.to_uppercase();
        ProjectRule::ALL.into_iter().find(|rule| rule.id() == upper)
    }

    /// 層ごとに判じる規則か(law の layers が効く規則)。DOEFF108 は業務の file 全体に当たり、層を持たない。
    pub fn is_layered(self) -> bool {
        match self {
            ProjectRule::LayerImportDirection
            | ProjectRule::LayerForbiddenModule
            | ProjectRule::LayerTypesOnly
            | ProjectRule::ModuleDeclaresTags
            | ProjectRule::RoleMatchesLayer
            | ProjectRule::RawSideEffectDirect
            | ProjectRule::RawSideEffectVia
            | ProjectRule::ServiceBoundary
            | ProjectRule::ContextMatchesService => true,
            ProjectRule::EnvironmentName
            | ProjectRule::DefnForbidden
            | ProjectRule::DeffNeedsReason
            | ProjectRule::DefinitionTagsRequired => false,
        }
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
            ProjectRule::ServiceBoundary => "Service Boundary",
            ProjectRule::DefnForbidden => "No defn",
            ProjectRule::DeffNeedsReason => "deff Needs A Reason",
            ProjectRule::DefinitionTagsRequired => "Definition Tags Required",
            ProjectRule::ContextMatchesService => "Context Matches Service",
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
            ProjectRule::ServiceBoundary => "ある service の判断と翻訳の層(設定の guarded_layers)は、別の service の同じ層を読まない。読んでよいのは別の service の open_layers と共有の置き場だけ",
            ProjectRule::DefnForbidden => "Hy の定義は defn / defn/a ではなく defk で書く(マクロの展開の時の関数と、設定で除いた置き場は除く)",
            ProjectRule::DeffNeedsReason => "deff の定義の行か直前の行に、defk にできない理由の註を書く",
            ProjectRule::DefinitionTagsRequired => "defk・deff・defp・defhandler・defeffect は契約の辞書の :tags で必須の鍵(設定)を名乗る",
            ProjectRule::ContextMatchesService => "タグの :context は、その file が置かれた service の名と合う(知らせ)",
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
            ProjectRule::ServiceBoundary => "別の service に頼むことは、その service の intent を出して頼む(判断や翻訳の module を直に読まない)。共有する物は共有の置き場へ移す",
            ProjectRule::DefnForbidden => "defk にする。外の library の callback のように素の関数が要るなら deff にして、同じ行に `; defk にできない: <理由>` と :tags を書く",
            ProjectRule::DeffNeedsReason => "deff の行か直前の行に `; defk にできない: <理由>` を書く(書けないなら defk にする)",
            ProjectRule::DefinitionTagsRequired => "契約の辞書に :tags {:context \"…\" :role \"…\"} を書く",
            ProjectRule::ContextMatchesService => ":context を service の名に合わせるか、file を :context の service の dir へ移す",
        }
    }
}
