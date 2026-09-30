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
    /// DOEFF168: 業務の層(architecture.hy の :environment-branches の :layers)の Hy の定義が、環境の名の値と比べるか dry-run の印で分岐する
    /// (agora-redesign #1906 — 業務の層は環境を知らない・環境の違いは handler の組の差し替えだけで表す)。
    EnvironmentBranch,
    /// DOEFF169: `match` の class pattern の keyword の欄の名に `-` が在る(Hy が属性名へ mangle しないので決して当たらない — agora-redesign #2036)。
    MatchFieldHyphen,
    /// DOEFF131: architecture.hy の許可名簿(:world-handlers)の外の定義が、名簿の :wraps に挙げた doeff の実 I/O の handler を名指す
    /// (外の世界に触れてよいのは名簿の定義だけ — agora-redesign #1106 の R1)。
    WorldHandlerNamedOutsideList,
    /// DOEFF132: 許可名簿の定義が実在しない・層 foundation の外に在る(外の世界に触れてよい定義は foundation の層にだけ置く —
    /// agora-redesign #1106 の R2)。位置は architecture.hy の名簿の要素。
    WorldHandlerMisplaced,
    /// DOEFF133: テストの種類(手元 / 縁)を届く先から導き、architecture.hy の :edge-mark の印と食い違う物 — 名簿の定義・:wraps の
    /// handler・生の I/O に届くのに印が無い / 届かないのに印が在る(agora-redesign #1106 の R3)。
    /// Hy の定義が名指す repo の中の Python の関数の中の生の I/O と呼び先も届く先に数える(agora-redesign #1798・python_reach)。
    TestKindMismatch,
    /// DOEFF137: architecture.hy の許可名簿(:world-handlers)の handler に、縁の検(空でない `:interpreters` を持ち、その handler の
    /// 定義に届く deftest)が 1 本も無い — 本物と模擬が同じ検を通ることを見ていない実 I/O の handler(agora-redesign #1363)。
    /// 理由つきの `:contract-test (none …)` の handler は判じない・理由の無い `:contract-test none` は鳴る(書き方は直し方の文 — #1796)。
    WorldHandlerWithoutContractTest,
    /// DOEFF135: deftest 以外のテストの形(Python の def test_*・module ごとの skip・pytest の外の check script・deftest の runner)—
    /// architecture.hy の :test-forms の綴りの型で file を選ぶ(agora-redesign #1106 の R6)。
    TestFormNotDeftest,
    /// DOEFF146: 判定を1か所に閉じ込めた語彙が :except の外に在る — architecture.hy の :single-point-vocabulary の群が
    /// 名指す語彙(正規表現)を、:except の file(判定の1点)の外の :files が読んでいる(agora-redesign #1192・#1371)。
    VocabularyOutsideSinglePoint,
    /// DOEFF148: 書いてよい file を決めた綴りが :except の外に在る — architecture.hy の :confined-spellings の群が名指す綴り
    /// (正規表現・文字列の中も数える)を、:except の外の :files が書いている(agora-redesign #1373・#1436)。
    SpellingOutsideItsFiles,
    /// DOEFF161: 数を決めた綴りの当たりの数が決めた数でない — architecture.hy の :counted-spellings の宣言の :pattern を、:files(か :within
    /// の定義)の中で数えた数が :count / :at-least に合わない(agora-redesign #1373・#1437)。
    SpellingCountDiffers,
    /// DOEFF162: effect の宣言の全体が一覧と食い違う — architecture.hy の :effect-census の :files で :base を継ぐ class の宣言が
    /// :effects の一覧に無い・2 度在る・一覧の effect の宣言が無い(agora-redesign #1373・#1438)。
    EffectOutsideCensus,
    /// DOEFF149: 型の欄を持つ class の顔ぶれが一覧と食い違う — architecture.hy の :field-holders の :files の Python の class のうち、
    /// 欄の注記に :type が在る class が :holders に無い・:holders の class がその欄を持たない(agora-redesign #1374)。
    FieldHoldersDiffer,
    /// DOEFF136: service の entry の層の定義に、模擬の環境(:verification-environment)の下の deftest が 1 本も届かない
    /// — 本番の組み立てを手元で回していない service(agora-redesign #1106 の R5・#1111)。
    ServiceUntestedOnSim,
    /// DOEFF163: code を持つ service(entry の層に定義が 1 本以上)が defservice に :invariants を宣言していない・名指した関数が実在しない・
    /// その関数の :role が judgment でない(agora-redesign #1559・#1155 の定義 1 — 業務ロジックを「テストした」の条 (b))。
    ServiceInvariantsMissing,
    /// DOEFF140: architecture.hy の :placed-dependencies の層の module(service と shared)が、root の下の層の置き場の外の module を
    /// import する — 置き場の決まっていない module への依存(agora-redesign #1188)。
    PlacedDependency,
    /// DOEFF141: architecture.hy の :blind-definitions の定義から届く定義が宣言した語を読む・定義の module が import を持つ
    /// — 決めた材料だけで判じる定義に、ほかの材料が入り込む(agora-redesign #1368)。
    BlindDefinitionReads,
    /// DOEFF147: architecture.hy の :allowed-heads の定義の中に、許した頭の一覧の外の頭の form が在る — 例外を上げない物だけを
    /// 呼ぶと決めた定義に、上げうる呼びが入り込む(agora-redesign #1372・#1413)。
    DefinitionCallsUnlistedHead,
    /// DOEFF159: architecture.hy の :call-sites で呼んでよい場所と回数を決めた頭を、場所の外で呼ぶ・回数や外の form や分岐が宣言と違う
    /// — 閉じ込めた 1 点が黙って 2 つ目を生やす(agora-redesign #1372・#1414)。
    CallOutsideDeclaredSites,
    /// DOEFF160: 広い例外の捕捉(`(except [] …)`・Exception・BaseException・AssertionError・Python の `except:`)が architecture.hy の
    /// :broad-catches の :carriers(捕まえた例外を名で束縛し、決めた出来事に載せて運ぶ境界)の外に在る(agora-redesign #1372・#1415)。
    BroadCatchOutsideCarrier,
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
    /// DOEFF143: 模擬の根から届き本番の入口から届かない定義が業務の効果に tap でなく答える(偽の handler)・外の世界の表と反例の表の腐り
    /// (agora-redesign #1375)。宣言は architecture.hy の :business-fakes。
    BusinessEffectFake,
    /// DOEFF155: 組み立ての形の破れ — 組の file が残る・組み立ての 1 点の関数の形(土台の列 1 つ + 翻訳の層から import した翻訳の列)・
    /// defk 以外で書いた組み立て・列の並び・翻訳の列が別の service の翻訳を並べる(agora-redesign #1376)。宣言は :assembly-shape。
    AssemblyShapeBroken,
    /// DOEFF156: 翻訳の handler が列の外の業務の効果を出し直す・土台の handler が業務の効果に答える(agora-redesign #1376)。
    AssemblyAnswerMisplaced,
    /// DOEFF157: 検の定義からだけ届く定義が業務の効果か下の層の効果に答える — 検だけの偽物(agora-redesign #1377)。宣言は :business-fakes。
    TestOnlyFake,
    /// DOEFF158: 本番の入口から届く intent の効果の答え手が翻訳の層の handler 1 つでない(agora-redesign #1377)。層の名は :assembly-shape。
    IntentAnswererNotTranslation,
    /// DOEFF164: entry の層を持つ service に、壊した handler の反例(反例の表の節に届き、その service の entry にも届く deftest)が 1 本も無い(agora-redesign #1560)。宣言は :business-fakes の :counterexamples と :verification-environment。
    ServiceWithoutCounterexample,
    /// DOEFF167: service の条(defservice の :clauses — :invariants の関数が返す条の名)に、その条を破ると反例の表で名乗る(`breaks:` の行)
    /// 壊した handler の反例が無く、外した理由(:clause-exemptions)も無い — 条ごとの網羅の欠け(agora-redesign #1713・ADR R2 の「条ごとに」)。
    /// code を持つ service が :clauses を宣言していなければ service の名で 1 つ。宣言は defservice と :business-fakes の :counterexamples。
    ClauseWithoutCounterexample,
    /// DOEFF165: intent の層の効果に、手元の検から出す定義・模擬の答え手・本番の答え手のどれかが無い — 網羅の表の欠け
    /// (agora-redesign #1561 K3・まず報告だけ = 重さ info。失敗にするのは #1562 K4)。層の名は :assembly-shape。
    IntentEffectUncovered,
    /// DOEFF166: 登録簿の鍵が、全体の実行で判じた規則のどの所見にも当たらない — 当たらなくなった古い行(agora-redesign #1724・#1706)。
    /// 鍵が名指す規則は enable に無くても同じ実行で当て、当たりは 166 の判じにだけ使う(agora-redesign #1999)。
    RegistryEntryStale,
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
        ProjectRule::EnvironmentBranch,
        ProjectRule::MatchFieldHyphen,
        ProjectRule::WorldHandlerNamedOutsideList,
        ProjectRule::WorldHandlerMisplaced,
        ProjectRule::TestKindMismatch,
        ProjectRule::WorldHandlerWithoutContractTest,
        ProjectRule::TestFormNotDeftest,
        ProjectRule::VocabularyOutsideSinglePoint,
        ProjectRule::SpellingOutsideItsFiles,
        ProjectRule::SpellingCountDiffers,
        ProjectRule::EffectOutsideCensus,
        ProjectRule::FieldHoldersDiffer,
        ProjectRule::ServiceUntestedOnSim,
        ProjectRule::ServiceInvariantsMissing,
        ProjectRule::PlacedDependency,
        ProjectRule::BlindDefinitionReads,
        ProjectRule::DefinitionCallsUnlistedHead,
        ProjectRule::CallOutsideDeclaredSites,
        ProjectRule::BroadCatchOutsideCarrier,
        ProjectRule::RetiredWord,
        ProjectRule::RetiredCall,
        ProjectRule::HandlerArgumentHoldsState,
        ProjectRule::UntypedStructuredValue,
        ProjectRule::RecordStubNotKwOnly,
        ProjectRule::BusinessEffectFake,
        ProjectRule::AssemblyShapeBroken,
        ProjectRule::AssemblyAnswerMisplaced,
        ProjectRule::TestOnlyFake,
        ProjectRule::IntentAnswererNotTranslation,
        ProjectRule::ServiceWithoutCounterexample,
        ProjectRule::ClauseWithoutCounterexample,
        ProjectRule::IntentEffectUncovered,
        ProjectRule::RegistryEntryStale,
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
            ProjectRule::EnvironmentBranch => "DOEFF168",
            ProjectRule::MatchFieldHyphen => "DOEFF169",
            ProjectRule::WorldHandlerNamedOutsideList => "DOEFF131",
            ProjectRule::WorldHandlerMisplaced => "DOEFF132",
            ProjectRule::TestKindMismatch => "DOEFF133",
            ProjectRule::WorldHandlerWithoutContractTest => "DOEFF137",
            ProjectRule::TestFormNotDeftest => "DOEFF135",
            ProjectRule::VocabularyOutsideSinglePoint => "DOEFF146",
            ProjectRule::SpellingOutsideItsFiles => "DOEFF148",
            ProjectRule::SpellingCountDiffers => "DOEFF161",
            ProjectRule::EffectOutsideCensus => "DOEFF162",
            ProjectRule::FieldHoldersDiffer => "DOEFF149",
            ProjectRule::ServiceUntestedOnSim => "DOEFF136",
            ProjectRule::ServiceInvariantsMissing => "DOEFF163",
            ProjectRule::PlacedDependency => "DOEFF140",
            ProjectRule::BlindDefinitionReads => "DOEFF141",
            ProjectRule::DefinitionCallsUnlistedHead => "DOEFF147",
            ProjectRule::CallOutsideDeclaredSites => "DOEFF159",
            ProjectRule::BroadCatchOutsideCarrier => "DOEFF160",
            ProjectRule::RetiredWord => "DOEFF150",
            ProjectRule::RetiredCall => "DOEFF151",
            ProjectRule::HandlerArgumentHoldsState => "DOEFF142",
            ProjectRule::UntypedStructuredValue => "DOEFF144",
            ProjectRule::RecordStubNotKwOnly => "DOEFF145",
            ProjectRule::BusinessEffectFake => "DOEFF143",
            ProjectRule::AssemblyShapeBroken => "DOEFF155",
            ProjectRule::AssemblyAnswerMisplaced => "DOEFF156",
            ProjectRule::TestOnlyFake => "DOEFF157",
            ProjectRule::IntentAnswererNotTranslation => "DOEFF158",
            ProjectRule::ServiceWithoutCounterexample => "DOEFF164",
            ProjectRule::ClauseWithoutCounterexample => "DOEFF167",
            ProjectRule::IntentEffectUncovered => "DOEFF165",
            ProjectRule::RegistryEntryStale => "DOEFF166",
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
            // 縁の検の無い実 I/O の handler(#1363 — 登録簿に載った既知の当たりは warning、新しい当たりは critical)。
            | ProjectRule::WorldHandlerWithoutContractTest
            | ProjectRule::TestFormNotDeftest
            | ProjectRule::VocabularyOutsideSinglePoint
            // 書いてよい file を決めた綴りが外に在る(#1436 — #1373 の孫。新しい当たりは critical)。
            | ProjectRule::SpellingOutsideItsFiles
            | ProjectRule::SpellingCountDiffers
            | ProjectRule::EffectOutsideCensus
            | ProjectRule::FieldHoldersDiffer
            | ProjectRule::ServiceUntestedOnSim
            | ProjectRule::ServiceInvariantsMissing
            // 置き場の外の module への依存(#1188 — 登録簿に載った既知の当たりは warning、新しい当たりは critical)。
            | ProjectRule::PlacedDependency
            // 決めた材料だけで判じる定義に、ほかの材料が入り込む(#1368 — #1188 の子。新しい当たりは critical)。
            | ProjectRule::BlindDefinitionReads
            // 呼んでよい頭を決めた定義に、一覧の外の呼びが入り込む(#1413 — #1372 の孫。新しい当たりは critical)。
            | ProjectRule::DefinitionCallsUnlistedHead
            // 呼んでよい場所と回数を決めた頭が場所の外で呼ばれる(#1414 — #1372 の孫。新しい当たりは critical)。
            | ProjectRule::CallOutsideDeclaredSites
            | ProjectRule::BroadCatchOutsideCarrier
            // 使わないと決めた綴りと呼び(#1193 の決め — 登録簿に載った既知の当たりは warning、新しい当たりは critical)。
            | ProjectRule::RetiredWord
            | ProjectRule::RetiredCall
            | ProjectRule::HandlerArgumentHoldsState
            // 公開面の型の素の写像・素の組と、.pyi の kw_only の食い違い(#1191 の決め — 既定は critical)。
            | ProjectRule::UntypedStructuredValue
            | ProjectRule::RecordStubNotKwOnly
            | ProjectRule::BusinessEffectFake
            | ProjectRule::AssemblyShapeBroken
            | ProjectRule::AssemblyAnswerMisplaced
            | ProjectRule::TestOnlyFake
            | ProjectRule::IntentAnswererNotTranslation
            | ProjectRule::ServiceWithoutCounterexample
            // 条ごとの反例の欠け(#1713 — 登録簿に載った既知の欠けは warning、新しい欠けは critical)。
            | ProjectRule::ClauseWithoutCounterexample
            // intent の効果の網羅の欠け(#1561 K3 の表を #1562 K4 で失敗に — 登録簿に載った既知の欠けは warning、新しい欠けは critical)。
            | ProjectRule::IntentEffectUncovered
            // 登録簿の当たらない古い行(縮める向きの登録簿を、消し忘れで緩めたままにしない — #1706)。
            | ProjectRule::RegistryEntryStale
            | ProjectRule::ServiceBoundary
            | ProjectRule::ServiceDependency
            | ProjectRule::TranslationEmitsIntent
            // 業務の層が環境の名や dry-run の印で分岐する(#1906 — 責務の境界の違反)。
            | ProjectRule::EnvironmentBranch
            // 決して当たらない match の節(#2036 — 黙って既定の枝に倒れる誤り)。
            | ProjectRule::MatchFieldHyphen
            // 宣言に無い置き場所(どの層の決まりも当たらない)・defk を素で呼ぶ(Program が値として流れる本物の誤り)・
            // 読めない file(判定が欠け、0 件に見えても合格ではない)。
            | ProjectRule::UndeclaredPlace
            | ProjectRule::UndeclaredDirectory
            | ProjectRule::DefkCalledBare
            | ProjectRule::UnreadableFile => Some(super::settings::RuleLevel::Critical),
            // Jev の判定(翻訳の層の業務の判断・判断の層の通信の手段・形の検めと判断の混ざり)は、較正が済んで信頼できるまで critical に
            // しない(operator の決定 B・2026-09-30 — agora-redesign #1762 / #1801。#942 で critical にしたのを戻す)。当たりの重さは
            // warning なので、宣言が無いと minor になる — 責務の境界の読みとして見落とさないよう major に置く。
            ProjectRule::SemanticBusinessDecision
            | ProjectRule::SemanticTransportKnowledge
            | ProjectRule::SemanticMixedConcerns => Some(super::settings::RuleLevel::Major),
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
            | ProjectRule::EnvironmentBranch
            | ProjectRule::MatchFieldHyphen
            | ProjectRule::SemanticBusinessDecision
            | ProjectRule::SemanticTransportKnowledge => true,
            ProjectRule::UnknownConfigKey
            | ProjectRule::UnreadableFile
            | ProjectRule::UndeclaredPlace
            | ProjectRule::UndeclaredDirectory
            | ProjectRule::UnusedDependency
            | ProjectRule::WorldHandlerMisplaced
            | ProjectRule::TestKindMismatch
            | ProjectRule::WorldHandlerWithoutContractTest
            | ProjectRule::TestFormNotDeftest
            | ProjectRule::VocabularyOutsideSinglePoint
            | ProjectRule::SpellingOutsideItsFiles
            | ProjectRule::SpellingCountDiffers
            | ProjectRule::EffectOutsideCensus
            | ProjectRule::FieldHoldersDiffer
            | ProjectRule::ServiceUntestedOnSim
            | ProjectRule::ServiceInvariantsMissing
            | ProjectRule::RetiredWord
            | ProjectRule::RetiredCall
            | ProjectRule::BlindDefinitionReads
            | ProjectRule::DefinitionCallsUnlistedHead
            | ProjectRule::CallOutsideDeclaredSites
            | ProjectRule::BroadCatchOutsideCarrier
            | ProjectRule::HandlerArgumentHoldsState
            | ProjectRule::UntypedStructuredValue
            | ProjectRule::RecordStubNotKwOnly
            | ProjectRule::BusinessEffectFake
            | ProjectRule::AssemblyShapeBroken
            | ProjectRule::AssemblyAnswerMisplaced
            | ProjectRule::TestOnlyFake
            | ProjectRule::IntentAnswererNotTranslation
            | ProjectRule::ServiceWithoutCounterexample
            | ProjectRule::ClauseWithoutCounterexample => false,
            | ProjectRule::IntentEffectUncovered
            | ProjectRule::RegistryEntryStale => false,
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
            ProjectRule::BlindDefinitionReads => "決めた材料の外を読む判断の定義",
            ProjectRule::DefinitionCallsUnlistedHead => "呼んでよい頭の一覧の外を呼ぶ定義",
            ProjectRule::CallOutsideDeclaredSites => "決めた場所の外で呼ぶ頭",
            ProjectRule::BroadCatchOutsideCarrier => "運搬の境界の外の広い例外の捕捉",
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
            ProjectRule::EnvironmentBranch => "業務の層が環境の名や dry-run の印で分岐する",
            ProjectRule::MatchFieldHyphen => "match の class pattern の欄の名に - が在る(決して当たらない)",
            ProjectRule::WorldHandlerNamedOutsideList => "許可名簿の外で実 I/O の handler を名指す",
            ProjectRule::WorldHandlerMisplaced => "許可名簿の定義が無い・foundation の外に在る",
            ProjectRule::TestKindMismatch => "テストの種類(手元 / 縁)と印が食い違う",
            ProjectRule::WorldHandlerWithoutContractTest => "縁の検の無い許可名簿の handler",
            ProjectRule::TestFormNotDeftest => "deftest 以外のテストの形",
            ProjectRule::VocabularyOutsideSinglePoint => "判定の1点の外で同じ語彙を読んでいる",
            ProjectRule::SpellingOutsideItsFiles => "書いてよい file の外の綴り",
            ProjectRule::SpellingCountDiffers => "数が決めた数でない綴り",
            ProjectRule::EffectOutsideCensus => "一覧と食い違う effect の宣言",
            ProjectRule::FieldHoldersDiffer => "一覧と食い違う型の欄の持ち手",
            ProjectRule::ServiceUntestedOnSim => "模擬の環境のテストが回さない service",
            ProjectRule::ServiceInvariantsMissing => "不変条件を宣言していない service",
            ProjectRule::RetiredWord => "使わないと決めた綴り",
            ProjectRule::RetiredCall => "使わないと決めた呼び",
            ProjectRule::HandlerArgumentHoldsState => "handler の引数が client・可変の店を取る",
            ProjectRule::UntypedStructuredValue => "公開面の型が素の写像・素の組",
            ProjectRule::RecordStubNotKwOnly => "型の宣言の @dataclass に kw_only=True が無い",
            ProjectRule::BusinessEffectFake => "業務の効果に答える偽の handler",
            ProjectRule::AssemblyShapeBroken => "組み立ての形の破れ",
            ProjectRule::AssemblyAnswerMisplaced => "翻訳の先か土台の答えが業務の効果",
            ProjectRule::TestOnlyFake => "検だけの偽物",
            ProjectRule::IntentAnswererNotTranslation => "intent の効果の答え手が翻訳の 1 つでない",
            ProjectRule::ServiceWithoutCounterexample => "壊した handler の反例が無い service",
            ProjectRule::ClauseWithoutCounterexample => "反例も外した理由も無い不変条件の条",
            ProjectRule::IntentEffectUncovered => "intent の効果の網羅の欠け",
            ProjectRule::RegistryEntryStale => "登録簿の当たらない古い行",
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
            | ProjectRule::TranslationEmitsIntent
            | ProjectRule::EnvironmentBranch
            | ProjectRule::MatchFieldHyphen => {
                RuleFamily::Layer
            }
            ProjectRule::ModuleDeclaresTags | ProjectRule::RoleMatchesLayer | ProjectRule::ContextMatchesService => {
                RuleFamily::Tags
            }
            ProjectRule::RawSideEffectDirect | ProjectRule::RawSideEffectVia | ProjectRule::WorldHandlerNamedOutsideList | ProjectRule::WorldHandlerMisplaced
            | ProjectRule::TestKindMismatch
            | ProjectRule::WorldHandlerWithoutContractTest => {
                RuleFamily::Raw
            }
            ProjectRule::EnvironmentName | ProjectRule::RetiredWord | ProjectRule::RetiredCall | ProjectRule::VocabularyOutsideSinglePoint
            | ProjectRule::SpellingOutsideItsFiles
            | ProjectRule::SpellingCountDiffers
            | ProjectRule::EffectOutsideCensus
            | ProjectRule::FieldHoldersDiffer => RuleFamily::Naming,
            ProjectRule::UnknownConfigKey
            | ProjectRule::UnreadableFile
            | ProjectRule::ServiceBoundary
            | ProjectRule::UndeclaredPlace
            | ProjectRule::UndeclaredDirectory
            | ProjectRule::ServiceDependency
            | ProjectRule::PlacedDependency
            | ProjectRule::BlindDefinitionReads
            | ProjectRule::UnusedDependency => RuleFamily::Place,
            ProjectRule::DefnForbidden
            | ProjectRule::DeffNeedsReason
            | ProjectRule::DefinitionTagsRequired
            | ProjectRule::TestIsDeftest
            | ProjectRule::TestFormNotDeftest
            | ProjectRule::ServiceUntestedOnSim
            | ProjectRule::ServiceInvariantsMissing
            | ProjectRule::DefinitionCallsUnlistedHead
            | ProjectRule::CallOutsideDeclaredSites
            | ProjectRule::BroadCatchOutsideCarrier
            | ProjectRule::HandlerArgumentHoldsState
            | ProjectRule::UntypedStructuredValue
            | ProjectRule::RecordStubNotKwOnly
            | ProjectRule::BusinessEffectFake
            | ProjectRule::AssemblyShapeBroken
            | ProjectRule::AssemblyAnswerMisplaced
            | ProjectRule::TestOnlyFake
            | ProjectRule::IntentAnswererNotTranslation
            | ProjectRule::ServiceWithoutCounterexample
            | ProjectRule::ClauseWithoutCounterexample
            | ProjectRule::IntentEffectUncovered
            | ProjectRule::RegistryEntryStale
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
            ProjectRule::BlindDefinitionReads => "Blind Definition Reads Beyond Its Inputs",
            ProjectRule::DefinitionCallsUnlistedHead => "Definition Calls A Head Outside Its Allowed List",
            ProjectRule::CallOutsideDeclaredSites => "Head Called Outside Its Declared Sites",
            ProjectRule::BroadCatchOutsideCarrier => "Broad Catch Outside Declared Carriers",
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
            ProjectRule::EnvironmentBranch => "Environment Branch In Business Code",
            ProjectRule::MatchFieldHyphen => "Hyphenated Field In Match Class Pattern",
            ProjectRule::WorldHandlerNamedOutsideList => "World Handler Named Outside The List",
            ProjectRule::WorldHandlerMisplaced => "World Handler Misplaced",
            ProjectRule::TestKindMismatch => "Test Kind Mismatch",
            ProjectRule::WorldHandlerWithoutContractTest => "World Handler Without Contract Test",
            ProjectRule::TestFormNotDeftest => "Test Form Not Deftest",
            ProjectRule::VocabularyOutsideSinglePoint => "Vocabulary Outside Single Point",
            ProjectRule::SpellingOutsideItsFiles => "Spelling Outside Its Files",
            ProjectRule::SpellingCountDiffers => "Spelling Count Differs",
            ProjectRule::EffectOutsideCensus => "Effect Outside Census",
            ProjectRule::FieldHoldersDiffer => "Field Holders Differ",
            ProjectRule::ServiceUntestedOnSim => "Service Untested On Sim",
            ProjectRule::ServiceInvariantsMissing => "Service Invariants Missing",
            ProjectRule::RetiredWord => "Retired Word",
            ProjectRule::RetiredCall => "Retired Call",
            ProjectRule::HandlerArgumentHoldsState => "Handler Argument Holds State",
            ProjectRule::UntypedStructuredValue => "Untyped Structured Value",
            ProjectRule::RecordStubNotKwOnly => "Record Stub Not Kw Only",
            ProjectRule::BusinessEffectFake => "Business Effect Fake",
            ProjectRule::AssemblyShapeBroken => "Assembly Shape Broken",
            ProjectRule::AssemblyAnswerMisplaced => "Assembly Answer Misplaced",
            ProjectRule::TestOnlyFake => "Test Only Fake",
            ProjectRule::IntentAnswererNotTranslation => "Intent Answerer Not Translation",
            ProjectRule::ServiceWithoutCounterexample => "Service Without Counterexample",
            ProjectRule::ClauseWithoutCounterexample => "Clause Without Counterexample",
            ProjectRule::IntentEffectUncovered => "Intent Effect Uncovered",
            ProjectRule::RegistryEntryStale => "Registry Entry Stale",
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
            ProjectRule::RawSideEffectVia => "呼ぶ定義を通して生の副作用に届く定義の知らせ(違反ではなく事実)— :world-handlers に宣言した定義の先は辿らない",
            ProjectRule::EnvironmentName => "業務の file・handler・組み立ての関数の名に環境の語を付けない",
            ProjectRule::ServiceBoundary => "ある service の判断と翻訳の層(設定の guarded_layers)は、別の service の同じ層を読まない。読んでよいのは別の service の open_layers と共有の置き場だけ",
            ProjectRule::DefnForbidden => "Hy の定義は defn / defn/a ではなく defk で書く(マクロの展開の時の関数と、設定で除いた置き場は除く)",
            ProjectRule::DeffNeedsReason => "deff の定義の行か直前の行に、defk にできない理由の註を書く",
            ProjectRule::DefinitionTagsRequired => "defk・deff・defp・defhandler・defeffect は契約の辞書の :tags で必須の鍵(設定)を名乗る",
            ProjectRule::ContextMatchesService => "タグの :context は、その file が置かれた service の名と合う(知らせ)",
            ProjectRule::UndeclaredPlace => "root の下の module は、architecture.hy で宣言した service の層・shared・foundation・legacy のどれかに置く",
            ProjectRule::UndeclaredDirectory => "root の下の dir は宣言した service か shared・foundation・legacy で、service の中の dir は宣言した層",
            ProjectRule::ServiceDependency => "service A が読んでよいのは、A の :depends-on に在る service の、A の module の層が読める層(その層の :dependency-layers — 組み立ての層は intent と protocol —、無ければ :open-layers の intent)と shared だけ",
            ProjectRule::BlindDefinitionReads => "architecture.hy の :blind-definitions の定義は決めた材料だけで判じる — その定義から呼び出しと名指しで推移的に届く repo の Hy の定義の本体(註を除く)に :forbid-words の綴りが無く、:no-imports なら定義の module が import と require を持たない(:allow-requires の module の require は macro の読み込みなので除く)。宣言した定義は実在する",
            ProjectRule::DefinitionCallsUnlistedHead => "architecture.hy の :allowed-heads の定義は、:heads に挙げた頭の form だけを持つ — 定義の form の中(入れ子を含む・文字列と註を除く)の `( … )` の頭の綴り(記号と keyword)が :heads の外なら、その頭ごとに当たる。宣言した定義は実在する",
            ProjectRule::CallOutsideDeclaredSites => "architecture.hy の :call-sites の頭は、:files の Hy の file(:except を除く)の中で :sites の定義の中でだけ呼ぶ — :count を書いた場所はその数ちょうど、:parent を書いた場所の呼びは直ぐ外の form の頭がその綴り、:branch を書いた場所の呼びは条件の form にその記号が在る cond・when・if・unless の枝の中。場所の定義は実在し、:files に当たる file は 1 つ以上",
            ProjectRule::BroadCatchOutsideCarrier => "architecture.hy の :broad-catches の :files の Hy・Python の file(:except を除く)に広い例外の捕捉(`(except [] …)`・`(except [… Exception …] …)`・BaseException・AssertionError・Python の `except:`・`except Exception:`)を置かない — 許すのは :carriers の定義の中で、捕捉が例外を名で束縛し、その捕捉の中でその名を :event の出来事の引数に渡す物だけ(契約の破れを握りつぶさず、列へ運ぶ境界)。運搬の境界の定義は実在し、:files に当たる file は 1 つ以上",
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
            ProjectRule::SpellingOutsideItsFiles => "architecture.hy の :confined-spellings の群の綴り(正規表現)は :except の file にだけ書く(:except が空なら :files のどこにも書かない)— :files に当たる Hy・Python の file の、註を落とした本文(文字列の中も数える)を読む。:files に当たる file が 1 つも無い群も当たる(母集団 0 を緑にしない)",
            ProjectRule::SpellingCountDiffers => "architecture.hy の :counted-spellings の宣言ごとに、:files に当たる file(Hy と Python は註を落とす・文字列は数える)の :pattern の当たりの数が :count(ちょうど)か :at-least(以上)に合う — :within があれば名指した top level の定義ごとに、その定義の中で数える。数える file や定義が無い宣言も当たる(母集団 0 を緑にしない)",
            ProjectRule::EffectOutsideCensus => "architecture.hy の :effect-census の宣言ごとに、:files の Hy・Python の file(註を除く)で :base を継ぐ class の宣言の集まりが :effects の一覧と一致する — 一覧に無い宣言・同じ名の 2 度の宣言・宣言の無い一覧の effect が当たる。:files に当たる file が無い宣言も当たる(母集団 0 を緑にしない)",
            ProjectRule::FieldHoldersDiffer => "architecture.hy の :field-holders の宣言ごとに、:files の Python の file の module の直下の class のうち、本体の直下の欄(注記つきの代入)の注記に :type の綴りが語として在る class が :holders の一覧ちょうど — 一覧に無い持ち手と、その欄を持たない(か無い)一覧の class が当たる。:classes を書けば名指した class だけを数え、無い class も当たる。:files に当たる Python の file が無い宣言も当たる(母集団 0 を緑にしない)",
            ProjectRule::RetiredWord => "architecture.hy の :retired-words で使わないと決めた綴りを、群の :files の file に書かない — :words は語として単独で在る所(前後が英字・_・- でない)、:patterns は行ごとの正規表現、:in names は定義の名だけ・:in paths は file の名だけを見る。:rule-lines の綴りを含む行(規則そのものを述べる行)と、:in lines の註・docstring・.md の file(使っていない綴り)は数えない。:contract-files の契約の file のキーの名・enum / const の値に在る語は、Hy・Python の文字列で中身がその語ちょうどの物と defwire の欄の定義の名では数えない(契約と wire の欄名は契約の綴りのまま)",
            ProjectRule::RetiredCall => "architecture.hy の :retired-calls で使わないと決めた呼び(退役した effect・時計 …)を、群の :files の Hy の file で呼ばない — 頭の記号が :calls の綴りの form を数え、註・文字列・読み捨てた form は数えない",
            ProjectRule::UntypedStructuredValue => "構造を持つ値は欄の名前と型を静的に持つ型(frozen の dataclass・defrecord・defwire)で表す — architecture.hy の :typed-values の file の公開面(名が _ で始まらない物)の、class の欄・関数と method の戻り値・defk / deff の :post の型に、素の写像(dict・Mapping・JsonValue …)・素の組(tuple)・値が object / Any の写像・長さの決まった組 tuple[A, B]・それらを中身に持つ入れ物を書かず、defn / defk / deff は長さ 2 以上の組の literal #(a b) を答えにしない。:post は isinstance の契約なので写像だけを赤にし、名に型の注記の無い defk / deff の :post は素の組も赤にする",
            ProjectRule::RecordStubNotKwOnly => "architecture.hy の :record-stubs の型の宣言(.pyi)は、同じ dir の同じ名の .hy の実行時の形を偽らない — .hy で欄を名でしか受けない record(defrecord か、飾りに (dataclass … :kw-only True …) を持つ defclass)を @dataclass で宣言するなら kw_only=True を書く",
            ProjectRule::ServiceUntestedOnSim => "業務の service は、本番の組み立て(entry の層)のまま模擬の環境に載せ、handler の差し替えだけで回して確かめる — 模擬の環境(:verification-environment)の下の deftest がその service の entry の層の定義に 1 本も届かなければ、未検証の service として赤にする",
            ProjectRule::ServiceInvariantsMissing => "code を持つ業務の service(entry の層に定義が 1 本以上)は、architecture.hy の defservice に :invariants(業務の不変条件の関数 `module:関数` の列)を宣言する — 名指した関数は実在し、:tags の :role が judgment(記録を受けて破りの列を返す純粋な判断)。宣言の無い service・実在しない関数・判断でない関数は赤",
            ProjectRule::HandlerArgumentHoldsState => "handler は接続の object や書き換える店を引数で受け取らない — 接続先と資格・設定は Ask で読み、client は本文の先頭の (session val client …) で 1 回だけ作り、状態は (session var …) で持つ(外側の handler が差し替え・観測できる)。引数に残す物は本文に architecture.hy の :handler-arguments の :keep-mark の註で理由を書く",
            ProjectRule::BusinessEffectFake => "業務の効果に答える偽物を作らない — 偽物は外の世界に触れる効果だけ(operator 2026-09-26 \"we only need fake for effects that access external world\")。業務の操作は下の層の効果を出す defk で書き、検査は外の世界の handler だけを差し替える。模擬の根と本番の入口と業務の module は architecture.hy の :business-fakes で宣言する",
            ProjectRule::AssemblyShapeBroken => "組み立ては 1 点 — 組み立ての層の関数(引数 = 土台の値)が、土台の handler の列 1 つと、各 service の翻訳の層の翻訳の列の定数を並べるだけ。本番と模擬の違いは渡す土台の値だけで、組の file は残さない。翻訳の列は自分の service の翻訳だけを持ち、別の service の効果を出し直す列の外側にはそれに答える列を並べる。名前は architecture.hy の :assembly-shape で宣言する",
            ProjectRule::AssemblyAnswerMisplaced => "業務の効果の答えは翻訳の列の handler に置く — 翻訳の handler が出し直すのは業務を知らない汎用の効果か同じ列の効果(答える handler は外側)か他の service の公開の効果だけで、土台の handler は外の世界の効果にだけ答える",
            ProjectRule::TestOnlyFake => "検だけの偽物を作らない — 業務の handler は本番の 1 つだけで、検は土台(記録の効果・時計・外の相手)の handler の差し替えで組む。わざと壊した反例の handler は反例の表(:counterexamples)に理由つきで載せる",
            ProjectRule::IntentAnswererNotTranslation => "intent の効果に答えるのは翻訳の層の handler 1 つだけ(本番と模擬で同じ)— 環境ごとの別の答え手や土台の handler で答えず、模擬は土台を差し替える(ADR R9)",
            ProjectRule::ServiceWithoutCounterexample => "業務の service ごとに、わざと壊した handler の反例を 1 本以上持つ — 反例の表(:counterexamples)の節に届く deftest のうち、その service の entry の層の定義に(DOEFF136 と同じ図を逆向きに)届く物が 1 本も無く、節の効果の定義元がその service か土台でなければ、反例の無い service として赤にする",
            ProjectRule::ClauseWithoutCounterexample => "業務の不変条件は条ごとに、わざと壊した handler の反例を 1 本以上持つか、持たない理由を宣言する — defservice の :clauses の条ごとに、反例の表(:counterexamples)の行のうち `breaks: <service>::<条>` でその条を名乗り、その節に届く deftest が service の entry の層の定義にも届く物が 1 本も無く、:clause-exemptions の理由も無ければ、網羅の欠けとして赤にする(service に 1 本あれば緑の DOEFF164 を条へ細かくした物・agora-redesign #1713)",
            ProjectRule::RegistryEntryStale => "既知の破れの登録簿は縮める向きだけ — 載った鍵は今も当たる所見を指す。全体の実行で、鍵の区切り(law の名か規則の ID)が指す規則を判じたのに、どの所見にも当たらない鍵は消し忘れの古い行(agora-redesign #1706)",
            ProjectRule::IntentEffectUncovered => "intent の層の効果は、手元の検から届く定義が出し、模擬の根と本番の入口の両方から届く答え手を持つ — 3 つのどれかが無い効果は、テストしたと言えない業務の操作(agora-redesign #1561・#1155)",
            ProjectRule::TestKindMismatch => "テストの種類は 2 つだけ — 手元(届く定義に外の世界に触れる handler が無い)/ 縁(名簿の定義・:wraps の handler・生の I/O に届く)。種類は人が決めず届く先から導き、縁のテストだけが architecture.hy の :edge-mark の印を持つ(operator 2026-09-29 \"everything is 'pure' until we apply handler that has real IO\")",
            ProjectRule::WorldHandlerWithoutContractTest => "architecture.hy の :world-handlers の handler には縁の検が 1 本以上在る — 空でない :interpreters を持つ deftest のうち、定義の辺(呼び出し・参照・入れ子 — DOEFF133 と同じ図)を辿ってその handler の定義に届く物。理由つきの :contract-test (none …) の handler は判じない・理由の無い :contract-test none は鳴る",
            ProjectRule::WorldHandlerMisplaced => "architecture.hy の :world-handlers に挙げた定義は実在し、層 foundation の module に在る(外の世界に触れてよい定義の置き場は foundation だけ)",
            ProjectRule::WorldHandlerNamedOutsideList => "architecture.hy の :world-handlers の :wraps に挙げた doeff の実 I/O の handler(os-file-handler・http-production-handler …)を名指してよいのは、許可名簿の定義(とその中の入れ子の定義)だけ — 値として渡す所(with-handlers の列)も呼び出しも数える",
            ProjectRule::TranslationEmitsIntent => "翻訳の層(設定の handler_layers)の handler — defhandler と [effect k] を受ける関数 — は doeff の汎用の effect だけを出し、業務の intent(設定の intent_layers の型)を出さない — 本体で実行する呼び((<- …)・(! …))を import した defk の先まで辿る",
            ProjectRule::EnvironmentBranch => "業務の層(architecture.hy の :environment-branches の :layers)の Hy の定義は、環境の名の値(:values — production・emulated …)と比べず(=・!=・is・is-not・in・not-in の引数の文字列の literal と、match の節の型)、dry-run の印(:flags)で分岐しない(if・when・unless・cond の条件と match の主語の中の記号)— 環境の違いは handler の組の差し替えだけで表す(業務の層は環境を知らない)",
            ProjectRule::MatchFieldHyphen => "match の節の型の class pattern(`(Class :欄 型)`)の keyword の欄の名は、Python の属性名の綴り(`-` でなく `_`)で書く — Hy は class pattern の keyword を mangle しないので `:a-b` は `case Class(a-b=…)` になり、どの値にも当たらない",
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
            ProjectRule::BlindDefinitionReads => "判断に要る材料は定義の引数で受け、語の読みは呼び手の側(判断の外)に置く — 届いた先の helper へ逃がしても推移閉包で当たる。module の import は外し、値は引数で渡す",
            ProjectRule::DefinitionCallsUnlistedHead => "一覧の外の呼びは定義の外(呼び手の側・例外を受け止める境界の中)へ移す — 移せない呼びが本当に例外を上げないなら、理由を :why に書いて :heads に足す",
            ProjectRule::CallOutsideDeclaredSites => "呼びを宣言した場所へ戻す(2 つ目の閉じ込めや断りの座を生やさない)— 場所を広げるのが意図した境界の変更なら、同じ変更で architecture.hy の :sites と :count を直し、理由を :why に書く",
            ProjectRule::BroadCatchOutsideCarrier => "捕捉を狭くする(ValueError など、起きると分かっている例外だけ)か、捕捉を消して上げる — 例外を列へ運ぶ境界なら、捕まえた例外を名で束縛して :event の出来事に渡し、その定義を同じ変更で :carriers に足して理由を :why に書く",
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
            ProjectRule::SpellingOutsideItsFiles => "綴りを :except の file へ移し、外の file はその file の口を通す — 書いてよい file を増やすなら、理由を :why に書いて :except に足す",
            ProjectRule::SpellingCountDiffers => "数を :count(:at-least)に合わせる — 足した当たりは決めた定義や file の口へ移し、消えた当たりは戻す。数を変えるなら、理由を :why に書いて宣言の数を同じ変更で直す",
            ProjectRule::EffectOutsideCensus => "effect を足すなら同じ変更で :effects の一覧にも足す(消すなら一覧からも消す)— 同じ名の 2 つ目の宣言は 1 つにまとめる",
            ProjectRule::FieldHoldersDiffer => "その型の欄は一覧の class にだけ置く(ほかの class は一覧の class を通して持つ)— 持ち手を変えるなら、理由を :why に書いて :holders を同じ変更で直す",
            ProjectRule::RetiredWord => "群の :instead の語に書き換える。契約の綴り(wire の欄名・契約の値)なら、群の :contract-files の契約の file に在るかを確かめる(在れば文字列と defwire の欄の定義は数えない)",
            ProjectRule::RetiredCall => "群の :instead の物に置き換える",
            ProjectRule::HandlerArgumentHoldsState => "引数を消し、接続先と設定は Ask、client は (session val …)、状態は (session var …) へ移す — 同じ handler を別の設定で並べるなど引数に残す物は本文に理由の註を書く",
            ProjectRule::UntypedStructuredValue => "欄の名前と型を持つ frozen の record(defrecord・defwire・dataclass)を定義して返す・持つ — :post は [(: % <record の型>)] で検める。キーで引く索引は dict[str, Row]・同じ型の列は tuple[X, ...] と中身の型を書く。値の形を外の系が決める境界なら登録簿に理由と共に載せる",
            ProjectRule::RecordStubNotKwOnly => "型の宣言の飾りを @dataclass(frozen=True, kw_only=True) にする",
            ProjectRule::BusinessEffectFake => "業務の操作を下の層の効果を出す defk で書き直して偽物を消す — 外の世界の効果なら外の世界の表に理由つきで足し、わざと壊した反例なら反例の表に載せる",
            ProjectRule::AssemblyShapeBroken => "組の file の組み立てを組み立ての層の関数へ移して file を消す・関数を defk で書き、本体を [土台の列 翻訳の列 …] だけにする・別の service の翻訳は組み立ての層で別の列として並べる",
            ProjectRule::AssemblyAnswerMisplaced => "業務の効果に答える所を翻訳の列の handler へ移すか、答えを汎用の効果へ出し直す・出し直した効果に答える handler を列の前(外側)へ並べる",
            ProjectRule::TestOnlyFake => "検の組み立てを本番の handler + 土台の差し替えに書き直して偽物を消す — わざと壊した反例なら反例の表に載せる",
            ProjectRule::IntentAnswererNotTranslation => "intent の効果の答えを翻訳の層の handler 1 つへまとめ、土台や環境ごとの handler からは外す",
            ProjectRule::ServiceWithoutCounterexample => "その service の effect(か土台の effect)に答える handler をわざと壊した反例の deftest を模擬の環境に書き、handler を反例の表に理由つきで載せる — 今すぐ書けない service は登録簿に理由と担い手つきで載せる",
            ProjectRule::ClauseWithoutCounterexample => "その条を破る壊した handler の反例の deftest を模擬の環境に書き、反例の表の行に `breaks: <service>::<条>` を足す — 壊した handler では破れない条(構造の保証)は defservice の :clause-exemptions に理由を書く・今すぐ書けない条は登録簿に理由と担い手つきで載せる",
            ProjectRule::RegistryEntryStale => "当たらなくなった登録簿の行(1 鍵 1 file の dir ならその file・1 行 1 鍵の file ならその行)を消す",
            ProjectRule::IntentEffectUncovered => "欠けた列を埋める — 検から出さないなら模擬の環境の deftest でその業務の操作を通す・答え手が無いなら翻訳の層の handler を組み立てに載せる(使わない効果なら宣言を消す)",
            ProjectRule::ServiceUntestedOnSim => "模擬の環境の tests に、その service の entry の組み立てを handler の差し替えだけで回す deftest を足す",
            ProjectRule::ServiceInvariantsMissing => "defservice に :invariants [\"<module>:<関数>\" …] を足し、関数は :role \"judgment\" の defk で置く(模擬の環境の <service>_invariants.hy など)。既知の欠けは登録簿に理由と持ち主を載せる",
            ProjectRule::TestKindMismatch => "縁なら印を付け(既定の pytest から外れる)、手元のつもりなら届く先の実 I/O の handler を模擬の handler に替える — 手元なのに印が在れば外す",
            ProjectRule::WorldHandlerWithoutContractTest => "本物(その handler)と模擬の解釈器を :interpreters に並べた deftest を書き、同じ検を両方に通す — 縁の検を持たない理由が在る handler だけ、名簿の行に理由のテストの名つきで none を書く: doeff の handler を 1 行で包むだけで契約テストが doeff の側に在るなら :contract-test (none :doeff-test \"packages/<pkg>/tests/<file>.hy::<テストの名>\")(doeff の repo の根からの path)、この repo の別のテストが契約を確かめるなら :contract-test (none :repo-test \"<file>.hy::<テストの名>\")(この repo の根からの path)。理由の無い :contract-test none は鳴る",
            ProjectRule::WorldHandlerMisplaced => "定義を foundation の層(architecture.hy の :foundation の dir)へ移すか、名簿の綴り(module:名)を実物に合わせる — 要らなくなった定義なら名簿から外す",
            ProjectRule::WorldHandlerNamedOutsideList => "名簿の定義(例 with-agora-process)の下で本体を走らせ、自分では実 I/O の handler を被せない — 新しく外の世界に触れる所が要るなら、その定義を foundation の層に置いて名簿に載せる",
            ProjectRule::TranslationEmitsIntent => "intent を出す業務の流れは層 core の program に置き、翻訳の handler は受けた intent を doeff の汎用の effect(HttpRequest・記録の読み書き・時計 …)へ出し直すだけにする — 経由した defk が intent を出すなら、その defk を呼ばずに汎用の effect を直に使う",
            ProjectRule::EnvironmentBranch => "環境で変わる振る舞いは effect にして、環境ごとの handler(本番・模擬・dry-run)に答えさせる — 業務の層の定義は環境の名も dry-run の印も読まない",
            ProjectRule::MatchFieldHyphen => "欄の名の `-` を `_` に書き換える(`:ended-reason` → `:ended_reason`)— 直すと今まで当たらなかった節が当たるようになるので、その定義の検を撃って振る舞いの変化を確かめる",
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
        ("DOEFF168", RuleFamily::Layer),
        ("DOEFF169", RuleFamily::Layer),
        ("DOEFF131", RuleFamily::Raw),
        ("DOEFF132", RuleFamily::Raw),
        ("DOEFF133", RuleFamily::Raw),
        ("DOEFF137", RuleFamily::Raw),
        ("DOEFF135", RuleFamily::Definition),
        ("DOEFF136", RuleFamily::Definition),
        ("DOEFF163", RuleFamily::Definition),
        ("DOEFF140", RuleFamily::Place),
        ("DOEFF141", RuleFamily::Place),
        ("DOEFF147", RuleFamily::Definition),
        ("DOEFF159", RuleFamily::Definition),
        ("DOEFF160", RuleFamily::Definition),
        ("DOEFF144", RuleFamily::Definition),
        ("DOEFF145", RuleFamily::Definition),
        ("DOEFF150", RuleFamily::Naming),
        ("DOEFF151", RuleFamily::Naming),
        ("DOEFF142", RuleFamily::Definition),
        ("DOEFF143", RuleFamily::Definition),
        ("DOEFF155", RuleFamily::Definition),
        ("DOEFF156", RuleFamily::Definition),
        ("DOEFF157", RuleFamily::Definition),
        ("DOEFF164", RuleFamily::Definition),
        ("DOEFF167", RuleFamily::Definition),
        ("DOEFF158", RuleFamily::Definition),
        ("DOEFF165", RuleFamily::Definition),
        ("DOEFF166", RuleFamily::Definition),
        ("DOEFF146", RuleFamily::Naming),
        ("DOEFF148", RuleFamily::Naming),
        ("DOEFF161", RuleFamily::Naming),
        ("DOEFF162", RuleFamily::Naming),
        ("DOEFF149", RuleFamily::Naming),
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
    fn jev_rules_are_not_critical_until_calibrated() {
        // 決定 B(#1762 / #1801): Jev の 201・202・205 は major。反例 = 責務の境界の規則(DOEFF101・130)は今までどおり critical。
        use super::super::settings::RuleLevel;
        for id in ["DOEFF201", "DOEFF202", "DOEFF205"] {
            let rule = ProjectRule::parse(id).unwrap_or_else(|| panic!("{} は ProjectRule に無い", id));
            assert_eq!(rule.default_level(), Some(RuleLevel::Major), "{} の既定の重大さ", id);
        }
        for id in ["DOEFF101", "DOEFF130"] {
            let rule = ProjectRule::parse(id).unwrap_or_else(|| panic!("{} は ProjectRule に無い", id));
            assert_eq!(rule.default_level(), Some(RuleLevel::Critical), "{} の既定の重大さ", id);
        }
        for id in ["DOEFF203", "DOEFF204"] {
            let rule = ProjectRule::parse(id).unwrap_or_else(|| panic!("{} は ProjectRule に無い", id));
            assert_eq!(rule.default_level(), None, "{} は宣言の無い規則のまま", id);
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
