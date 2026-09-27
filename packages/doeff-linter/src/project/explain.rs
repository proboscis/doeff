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
}

/// 宣言に無い dir の種類(DOEFF115)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DirectoryProblem {
    /// root の直下の dir が、宣言した service でも shared・foundation・legacy でもない。
    ServiceNotDeclared { dir: String, services: Vec<String> },
    /// service の中の dir が、その service の宣言した層でない。
    LayerNotDeclared { service: String, layer: String, declared: Vec<String> },
}

/// deff の理由の註の問題(DOEFF111)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeffReasonProblem {
    /// 註が無い。
    Missing,
    /// 種類の無い旧い形(移行の間は warning)。
    Legacy,
    /// 種類が一覧に無い。
    UnknownKind { kind: String },
    /// 詳細が空か「同上」。
    NoDetail { kind: String, detail: String },
}

impl DeffReasonProblem {
    /// 短い 1 行(message のため)。
    pub fn short(&self) -> String {
        match self {
            DeffReasonProblem::Missing => "理由の註が無い".to_string(),
            DeffReasonProblem::Legacy => "種類の無い旧い形".to_string(),
            DeffReasonProblem::UnknownKind { kind } => format!("種類 {} は一覧に無い", kind),
            DeffReasonProblem::NoDetail { kind, .. } => format!("種類 {} の詳細が空か「同上」", kind),
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
    UndeclaredPlace { rel: String, problem: PlaceProblem, root: String },
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
        open_layers: Vec<String>,
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
    PlainCallableDoubt {
        definition: String,
        kind: &'static str,
        declared: String,
        declared_description: String,
        declared_probability: f64,
        chosen: String,
        chosen_probability: f64,
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
            Explain::DefnForbidden { name, head, .. } => (
                format!("定義 {}({})", name, head),
                "defn は契約の辞書を持てず、:tags を書けない — タグで層・役・文脈を名乗れないので、閲覧のパネルにも linter の役の規則にも乗らない。defk で書く(素の callable が避けられない時だけ deff)。".to_string(),
            ),
            Explain::DeffWithoutReason { name, marker, problem, kinds } => {
                let marker = marker.trim_end_matches([':', '\u{ff1a}']);
                let list = if kinds.is_empty() {
                    "(architecture.hy に種類の一覧が無い)".to_string()
                } else {
                    kinds.iter().map(|k| format!("{}({})", k.name, k.description)).collect::<Vec<_>>().join("・")
                };
                let subject = match problem {
                    DeffReasonProblem::Missing => format!("定義 {}(deff)— 理由の註 `; {}(<種類>): <詳細>` が定義の行にも直前の行にも無い", name, marker),
                    DeffReasonProblem::Legacy => format!("定義 {}(deff)— 理由の註に種類が無い旧い形(`; {}(<種類>): <詳細>` にする)", name, marker),
                    DeffReasonProblem::UnknownKind { kind } => format!("定義 {}(deff)— 理由の種類 {} は architecture.hy の一覧に無い", name, kind),
                    DeffReasonProblem::NoDetail { kind, detail } => {
                        format!("定義 {}(deff)— 種類 {} の詳細が{}", name, kind, if detail.is_empty() { "空".to_string() } else { format!("「{}」", detail) })
                    }
                };
                let declared = match problem {
                    DeffReasonProblem::NoDetail { kind, .. } => kinds.iter().find(|k| &k.name == kind).map(|k| format!("種類 {} = {}。", k.name, k.description)).unwrap_or_default(),
                    _ => String::new(),
                };
                (
                    subject,
                    format!(
                        "deff は defk の契約(:pre・:post と effect の検査)を外す逃げ道なので、素の関数でなければならない理由を、決まった種類({})と、この定義に固有の詳細で書く。{}種類に当たらないなら、呼び手を Program にして defk にする。",
                        list, declared
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
            Explain::UndeclaredPlace { rel, problem, root } => (
                match problem {
                    PlaceProblem::DirectlyUnderRoot => format!("module {} — root {}/ の直下に在り、どの service の層にも入っていない", rel, root),
                    PlaceProblem::DirectlyUnderService { service } => {
                        format!("module {} — service {} の dir の直下に在り、層の dir(core・intent …)に入っていない", rel, service)
                    }
                },
                format!(
                    "architecture.hy が宣言した置き場所({}/<service>/<層>/・shared・foundation・legacy)のどれでもないので、どの service の何の層か分からない — 層の規則にも閲覧にも乗らない。architecture.hy に宣言するか、宣言した置き場所へ移す。",
                    root
                ),
            ),
            Explain::UndeclaredDirectory { dir, problem } => match problem {
                DirectoryProblem::ServiceNotDeclared { dir: name, services } => (
                    format!("dir {} — service {} は architecture.hy に宣言されていない(宣言した service = {})", dir, name, if services.is_empty() { "無し".to_string() } else { services.join("・") }),
                    "宣言に無い service の dir は、何の仕事で何に依存するかが分からない。defservice で宣言するか、legacy に入れて移行を待つか、宣言した service の dir へ移す。".to_string(),
                ),
                DirectoryProblem::LayerNotDeclared { service, layer, declared } => (
                    format!("dir {} — service {} の中の {} は、その service の宣言した層({})に無い", dir, service, layer, declared.join("・")),
                    format!("service の中の dir は層(外の世界からの遠さ)だけで切る。{} は宣言した層でないので、中の module の層が決まらない。service の :layers に足すか、層の dir へ移す。", layer),
                ),
            },
            Explain::ServiceDependency { placement, own, target, target_service, target_layer, target_dir, declared, depends_on, open_layers } => (
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
                        "service {} は {} に依存すると宣言しているが、読めるのは {} の {} だけ。{} の判断や翻訳を直に読むと、{} の中身を変えた時に {} が壊れる。{} に頼むことは {} の intent を通す。",
                        own, target_service, target_service, open_layers.join("・"), target_service, target_service, own, target_service, target_service
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
                    "Jev の判定 p={:.2} — {}。{}(これは決定的な規則ではなく意味の判定で、当たり外れを測っている途中 — 外れなら登録簿に載せる)",
                    probability,
                    question.meaning(),
                    self.character(placement.layer)
                ),
            ),
            Explain::PlainCallableDoubt { definition, kind, declared, declared_description, declared_probability, chosen, chosen_probability } => (
                format!("定義 {}({})— 名乗った理由の種類 {}({})", definition, kind, declared, declared_description),
                format!(
                    "Jev の判定 p({})={:.2} — コードを読むと、名乗った種類は素の関数でなければならない理由に当たらない見込み。Jev が選んだのは {}(p={:.2}){}。(これは意味の判定で、当たり外れを測っている途中 — 外れなら登録簿に載せる)",
                    declared,
                    declared_probability,
                    chosen,
                    chosen_probability,
                    if chosen == "none" { " — どの種類にも当たらず、呼び手を Program にして defk にできる見込み" } else { "" }
                ),
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
        const PROGRAM_WAYS: &str = "組み立て(handler の並び)なら `(defk handlers-of [foundation])` に・テストなら deftest に・値を組む補助なら defk にして `(<- …)` で呼ぶ";
        match explain {
            Explain::DefnForbidden { declared_kind: Some(kind), .. } => {
                Some(format!("deff にする(種類 {} — {})。同じ行の註を `; defk にできない({}): <詳細>` の形にして :tags を書く", kind.name, kind.description, kind.name))
            }
            Explain::DefnForbidden { declared_kind: None, .. } => {
                Some(format!("defk にする(素の関数でなければならない理由が種類に当たらない)— {}", PROGRAM_WAYS))
            }
            Explain::DeffWithoutReason { problem: DeffReasonProblem::UnknownKind { .. } | DeffReasonProblem::Missing, .. } => {
                Some(format!("種類に当たるなら `; defk にできない(<種類>): <詳細>` を書く。当たらないなら defk にする — {}", PROGRAM_WAYS))
            }
            Explain::DeffWithoutReason { problem: DeffReasonProblem::Legacy, .. } => {
                Some(format!("註を `; defk にできない(<種類>): <詳細>` の形に直す。種類に当たらないなら defk にする — {}", PROGRAM_WAYS))
            }
            Explain::DeffWithoutReason { problem: DeffReasonProblem::NoDetail { .. }, .. } => {
                Some(format!("この定義に固有の詳細(誰が・どう呼ぶか)を書く(「同上」は使わない)。書けないなら defk にする — {}", PROGRAM_WAYS))
            }
            _ => None,
        }
    }

    /// module の層を何で決めたか(path の置き場所・タグ・両方の食い違い)。
    pub fn layer_reason(&self, placement: &Placement) -> String {
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
            role_descriptions: [("translation".to_string(), "翻訳の handler".to_string())].into_iter().collect::<BTreeMap<_, _>>(),
            exclude: BTreeSet::new(),
            extensions: BTreeSet::new(),
            tags: TagReading {
                module_variable_hy: String::new(),
                module_variable_py: String::new(),
                contract_definers: BTreeSet::new(),
                plain_definers: BTreeSet::new(),
                effect_definers: BTreeSet::new(),
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
        let placement = Placement { layer: LayerId(0), dir: "app/core".into(), service: None, roles: vec!["translation".into()] };
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
        let placement = Placement { layer: LayerId(0), dir: "app/core".into(), service: None, roles: vec!["judgment".into()] };
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
        let untagged = Placement { layer: LayerId(1), dir: "app/foundation".into(), service: None, roles: Vec::new() };
        assert_eq!(narrator.layer_reason(&untagged), "path の置き場所で決めた — app/foundation/ の下は層 foundation(タグで役を名乗っていない)");
    }
}
