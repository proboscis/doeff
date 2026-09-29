//! 層の規則(repo をまたいで判じる規則)の閉じた一覧。ID・題・文・直し方の既定の 1 行はここだけに書く。

use serde::Serialize;

/// 規則の家族(エディタの一覧で規則を束ねる、閉じた分類)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RuleFamily {
    /// 層の向き・置ける物(DOEFF101〜103)と、翻訳の層の handler が出す effect(DOEFF130)。
    Layer,
    /// タグ(:context・role)の宣言と食い違い(DOEFF104・105・113)。
    Tags,
    /// 生の副作用(DOEFF106・107)。
    Raw,
    /// 名の付け方(DOEFF108)。
    Naming,
    /// 置き場所・依存(DOEFF109・114〜117)と、設定の知らない鍵(DOEFF100 — 置き場所の宣言 architecture.hy と同じ設定の話なので
    /// この家族に入れる。拡張の家族の一覧は閉じているので、新しい家族を足すと古い拡張が出力を丸ごと捨てる)。
    Place,
    /// 定義の書き方(DOEFF110〜112・118)。
    Definition,
    /// class(DOEFF119・204)。
    Class,
    /// JsonValue と wire(DOEFF120)。
    Wire,
    /// 臭い(DOEFF121〜125・205)。
    Smell,
    /// Jev(意味の判定・DOEFF201〜203)。
    Jev,
    /// Python の文ごとの規則(DOEFF001〜031・NOQA001・知らない ID)。
    Python,
    /// 規則を持たない law。
    Law,
}

/// 層の規則の種類。Python の文ごとの規則(DOEFF001〜031)と違い、repo の module の一覧と設定を見て判じる。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ProjectRule {
    /// DOEFF100: 設定(pyproject の [tool.doeff-linter]・architecture.hy)にこの linter の知らない鍵か規則の ID がある
    /// (その鍵だけを読まずに残りの規則を走らせた知らせ・warning — linter が設定より古いか、書き違い)。
    UnknownConfigKey,
    /// DOEFF128: Hy の file を読み取り器が最後まで読めない(違反が欠ける — 有効な規則の一覧に関わらず出す)。
    UnreadableFile,
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
    /// DOEFF113: タグの :context と dir の service が食い違う(info。service を宣言していれば warning)。
    ContextMatchesService,
    /// DOEFF114: 宣言されていない置き場所の module(architecture.hy の service の層・shared・foundation・legacy のどれでもない)。
    UndeclaredPlace,
    /// DOEFF115: 宣言に無い service の dir、service の中の宣言に無い層の dir。
    UndeclaredDirectory,
    /// DOEFF116: service の依存 — 宣言の :depends-on に無い service を読む、または依存先の読めない層(層の :dependency-layers か :open-layers の外)を読む。
    ServiceDependency,
    /// DOEFF117: 宣言したのに使っていない依存(info)。
    UnusedDependency,
    /// DOEFF118: 検の置き場の検の関数は deftest だけで書く(名が test_ の defn・deff・defk・fn の束縛を置かない)。
    TestIsDeftest,
    /// DOEFF119: 処理を持つ method のある defclass を業務の code に書かない(欄だけの class は defrecord を勧める info)。
    ClassWithBehaviour,
    /// DOEFF120: JsonValue(素の dict を名で包んだだけの型)を、汎用の解き手と :wire-modules に挙げた foundation の送受信の module の外で使わない。
    JsonValueOutsideWire,
    /// DOEFF121: 判断の層の定義が、文字列の鍵で読んだ欄を isinstance で検める(JSON の形の検めが core に入っている)。
    ShapeCheckInJudgment,
    /// DOEFF122: match の腕が、失敗の型を受けて受けた値を return し直すだけ(手書きの例外の再送出)。
    FailureRethrow,
    /// DOEFF123: `(<- x T (f …))` の直後に `(return x)` が来て、x を他で使わない。
    BindThenReturn,
    /// DOEFF124: 同じ値の 2 つ以上の欄を 1 本の文字列につなぐ。
    FieldsJoinedIntoText,
    /// DOEFF125: for / while の中で蓄えを毎回作り直す。
    RebuiltAccumulator,
    /// DOEFF126: defk の定義を素で呼ぶ(答えではなく Program が返り、静かに間違った値として流れる)。
    DefkCalledBare,
    /// DOEFF127: defk の `:effects` の宣言が推論と合わない(宣言に無い effect を起こす・宣言した effect を起こさない)。
    EffectsDisagreeWithInference,
    /// DOEFF130: 翻訳の層の handler が業務の intent(層 intent の型の effect)を出す — import した defk の先まで辿る。
    TranslationEmitsIntent,
    /// DOEFF131: architecture.hy の許可名簿(:world-handlers)の外の定義が、名簿の :wraps に挙げた doeff の実 I/O の handler を名指す
    /// (外の世界に触れてよいのは名簿の定義だけ — agora-redesign #1106 の R1)。
    WorldHandlerNamedOutsideList,
    /// DOEFF132: 許可名簿の定義が実在しない・層 foundation の外に在る(外の世界に触れてよい定義は foundation の層にだけ置く —
    /// agora-redesign #1106 の R2)。位置は architecture.hy の名簿の要素。
    WorldHandlerMisplaced,
    /// DOEFF133: テストの種類(手元 / 縁)を届く先から導き、architecture.hy の :edge-mark の印と食い違う物 — 名簿の定義・:wraps の
    /// handler・生の I/O に届くのに印が無い / 届かないのに印が在る(agora-redesign #1106 の R3)。
    TestKindMismatch,
    /// DOEFF135: deftest 以外のテストの形(Python の def test_*・module ごとの skip・pytest の外の check script・deftest の runner)—
    /// architecture.hy の :test-forms の綴りの型で file を選ぶ(agora-redesign #1106 の R6)。
    TestFormNotDeftest,
    /// DOEFF146: 判定を1か所に閉じ込めた語彙が :except の外に在る — architecture.hy の :single-point-vocabulary の群が
    /// 名指す語彙(正規表現)を、:except の file(判定の1点)の外の :files が読んでいる(agora-redesign #1192・#1371)。
    VocabularyOutsideSinglePoint,
    /// DOEFF136: service の entry の層の定義に、模擬の環境(:verification-environment)の下の deftest が 1 本も届かない
    /// — 本番の組み立てを手元で回していない service(agora-redesign #1106 の R5・#1111)。
    ServiceUntestedOnSim,
    /// DOEFF140: architecture.hy の :placed-dependencies の層の module(service と shared)が、root の下の層の置き場の外の module を
    /// import する — 置き場の決まっていない module への依存(agora-redesign #1188)。
    PlacedDependency,
    /// DOEFF150: architecture.hy の :retired-words で使わないと決めた綴り(語・正規表現・定義の名)が、宣言の file に在る
    /// (agora-redesign #1193 — 語の表は repo の宣言にだけ在る)。
    RetiredWord,
    /// DOEFF151: architecture.hy の :retired-calls で使わないと決めた呼び(退役した effect・時計 …)を、宣言の Hy の file が呼ぶ(#1193)。
    RetiredCall,
    /// DOEFF142: defhandler の引数に client・可変の入れ物・可変の object・店の名の引数を取る(接続先と設定は Ask・状態は session に持つ —
    /// agora-redesign #1189 / #1366)。母集団と店の名は architecture.hy の :handler-arguments から読む。
    HandlerArgumentHoldsState,
    /// DOEFF144: 公開面の型の注記(class の欄・関数の戻り値・defk / deff の :post)が素の写像・素の組・値の開いた写像・長さの決まった組、
    /// または関数が長さ 2 以上の組の literal を答えにする — 構造を持つ値は欄の名前と型を持つ型で表す(agora-redesign #1191)。
    UntypedStructuredValue,
    /// DOEFF145: 同じ dir の同じ名の .hy で kw-only の record(defrecord か、飾りに :kw-only True の dataclass を持つ defclass)を、
    /// 型の宣言(.pyi)が kw_only=True の無い @dataclass で宣言する — 型検査は位置の引数の呼びを通すが実行時は TypeError(#1191)。
    RecordStubNotKwOnly,
    /// DOEFF201(意味・Jev): 翻訳の層の定義が業務の判断をしている。
    SemanticBusinessDecision,
    /// DOEFF202(意味・Jev): 判断の層の定義が通信の手段を知っている。
    SemanticTransportKnowledge,
    /// DOEFF203(意味・Jev): deff が名乗った素の関数の理由の種類が、コードに当たらない見込み。
    SemanticPlainCallable,
    /// DOEFF204(意味・Jev): 処理を持つ method のある class が、外の世界の窓口か状態を持つ物の見込み。
    SemanticClassRole,
    /// DOEFF205(意味・Jev): 判断の定義が、入力の形の検めと業務の判断を混ぜている見込み。
    SemanticMixedConcerns,
}

impl ProjectRule {
    /// 全部の層の規則(出力の一覧と `ALL` の展開のため)。
    pub const ALL: &'static [ProjectRule] = &[
        ProjectRule::UnknownConfigKey,
        ProjectRule::UnreadableFile,
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
        ProjectRule::UndeclaredPlace,
        ProjectRule::UndeclaredDirectory,
        ProjectRule::ServiceDependency,
        ProjectRule::UnusedDependency,
        ProjectRule::TestIsDeftest,
        ProjectRule::ClassWithBehaviour,
        ProjectRule::JsonValueOutsideWire,
        ProjectRule::ShapeCheckInJudgment,
        ProjectRule::FailureRethrow,
        ProjectRule::BindThenReturn,
        ProjectRule::FieldsJoinedIntoText,
        ProjectRule::RebuiltAccumulator,
        ProjectRule::DefkCalledBare,
        ProjectRule::EffectsDisagreeWithInference,
        ProjectRule::TranslationEmitsIntent,
        ProjectRule::WorldHandlerNamedOutsideList,
        ProjectRule::WorldHandlerMisplaced,
        ProjectRule::TestKindMismatch,
        ProjectRule::TestFormNotDeftest,
        ProjectRule::VocabularyOutsideSinglePoint,
        ProjectRule::ServiceUntestedOnSim,
        ProjectRule::PlacedDependency,
        ProjectRule::RetiredWord,
        ProjectRule::RetiredCall,
        ProjectRule::HandlerArgumentHoldsState,
        ProjectRule::UntypedStructuredValue,
        ProjectRule::RecordStubNotKwOnly,
        ProjectRule::SemanticBusinessDecision,
        ProjectRule::SemanticTransportKnowledge,
        ProjectRule::SemanticPlainCallable,
        ProjectRule::SemanticClassRole,
        ProjectRule::SemanticMixedConcerns,
    ];

    /// 規則の ID。
    pub fn id(self) -> &'static str {
        match self {
            ProjectRule::UnknownConfigKey => "DOEFF100",
            ProjectRule::UnreadableFile => "DOEFF128",
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
            ProjectRule::UndeclaredPlace => "DOEFF114",
            ProjectRule::UndeclaredDirectory => "DOEFF115",
            ProjectRule::ServiceDependency => "DOEFF116",
            ProjectRule::UnusedDependency => "DOEFF117",
            ProjectRule::TestIsDeftest => "DOEFF118",
            ProjectRule::ClassWithBehaviour => "DOEFF119",
            ProjectRule::JsonValueOutsideWire => "DOEFF120",
            ProjectRule::ShapeCheckInJudgment => "DOEFF121",
            ProjectRule::FailureRethrow => "DOEFF122",
            ProjectRule::BindThenReturn => "DOEFF123",
            ProjectRule::FieldsJoinedIntoText => "DOEFF124",
            ProjectRule::RebuiltAccumulator => "DOEFF125",
            ProjectRule::DefkCalledBare => "DOEFF126",
            ProjectRule::EffectsDisagreeWithInference => "DOEFF127",
            ProjectRule::TranslationEmitsIntent => "DOEFF130",
            ProjectRule::WorldHandlerNamedOutsideList => "DOEFF131",
            ProjectRule::WorldHandlerMisplaced => "DOEFF132",
            ProjectRule::TestKindMismatch => "DOEFF133",
            ProjectRule::TestFormNotDeftest => "DOEFF135",
            ProjectRule::VocabularyOutsideSinglePoint => "DOEFF146",
            ProjectRule::ServiceUntestedOnSim => "DOEFF136",
            ProjectRule::PlacedDependency => "DOEFF140",
            ProjectRule::RetiredWord => "DOEFF150",
            ProjectRule::RetiredCall => "DOEFF151",
            ProjectRule::HandlerArgumentHoldsState => "DOEFF142",
            ProjectRule::UntypedStructuredValue => "DOEFF144",
            ProjectRule::RecordStubNotKwOnly => "DOEFF145",
            ProjectRule::SemanticBusinessDecision => "DOEFF201",
            ProjectRule::SemanticTransportKnowledge => "DOEFF202",
            ProjectRule::SemanticPlainCallable => "DOEFF203",
            ProjectRule::SemanticClassRole => "DOEFF204",
            ProjectRule::SemanticMixedConcerns => "DOEFF205",
        }
    }

    /// 規則の既定の重大さ(repo の宣言 `rules.<ID>.level` が無い時 — agora-redesign #1041)。None の規則は規則そのものの重さから決める
    /// (error = major・warning = minor・info = info)。責務の境界の違反は常に critical(operator 2026-09-29 "responsibility boundary
    /// violations are always CRITICAL to make our doeff code testable")— repo ごとに写すと片方が黙って古くなるので、既定はここ 1 か所。
    /// 新しい規則はどちらかに置く(網羅の match — 決めずに足せない)。
    pub fn default_level(self) -> Option<super::settings::RuleLevel> {
        match self {
            // 責務の境界: 層の向き・層で禁じた module・型だけの層・生の副作用・service の境界と依存・翻訳が業務の intent を出す・
            // Jev の判定(翻訳の層の業務の判断・判断の層の通信の手段・形の検めと判断の混ざり)。
            ProjectRule::LayerImportDirection
            | ProjectRule::LayerForbiddenModule
            | ProjectRule::LayerTypesOnly
            | ProjectRule::RawSideEffectDirect
            | ProjectRule::WorldHandlerNamedOutsideList
            | ProjectRule::WorldHandlerMisplaced
            | ProjectRule::TestKindMismatch
            | ProjectRule::TestFormNotDeftest
            | ProjectRule::VocabularyOutsideSinglePoint
            | ProjectRule::ServiceUntestedOnSim
            // 置き場の外の module への依存(#1188 — 登録簿に載った既知の当たりは warning、新しい当たりは critical)。
            | ProjectRule::PlacedDependency
            // 使わないと決めた綴りと呼び(#1193 の決め — 登録簿に載った既知の当たりは warning、新しい当たりは critical)。
            | ProjectRule::RetiredWord
            | ProjectRule::RetiredCall
            | ProjectRule::HandlerArgumentHoldsState
            // 公開面の型の素の写像・素の組と、.pyi の kw_only の食い違い(#1191 の決め — 既定は critical)。
            | ProjectRule::UntypedStructuredValue
            | ProjectRule::RecordStubNotKwOnly
            | ProjectRule::ServiceBoundary
            | ProjectRule::ServiceDependency
            | ProjectRule::TranslationEmitsIntent
            | ProjectRule::SemanticBusinessDecision
            | ProjectRule::SemanticTransportKnowledge
            | ProjectRule::SemanticMixedConcerns
            // 宣言に無い置き場所(どの層の決まりも当たらない)・defk を素で呼ぶ(Program が値として流れる本物の誤り)・
            // 読めない file(判定が欠け、0 件に見えても合格ではない)。
            | ProjectRule::UndeclaredPlace
            | ProjectRule::UndeclaredDirectory
            | ProjectRule::DefkCalledBare
            | ProjectRule::UnreadableFile => Some(super::settings::RuleLevel::Critical),
            ProjectRule::UnknownConfigKey
            | ProjectRule::ModuleDeclaresTags
            | ProjectRule::RoleMatchesLayer
            | ProjectRule::RawSideEffectVia
            | ProjectRule::EnvironmentName
            | ProjectRule::DefnForbidden
            | ProjectRule::DeffNeedsReason
            | ProjectRule::DefinitionTagsRequired
            | ProjectRule::ContextMatchesService
            | ProjectRule::UnusedDependency
            | ProjectRule::TestIsDeftest
            | ProjectRule::ClassWithBehaviour
            | ProjectRule::JsonValueOutsideWire
            | ProjectRule::ShapeCheckInJudgment
            | ProjectRule::FailureRethrow
            | ProjectRule::BindThenReturn
            | ProjectRule::FieldsJoinedIntoText
            | ProjectRule::RebuiltAccumulator
            | ProjectRule::EffectsDisagreeWithInference
            | ProjectRule::SemanticPlainCallable
            | ProjectRule::SemanticClassRole => None,
        }
    }

    /// 臭いの規則(DOEFF121〜125 — 既定の重さ warning・設定の severity で info に下げられる)か。
    pub fn is_smell(self) -> bool {
        matches!(
            self,
            ProjectRule::ShapeCheckInJudgment
                | ProjectRule::FailureRethrow
                | ProjectRule::BindThenReturn
                | ProjectRule::FieldsJoinedIntoText
                | ProjectRule::RebuiltAccumulator
        )
    }

    /// Jev に問う意味の規則(DOEFF201〜205)か — 誤判定の一覧が効くのはこの規則の当たりだけ。
    pub fn is_semantic(self) -> bool {
        matches!(
            self,
            ProjectRule::SemanticBusinessDecision
                | ProjectRule::SemanticTransportKnowledge
                | ProjectRule::SemanticPlainCallable
                | ProjectRule::SemanticClassRole
                | ProjectRule::SemanticMixedConcerns
        )
    }

    /// ID の綴り(大文字小文字は問わない)から規則を引く。層の規則でなければ None。
    pub fn parse(id: &str) -> Option<ProjectRule> {
        let upper = id.to_uppercase();
        ProjectRule::ALL.iter().copied().find(|rule| rule.id() == upper)
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
            | ProjectRule::WorldHandlerNamedOutsideList
            | ProjectRule::ServiceBoundary
            | ProjectRule::ContextMatchesService
            | ProjectRule::ServiceDependency
            | ProjectRule::PlacedDependency
            | ProjectRule::TranslationEmitsIntent
            | ProjectRule::SemanticBusinessDecision
            | ProjectRule::SemanticTransportKnowledge => true,
            ProjectRule::UnknownConfigKey
            | ProjectRule::UnreadableFile
            | ProjectRule::UndeclaredPlace
            | ProjectRule::UndeclaredDirectory
            | ProjectRule::UnusedDependency
            | ProjectRule::WorldHandlerMisplaced
            | ProjectRule::TestKindMismatch
            | ProjectRule::TestFormNotDeftest
            | ProjectRule::VocabularyOutsideSinglePoint
            | ProjectRule::ServiceUntestedOnSim
            | ProjectRule::RetiredWord
            | ProjectRule::RetiredCall
            | ProjectRule::HandlerArgumentHoldsState
            | ProjectRule::UntypedStructuredValue
            | ProjectRule::RecordStubNotKwOnly => false,
            ProjectRule::EnvironmentName
            | ProjectRule::DefnForbidden
            | ProjectRule::DeffNeedsReason
            | ProjectRule::DefinitionTagsRequired
            | ProjectRule::TestIsDeftest
            | ProjectRule::ClassWithBehaviour
            | ProjectRule::JsonValueOutsideWire
            | ProjectRule::ShapeCheckInJudgment
            | ProjectRule::FailureRethrow
            | ProjectRule::BindThenReturn
            | ProjectRule::FieldsJoinedIntoText
            | ProjectRule::RebuiltAccumulator
            | ProjectRule::DefkCalledBare
            | ProjectRule::EffectsDisagreeWithInference
            | ProjectRule::SemanticMixedConcerns
            | ProjectRule::SemanticPlainCallable
            | ProjectRule::SemanticClassRole => false,
        }
    }

    /// 短い日本語の名(違反の形。エディタの一覧の見出しに使う)。
    pub fn label(self) -> &'static str {
        match self {
            ProjectRule::UnknownConfigKey => "設定の知らない鍵",
            ProjectRule::UnreadableFile => "Hy の file を読めない",
            ProjectRule::LayerImportDirection => "層の向きに逆らう import",
            ProjectRule::LayerForbiddenModule => "層に禁じた module の import",
            ProjectRule::LayerTypesOnly => "型だけの層に関数がある",
            ProjectRule::ModuleDeclaresTags => "文脈と役のタグが無い",
            ProjectRule::RoleMatchesLayer => "層に合わない role",
            ProjectRule::RawSideEffectDirect => "許されない層で生の副作用",
            ProjectRule::RawSideEffectVia => "呼んだ先で生の副作用に届く",
            ProjectRule::EnvironmentName => "業務の名に環境の語",
            ProjectRule::ServiceBoundary => "別の service の内側を読む",
            ProjectRule::DefnForbidden => "defn を使っている",
            ProjectRule::DeffNeedsReason => "deff に理由の註が無い",
            ProjectRule::DefinitionTagsRequired => "定義の :tags が足りない",
            ProjectRule::ContextMatchesService => ":context が service と違う",
            ProjectRule::UndeclaredPlace => "宣言に無い置き場所の module",
            ProjectRule::UndeclaredDirectory => "宣言に無い dir",
            ProjectRule::ServiceDependency => "宣言に無い service への依存",
            ProjectRule::PlacedDependency => "置き場の外の module への依存",
            ProjectRule::UnusedDependency => "使っていない依存",
            ProjectRule::TestIsDeftest => "deftest でないテスト",
            ProjectRule::ClassWithBehaviour => "処理を持つ class",
            ProjectRule::JsonValueOutsideWire => "送受信の外で JsonValue",
            ProjectRule::ShapeCheckInJudgment => "判断の層で入力の形を調べる",
            ProjectRule::FailureRethrow => "失敗を受けて返し直すだけ",
            ProjectRule::BindThenReturn => "<- の直後に return するだけ",
            ProjectRule::FieldsJoinedIntoText => "欄をつないで 1 本の文字列にする",
            ProjectRule::RebuiltAccumulator => "ループの中で蓄えを作り直す",
            ProjectRule::DefkCalledBare => "defk を素で呼んで答えに使う",
            ProjectRule::EffectsDisagreeWithInference => ":effects の宣言が推論と合わない",
            ProjectRule::TranslationEmitsIntent => "翻訳の handler が業務の intent を出す",
            ProjectRule::WorldHandlerNamedOutsideList => "許可名簿の外で実 I/O の handler を名指す",
            ProjectRule::WorldHandlerMisplaced => "許可名簿の定義が無い・foundation の外に在る",
            ProjectRule::TestKindMismatch => "テストの種類(手元 / 縁)と印が食い違う",
            ProjectRule::TestFormNotDeftest => "deftest 以外のテストの形",
            ProjectRule::VocabularyOutsideSinglePoint => "判定の1点の外で同じ語彙を読んでいる",
            ProjectRule::ServiceUntestedOnSim => "模擬の環境のテストが回さない service",
            ProjectRule::RetiredWord => "使わないと決めた綴り",
            ProjectRule::RetiredCall => "使わないと決めた呼び",
            ProjectRule::HandlerArgumentHoldsState => "handler の引数が client・可変の店を取る",
            ProjectRule::UntypedStructuredValue => "公開面の型が素の写像・素の組",
            ProjectRule::RecordStubNotKwOnly => "型の宣言の @dataclass に kw_only=True が無い",
            ProjectRule::SemanticBusinessDecision => "翻訳の層で業務の判断(Jev)",
            ProjectRule::SemanticTransportKnowledge => "判断の層が通信の手段を知る(Jev)",
            ProjectRule::SemanticPlainCallable => "deff の理由が合わない(Jev)",
            ProjectRule::SemanticClassRole => "class が外の窓口か状態を持つ(Jev)",
            ProjectRule::SemanticMixedConcerns => "入力の形の確認と判断が混ざる(Jev)",
        }
    }

    /// 規則の家族(エディタの一覧で束ねる分類)。
    pub fn family(self) -> RuleFamily {
        match self {
            ProjectRule::LayerImportDirection
            | ProjectRule::LayerForbiddenModule
            | ProjectRule::LayerTypesOnly
            | ProjectRule::TranslationEmitsIntent => {
                RuleFamily::Layer
            }
            ProjectRule::ModuleDeclaresTags | ProjectRule::RoleMatchesLayer | ProjectRule::ContextMatchesService => {
                RuleFamily::Tags
            }
            ProjectRule::RawSideEffectDirect | ProjectRule::RawSideEffectVia | ProjectRule::WorldHandlerNamedOutsideList | ProjectRule::WorldHandlerMisplaced
            | ProjectRule::TestKindMismatch => {
                RuleFamily::Raw
            }
            ProjectRule::EnvironmentName | ProjectRule::RetiredWord | ProjectRule::RetiredCall | ProjectRule::VocabularyOutsideSinglePoint => RuleFamily::Naming,
            ProjectRule::UnknownConfigKey
            | ProjectRule::UnreadableFile
            | ProjectRule::ServiceBoundary
            | ProjectRule::UndeclaredPlace
            | ProjectRule::UndeclaredDirectory
            | ProjectRule::ServiceDependency
            | ProjectRule::PlacedDependency
            | ProjectRule::UnusedDependency => RuleFamily::Place,
            ProjectRule::DefnForbidden
            | ProjectRule::DeffNeedsReason
            | ProjectRule::DefinitionTagsRequired
            | ProjectRule::TestIsDeftest
            | ProjectRule::TestFormNotDeftest
            | ProjectRule::ServiceUntestedOnSim
            | ProjectRule::HandlerArgumentHoldsState
            | ProjectRule::UntypedStructuredValue
            | ProjectRule::RecordStubNotKwOnly
            | ProjectRule::DefkCalledBare
            | ProjectRule::EffectsDisagreeWithInference => RuleFamily::Definition,
            ProjectRule::ClassWithBehaviour | ProjectRule::SemanticClassRole => RuleFamily::Class,
            ProjectRule::JsonValueOutsideWire => RuleFamily::Wire,
            ProjectRule::ShapeCheckInJudgment
            | ProjectRule::FailureRethrow
            | ProjectRule::BindThenReturn
            | ProjectRule::FieldsJoinedIntoText
            | ProjectRule::RebuiltAccumulator
            | ProjectRule::SemanticMixedConcerns => RuleFamily::Smell,
            ProjectRule::SemanticBusinessDecision | ProjectRule::SemanticTransportKnowledge | ProjectRule::SemanticPlainCallable => {
                RuleFamily::Jev
            }
        }
    }

    /// 題(人が読む短い名)。
    pub fn title(self) -> &'static str {
        match self {
            ProjectRule::UnknownConfigKey => "Unknown Config Key",
            ProjectRule::UnreadableFile => "Unreadable Hy File",
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
            ProjectRule::UndeclaredPlace => "Undeclared Place",
            ProjectRule::UndeclaredDirectory => "Undeclared Directory",
            ProjectRule::ServiceDependency => "Service Dependency",
            ProjectRule::PlacedDependency => "Placed Dependency",
            ProjectRule::UnusedDependency => "Unused Dependency",
            ProjectRule::TestIsDeftest => "Tests Are deftest",
            ProjectRule::ClassWithBehaviour => "Class Touches The World Or Holds State",
            ProjectRule::JsonValueOutsideWire => "JsonValue Outside Wire Modules",
            ProjectRule::ShapeCheckInJudgment => "Shape Check In Judgment",
            ProjectRule::FailureRethrow => "Hand-Written Failure Rethrow",
            ProjectRule::BindThenReturn => "Bind Then Return",
            ProjectRule::FieldsJoinedIntoText => "Fields Joined Into Text",
            ProjectRule::RebuiltAccumulator => "Rebuilt Accumulator",
            ProjectRule::DefkCalledBare => "defk Called Bare",
            ProjectRule::EffectsDisagreeWithInference => "Effects Disagree With Inference",
            ProjectRule::TranslationEmitsIntent => "Translation Emits Intent",
            ProjectRule::WorldHandlerNamedOutsideList => "World Handler Named Outside The List",
            ProjectRule::WorldHandlerMisplaced => "World Handler Misplaced",
            ProjectRule::TestKindMismatch => "Test Kind Mismatch",
            ProjectRule::TestFormNotDeftest => "Test Form Not Deftest",
            ProjectRule::VocabularyOutsideSinglePoint => "Vocabulary Outside Single Point",
            ProjectRule::ServiceUntestedOnSim => "Service Untested On Sim",
            ProjectRule::RetiredWord => "Retired Word",
            ProjectRule::RetiredCall => "Retired Call",
            ProjectRule::HandlerArgumentHoldsState => "Handler Argument Holds State",
            ProjectRule::UntypedStructuredValue => "Untyped Structured Value",
            ProjectRule::RecordStubNotKwOnly => "Record Stub Not Kw Only",
            ProjectRule::SemanticBusinessDecision => "Business Decision In Translation (Jev)",
            ProjectRule::SemanticTransportKnowledge => "Transport Knowledge In Core (Jev)",
            ProjectRule::SemanticPlainCallable => "Plain Callable Reason (Jev)",
            ProjectRule::SemanticClassRole => "Class Role (Jev)",
            ProjectRule::SemanticMixedConcerns => "Shape Check Mixed With Judgment (Jev)",
        }
    }

    /// 規則の文(law が結びついていない時に一覧へ出す)。
    pub fn statement(self) -> &'static str {
        match self {
            ProjectRule::UnreadableFile => "Hy の file は読み取り器が最後まで読める(読めない file は違反が欠けるので、有効な規則の一覧に関わらず error で知らせる)",
            ProjectRule::UnknownConfigKey => "設定(pyproject の [tool.doeff-linter]・architecture.hy)の鍵と規則の ID は、この linter が知っている物だけ — 知らない物はその鍵だけを読まずに残りの規則を走らせ、知らせる(linter が設定より古いか、書き違い)",
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
            ProjectRule::UndeclaredPlace => "root の下の module は、architecture.hy で宣言した service の層・shared・foundation・legacy のどれかに置く",
            ProjectRule::UndeclaredDirectory => "root の下の dir は宣言した service か shared・foundation・legacy で、service の中の dir は宣言した層",
            ProjectRule::ServiceDependency => "service A が読んでよいのは、A の :depends-on に在る service の、A の module の層が読める層(その層の :dependency-layers — 組み立ての層は intent と protocol —、無ければ :open-layers の intent)と shared だけ",
            ProjectRule::PlacedDependency => "architecture.hy の :placed-dependencies の層の module(service と shared)は、層の置き場(service の層・shared の層・foundation と、層の名の段を持つ dir)に在る module にだけ依存する — root の下の置き場の決まっていない module(service の dir の直下・宣言に無い dir の中)を import しない",
            ProjectRule::UnusedDependency => "宣言した依存(:depends-on)を、その service のどの module も読んでいない(知らせ)",
            ProjectRule::TestIsDeftest => "検の置き場(設定の test_paths)の検は deftest で書く — 名が test- / test_ で始まる defn・deff・defk・fn の束縛を置かない",
            ProjectRule::ClassWithBehaviour => "業務の code の defclass は値の class だけ — method か欄の初期値が生の副作用に触る class(error)と、method が self の欄を書き換える class(warning)を書かない。欄だけの class は defrecord を勧める(info)。例外・Enum・Protocol・外の library の基底を継ぐ class は許す。名前では判じない",
            ProjectRule::JsonValueOutsideWire => "JsonValue・JSONValue・JsonObject・JSONObject(素の dict・list・str … を名で包んだだけの型)を使ってよいのは、汎用の解き手(doeff_hy.wire・doeff_records.wire)と、architecture.hy の :wire-modules に挙げた foundation の層の送受信の module だけ。ほかの module(protocol・intent・core …)は、解き手が形を確かめた型のある値だけを見る",
            ProjectRule::SemanticBusinessDecision => "翻訳の層の定義は、要求を相手の話し方へ言い換えるだけで、業務の判断をしない(Jev の判定・warning か info)",
            ProjectRule::SemanticTransportKnowledge => "判断の層の定義は、通信の手段(URL・HTTP・JSON の wire・SQL)を知らない(Jev の判定・warning か info)",
            ProjectRule::SemanticPlainCallable => "deff の理由の註の文は、architecture.hy が受け入れる理由(外の library が素の関数を呼ぶ等)に当たる(Jev の判定・warning か info)",
            ProjectRule::ShapeCheckInJudgment => "判断の層(設定の smells.shape_check_layers)の定義は、文字列の鍵の (.get x \"欄\") とその欄への isinstance で入力の形を検めない",
            ProjectRule::FailureRethrow => "match の腕が、失敗の型(defrecord の :failure True・defeffect の :failure / :absent の宣言)を受けて、受けた値かそれを包み直した値を return するだけにしない",
            ProjectRule::BindThenReturn => "(<- x T (f …)) の直後に (return x) を置き、x を他で使わない形にしない",
            ProjectRule::FieldsJoinedIntoText => "同じ値の 2 つ以上の欄を + か f 文字列で 1 本の文字列につながない",
            ProjectRule::RebuiltAccumulator => "for / while の中で (:= xs (+ xs #(…))) と蓄えを毎回作り直さない",
            ProjectRule::EffectsDisagreeWithInference => "defk の :effects を書いたなら、本体で撃つ呼び((<- …)・(! …))から推論した effect と同じ集合にする — 宣言に無い effect を起こさず、起こさない effect を宣言しない(:effects の無い defk は対象外)",
            ProjectRule::TestFormNotDeftest => "テストは deftest だけで書き pytest が収集する — Python の def test_*・module ごとの skip・pytest の外で走る check script・deftest を自分で回す runner は置かない(operator 2026-09-27「検は deftest だけ」)。file は architecture.hy の :test-forms の綴りの型で選ぶ",
            ProjectRule::VocabularyOutsideSinglePoint => "ある語彙(job の phase・turn の state・担い手の欄 等)の判定は 1 つの file(architecture.hy の :single-point-vocabulary の :except)に閉じ込める — 他の file が同じ正規表現に当たる綴りを読むと、そこが第 2 の判定点になり、直す時に片方だけ直して食い違う",
            ProjectRule::RetiredWord => "architecture.hy の :retired-words で使わないと決めた綴りを、群の :files の file に書かない — :words は語として単独で在る所(前後が英字・_・- でない)、:patterns は行ごとの正規表現、:in names は定義の名だけを見る。:rule-lines の綴りを含む行(規則そのものを述べる行)は数えない",
            ProjectRule::RetiredCall => "architecture.hy の :retired-calls で使わないと決めた呼び(退役した effect・時計 …)を、群の :files の Hy の file で呼ばない — 頭の記号が :calls の綴りの form を数え、註・文字列・読み捨てた form は数えない",
            ProjectRule::UntypedStructuredValue => "構造を持つ値は欄の名前と型を静的に持つ型(frozen の dataclass・defrecord・defwire)で表す — architecture.hy の :typed-values の file の公開面(名が _ で始まらない物)の、class の欄・関数と method の戻り値・defk / deff の :post の型に、素の写像(dict・Mapping・JsonValue …)・素の組(tuple)・値が object / Any の写像・長さの決まった組 tuple[A, B]・それらを中身に持つ入れ物を書かず、defn / defk / deff は長さ 2 以上の組の literal #(a b) を答えにしない。:post は isinstance の契約なので写像だけを赤にし、名に型の注記の無い defk / deff の :post は素の組も赤にする",
            ProjectRule::RecordStubNotKwOnly => "architecture.hy の :record-stubs の型の宣言(.pyi)は、同じ dir の同じ名の .hy の実行時の形を偽らない — .hy で欄を名でしか受けない record(defrecord か、飾りに (dataclass … :kw-only True …) を持つ defclass)を @dataclass で宣言するなら kw_only=True を書く",
            ProjectRule::ServiceUntestedOnSim => "業務の service は、本番の組み立て(entry の層)のまま模擬の環境に載せ、handler の差し替えだけで回して確かめる — 模擬の環境(:verification-environment)の下の deftest がその service の entry の層の定義に 1 本も届かなければ、未検証の service として赤にする",
            ProjectRule::HandlerArgumentHoldsState => "handler は接続の object や書き換える店を引数で受け取らない — 接続先と資格・設定は Ask で読み、client は本文の先頭の (session val client …) で 1 回だけ作り、状態は (session var …) で持つ(外側の handler が差し替え・観測できる)。引数に残す物は本文に architecture.hy の :handler-arguments の :keep-mark の註で理由を書く",
            ProjectRule::TestKindMismatch => "テストの種類は 2 つだけ — 手元(届く定義に外の世界に触れる handler が無い)/ 縁(名簿の定義・:wraps の handler・生の I/O に届く)。種類は人が決めず届く先から導き、縁のテストだけが architecture.hy の :edge-mark の印を持つ(operator 2026-09-29 \"everything is 'pure' until we apply handler that has real IO\")",
            ProjectRule::WorldHandlerMisplaced => "architecture.hy の :world-handlers に挙げた定義は実在し、層 foundation の module に在る(外の世界に触れてよい定義の置き場は foundation だけ)",
            ProjectRule::WorldHandlerNamedOutsideList => "architecture.hy の :world-handlers の :wraps に挙げた doeff の実 I/O の handler(os-file-handler・http-production-handler …)を名指してよいのは、許可名簿の定義(とその中の入れ子の定義)だけ — 値として渡す所(with-handlers の列)も呼び出しも数える",
            ProjectRule::TranslationEmitsIntent => "翻訳の層(設定の handler_layers)の handler — defhandler と [effect k] を受ける関数 — は doeff の汎用の effect だけを出し、業務の intent(設定の intent_layers の型)を出さない — 本体で実行する呼び((<- …)・(! …))を import した defk の先まで辿る",
            ProjectRule::DefkCalledBare => "defk の定義は Program として渡す所((<- …) の右辺・(! …)・(return …)・Program を受ける呼びの引数)だけで呼ぶ — 素で呼ぶと答えではなく Program が返る",
            ProjectRule::SemanticMixedConcerns => "役が judgment / program の定義は、入力の形の検めと業務の判断を混ぜない(Jev の判定 — warning か info)",
            ProjectRule::SemanticClassRole => "DOEFF119 が何も出さない、処理を持つ method のある class は値の class(欄から計算するだけ)である(Jev の判定 — 外の世界の窓口か状態を持つ物なら warning か info)",
        }
    }

    /// 直し方の既定の 1 行。
    pub fn hint(self) -> &'static str {
        match self {
            ProjectRule::UnreadableFile => "Hy 本体(hy.read_many)が読めるなら doeff-linter の読み取り器の誤りなので知らせる。Hy も読めないなら括弧か文字列を直す",
            ProjectRule::UnknownConfigKey => "linter が古いなら本線からの自動の組み直しを待つ(開発版の置き場は数分以内に置き換わる — 手で組んで差し替えない)。書き違いなら鍵の名を直す",
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
            ProjectRule::UndeclaredPlace => "architecture.hy に宣言するか、宣言した置き場所(<root>/<service>/<層>/)へ移す",
            ProjectRule::UndeclaredDirectory => "architecture.hy に defservice か service の :layers を足すか、dir を宣言した置き場所へ移す",
            ProjectRule::ServiceDependency => "依存先を :depends-on に足し、依存先の intent を出して頼む(判断や翻訳の module を直に読まない)",
            ProjectRule::PlacedDependency => "読む先の module を層の置き場(<root>/<service>/<層>/)へ移すか、要る型を intent へ移して読む — 移す前の置き場への依存は移す変更で消す",
            ProjectRule::UnusedDependency => "使っていない依存を :depends-on から外す",
            ProjectRule::TestIsDeftest => "deftest にする(検の値を組む補助は defk にして deftest の中で `(<- …)` で呼ぶ)",
            ProjectRule::ClassWithBehaviour => "外の世界の窓口は土台の handler にする — 資源(接続・client・file の手)は defhandler の直下の (session val …) に持ち、ListRows・PutRow などの effect に答える(模擬なら模擬の土台の handler)。状態なら 値は defrecord(不変)、振る舞いは新しい値を返す純粋な関数、状態は world などの handler の (session var …) 1 か所に置き、変化は effect で流す。速さのために書き換えが要る時も書き換えは handler の中だけ",
            ProjectRule::JsonValueOutsideWire => "JSON の形を defwire で型に起こし、送受信の foundation の module が parse した型のある値を渡す(JsonValue を手で分解して読む関数は書かない)。送受信そのものを行う foundation の module なら architecture.hy の :wire-modules に挙げる",
            ProjectRule::SemanticBusinessDecision => "業務の判断は core の judgment へ移し、翻訳の handler はその答えを使うだけにする(Jev の外れなら誤判定の一覧に載せる)",
            ProjectRule::SemanticTransportKnowledge => "通信の手段は protocol の翻訳の handler へ移し、core は intent を出すだけにする(Jev の外れなら誤判定の一覧に載せる)",
            ProjectRule::SemanticPlainCallable => "種類が当たらないなら defk にする — 組み立て(handler の並び)なら `(defk handlers-of [foundation])` に・テストなら deftest に・値を組む補助なら defk にして `(<- …)` で呼ぶ(Jev の外れなら誤判定の一覧に載せる)",
            ProjectRule::ShapeCheckInJudgment => "形の検めは protocol の境目で defwire の型に parse し(形が合わなければ解く所で失敗)、この定義は型のある値を受けて判断だけをする",
            ProjectRule::FailureRethrow => "失敗は (<- (Raise 失敗の値)) で出し(呼び手へ手で return し直さない)、受けて写す所だけ呼ぶ側で (on-raise 本文 (Refusal r) 写し先) と受ける(ADR-DOE-CORE-EFFECTS-003 R3・R7)",
            ProjectRule::BindThenReturn => "(return (! (f …))) と 1 つにするか、失敗なら (<- (Raise …)) で出す",
            ProjectRule::FieldsJoinedIntoText => "型のある値のまま渡す(欄を文字列に潰さない)— 文にするのは人に見せる境目の 1 か所だけ",
            ProjectRule::RebuiltAccumulator => "蓄えは内包表記(lfor)で 1 度に作る — ループの中で (+ xs #(…)) の作り直しを重ねない",
            ProjectRule::EffectsDisagreeWithInference => ":effects に起こしている effect を足すか、起こしていない effect を消す(推論は handler で受けた effect を引かない — 本体で受けているなら登録簿に載せる)",
            ProjectRule::TestFormNotDeftest => "deftest に書き直す(判定の関数は同じ process で呼ぶ)— 守る物が消えたなら file ごと消す。module ごとの skip は足りない物を直すか、その検だけを skip する",
            ProjectRule::VocabularyOutsideSinglePoint => "群の :instead が名指す判定の1点(:except の file)の答えを読む — 直せない既存の当たりは登録簿に載せる",
            ProjectRule::RetiredWord => "群の :instead の語に書き換える(規則そのものを述べる行なら、:rule-lines の綴りを含めて書く)— 直せない既存の当たりは登録簿に載せる",
            ProjectRule::RetiredCall => "群の :instead の物に置き換える — 直せない既存の当たりは登録簿に載せる",
            ProjectRule::HandlerArgumentHoldsState => "引数を消し、接続先と設定は Ask、client は (session val …)、状態は (session var …) へ移す — 同じ handler を別の設定で並べるなど引数に残す物は本文に理由の註を書く",
            ProjectRule::UntypedStructuredValue => "欄の名前と型を持つ frozen の record(defrecord・defwire・dataclass)を定義して返す・持つ — :post は [(: % <record の型>)] で検める。キーで引く索引は dict[str, Row]・同じ型の列は tuple[X, ...] と中身の型を書く。値の形を外の系が決める境界なら登録簿に理由と共に載せる",
            ProjectRule::RecordStubNotKwOnly => "型の宣言の飾りを @dataclass(frozen=True, kw_only=True) にする",
            ProjectRule::ServiceUntestedOnSim => "模擬の環境の tests に、その service の entry の組み立てを handler の差し替えだけで回す deftest を足す",
            ProjectRule::TestKindMismatch => "縁なら印を付け(既定の pytest から外れる)、手元のつもりなら届く先の実 I/O の handler を模擬の handler に替える — 手元なのに印が在れば外す",
            ProjectRule::WorldHandlerMisplaced => "定義を foundation の層(architecture.hy の :foundation の dir)へ移すか、名簿の綴り(module:名)を実物に合わせる — 要らなくなった定義なら名簿から外す",
            ProjectRule::WorldHandlerNamedOutsideList => "名簿の定義(例 with-agora-process)の下で本体を走らせ、自分では実 I/O の handler を被せない — 新しく外の世界に触れる所が要るなら、その定義を foundation の層に置いて名簿に載せる",
            ProjectRule::TranslationEmitsIntent => "intent を出す業務の流れは層 core の program に置き、翻訳の handler は受けた intent を doeff の汎用の effect(HttpRequest・記録の読み書き・時計 …)へ出し直すだけにする — 経由した defk が intent を出すなら、その defk を呼ばずに汎用の effect を直に使う",
            ProjectRule::DefkCalledBare => "(<- x (f …)) で束ねるか (! (f …)) で答えを受ける — 素の関数の中なら、その関数を defk にして呼び手を Program にする",
            ProjectRule::SemanticMixedConcerns => "形の検めは protocol の境目で defwire の型に parse し(形が合わなければ解く所で失敗)、この定義は型のある値を受けて判断だけをする(Jev の外れなら誤判定の一覧に載せる)",
            ProjectRule::SemanticClassRole => "外の世界の窓口なら土台の handler(資源は (session val …))、状態なら handler の (session var …) 1 か所(Jev の外れなら誤判定の一覧に載せる)",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// DOEFF の ID → 割り当てるべき家族(依頼の表そのもの)。
    const EXPECTED_FAMILIES: &[(&str, RuleFamily)] = &[
        ("DOEFF100", RuleFamily::Place),
        ("DOEFF128", RuleFamily::Place),
        ("DOEFF101", RuleFamily::Layer),
        ("DOEFF102", RuleFamily::Layer),
        ("DOEFF103", RuleFamily::Layer),
        ("DOEFF104", RuleFamily::Tags),
        ("DOEFF105", RuleFamily::Tags),
        ("DOEFF113", RuleFamily::Tags),
        ("DOEFF106", RuleFamily::Raw),
        ("DOEFF107", RuleFamily::Raw),
        ("DOEFF108", RuleFamily::Naming),
        ("DOEFF109", RuleFamily::Place),
        ("DOEFF114", RuleFamily::Place),
        ("DOEFF115", RuleFamily::Place),
        ("DOEFF116", RuleFamily::Place),
        ("DOEFF117", RuleFamily::Place),
        ("DOEFF110", RuleFamily::Definition),
        ("DOEFF111", RuleFamily::Definition),
        ("DOEFF112", RuleFamily::Definition),
        ("DOEFF118", RuleFamily::Definition),
        ("DOEFF119", RuleFamily::Class),
        ("DOEFF204", RuleFamily::Class),
        ("DOEFF120", RuleFamily::Wire),
        ("DOEFF121", RuleFamily::Smell),
        ("DOEFF122", RuleFamily::Smell),
        ("DOEFF123", RuleFamily::Smell),
        ("DOEFF124", RuleFamily::Smell),
        ("DOEFF125", RuleFamily::Smell),
        ("DOEFF205", RuleFamily::Smell),
        ("DOEFF126", RuleFamily::Definition),
        ("DOEFF127", RuleFamily::Definition),
        ("DOEFF130", RuleFamily::Layer),
        ("DOEFF131", RuleFamily::Raw),
        ("DOEFF132", RuleFamily::Raw),
        ("DOEFF133", RuleFamily::Raw),
        ("DOEFF135", RuleFamily::Definition),
        ("DOEFF136", RuleFamily::Definition),
        ("DOEFF140", RuleFamily::Place),
        ("DOEFF144", RuleFamily::Definition),
        ("DOEFF145", RuleFamily::Definition),
        ("DOEFF150", RuleFamily::Naming),
        ("DOEFF151", RuleFamily::Naming),
        ("DOEFF142", RuleFamily::Definition),
        ("DOEFF146", RuleFamily::Naming),
        ("DOEFF201", RuleFamily::Jev),
        ("DOEFF202", RuleFamily::Jev),
        ("DOEFF203", RuleFamily::Jev),
    ];

    #[test]
    fn every_rule_has_a_non_empty_label() {
        for rule in ProjectRule::ALL {
            assert!(!rule.label().is_empty(), "{} の label が空", rule.id());
        }
    }

    #[test]
    fn family_matches_the_assignment_table_for_all_33_rules() {
        assert_eq!(EXPECTED_FAMILIES.len(), ProjectRule::ALL.len(), "割り当ての表が ALL の数と食い違う");
        for &(id, expected) in EXPECTED_FAMILIES {
            let rule = ProjectRule::parse(id).unwrap_or_else(|| panic!("{} は ProjectRule に無い", id));
            assert_eq!(rule.family(), expected, "{} の family が違う", id);
        }
    }
}
