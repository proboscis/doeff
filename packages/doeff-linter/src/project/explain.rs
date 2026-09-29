//! 違反の説明 — 「これは何か(subject)」と「なぜ違反か(reason)」の文を作る。
//!
//! 文の雛形は規則ごとにここの 1 か所だけに書き、層の名前・層の説明・role の説明・law の文は設定から差し込む(Rust に
//! 層の意味を書かない)。層の説明が設定に無い層は、名前と置き場所と規則の決まりだけで文を作る。
//! operator 2026-09-27 逐語: "yeah so the linter should tell what they are and why they violate"

use serde::Serialize;

use super::settings::{LawSpec, LayerId, LayerSettings, RawSettingsSpec};

/// file がどの層に在るかと、その根拠(path の置き場所と、タグで名乗った役)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Placement {
    pub layer: LayerId,
    /// 当たった置き場の実際の dir(例 controllers/land_notice/core)。
    pub dir: String,
    /// 置き場の `*` の段に当たった service の名(層が先の形なら None)。
    pub service: Option<String>,
    /// 実効のタグの role(重なりを除き、出てきた順)。
    pub roles: Vec<String>,
    /// 層を path ではなくタグの role から推したか(宣言した置き場所の外の module)。
    pub by_tags: bool,
}

/// 語の後に助詞を付ける(英数字で終わる語は空白を挟む — 「core が」「層 core(業務)は」)。助詞が空なら語だけ。
fn particle(word: &str, particle: &str) -> String {
    if particle.is_empty() {
        return word.to_string();
    }
    match word.chars().last() {
        Some(c) if c.is_ascii_alphanumeric() || c == '_' || c == '-' => format!("{} {}", word, particle),
        _ => format!("{}{}", word, particle),
    }
}

/// DOEFF120 の主体の後半(「 3 か所で使う(最初 2 行目・ほかに 5・9 行目)」)。ほかの行は 20 まで並べ、残りは数だけ書く。
fn json_value_where(uses: &JsonValueUses) -> String {
    const SHOWN: usize = 20;
    let others = match uses.other_lines.len() {
        0 if uses.count <= 1 => return format!(" 1 か所で使う({} 行目)", uses.first_line),
        0 => return format!(" {} か所で使う(すべて {} 行目)", uses.count, uses.first_line),
        n if n > SHOWN => format!(
            "{}・…(ほか {} 行)",
            uses.other_lines[..SHOWN].iter().map(usize::to_string).collect::<Vec<_>>().join("・"),
            n - SHOWN
        ),
        _ => uses.other_lines.iter().map(usize::to_string).collect::<Vec<_>>().join("・"),
    };
    format!(" {} か所で使う(最初 {} 行目・ほかに {} 行目)", uses.count, uses.first_line, others)
}

/// 助詞の直後に英数字で始まる語が来る時は空白を挟む(「この file は service billing の…」)。
fn lead(text: &str) -> String {
    match text.chars().next() {
        Some(c) if c.is_ascii_alphanumeric() => format!(" {}", text),
        _ => text.to_string(),
    }
}

/// 宣言されていない置き場所の file の種類(DOEFF114)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PlaceProblem {
    /// root の直下の file。
    DirectlyUnderRoot,
    /// service(か shared)の dir の直下の file — 層の dir の中に無い。
    DirectlyUnderService { service: String },
    /// 宣言に無い dir(層が先の dir・旧い機能の dir・service の中の宣言に無い層)の中の file。
    InUndeclaredDirectory { dir: String },
}

/// 宣言に無い dir の種類(DOEFF115)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DirectoryProblem {
    /// root の直下の dir が、宣言した service でも shared・foundation・legacy でもない。
    ServiceNotDeclared { dir: String, services: Vec<String> },
    /// service の中の dir が、その service の宣言した層でない。
    LayerNotDeclared { service: String, layer: String, declared: Vec<String> },
}

/// DOEFF119 が見た class の形(説明の文のため)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClassShapeFacts {
    /// 書かれた基底の綴り(どれも repo の中の class か、無い)。
    pub bases: Vec<String>,
    /// dataclass の decorator が付いているか。
    pub dataclass: bool,
}

/// DOEFF119 の判定(閉じた集合 — 許す class は違反を作らないのでここに無い)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ClassVerdict {
    /// 外の世界に触る(method か欄の初期値に生の副作用の強い証拠がある)— error。証拠は `定義: 名` の綴り。
    ExternalWorld { evidence: Vec<String> },
    /// 変わる状態を持つ(method が self の欄を書き換える)— warning。`method: 欄` の綴り。
    Stateful { mutations: Vec<String> },
    /// 欄だけ(dunder 以外に処理を持つ method が無い)— defrecord を勧める info。
    DataOnly,
}

/// DOEFF120 で module に JsonValue を許さなかった訳(閉じた集合 — 許した module は違反を作らないのでここに無い)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JsonValueRefusal {
    /// architecture.hy の :wire-modules のどれにも当たらない(組み込みの解き手でもない)。
    NotListed,
    /// :wire-modules の pattern に当たるが、foundation の層に無い(送受信そのものは foundation だけが行う)。
    ListedOutsideFoundation { pattern: String, foundation_dir: Option<String> },
}

/// DOEFF120 が数えた JsonValue の使用(module ごと)。行は 1 始まり。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JsonValueUses {
    /// 出てきた名(JsonValue・JsonObject …・重なりを除き出てきた順)。
    pub names: Vec<String>,
    /// 使った数(import・定義・注釈・文字列の型の中の語を全部)。
    pub count: usize,
    /// 最初の使用の行。
    pub first_line: usize,
    /// ほかの使用の行(重なりを除く・最初の行は含めない)。
    pub other_lines: Vec<usize>,
}

/// deff の理由の註の問題(DOEFF111)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeffReasonProblem {
    /// 註が無い。
    Missing,
    /// 理由が空か「同上」とその変形(その定義に固有の理由になっていない)。
    NoDetail { detail: String },
}

impl DeffReasonProblem {
    /// 短い 1 行(message のため)。
    pub fn short(&self) -> String {
        match self {
            DeffReasonProblem::Missing => "理由の註が無い".to_string(),
            DeffReasonProblem::NoDetail { detail } if detail.is_empty() => "理由が空".to_string(),
            DeffReasonProblem::NoDetail { detail } => format!("理由が「{}」(この定義に固有の理由でない)", detail),
        }
    }
}

/// 違反の主体が定義の名だけの時の種類(業務の file の名か、定義の名か)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NameSubject {
    File { stem: String },
    Definition { name: String, kind: &'static str },
}

/// 規則ごとの説明の材料(規則の判定が見た事実だけ)。
#[derive(Debug, Clone, PartialEq)]
pub enum Explain {
    /// DOEFF101: 許されない層の module を import した。
    ImportDirection { placement: Placement, target: String, target_layer: LayerId, target_dir: String },
    /// DOEFF102: 層で禁じた module を import した。
    ForbiddenModule { placement: Placement, module: String },
    /// DOEFF103: 型だけの層に関数を置いた。
    TypesOnly { placement: Placement, functions: Vec<String> },
    /// DOEFF104: タグの無い定義がある。
    UntaggedDefinitions { placement: Placement, names: Vec<String> },
    /// DOEFF104: module が何も名乗っていない。
    NoTags { placement: Placement },
    /// DOEFF105: タグの role が層に合わない(または role / context が無い)。
    RoleMismatch { placement: Placement, role: Option<String>, context: Option<String> },
    /// DOEFF106: 定義が生の副作用に直に触る。
    RawDirect { placement: Placement, definition: String, kind: &'static str, evidence: String, category: &'static str, weak: bool },
    /// DOEFF131: 許可名簿の外の定義が、:wraps に挙げた doeff の実 I/O の handler を名指す。
    WorldHandlerNamed { placement: Placement, definition: String, wrapped: String, listed_by: String },
    /// DOEFF132: 許可名簿の定義が実在しない・foundation の外に在る。
    WorldHandlerMisplaced { definition: String, problem: String },
    /// DOEFF133: テストの種類と印が食い違う(edge = 縁なのに印が無い / 手元なのに印が在る)。
    TestKindMismatch { test: String, edge: bool, mark: String, reached: Vec<String> },
    /// DOEFF135: deftest 以外のテストの形。
    TestFormNotDeftest { form: &'static str, detail: String },
    /// DOEFF146: 判定を1か所に閉じ込めた語彙が :except の外に在る。
    VocabularyOutsideSinglePoint { group: String, count: usize, instead: String },
    /// DOEFF148: 書いてよい file を決めた綴り(群の名・理由・当たりの種類)。
    ConfinedSpelling { group: String, why: String, problem: super::confined_spellings::ConfinedProblem },
    /// DOEFF161: 数を決めた綴り(宣言の名・理由・当たりの種類)。
    CountedSpelling { group: String, why: String, problem: super::counted_spellings::CountProblem },
    /// DOEFF162: effect の宣言の全体(一覧の名・理由・当たりの種類)。
    EffectCensus { group: String, why: String, problem: super::effect_census::CensusProblem },
    /// DOEFF149: 型の欄を持つ class の顔ぶれ(一覧の名・型の綴り・理由・当たりの種類)。
    FieldHolders { group: String, type_name: String, why: String, problem: super::field_holders::HolderProblem },
    /// DOEFF150: 使わないと決めた綴り(群の名・当たった綴り・代わりの語・:in names なら定義の名)。
    RetiredWord { group: String, spelling: String, instead: String, name: Option<String>, place: super::architecture::WordPlace },
    /// DOEFF151: 使わないと決めた呼び。
    RetiredCall { group: String, call: String, instead: String },
    /// DOEFF141: 決めた材料だけで判じる定義(宣言の綴り・理由・当たりの種類)。
    BlindDefinition { declared: String, why: String, problem: super::blind::BlindProblem },
    /// DOEFF147: 呼んでよい頭を決めた定義(宣言の綴り・理由・当たりの種類)。
    AllowedHeads { declared: String, why: String, problem: super::allowed_heads::HeadProblem },
    /// DOEFF159: 頭を呼んでよい場所と回数(頭・理由・当たりの種類)。
    CallSites { head: String, why: String, problem: super::call_sites::CallSiteProblem },
    /// DOEFF160: 広い例外の捕捉を置かない群(群の名・理由・当たりの種類)。
    BroadCatches { group: String, why: String, problem: super::broad_catches::BroadCatchProblem },
    /// DOEFF140: 置き場の外の module への依存(この file の置き場・読む先の module・その file の root からの path)。
    PlacedDependency { placement: Placement, owner: String, owner_rel: String },
    /// DOEFF144: 公開面の型の注記の素の写像・素の組(どこの注記か・名・赤の理由)。
    UntypedStructuredValue { what: String, name: String, problem: String },
    /// DOEFF145: .pyi の @dataclass に kw_only=True が無い(class の名・.hy の側の書き方)。
    RecordStubNotKwOnly { class: String, form: String },
    ServiceUntestedOnSim { service: String, entry: String, definitions: usize, sim: String },
    /// DOEFF142: defhandler の引数が client・可変の店を取る。
    HandlerArgumentHoldsState { handler: String, param: String, kind: &'static str, type_text: String },
    /// DOEFF143: 業務の効果の偽物・表の腐り。
    BusinessEffectFake { subject: String, reason: String },
    /// DOEFF106・131 を層の置き場の外の file に当てた当たり(層の説明の主体が無い — message をそのまま主体にする)。
    WorldOutsideLayers { subject: String },
    /// DOEFF107: 定義が呼ぶ定義を通して生の副作用に届く。
    RawVia { placement: Placement, definition: String, through: Vec<String>, evidence: String, category: &'static str },
    /// DOEFF108: 業務の名に環境の語がある。
    EnvironmentName { subject: NameSubject, words: Vec<String> },
    /// DOEFF109: 別の service の守る層を import した。
    ServiceBoundary {
        placement: Placement,
        target: String,
        target_service: String,
        target_layer: LayerId,
        target_dir: String,
        open_layers: Vec<LayerId>,
        shared: Vec<String>,
    },
    /// DOEFF110: defn / defn/a の定義。
    DefnForbidden { name: String, head: String, declared_kind: Option<super::architecture::ReasonKind> },
    /// DOEFF111: 理由の註の無い・形の違う deff。
    DeffWithoutReason { name: String, marker: String, problem: DeffReasonProblem, kinds: Vec<super::architecture::ReasonKind> },
    /// DOEFF112: 定義の :tags に必須の鍵が無い。
    DefinitionTagsMissing { name: String, head: String, missing: Vec<String>, has_tags: bool, module_default: bool },
    /// DOEFF114: 宣言されていない置き場所の file。
    UndeclaredPlace { rel: String, problem: PlaceProblem, root: String, destination: String },
    /// DOEFF115: 宣言に無い dir。
    UndeclaredDirectory { dir: String, problem: DirectoryProblem },
    /// DOEFF116: service の依存の宣言に反する import。
    ServiceDependency {
        placement: Placement,
        own: String,
        target: String,
        target_service: String,
        target_layer: LayerId,
        target_dir: String,
        declared: bool,
        depends_on: Vec<String>,
        /// この file の層が依存先で読んでよい層(層の :dependency-layers か :open-layers)。
        open_layers: Vec<String>,
        /// この file の層が :dependency-layers で :open-layers より広く読める層か(組み立ての entry)。
        widened: bool,
    },
    /// DOEFF117: 宣言したのに使っていない依存。
    UnusedDependency { service: String, dependency: String },
    /// DOEFF201・202: Jev の判定(意味の規則)。
    Semantic {
        placement: Placement,
        definition: String,
        kind: &'static str,
        question: super::semantic::SemanticQuestion,
        probability: f64,
    },
    /// DOEFF203: 名乗った素の関数の理由の種類が、Jev の判定で当たらない見込み。
    /// DOEFF118: 検の置き場の、deftest でない検の関数。
    TestNotDeftest { name: String, head: String },
    /// DOEFF119: 業務の code の defclass(外の世界に触る / 変わる状態を持つ / 欄だけ)。
    ClassShape { name: String, shape: ClassShapeFacts, verdict: ClassVerdict },
    /// DOEFF126: defk の定義を素で呼んでいる。
    BareDefkCall { call: super::bare_calls::BareCall },
    /// DOEFF126: 引数で受けた関数を素で呼んでいる(呼び手が defk か fnk を渡している)。
    ParamCalledBare { call: super::param_calls::ParamCall },
    /// DOEFF127: defk の `:effects` の宣言が推論と合わない。
    EffectMismatch { mismatch: super::signatures::EffectMismatch },
    /// DOEFF130: 翻訳の層の handler が業務の intent を出す(層の名は設定から)。
    TranslationIntent { intent: super::signatures::TranslationIntent, handler_layer: String, intent_layer: String },
    /// DOEFF121〜125: 臭いの規則(形の照らし)。
    Smell { smell: super::smells::Smell },
    /// DOEFF205: Jev が、判断の定義に形の検めと業務の判断が混ざっていると見た。
    MixedConcerns { name: String, kind: &'static str, probability: f64, layer: String },
    /// DOEFF204: Jev が、処理を持つ method のある class を外の世界の窓口か状態を持つ物と見た。
    ClassRoleDoubt { name: String, chosen: String, probability: f64, fields: Vec<String> },
    /// DOEFF120: 許されない module が JsonValue を使う。
    JsonValueUse { module: String, uses: JsonValueUses, refusal: JsonValueRefusal },
    PlainCallableDoubt {
        definition: String,
        kind: &'static str,
        /// 書かれた理由の文。
        stated: String,
        /// Jev が選んだ答え(受け入れる理由・受け入れない型・none)と確率。
        chosen: String,
        chosen_probability: f64,
        /// 選んだ答えが受け入れる理由か。
        chosen_accepted: bool,
        /// 選んだ答えの説明(none は None)。
        chosen_description: Option<String>,
        /// 受け入れない型の直し方(architecture.hy の :fix)。
        fix: Option<String>,
        /// 受け入れない答えの確率の和。
        rejected_total: f64,
    },
    /// DOEFF113: タグの :context が置き場の service と食い違う。
    ContextMismatch { placement: Placement, contexts: Vec<String> },
}

/// 違反 1 件の説明(出力の explanation の欄)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Explanation {
    pub subject: String,
    pub reason: String,
    /// 結びつけた ADR の law の :statement の逐語(無ければ null)。
    pub law_statement: Option<String>,
}

/// 文を作るための道具(層の設定と、生の副作用を許す層)。
pub struct Narrator<'a> {
    pub layers: Option<&'a LayerSettings>,
    pub raw: Option<&'a RawSettingsSpec>,
}

impl<'a> Narrator<'a> {
    /// 説明を作る(law が結びついていれば、その :statement を逐語で添える)。
    pub fn explain(&self, explain: &Explain, law: Option<&LawSpec>) -> Explanation {
        let (subject, reason) = match explain {
            Explain::ImportDirection { placement, target, target_layer, target_dir } => (
                format!("import 先 {} は{}(path が {}/ の下) — {}", target, self.layer_phrase(*target_layer), target_dir, self.file_subject(placement)),
                format!(
                    "{}{} import してよいのは {} だけ。{}{}読むと、{}{}に触れる(模擬で handler を差し替えても、その所だけ本物に触る)。",
                    self.character(placement.layer),
                    particle(&self.name(placement.layer), "が"),
                    self.allowed_imports(placement.layer),
                    self.target_character(*target_layer),
                    particle(&self.name(placement.layer), "から"),
                    particle(&self.name(placement.layer), "が"),
                    particle(&format!("層 {} の持つ物", self.name(*target_layer)), "")
                ),
            ),
            Explain::ForbiddenModule { placement, module } => (
                format!("import 先 {}(層 {} で禁じた I/O の module) — {}", module, self.name(placement.layer), self.file_subject(placement)),
                format!(
                    "{}{} は外の世界に直に触る module なので、この層では使わない。I/O は{} handler が持ち、この層は effect を出す。",
                    self.character(placement.layer),
                    module,
                    particle(&self.io_layers(), "の")
                ),
            ),
            Explain::TypesOnly { placement, functions } => (
                format!("定義 {}(関数 / handler) — {}", functions.join("・"), self.file_subject(placement)),
                format!(
                    "{}型の宣言だけを置く層なので、処理の中身を持つ関数と handler は置けない(別の層へ移す)。",
                    self.character(placement.layer)
                ),
            ),
            Explain::UntaggedDefinitions { placement, names } => (
                format!("定義 {}(タグ無し) — {}", names.join("・"), self.file_subject(placement)),
                format!(
                    "層の dir は外の世界からの遠さだけを決め、文脈(context)と役(role)は定義のタグで名乗る。タグが無いと、この定義が層 {} で許される役({})かを確かめられない。",
                    self.name(placement.layer),
                    self.allowed_roles(placement.layer)
                ),
            ),
            Explain::NoTags { placement } => (
                format!("この module はタグを何も名乗っていない — {}", self.file_subject(placement)),
                format!(
                    "層の dir は外の世界からの遠さだけを決め、文脈(context)と役(role)はタグで名乗る。タグが無いと、この module が層 {} で許される役({})かを確かめられない。",
                    self.name(placement.layer),
                    self.allowed_roles(placement.layer)
                ),
            ),
            Explain::RoleMismatch { placement, role, context } => (self.role_subject(placement, role, context), self.role_reason(placement, role, context)),
            Explain::RawDirect { placement, definition, kind, evidence, category, weak } => (
                format!(
                    "定義 {}({})が {}({} の生の副作用・{})に直に触る — {}",
                    definition,
                    kind,
                    evidence,
                    category,
                    if *weak { "method 名だけで見つけた弱い証拠" } else { "import を通した名前か組み込みの強い証拠" },
                    self.file_subject(placement)
                ),
                format!(
                    "{}{} の I/O を直にすると、模擬で handler を差し替えてもこの定義だけ本物の世界に触る。effect を出して、{} handler に答えさせる。",
                    self.character(placement.layer),
                    category,
                    particle(&self.io_layers(), "の")
                ),
            ),
            Explain::WorldHandlerNamed { placement, definition, wrapped, listed_by } => (
                format!("{} が doeff の実 I/O の handler {} を名指す — {}", definition, wrapped, self.file_subject(placement)),
                format!(
                    "{} は外の世界に触れる handler で、許可名簿では {} だけが中で動かしてよい。ここで直に被せると、模擬で handler の組を差し替えてもこの所だけ本物の世界に触る。名簿の定義 {} を使うか、この定義を名簿に載せる(foundation の層に置く)。",
                    wrapped, listed_by, listed_by
                ),
            ),
            Explain::WorldHandlerMisplaced { definition, problem } => (
                format!("許可名簿の定義 {} — {}", definition, problem),
                "外の世界に触れてよい定義は層 foundation にだけ置く(operator 2026-09-29 \"placed in specific dir with rules\")。名簿に在って実物が無い・foundation の外に在ると、名簿が外の世界に触れる所を言い当てなくなる。".to_string(),
            ),
            Explain::TestKindMismatch { test, edge, mark, reached } => (
                if *edge {
                    format!("テスト {} は外の世界に届く(縁)のに印 {} が無い", test, mark)
                } else {
                    format!("テスト {} は外の世界に届かない(手元)のに印 {} が在る", test, mark)
                },
                if *edge {
                    format!("届く道: {}。縁のテストは印で既定の pytest から外す — 印が無いと手元のテストの列に実 I/O が混ざる。", reached.join(" → "))
                } else {
                    "印は縁のテストの目印で、手元のテストに付けると既定の pytest から外れて回らなくなる。".to_string()
                },
            ),
            Explain::TestFormNotDeftest { form, detail } => (
                format!("テストの形 {} — {}", form, detail),
                "テストは deftest だけ。pytest の外で走る検査や pytest の Python の形は、赤になっても誰も気づかない(#1104 の実測 — pytest の外の検査 5 本が赤のまま放置されていた)。".to_string(),
            ),
            Explain::VocabularyOutsideSinglePoint { group, count, instead } => (
                format!("語彙 {} が :except の外に {} 行 — {}", group, count, instead),
                "この語彙の判定は architecture.hy の :single-point-vocabulary が名指す 1 点だけに閉じ込める決まり。他の file が同じ語彙を読んで判定を写すと、直した時に写しの方を直し忘れて2つの判定が食い違う。".to_string(),
            ),
            Explain::ConfinedSpelling { group, why, problem } => match problem {
                super::confined_spellings::ConfinedProblem::Outside { count } => (
                    format!("綴りの群 {} が書いてよい file の外に {} か所", group, count),
                    format!("この綴りを書いてよい file は architecture.hy の :confined-spellings の :except で決めてある。外の file に書くと、その口を通らずに同じ事をする 2 つ目の場所ができる。理由: {}", why),
                ),
                super::confined_spellings::ConfinedProblem::Missing => (
                    format!("綴りの群 {} の :files に当たる file が無い", group),
                    format!("読む file が 1 つも無いと、規則は何も見ずに緑になる(母集団 0 を緑にしない)。理由: {}", why),
                ),
            },
            Explain::CountedSpelling { group, why, problem } => match problem {
                super::counted_spellings::CountProblem::Mismatch { found, wanted, within } => (
                    match within {
                        Some(name) => format!("数を決めた綴り {} が定義 {} の中に {} か所({} のはず)", group, name, found, wanted.spelling()),
                        None => format!("数を決めた綴り {} が {} か所({} のはず)", group, found, wanted.spelling()),
                    },
                    format!("この綴りの数は architecture.hy の :counted-spellings で決めてある。数が変わると、決めた口の外に同じ事をする所が増えたか、決めた口が消えている。理由: {}", why),
                ),
                super::counted_spellings::CountProblem::Missing { reason } => (
                    format!("数を決めた綴り {} の数える所が無い({})", group, reason),
                    format!("数える file や定義が無いと、規則は何も見ずに緑になる(母集団 0 を緑にしない)。理由: {}", why),
                ),
            },
            Explain::EffectCensus { group, why, problem } => {
                let rule = "effect の全体は architecture.hy の :effect-census の一覧で閉じてある。黙って増えた effect は、決めた口の外に新しい外への要求を生やす。";
                match problem {
                    super::effect_census::CensusProblem::Unlisted { effect } => {
                        (format!("effect {} が一覧 {} の外で宣言されている", effect, group), format!("{}理由: {}", rule, why))
                    }
                    super::effect_census::CensusProblem::Twice { effect } => {
                        (format!("effect {} が 2 度宣言されている(一覧 {})", effect, group), format!("{}理由: {}", rule, why))
                    }
                    super::effect_census::CensusProblem::Undeclared { effect } => {
                        (format!("一覧 {} の effect {} の宣言が無い", group, effect), format!("{}理由: {}", rule, why))
                    }
                    super::effect_census::CensusProblem::NoFiles => (
                        format!("一覧 {} の :files に当たる file が無い", group),
                        format!("読む file が 1 つも無いと、規則は何も見ずに緑になる(母集団 0 を緑にしない)。理由: {}", why),
                    ),
                }
            }
            Explain::FieldHolders { group, type_name, why, problem } => {
                let rule = format!(
                    "{} の欄を持ってよい class は architecture.hy の :field-holders の一覧で閉じてある。ほかの class に同じ欄が生えると、その型の値の置き場が 2 つになる。",
                    type_name
                );
                match problem {
                    super::field_holders::HolderProblem::Unlisted { class } => {
                        (format!("class {} が {} の欄を持つ(持ち手の一覧 {} の外)", class, type_name, group), format!("{}理由: {}", rule, why))
                    }
                    super::field_holders::HolderProblem::Absent { class } => {
                        (format!("持ち手の一覧 {} の class {} が {} の欄を持たない", group, class, type_name), format!("{}理由: {}", rule, why))
                    }
                    super::field_holders::HolderProblem::NoClass { class } => {
                        (format!("持ち手の一覧 {} が名指す class {} が無い", group, class), format!("{}理由: {}", rule, why))
                    }
                    super::field_holders::HolderProblem::NoFiles => (
                        format!("持ち手の一覧 {} の :files に当たる Python の file が無い", group),
                        format!("読む file が 1 つも無いと、規則は何も見ずに緑になる(母集団 0 を緑にしない)。理由: {}", why),
                    ),
                }
            }
            Explain::RetiredWord { group, spelling, instead, name, place } => (
                match (place, name) {
                    (super::architecture::WordPlace::Paths, _) => format!("file の名の綴り {}(使わないと決めた名 — 群 {})", spelling, group),
                    (_, Some(name)) => format!("定義の名 {}(使わないと決めた綴り {} — 群 {})", name, spelling, group),
                    (_, None) => format!("綴り {}(使わないと決めた語 — 群 {})", spelling, group),
                },
                format!("この repo は architecture.hy の :retired-words でこの綴りを使わないと決めた(代わり: {})。旧い語が残ると、同じ物を 2 つの名で呼ぶ code と文書が増え、読み手が別の物と取り違える。", instead),
            ),
            Explain::BlindDefinition { declared, why, problem } => (
                match problem {
                    super::blind::BlindProblem::ReadsWord { reached, word } => {
                        format!("{} から届く定義 {} の本体の綴り {}", declared, reached, word)
                    }
                    super::blind::BlindProblem::Imports { module } => format!("{} の module の import {}", declared, module),
                    super::blind::BlindProblem::Missing { reason } => format!("宣言した定義 {}({})", declared, reason),
                },
                match problem {
                    super::blind::BlindProblem::Missing { .. } => format!(
                        "architecture.hy の :blind-definitions が名指す定義が無いと、規則は何も見ずに緑になる(母集団 0 を緑にしない)。理由: {}",
                        why
                    ),
                    super::blind::BlindProblem::ReadsWord { .. } | super::blind::BlindProblem::Imports { .. } => format!(
                        "この定義は決めた材料だけで判じると architecture.hy の :blind-definitions で宣言した。届く先の定義がほかの材料を読むか、module が依存を持つと、判断が宣言の外の材料で変わる。理由: {}",
                        why
                    ),
                },
            ),
            Explain::CallSites { head, why, problem } => (
                super::call_sites::describe(head, problem),
                match problem {
                    super::call_sites::CallSiteProblem::Missing { .. } | super::call_sites::CallSiteProblem::Empty => format!(
                        "architecture.hy の :call-sites が名指す場所か探す file が無いと、規則は何も見ずに緑になる(母集団 0 を緑にしない)。理由: {}",
                        why
                    ),
                    super::call_sites::CallSiteProblem::Outside { .. }
                    | super::call_sites::CallSiteProblem::Count { .. }
                    | super::call_sites::CallSiteProblem::Parent { .. }
                    | super::call_sites::CallSiteProblem::Branch { .. } => format!(
                        "この頭を呼んでよい場所と回数は architecture.hy の :call-sites で決めてある。場所の外の呼びや回数の食い違いは、1 点に閉じ込めた境界が黙って 2 つ目を生やすか、境界が外れた形。理由: {}",
                        why
                    ),
                },
            ),
            Explain::BroadCatches { group, why, problem } => (
                super::broad_catches::describe(group, problem),
                match problem {
                    super::broad_catches::BroadCatchProblem::Missing { .. } | super::broad_catches::BroadCatchProblem::Empty => format!(
                        "architecture.hy の :broad-catches が名指す運搬の境界か探す file が無いと、規則は何も見ずに緑になる(母集団 0 を緑にしない)。理由: {}",
                        why
                    ),
                    super::broad_catches::BroadCatchProblem::Outside { .. } | super::broad_catches::BroadCatchProblem::Unbound { .. } => format!(
                        "広い例外の捕捉は契約の破れ(:pre / :post・型の束縛・assert)まで握りつぶす。許すのは architecture.hy の :broad-catches の :carriers で、捕まえた例外を名で束縛して決めた出来事に載せ、列へ運ぶ境界だけ。理由: {}",
                        why
                    ),
                },
            ),
            Explain::AllowedHeads { declared, why, problem } => match problem {
                super::allowed_heads::HeadProblem::Unlisted { head } => (
                    format!("{} の中の ({} …)(呼んでよい頭の一覧の外)", declared, head),
                    format!(
                        "この定義の中で呼んでよい頭は architecture.hy の :allowed-heads で決めてある。一覧の外の呼びが例外を上げると、それを受け止める境界の外で process ごと落ちる。理由: {}",
                        why
                    ),
                ),
                super::allowed_heads::HeadProblem::Missing { reason } => (
                    format!("宣言した定義 {}({})", declared, reason),
                    format!("architecture.hy の :allowed-heads が名指す定義が無いと、規則は何も見ずに緑になる(母集団 0 を緑にしない)。理由: {}", why),
                ),
            },
            Explain::RetiredCall { group, call, instead } => (
                format!("呼び ({} …)(使わないと決めた呼び — 群 {})", call, group),
                format!("この repo は architecture.hy の :retired-calls でこの呼びを退役させた(代わり: {})。退役した物を呼ぶ所が残ると、同じ役の物が 2 つ並び、座標や答えが黙って食い違う。", instead),
            ),
            Explain::PlacedDependency { placement, owner, owner_rel } => (
                format!("import 先 {}(path が {} — 層の置き場の外) — {}", owner, owner_rel, self.file_subject(placement)),
                format!("置き場の決まっていない module {}({})に依存する。architecture.hy の :placed-dependencies の層は、層の置き場に在る module にだけ依存する — 置き場の外の module は層の規則(向き・タグ・service の境界)の外なので、それを読むとその規則を迂回できる。", owner, owner_rel),
            ),
            Explain::UntypedStructuredValue { what, name, problem } => (
                format!("{} {} の型 — {}", what, name, problem),
                "構造を持つ値を素の写像や素の組で運ぶと、欄の名前と型が静的に見えず、綴りの誤りや欄の食い違いが実行まで見つからない(型検査が何も守らない)。欄の名前と型を持つ型で表す。".to_string(),
            ),
            Explain::RecordStubNotKwOnly { class, form } => (
                format!("型の宣言の class {}(.hy の側は {})", class, form),
                "実行時の __init__ は欄を名でしか受けないのに、型の宣言は位置の引数を許す。型検査は位置の引数の呼びを通すが、実行時は TypeError になる — 型の宣言が実行時の形を偽る。".to_string(),
            ),
            Explain::ServiceUntestedOnSim { service, entry, definitions, sim } => (
                format!("service {} の組み立て {}({} 本の定義)", service, entry, definitions),
                format!("{} の下の deftest から呼び出し・参照を辿っても届かない。本番の組み立てのまま handler だけを差し替えて回していない service は、業務の不変条件を確かめていない(未検証のまま配備しない)。", sim),
            ),
            Explain::HandlerArgumentHoldsState { handler, param, kind, type_text } => (
                format!("handler {} の引数 {}({}{})", handler, param, kind, if type_text.is_empty() { String::new() } else { format!(" {}", type_text) }),
                "引数で受けた client や店は外側の handler から差し替えも観測もできず、模擬の環境で handler の差し替えだけで回せない。接続先と資格・設定は Ask で読み、client は handler の本文の先頭の (session val client …) で 1 回だけ作り、状態は (session var …) で持つ。".to_string(),
            ),
            Explain::BusinessEffectFake { subject, reason } => (subject.clone(), reason.clone()),
            Explain::WorldOutsideLayers { subject } => (
                subject.clone(),
                "層の置き場の外の file も、外の世界に触れてよいのは architecture.hy の :world-handlers の定義だけ(agora-redesign #1147)。effect を出して名簿の定義の handler に答えさせるか、置き場を foundation に移して名簿に載せる。".to_string(),
            ),
            Explain::RawVia { placement, definition, through, evidence, category } => (
                format!("定義 {} が {} を通して {}({} の生の副作用)に届く — {}", definition, through.join(" → "), evidence, category, self.file_subject(placement)),
                format!(
                    "{} 自身は直に触っていないが、呼ぶ定義 {} の先で {} の I/O に届く(違反ではなく知らせ)。経路の先を effect に替えれば、{}は外の世界から離れたままになる。",
                    definition,
                    through.first().map(String::as_str).unwrap_or("?"),
                    category,
                    self.name(placement.layer)
                ),
            ),
            Explain::ServiceBoundary { placement, target, target_service, target_layer, target_dir, open_layers, shared } => {
                let own = placement.service.clone().unwrap_or_default();
                let open: Vec<String> = open_layers.iter().map(|id| format!("層 {}", self.name(*id))).collect();
                let mut readable = open.clone();
                if !shared.is_empty() {
                    readable.push(format!("共有の置き場 {}", shared.join("・")));
                }
                (
                    format!(
                        "import 先 {} は service {} の{}(path が {}/ の下) — {}",
                        target,
                        target_service,
                        self.layer_phrase(*target_layer),
                        target_dir,
                        self.file_subject(placement)
                    ),
                    format!(
                        "service {} の層 {} が service {} の層 {} を読んでいる。service をまたいで判断や翻訳を読むと、{} の中身を変えた時に {} が壊れる。{} に頼むことは {} の intent を通す(別の service から読んでよいのは {} だけ)。",
                        own,
                        self.name(placement.layer),
                        target_service,
                        self.name(*target_layer),
                        target_service,
                        own,
                        target_service,
                        target_service,
                        if readable.is_empty() { "無し".to_string() } else { readable.join("・") }
                    ),
                )
            }
            Explain::ContextMismatch { placement, contexts } => (
                format!("タグの :context = {} — {}", contexts.join("・"), self.file_subject(placement)),
                format!(
                    "dir は service {} を指すのに、タグは文脈 {} を名乗っている。タグで引いた時と dir で見た時に別の service に見える。:context を service の名に合わせるか、file を {} の dir へ移す(知らせ)。",
                    placement.service.clone().unwrap_or_default(),
                    contexts.join("・"),
                    contexts.join("・")
                ),
            ),
            Explain::ClassShape { name, shape, verdict } => {
                let mut parts = vec!["defclass".to_string()];
                if shape.dataclass {
                    parts.push("dataclass".to_string());
                }
                if !shape.bases.is_empty() {
                    parts.push(format!("基底 {}(repo の中)", shape.bases.join("・")));
                }
                match verdict {
                    ClassVerdict::ExternalWorld { evidence } => (
                        format!("定義 {}({})— 外の世界に触る class(証拠 {})", name, parts.join("・"), evidence.join("・")),
                        "method か欄の初期値が生の副作用(http・DB・file・process・時計 …)に触る。外の世界の窓口を class と method で書くと、effect と handler の差し替えを通らないので、模擬で差し替えてもこの class だけ本物に触り、閲覧のパネルにも linter の層の規則にも乗らない。".to_string(),
                    ),
                    ClassVerdict::Stateful { mutations } => (
                        format!("定義 {}({})— 変わる状態を持つ class(書き換え {})", name, parts.join("・"), mutations.join("・")),
                        "method が self の欄を書き換える。状態が値の中に散ると、どこで何が変わったかを effect と handler の記録で追えず、模擬と本物で同じ流れを確かめられない。".to_string(),
                    ),
                    ClassVerdict::DataOnly => (
                        format!("定義 {}({}・欄だけ)", name, parts.join("・")),
                        "欄だけの data class は defrecord で書ける。defrecord は :tags で文脈と役を名乗り、:check で値を検める(__post_init__ の検めは :check へ)。(知らせ — 違反ではない)".to_string(),
                    ),
                }
            }
            Explain::Smell { smell } => smell_text(smell),
            Explain::ParamCalledBare { call } => (
                format!(
                    "定義 {}({})が引数 {} を素で呼んでいる — 呼び手 {} がそこに {} を渡す",
                    call.definition, call.container, call.param, call.caller, call.passed
                ),
                format!(
                    "呼び手が {} に Program を返す関数({})を渡しているので、{} を素で呼ぶと答えではなく Program が返る。その Program は型の誤りで落ちずに値として流れ、答えを使ったつもりの所で静かに間違う(isinstance が常に偽・索引が常に空 …)。(<- x ({} …)) で受ける。",
                    call.param, call.passed, call.param, call.param
                ),
            ),
            Explain::EffectMismatch { mismatch } => match mismatch {
                super::signatures::EffectMismatch::Undeclared { definition, effect, via, .. } => (
                    match via {
                        Some(via) => format!("defk {} が {} を経由して effect {} を起こしている", definition, via, effect),
                        None => format!("defk {} が effect {} を撃っている", definition, effect),
                    },
                    format!(
                        "{} は :effects を宣言しているが、その中に {} が無い。:effects は定義が起こす effect の宣言で、handler の組がそれを受けるかを確かめる材料になる — 無い effect を起こすと、宣言を信じて組んだ handler の組で受けきれない。推論は本体で撃つ呼び((<- …)・(! …))を defk の中まで辿って集める(handler で受けた分は引かない)。",
                        definition, effect
                    ),
                ),
                super::signatures::EffectMismatch::Unused { definition, effect, .. } => (
                    format!("defk {} の :effects の {}", definition, effect),
                    format!(
                        "{} は :effects に {} を書いているが、本体で撃つ呼びを defk の中まで辿っても {} を起こしていない。起こさない effect の宣言は、handler の組に要らない受け手を求める。",
                        definition, effect, effect
                    ),
                ),
            },
            Explain::TranslationIntent { intent, handler_layer, intent_layer } => (
                match intent.via() {
                    Some(via) => format!(
                        "handler {}(層 {})が {} を経由して、層 {} の intent {} を出している",
                        intent.handler, handler_layer, via, intent_layer, intent.effect()
                    ),
                    None => format!("handler {}(層 {})が層 {} の intent {} を出している", intent.handler, handler_layer, intent_layer, intent.effect()),
                },
                format!(
                    "層 {} の handler は受けた intent を doeff の汎用の effect(HttpRequest・記録の読み書き・時計・file・process・Ask …)へ出し直す翻訳で、業務の流れを持たない。{} は層 {} の業務の intent なので、それを出すと翻訳の handler が業務の判断と流れを抱え込み、責務の境界が崩れる。import した関数を経由しても同じ — 推論は本体で実行する呼び((<- …)・(! …))を defk の先まで辿る。intent を出す流れは層 core の program に置く。",
                    handler_layer, intent.effect(), intent_layer
                ),
            ),
            Explain::BareDefkCall { call } => (
                format!("定義 {}({})が defk {} を素で呼んでいる", call.definition, call.container, call.callee),
                format!(
                    "{} は defk なので、素で呼ぶと答えではなく Program が返る。その Program は型の誤りで落ちずに値として流れ、答えを使ったつもりの所で静かに間違う(dict の .get・比べ・文字列への埋め込み)。Program として渡す所 — (<- x …) の右辺・(! …)・(return …)・Program を受ける呼びの引数 — で呼ぶ。",
                    call.callee
                ),
            ),
            Explain::MixedConcerns { name, kind, probability, layer } => (
                format!("定義 {}({})", name, kind),
                format!(
                    "Jev の判定 p={:.2}: 入力の形の検め(辞書の鍵を読む・isinstance・空の検め)と業務の判断が 1 つの定義に混ざっている見込み。層 {} の定義は型のある値を受けて判断だけをする — 形を検める所が判断の中に散ると、何が入力の形の違いで何が業務の断りかを型で分けられない。(意味の判定 — 外れなら誤判定の一覧に載せる)",
                    probability, layer
                ),
            ),
            Explain::ClassRoleDoubt { name, chosen, probability, fields } => (
                format!(
                    "定義 {}(defclass・欄 {})",
                    name,
                    if fields.is_empty() { "なし".to_string() } else { fields.join("・") }
                ),
                match chosen.as_str() {
                    "external-world" => format!(
                        "Jev の判定 p={:.2}: 外の世界の窓口 — 外から渡された client や store を欄に持ち、method で使っている見込み(生の呼び出しが見えなくても)。class の method にすると effect と handler の差し替えを通らない。(意味の判定 — 外れなら誤判定の一覧に載せる)",
                        probability
                    ),
                    _ => format!(
                        "Jev の判定 p={:.2}: 状態を持つ class — method が自分か別の物の状態を変える見込み。状態が値の中に散ると、どこで何が変わったかを effect と handler の記録で追えない。(意味の判定 — 外れなら誤判定の一覧に載せる)",
                        probability
                    ),
                },
            ),
            Explain::JsonValueUse { module, uses, refusal } => (
                format!("module {} — {} を{}", module, uses.names.join("・"), json_value_where(uses)),
                format!(
                    "JsonValue は素の dict・list・str … を名で包んだだけの型で、中の形を何も約束しない。使う側が手で分解して読むと、形の食い違いが読む所ごとに違う形で漏れる。JSON に触ってよいのは、defwire が生む汎用の解き手(doeff_hy.wire・doeff_records.wire)と、architecture.hy の :wire-modules に挙げた foundation の層の送受信の module だけで、ほかの module(protocol・intent・core …)は、解き手が一度に形を確かめた型のある値だけを見る。{}",
                    match refusal {
                        JsonValueRefusal::NotListed => "この module は :wire-modules に無い。".to_string(),
                        JsonValueRefusal::ListedOutsideFoundation { pattern, foundation_dir: Some(dir) } => {
                            format!("この module は :wire-modules の {} に当たるが、foundation の層({}/)に無いので許さない — 送受信そのものは foundation だけが行う。", pattern, dir)
                        }
                        JsonValueRefusal::ListedOutsideFoundation { pattern, foundation_dir: None } => {
                            format!("この module は :wire-modules の {} に当たるが、architecture.hy に foundation の層が無いので許さない。", pattern)
                        }
                    }
                ),
            ),
            Explain::TestNotDeftest { name, head } => (
                format!("定義 {}({})— 検の置き場の、名が test で始まる関数", name, head),
                "検は deftest だけで書く。deftest は doeff の Program として走り、handler の組み合わせを明示して検める。素の関数や fn の束縛の検は pytest が Program の外で呼ぶので、effect と handler の差し替えを通らない。".to_string(),
            ),
            Explain::DefnForbidden { name, head, .. } => (
                format!("定義 {}({})", name, head),
                "defn は契約の辞書を持てず、:tags を書けない — タグで層・役・文脈を名乗れないので、閲覧のパネルにも linter の役の規則にも乗らない。defk で書く(素の callable が避けられない時だけ deff)。".to_string(),
            ),
            Explain::DeffWithoutReason { name, marker, problem, kinds } => {
                let marker = marker.trim_end_matches([':', '\u{ff1a}']);
                let list = if kinds.is_empty() {
                    String::new()
                } else {
                    format!("受け入れてよい理由は {}。", kinds.iter().map(|k| format!("{}({})", k.name, k.description)).collect::<Vec<_>>().join("・"))
                };
                let subject = match problem {
                    DeffReasonProblem::Missing => format!("定義 {}(deff)— 理由の註 `; {}: <理由>` が定義の行にも直前の行にも無い", name, marker),
                    DeffReasonProblem::NoDetail { detail } if detail.is_empty() => format!("定義 {}(deff)— 理由の註の理由が空", name),
                    DeffReasonProblem::NoDetail { detail } => format!("定義 {}(deff)— 理由の註が「{}」で、この定義に固有の理由でない", name, detail),
                };
                (
                    subject,
                    format!(
                        "deff は defk の契約(:pre・:post と effect の検査)を外す逃げ道なので、なぜ素の関数でなければならないかを、この定義に固有の文で書く(受け入れるかは Jev の DOEFF203 が理由の文と source を見て決める)。{}",
                        list
                    ),
                )
            }
            Explain::DefinitionTagsMissing { name, head, missing, has_tags, module_default } => (
                format!(
                    "定義 {}({})の :tags に {} が無い{}",
                    name,
                    head,
                    missing.join("・"),
                    if *has_tags { "" } else { "(契約の辞書に :tags そのものが無い)" }
                ),
                format!(
                    "定義は :tags で文脈(context)と役(role)を名乗る。タグが無いと、この定義がどの層のどの役かを linter も閲覧のパネルも知れない。{}",
                    if *module_default {
                        "module の頭のタグでも補えていない。"
                    } else {
                        "この repo の設定では module の頭のタグで補えない(module_default = false)— 定義ごとに書く。"
                    }
                ),
            ),
            Explain::UndeclaredPlace { rel, problem, root, .. } => (
                match problem {
                    PlaceProblem::DirectlyUnderRoot => format!("module {} — root {}/ の直下に在り、どの service の層にも入っていない", rel, root),
                    PlaceProblem::DirectlyUnderService { service } => {
                        format!("module {} — service {} の dir の直下に在り、層の dir(core・intent …)に入っていない", rel, service)
                    }
                    PlaceProblem::InUndeclaredDirectory { dir } => format!("module {} — dir {} は architecture.hy に宣言した置き場所ではない", rel, dir),
                },
                format!(
                    "コードは service → 層 の dir({}/<service>/<層>/)か shared・foundation に置く。層が先の dir(core・protocol …)も旧い機能の dir も宣言の外 — どの service の何の層かが置き場所から分からず、層の規則も閲覧も置き場所で決められない。既存の分は登録簿で受けるが、エディタでは常に見える。",
                    root
                ),
            ),
            Explain::UndeclaredDirectory { dir, problem } => match problem {
                DirectoryProblem::ServiceNotDeclared { dir: name, services } => (
                    format!("dir {} — service {} は architecture.hy に宣言されていない(宣言した service = {})", dir, name, if services.is_empty() { "無し".to_string() } else { services.join("・") }),
                    "宣言に無い service の dir は、何の仕事で何に依存するかが分からない。defservice で宣言するか、中の module を宣言した service の層の dir へ移す。".to_string(),
                ),
                DirectoryProblem::LayerNotDeclared { service, layer, declared } => (
                    format!("dir {} — service {} の中の {} は、その service の宣言した層({})に無い", dir, service, layer, declared.join("・")),
                    format!("service の中の dir は層(外の世界からの遠さ)だけで切る。{} は宣言した層でないので、中の module の層が決まらない。service の :layers に足すか、層の dir へ移す。", layer),
                ),
            },
            Explain::ServiceDependency { placement, own, target, target_service, target_layer, target_dir, declared, depends_on, open_layers, widened } => (
                format!(
                    "import 先 {} は service {} の{}(path が {}/ の下) — {}",
                    target,
                    target_service,
                    self.layer_phrase(*target_layer),
                    target_dir,
                    self.file_subject(placement)
                ),
                if *declared {
                    format!(
                        "service {} は {} に依存すると宣言しているが、{}の module が読めるのは {} の {} だけ。{} の判断や翻訳を直に読むと、{} の中身を変えた時に {} が壊れる。{} に頼むことは {} の intent を通す。{}",
                        own,
                        target_service,
                        self.layer_phrase(placement.layer),
                        target_service,
                        open_layers.join("・"),
                        target_service,
                        target_service,
                        own,
                        target_service,
                        target_service,
                        if *widened {
                            ""
                        } else {
                            "(依存先のほかの層を読めるのは、architecture.hy の layer に :dependency-layers で広げた層 — 全体を組む組み立ての層 — だけ)"
                        }
                    )
                } else {
                    format!(
                        "service {} の依存の宣言(:depends-on = {})に {} が無い。宣言に無い依存は、service の間の向きを architecture.hy から読めなくする。依存が要るなら :depends-on に足して {} の intent を通して頼み、要らないなら import を外す。",
                        own,
                        if depends_on.is_empty() { "無し".to_string() } else { depends_on.join("・") },
                        target_service,
                        target_service
                    )
                },
            ),
            Explain::UnusedDependency { service, dependency } => (
                format!("service {} の :depends-on の {}", service, dependency),
                format!("{} のどの module も {} を読んでいない(知らせ)。使っていない依存の宣言は、service の間の向きを実物より多く見せる。要らなければ :depends-on から外す。", service, dependency),
            ),
            Explain::Semantic { placement, definition, kind, question, probability } => (
                format!("定義 {}({}) — {}", definition, kind, self.file_subject(placement)),
                format!(
                    "Jev の判定 p={:.2} — {}。{}(これは決定的な規則ではなく意味の判定で、当たり外れを測っている途中 — 外れなら誤判定の一覧に載せる)",
                    probability,
                    question.meaning(),
                    self.character(placement.layer)
                ),
            ),
            Explain::PlainCallableDoubt { definition, kind, stated, chosen, chosen_probability, chosen_accepted, chosen_description, fix, rejected_total } => (
                format!("定義 {}({})— 書かれた理由「{}」", definition, kind, stated),
                match (chosen_accepted, chosen.as_str()) {
                    (true, _) => format!(
                        "Jev の判定: 受け入れる理由 {}({})に近い(p={:.2})が、受け入れない答えの確率の和が {:.2} ある。理由の文をこの定義に固有に書き直すか、defk にできないかを確かめる。(意味の判定 — 外れなら誤判定の一覧に載せる)",
                        chosen,
                        chosen_description.clone().unwrap_or_default(),
                        chosen_probability,
                        rejected_total
                    ),
                    (false, "none") => format!(
                        "Jev の判定 p={:.2}: この理由は受け入れられない — 宣言した受け入れる理由のどれにも当たらず、素の関数でなければならない理由が見えない。呼び手を Program にして defk にできる見込み。(意味の判定 — 外れなら誤判定の一覧に載せる)",
                        chosen_probability
                    ),
                    (false, _) => format!(
                        "Jev の判定 p={:.2}: この理由は受け入れられない — 近い型は {}({})。{}(意味の判定 — 外れなら誤判定の一覧に載せる)",
                        chosen_probability,
                        chosen,
                        chosen_description.clone().unwrap_or_default(),
                        fix.as_ref().map(|f| format!("直し方: {}。", f)).unwrap_or_default()
                    ),
                },
            ),
            Explain::EnvironmentName { subject, words } => (
                match subject {
                    NameSubject::File { stem } => format!("業務の file の名 {}(環境の語 {} を含む)", stem, words.join("・")),
                    NameSubject::Definition { name, kind } => format!("定義 {}({})の名(環境の語 {} を含む)", name, kind, words.join("・")),
                },
                format!(
                    "業務の code は環境(本番・模擬・手元)を知らない。語 {} は環境の違いを表すので、業務の名に入ると環境ごとに業務の答え手が分かれ、模擬の緑が本番の道を確かめなくなる。環境の違いは土台の汎用の handler の差し替えだけで表す。",
                    words.join("・")
                ),
            ),
        };
        let law_statement = law.map(|l| l.statement.clone()).filter(|s| !s.is_empty());
        Explanation { subject, reason, law_statement }
    }

    /// 違反ごとに変わる直し方(無ければ規則の既定の 1 行)。
    pub fn hint(&self, explain: &Explain) -> Option<String> {
        const WORLD_FIX: &str = "外の世界の窓口は土台の handler にする — 資源(接続・client・file の手)は defhandler の直下の (session val …) に持ち、ListRows・PutRow などの effect に答える(模擬なら模擬の土台の handler)";
        const STATE_FIX: &str = "値は defrecord(不変)、振る舞いは新しい値を返す純粋な関数、状態は world などの handler の (session var …) 1 か所に置き、変化は effect で流す。速さのために書き換えが要る時も書き換えは handler の中だけ";
        const PROGRAM_WAYS: &str = "組み立て(handler の並び)なら `(defk handlers-of [foundation])` に・テストなら deftest に・値を組む補助なら defk にして `(<- …)` で呼ぶ";
        match explain {
            Explain::DefnForbidden { declared_kind: Some(kind), .. } => {
                Some(format!("deff にする(理由 {} — {})。同じ行に `; defk にできない: <誰がどう呼ぶか>` と :tags を書く", kind.name, kind.description))
            }
            Explain::DefnForbidden { declared_kind: None, .. } => {
                Some(format!("defk にする(素の関数でなければならない理由が見当たらない)— {}", PROGRAM_WAYS))
            }
            Explain::ClassShape { verdict: ClassVerdict::ExternalWorld { .. }, .. } => Some(WORLD_FIX.to_string()),
            Explain::ClassShape { verdict: ClassVerdict::Stateful { .. }, .. } => Some(STATE_FIX.to_string()),
            Explain::Smell { smell } => Some(
                match &smell.kind {
                    super::smells::SmellKind::ShapeCheck { .. } => "形の検めは protocol の境目で defwire の型に parse し(形が合わなければ解く所で失敗)、この定義は型のある値を受けて判断だけをする",
                    super::smells::SmellKind::FailureRethrow { .. } => "失敗は (<- (Raise 失敗の値)) で出し(呼び手へ手で return し直さない)、受けて写す所だけ呼ぶ側で (on-raise 本文 (Refusal r) 写し先) と受ける(ADR-DOE-CORE-EFFECTS-003 R3・R7)",
                    super::smells::SmellKind::BindThenReturn { .. } => "(return (! (f …))) と 1 つにするか、失敗なら (<- (Raise …)) で出す",
                    super::smells::SmellKind::FieldsJoined { .. } => "型のある値のまま渡す(欄を文字列に潰さない)— 文にするのは人に見せる境目の 1 か所だけ",
                    super::smells::SmellKind::RebuiltAccumulator { .. } => "蓄えは内包表記(lfor)で 1 度に作る — ループの中で (+ xs #(…)) の作り直しを重ねない",
                }
                .to_string(),
            ),
            Explain::RetiredWord { instead, .. } => Some(format!("{} に書き換える(規則そのものを述べる行なら :rule-lines の綴りを含めて書く)— 直せない既存の当たりは登録簿に載せる", instead)),
            Explain::RetiredCall { instead, .. } => Some(format!("{} に置き換える — 直せない既存の当たりは登録簿に載せる", instead)),
            Explain::PlacedDependency { owner_rel, .. } => Some(format!("{} を層の置き場(<root>/<service>/<層>/)へ移すか、要る型を intent へ移して読む — 直せない既存の当たりは登録簿に載せる", owner_rel)),
            Explain::MixedConcerns { .. } => Some("形の検めは protocol の境目で defwire の型に parse し(形が合わなければ解く所で失敗)、この定義は型のある値を受けて判断だけをする".to_string()),
            Explain::ClassRoleDoubt { chosen, .. } if chosen == "external-world" => Some(WORLD_FIX.to_string()),
            Explain::ClassRoleDoubt { .. } => Some(STATE_FIX.to_string()),
            Explain::ClassShape { verdict: ClassVerdict::DataOnly, .. } => Some("defrecord にする(:tags で文脈と役・:check で値の検め)".to_string()),
            Explain::UndeclaredPlace { destination, .. } => Some(format!("{} へ移す(service は :context のタグ、層は今の置き場所か :role のタグから推した案)", destination)),
            Explain::JsonValueUse { refusal: JsonValueRefusal::ListedOutsideFoundation { pattern, foundation_dir: Some(dir) }, .. } => Some(format!(
                "送受信そのものを {}/ の module へ移して :wire-modules はそこを指すか、{} を :wire-modules から外し、この module は defwire の型のある値を使う",
                dir, pattern
            )),
            Explain::PlainCallableDoubt { chosen_accepted: false, fix: Some(fix), .. } => Some(fix.clone()),
            Explain::PlainCallableDoubt { .. } => Some(format!("defk にできないかを確かめる — {}", PROGRAM_WAYS)),
            Explain::DeffWithoutReason { problem: DeffReasonProblem::Missing, .. } => {
                Some(format!("素の関数でなければならないなら `; defk にできない: <誰がどう呼ぶか>` を書く。そうでなければ defk にする — {}", PROGRAM_WAYS))
            }
            Explain::DeffWithoutReason { problem: DeffReasonProblem::NoDetail { .. }, .. } => {
                Some(format!("この定義に固有の理由(誰が・どう呼ぶか)を書く(「同上」は使わない)。書けないなら defk にする — {}", PROGRAM_WAYS))
            }
            _ => None,
        }
    }

    /// module の層を何で決めたか(path の置き場所・タグ・両方の食い違い)。
    pub fn layer_reason(&self, placement: &Placement) -> String {
        if placement.by_tags {
            return format!(
                "タグで決めた — {}/ は architecture.hy の宣言した置き場所ではないので、タグの role = {} から{}と推した",
                placement.dir,
                placement.roles.join("・"),
                lead(&self.site_phrase(placement))
            );
        }
        let base = format!("path の置き場所で決めた — {}/ の下は{}", placement.dir, lead(&self.site_phrase(placement)));
        if placement.roles.is_empty() {
            return format!("{}(タグで役を名乗っていない)", base);
        }
        let foreign: Vec<&String> = placement.roles.iter().filter(|r| !self.role_allowed(placement.layer, r)).collect();
        if foreign.is_empty() {
            return format!("{}。タグの role = {} もこの層の役", base, placement.roles.join("・"));
        }
        let homes: Vec<String> = foreign.iter().map(|r| format!("タグの role {} は{}", r, self.role_home(r))).collect();
        format!(
            "path とタグが食い違う — path の置き場所 {}/ では{}、{}(層は path で決める)",
            placement.dir,
            lead(&self.site_phrase(placement)),
            homes.join("、")
        )
    }

    // --- 部品 -------------------------------------------------------------------------

    /// 層の名前。
    fn name(&self, id: LayerId) -> String {
        self.layers.and_then(|l| l.layers.get(id.0)).map(|l| l.name.clone()).unwrap_or_else(|| "?".to_string())
    }

    /// 「service X の層 Y(要約)」(service の段が無ければ層だけ)。
    fn site_phrase(&self, placement: &Placement) -> String {
        match &placement.service {
            Some(service) => format!("service {} の{}", service, self.layer_phrase(placement.layer)),
            None => self.layer_phrase(placement.layer),
        }
    }

    /// 「層 X(要約)」。
    fn layer_phrase(&self, id: LayerId) -> String {
        let summary = self.layers.and_then(|l| l.layers.get(id.0)).and_then(|l| l.description.summary.clone());
        match summary {
            Some(summary) => format!("層 {}({})", self.name(id), summary),
            None => format!("層 {}", self.name(id)),
        }
    }

    /// 外の世界からの遠さ(層の順から)。
    fn distance(&self, id: LayerId) -> String {
        let count = self.layers.map(|l| l.layers.len()).unwrap_or(0);
        match id.0 {
            0 => "外の世界から最も遠い層".to_string(),
            index if count > 0 && index + 1 == count => "外の世界に最も近い層".to_string(),
            index => format!("外の世界からの遠さの順で {} 番目の層", index + 1),
        }
    }

    /// 層の性格の文(「層 core(要約)は外の世界から最も遠い層で、X を知り、Y は知らない。」)。
    fn character(&self, id: LayerId) -> String {
        let description = self.layers.and_then(|l| l.layers.get(id.0)).map(|l| l.description.clone()).unwrap_or_default();
        let mut text = format!("{}{}", particle(&self.layer_phrase(id), "は"), self.distance(id));
        match (&description.knows, &description.does_not_know) {
            (Some(knows), Some(not)) => text.push_str(&format!("で、{}知り、{}知らない。", particle(knows, "を"), particle(not, "は"))),
            (Some(knows), None) => text.push_str(&format!("で、{}知る。", particle(knows, "を"))),
            (None, Some(not)) => text.push_str(&format!("で、{}知らない。", particle(not, "は"))),
            (None, None) => text.push('。'),
        }
        text
    }

    /// import 先の層の性格(「層 foundation(要約)は X を持つので、」)。
    fn target_character(&self, id: LayerId) -> String {
        let knows = self.layers.and_then(|l| l.layers.get(id.0)).and_then(|l| l.description.knows.clone());
        match knows {
            Some(knows) => format!("import 先の{}{}持つので、", particle(&self.layer_phrase(id), "は"), particle(&knows, "を")),
            None => format!("import 先の{}{}なので、", particle(&self.layer_phrase(id), "は"), self.distance(id)),
        }
    }

    /// file の主体の文(「この file は層 core — path が controllers/core/ の下、タグの role = translation」)。
    fn file_subject(&self, placement: &Placement) -> String {
        let tags = if placement.roles.is_empty() {
            "タグで役を名乗っていない".to_string()
        } else {
            format!("タグの role = {}", placement.roles.join("・"))
        };
        format!("この file は{} — path が {}/ の下、{}", lead(&self.site_phrase(placement)), placement.dir, tags)
    }

    /// 層が import してよい層の並び。
    fn allowed_imports(&self, id: LayerId) -> String {
        let spec = self.layers.and_then(|l| l.layers.get(id.0));
        match spec.and_then(|s| s.allowed.as_ref()) {
            Some(allowed) => {
                let names: Vec<String> = allowed.iter().map(|a| format!("層 {}", self.name(*a))).collect();
                names.join("・")
            }
            None => "どの層でも".to_string(),
        }
    }

    /// 層で許す role の並び。
    fn allowed_roles(&self, id: LayerId) -> String {
        let spec = self.layers.and_then(|l| l.layers.get(id.0));
        match spec.and_then(|s| s.roles.as_ref()) {
            Some(roles) => roles.iter().cloned().collect::<Vec<_>>().join(" / "),
            None => "設定に無い".to_string(),
        }
    }

    /// role がその層で許されるか。
    fn role_allowed(&self, id: LayerId, role: &str) -> bool {
        self.layers.and_then(|l| l.layers.get(id.0)).and_then(|s| s.roles.as_ref()).is_none_or(|roles| roles.contains(role))
    }

    /// role を許す層の文(「層 protocol の役」・無ければ「どの層の役でもない」)。
    fn role_home(&self, role: &str) -> String {
        let homes: Vec<String> = self
            .layers
            .map(|l| l.layers.iter().filter(|s| s.roles.as_ref().is_some_and(|r| r.contains(role))).map(|s| format!("層 {}", s.name)).collect())
            .unwrap_or_default();
        if homes.is_empty() {
            "どの層の役でもない(今の role の一覧に無い)".to_string()
        } else {
            format!("{}役", particle(&homes.join("・"), "の"))
        }
    }

    /// role の説明(設定に在れば「role X(説明)」)。
    fn role_phrase(&self, role: &str) -> String {
        match self.layers.and_then(|l| l.role_descriptions.get(role)) {
            Some(text) => format!("role {}({})", role, text),
            None => format!("role {}", role),
        }
    }

    /// 生の副作用を許す層の文。
    fn io_layers(&self) -> String {
        if self.raw.is_some_and(|r| r.world_modules.is_some()) {
            return "architecture.hy の :world-handlers(外の世界に触れてよい定義の許可名簿)の定義の module".to_string();
        }
        match self.raw.filter(|r| !r.allowed.is_empty()) {
            Some(raw) => raw.allowed.iter().map(|id| format!("層 {}", self.name(*id))).collect::<Vec<_>>().join("・"),
            None => "外の世界に触ってよい層".to_string(),
        }
    }

    /// DOEFF105 の主体(食い違いを書く)。
    fn role_subject(&self, placement: &Placement, role: &Option<String>, context: &Option<String>) -> String {
        let role_text = match role.as_deref().filter(|r| !r.is_empty()) {
            Some(role) => format!("タグの role = {}", role),
            None => "タグに role が無い".to_string(),
        };
        let context_text = match context.as_deref().filter(|c| !c.is_empty()) {
            Some(context) => format!("context = {}", context),
            None => "context が無い".to_string(),
        };
        let mismatch = match role.as_deref().filter(|r| !r.is_empty() && !self.role_allowed(placement.layer, r)) {
            Some(role) => format!(" — path とタグが食い違う(role {} は{})", role, self.role_home(role)),
            None => String::new(),
        };
        format!(
            "この file は{} — path が {}/ の下、{}・{}{}",
            lead(&self.site_phrase(placement)),
            placement.dir,
            role_text,
            context_text,
            mismatch
        )
    }

    /// DOEFF105 の理由。
    fn role_reason(&self, placement: &Placement, role: &Option<String>, context: &Option<String>) -> String {
        let allowed = self.allowed_roles(placement.layer);
        match (role.as_deref().filter(|r| !r.is_empty()), context.as_deref().filter(|c| !c.is_empty())) {
            (Some(role), _) if !self.role_allowed(placement.layer, role) => format!(
                "{}{}。{}ここに置けない。層 {} で許す role = {}。",
                particle(&self.role_phrase(role), "は"),
                self.role_home(role),
                self.character(placement.layer).trim_end_matches('。').to_string() + "ので、",
                self.name(placement.layer),
                allowed
            ),
            (None, _) => format!(
                "役(role)を名乗らないと、この定義が層 {} に置いてよい物かを確かめられない。層 {} で許す role = {}。",
                self.name(placement.layer),
                self.name(placement.layer),
                allowed
            ),
            (Some(_), None) => "文脈(context)を名乗らないと、どの業務の定義かをタグで引けない。:context も書く。".to_string(),
            (Some(_), Some(_)) => format!("タグは層 {} の決まりに合っている。", self.name(placement.layer)),
        }
    }
}

/// 臭い 1 件の subject と reason(DOEFF121〜125)。
fn smell_text(smell: &super::smells::Smell) -> (String, String) {
    use super::smells::SmellKind;
    match &smell.kind {
        SmellKind::ShapeCheck { field, holder } => (
            format!("定義 {} — {} の欄 \"{}\" を文字列の鍵で読み、isinstance で形を検めている", smell.definition, holder, field),
            "判断の層に JSON の形の検めが入っている。形を検める所が判断の中に散ると、何が入力の形の違いで何が業務の断りかを型で分けられず、同じ検めが呼ぶ所ごとに写される。形は通信の境目で型のある値に解き、判断は型のある値だけを見る。(知らせ)".to_string(),
        ),
        SmellKind::FailureRethrow { failure_type, subject } => (
            format!("定義 {} — match の腕が失敗の型 {} を受け、{} をそのまま(か包み直して)return している", smell.definition, failure_type, subject),
            "受けた失敗を 1 段ずつ手で return し直すのは、例外の再送出を手で書いた形。呼ぶ段ごとに同じ腕が要り、1 か所でも写し忘れると失敗が成功の道へ流れる。失敗は Raise で出し、受けて写したい境目だけで on-raise で受ける。(失敗の型は defrecord の :failure True と defeffect の :failure / :absent の宣言から取る)".to_string(),
        ),
        SmellKind::BindThenReturn { name } => (
            format!("定義 {} — (<- {} …) の直後に (return {}) だけが続く", smell.definition, name, name),
            format!("{} は返すためだけの名で、ほかで使われない。束ねと return を 1 つにすれば名が要らない — 失敗を返しているなら Raise で出す形が正しい。", name),
        ),
        SmellKind::FieldsJoined { value, fields } => (
            format!("定義 {} — {} の欄 {} を 1 本の文字列につないでいる", smell.definition, value, fields.join("・")),
            "型のある値を文字列に潰すと、受け手は欄を読み直せず(文字列を切り分けるしかない)、欄の意味が型から消える。値のまま渡し、文にするのは人に見せる境目だけにする。".to_string(),
        ),
        SmellKind::RebuiltAccumulator { name } => (
            format!("定義 {} — ループの中で {} を (+ {} …) で毎回作り直している", smell.definition, name, name),
            "蓄えを毎回作り直すと、長さに比例した写しが繰り返されて 2 乗の手間になり、名の書き換えも増える。内包表記で 1 度に作れば、書き換えも作り直しも要らない。".to_string(),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::project::settings::{LayerDescription, LayerSpec, TagReading};
    use std::collections::{BTreeMap, BTreeSet};

    /// 層 2 つ(core に説明あり・foundation は説明なし)の設定。
    fn layers() -> LayerSettings {
        let spec = |name: &str, dir: &str, roles: &[&str], description: LayerDescription| LayerSpec {
            name: name.to_string(),
            places: vec![crate::project::settings::PlacePattern::parse(dir).unwrap()],
            allowed: Some([LayerId(0)].into_iter().collect()),
            forbid_modules: BTreeSet::new(),
            types_only: false,
            roles: Some(roles.iter().map(|r| r.to_string()).collect()),
            description,
        };
        LayerSettings {
            layers: vec![
                spec(
                    "core",
                    "app/core",
                    &["judgment"],
                    LayerDescription {
                        summary: Some("業務の判断".into()),
                        knows: Some("業務の判断".into()),
                        does_not_know: Some("通信の手段".into()),
                        question: None,
                    },
                ),
                spec("foundation", "app/foundation", &["foundation", "translation"], LayerDescription::default()),
            ],
            infer_root: None,
            role_descriptions: [("translation".to_string(), "翻訳の handler".to_string())].into_iter().collect::<BTreeMap<_, _>>(),
            exclude: BTreeSet::new(),
            extensions: BTreeSet::new(),
            tags: TagReading {
                module_variable_hy: String::new(),
                module_variable_py: String::new(),
                contract_definers: BTreeSet::new(),
                plain_definers: BTreeSet::new(),
                effect_definers: BTreeSet::new(),
                record_definers: BTreeSet::new(),
                function_definers: BTreeSet::new(),
                required: Vec::new(),
                module_default: true,
                require_on: BTreeSet::new(),
            },
        }
    }

    #[test]
    fn role_mismatch_names_both_the_path_and_the_tag() {
        let settings = layers();
        let narrator = Narrator { layers: Some(&settings), raw: None };
        let placement = Placement { layer: LayerId(0), dir: "app/core".into(), service: None, roles: vec!["translation".into()], by_tags: false };
        let explain = Explain::RoleMismatch { placement: placement.clone(), role: Some("translation".into()), context: Some("peer".into()) };
        let law = LawSpec { name: "role-law".into(), adr: None, statement: "role は層に合う".into(), rules: Vec::new(), layers: BTreeSet::new() };
        let out = narrator.explain(&explain, Some(&law));
        assert_eq!(
            out.subject,
            "この file は層 core(業務の判断) — path が app/core/ の下、タグの role = translation・context = peer — path とタグが食い違う(role translation は層 foundation の役)"
        );
        assert_eq!(
            out.reason,
            "role translation(翻訳の handler)は層 foundation の役。層 core(業務の判断)は外の世界から最も遠い層で、業務の判断を知り、通信の手段は知らないので、ここに置けない。層 core で許す role = judgment。"
        );
        assert_eq!(out.law_statement.as_deref(), Some("role は層に合う"));
        assert_eq!(
            narrator.layer_reason(&placement),
            "path とタグが食い違う — path の置き場所 app/core/ では層 core(業務の判断)、タグの role translation は層 foundation の役(層は path で決める)"
        );
    }

    #[test]
    fn import_direction_uses_descriptions_and_falls_back_without_them() {
        let settings = layers();
        let narrator = Narrator { layers: Some(&settings), raw: None };
        let placement = Placement { layer: LayerId(0), dir: "app/core".into(), service: None, roles: vec!["judgment".into()], by_tags: false };
        let out = narrator.explain(
            &Explain::ImportDirection { placement, target: "app.foundation.io.send".into(), target_layer: LayerId(1), target_dir: "app/foundation".into() },
            None,
        );
        assert!(out.subject.starts_with("import 先 app.foundation.io.send は層 foundation(path が app/foundation/ の下)"), "{}", out.subject);
        // foundation には説明が無い — 層の順から「外の世界に最も近い層」とだけ言う。
        assert_eq!(
            out.reason,
            "層 core(業務の判断)は外の世界から最も遠い層で、業務の判断を知り、通信の手段は知らない。core が import してよいのは 層 core だけ。import 先の層 foundation は外の世界に最も近い層なので、core から読むと、core が層 foundation の持つ物に触れる(模擬で handler を差し替えても、その所だけ本物に触る)。"
        );
        assert_eq!(out.law_statement, None);
        let untagged = Placement { layer: LayerId(1), dir: "app/foundation".into(), service: None, roles: Vec::new(), by_tags: false };
        assert_eq!(narrator.layer_reason(&untagged), "path の置き場所で決めた — app/foundation/ の下は層 foundation(タグで役を名乗っていない)");
    }
}
