//! 宣言した effect・引数と答えの型・型でない契約・effect 節の解く effect(版 5)のテスト — Hy の source を索引して確かめる。

use std::path::Path;

use super::index_source;
use super::model::{ContractSide, Definition, HyFileIndex, NameRef, TypeNote};

/// source を `/r/pkg/sample.hy` として索引する(root は `/r`)。
fn index(source: &str) -> HyFileIndex {
    index_source(Path::new("/r"), Path::new("/r/pkg/sample.hy"), source)
}

/// 名と kind で定義を引く。
fn def<'a>(file: &'a HyFileIndex, name: &str, kind: &str) -> &'a Definition {
    file.definitions
        .iter()
        .find(|d| d.name == name && d.kind.as_str() == kind)
        .unwrap_or_else(|| panic!("{kind} {name} が無い"))
}

/// 名の (綴り, 完全修飾名) の列。
fn refs(names: &[NameRef]) -> Vec<(&str, Option<&str>)> {
    names.iter().map(|n| (n.name.as_str(), n.target.as_deref())).collect()
}

/// 型の注記の (綴り, 名の列)。
fn note(note: &TypeNote) -> (&str, Vec<(&str, Option<&str>)>) {
    (note.text.as_str(), refs(&note.names))
}

const SAMPLE: &str = r#"(import controllers.messaging.intent.conversation-input [ReadInput WriteInput])
(import .model [InputRequest RunOutcome])
(require doeff-hy.macros [defk defhandler defeffect defrecord])

(defeffect Settle "決着を積む。" {:fields [(: settlement IntakeSettlement) note] :answer (| Landed Refused) :tags {:context "m"}})

(defrecord IntakeSettlement "決着。" #^ str request-id #^ (| int None) count plain)

(defk run-it [request budget]
  {:pre [(: request InputRequest) (> budget 0) (: budget int) (: other.x str)] :post [(: % RunOutcome) (!= % None)]
   :effects [ReadInput WriteInput Settle] :tags {:context "messaging"}}
  "要求 1 つを実体化するため。"
  (<- (ReadInput 1)))

(defk no-effects [] {:pre [] :post [] :effects []} 1)
(defk undeclared [x] {:pre [(: x int)]} x)
(defk #^ int post-wins [] {:post [(: % str)]} 1)
(defk #^ int annotated-only [] 1)

(defhandler store []
  (ReadInput [id] (resume None))
  (Settle [s n] (resume None))
  (Unknown [] (resume None)))
"#;

#[test]
fn contract_dict_gives_declared_effects_types_and_non_type_predicates() {
    let file = index(SAMPLE);
    let run = def(&file, "run-it", "defk");
    assert_eq!(
        refs(run.effects.as_deref().expect(":effects を書いた")),
        vec![
            ("ReadInput", Some("controllers.messaging.intent.conversation_input.ReadInput")),
            ("WriteInput", Some("controllers.messaging.intent.conversation_input.WriteInput")),
            ("Settle", Some("pkg.sample.Settle")),
        ]
    );
    let params: Vec<(&str, (&str, Vec<(&str, Option<&str>)>))> =
        run.param_types.iter().map(|p| (p.name.as_str(), note(&p.type_note))).collect();
    assert_eq!(
        params,
        vec![
            ("request", ("InputRequest", vec![("InputRequest", Some("pkg.model.InputRequest"))])),
            ("budget", ("int", vec![("int", None)])),
        ],
        "引数の順に並べ、相対 import は書いた file の package を基準に解く"
    );
    assert_eq!(note(run.answer_type.as_ref().expect("答えの型")), ("RunOutcome", vec![("RunOutcome", Some("pkg.model.RunOutcome"))]));
    let contracts: Vec<(ContractSide, &str)> = run.contracts.iter().map(|c| (c.side, c.text.as_str())).collect();
    assert_eq!(
        contracts,
        vec![
            (ContractSide::Pre, "(> budget 0)"),
            (ContractSide::Pre, "(: other.x str)"),
            (ContractSide::Post, "(!= % None)"),
        ],
        "型でない述語と、引数でない名への型の注記は契約に残る"
    );
    assert_eq!(run.handles, None);
}

#[test]
fn empty_and_missing_effects_are_distinct() {
    let file = index(SAMPLE);
    assert_eq!(def(&file, "no-effects", "defk").effects.as_deref().map(<[NameRef]>::len), Some(0), "`:effects []` は起こさない宣言");
    assert!(def(&file, "no-effects", "defk").contracts.is_empty());
    assert_eq!(def(&file, "undeclared", "defk").effects, None, ":effects を書いていない");
    assert_eq!(def(&file, "undeclared", "defk").param_types.len(), 1);
}

#[test]
fn post_type_wins_over_the_name_annotation() {
    let file = index(SAMPLE);
    assert_eq!(def(&file, "post-wins", "defk").answer_type.as_ref().map(|t| t.text.as_str()), Some("str"));
    assert_eq!(def(&file, "annotated-only", "defk").answer_type.as_ref().map(|t| t.text.as_str()), Some("int"));
}

#[test]
fn defeffect_fields_and_answer_are_types() {
    let file = index(SAMPLE);
    let settle = def(&file, "Settle", "defeffect");
    let params: Vec<(&str, (&str, Vec<(&str, Option<&str>)>))> =
        settle.param_types.iter().map(|p| (p.name.as_str(), note(&p.type_note))).collect();
    assert_eq!(
        params,
        vec![("settlement", ("IntakeSettlement", vec![("IntakeSettlement", Some("pkg.sample.IntakeSettlement"))]))],
        "型の無い欄(note)は入れない"
    );
    assert_eq!(
        note(settle.answer_type.as_ref().expect(":answer")),
        ("(| Landed Refused)", vec![("Landed", None), ("Refused", None)]),
        "`|` は構文なので名に数えない"
    );
    assert_eq!(settle.effects, None);
}

#[test]
fn defrecord_field_annotations_are_param_types() {
    let file = index(SAMPLE);
    let record = def(&file, "IntakeSettlement", "defrecord");
    let params: Vec<(&str, (&str, Vec<(&str, Option<&str>)>))> =
        record.param_types.iter().map(|p| (p.name.as_str(), note(&p.type_note))).collect();
    assert_eq!(
        params,
        vec![
            ("request-id", ("str", vec![("str", None)])),
            ("count", ("(| int None)", vec![("int", None), ("None", None)])),
        ]
    );
}

#[test]
fn effect_clauses_name_the_effect_they_handle() {
    let file = index(SAMPLE);
    let handles = |name: &str| def(&file, name, "effect-clause").handles.as_ref().map(|h| (h.name.as_str(), h.target.as_deref()));
    assert_eq!(handles("ReadInput"), Some(("ReadInput", Some("controllers.messaging.intent.conversation_input.ReadInput"))));
    assert_eq!(handles("Settle"), Some(("Settle", Some("pkg.sample.Settle"))), "同じ file の defeffect");
    assert_eq!(handles("Unknown"), Some(("Unknown", None)), "import も定義も無い effect は target なし");
    // 解く handler = handles の target が一致する effect 節の container
    let handler_of_settle: Vec<&str> = file
        .definitions
        .iter()
        .filter(|d| d.handles.as_ref().and_then(|h| h.target.as_deref()) == Some(def(&file, "Settle", "defeffect").qualified_name.as_str()))
        .filter_map(|d| d.container.as_deref())
        .collect();
    assert_eq!(handler_of_settle, vec!["store"]);
}

/// 定義の引数の型を (名, 型の綴り) の列にする。
fn typed(definition: &Definition) -> Vec<(&str, &str)> {
    definition.param_types.iter().map(|p| (p.name.as_str(), p.type_note.text.as_str())).collect()
}

const CLASSES: &str = r#"(import dataclasses [dataclass field])

(defclass [(dataclass :frozen True)] PlacedVersion []
  "置いた版。"
  (#^ str ref)
  (#^ (| str None) request-id)
  #^ int count
  (setv #^ bool ok True)
  (setv plain 1)
  (defn [staticmethod] make [] 1))

(defclass [dataclass
           (field   :default   "a  b"
                    :repr False)] Wide [] #^ str x)

(defclass Plain [Base] "素の class。" (defn run [self] 1))

(defn [functools.cache] cached [x] x)
(defn undecorated [x] x)
(defrecord Row #^ str key)
"#;

#[test]
fn defclass_field_annotations_are_param_types_like_defrecord() {
    let file = index(CLASSES);
    assert_eq!(
        typed(def(&file, "PlacedVersion", "defclass")),
        vec![("ref", "str"), ("request-id", "(| str None)"), ("count", "int"), ("ok", "bool")],
        "括弧つき・裸・setv の注記の欄を書いた順に。注記の無い setv は型なし(欄の名だけ)"
    );
    let fields: Vec<&str> = file
        .definitions
        .iter()
        .filter(|d| d.container.as_deref() == Some("PlacedVersion") && d.kind.as_str() == "field")
        .map(|d| d.name.as_str())
        .collect();
    assert_eq!(fields, vec!["ref", "request-id", "count", "ok", "plain"], "欄の定義は今までどおり");
    let request_id = &def(&file, "PlacedVersion", "defclass").param_types[1].type_note;
    assert_eq!(refs(&request_id.names), vec![("str", None), ("None", None)]);
    assert!(def(&file, "Plain", "defclass").param_types.is_empty());
}

#[test]
fn decorators_are_written_spellings_without_outer_parens() {
    let file = index(CLASSES);
    assert_eq!(def(&file, "PlacedVersion", "defclass").decorators, vec!["dataclass :frozen True"]);
    assert_eq!(
        def(&file, "Wide", "defclass").decorators,
        vec!["dataclass", r#"field :default "a  b" :repr False"#],
        "文字列の外の空白の連なり(改行を含む)は 1 つに詰め、文字列の中はそのまま"
    );
    assert_eq!(def(&file, "make", "method").decorators, vec!["staticmethod"], "method の decorator も積む");
    assert_eq!(def(&file, "cached", "defn").decorators, vec!["functools.cache"]);
    for (name, kind) in [("Plain", "defclass"), ("undecorated", "defn"), ("Row", "defrecord"), ("run", "method")] {
        assert!(def(&file, name, kind).decorators.is_empty(), "{kind} {name} は decorator なし = 空の列");
    }
}
