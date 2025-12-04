//! Lint rules for doeff-linter

pub mod base;

// Rule implementations
pub mod doeff001_builtin_shadowing;
pub mod doeff002_mutable_attribute_naming;
pub mod doeff003_max_mutable_attributes;
pub mod doeff004_no_os_environ;
pub mod doeff005_no_setter_methods;
pub mod doeff006_no_tuple_returns;
pub mod doeff007_no_mutable_argument_mutations;
pub mod doeff008_no_dataclass_attribute_mutation;
pub mod doeff009_missing_return_type_annotation;
pub mod doeff010_test_file_placement;
pub mod doeff011_no_flag_arguments;
pub mod doeff012_no_append_loop;
pub mod doeff013_prefer_maybe_monad;
pub mod doeff014_no_try_except;
pub mod doeff015_no_zero_arg_program;
pub mod doeff016_no_relative_import;
pub mod doeff017_no_program_type_param;
pub mod doeff018_no_ask_in_try;
pub mod doeff019_no_ask_with_fallback;
pub mod doeff020_program_naming_convention;
pub mod doeff021_no_dunder_all;
pub mod doeff022_prefer_do_function;
pub mod doeff023_pipeline_marker;

use base::LintRule;
use std::collections::HashMap;

/// Get all available rules
pub fn get_all_rules() -> Vec<Box<dyn LintRule>> {
    vec![
        Box::new(doeff001_builtin_shadowing::BuiltinShadowingRule::new()),
        Box::new(doeff002_mutable_attribute_naming::MutableAttributeNamingRule::new()),
        Box::new(doeff003_max_mutable_attributes::MaxMutableAttributesRule::new()),
        Box::new(doeff004_no_os_environ::NoOsEnvironRule::new()),
        Box::new(doeff005_no_setter_methods::NoSetterMethodsRule::new()),
        Box::new(doeff006_no_tuple_returns::NoTupleReturnsRule::new()),
        Box::new(doeff007_no_mutable_argument_mutations::NoMutableArgumentMutationsRule::new()),
        Box::new(doeff008_no_dataclass_attribute_mutation::NoDataclassAttributeMutationRule::new()),
        Box::new(doeff009_missing_return_type_annotation::MissingReturnTypeAnnotationRule::new()),
        Box::new(doeff010_test_file_placement::TestFilePlacementRule::new()),
        Box::new(doeff011_no_flag_arguments::NoFlagArgumentsRule::new()),
        Box::new(doeff012_no_append_loop::NoAppendLoopRule::new()),
        Box::new(doeff013_prefer_maybe_monad::PreferMaybeMonadRule::new()),
        Box::new(doeff014_no_try_except::NoTryExceptRule::new()),
        Box::new(doeff015_no_zero_arg_program::NoZeroArgProgramRule::new()),
        Box::new(doeff016_no_relative_import::NoRelativeImportRule::new()),
        Box::new(doeff017_no_program_type_param::NoProgramTypeParamRule::new()),
        Box::new(doeff018_no_ask_in_try::NoAskInTryRule::new()),
        Box::new(doeff019_no_ask_with_fallback::NoAskWithFallbackRule::new()),
        Box::new(doeff020_program_naming_convention::ProgramNamingConventionRule::new()),
        Box::new(doeff021_no_dunder_all::NoDunderAllRule::new()),
        Box::new(doeff022_prefer_do_function::PreferDoFunctionRule::new()),
        Box::new(doeff023_pipeline_marker::PipelineMarkerRule::new()),
    ]
}

/// Get rules by ID for quick lookup
pub fn get_rules_by_id() -> HashMap<String, Box<dyn LintRule>> {
    get_all_rules()
        .into_iter()
        .map(|rule| (rule.rule_id().to_string(), rule))
        .collect()
}

/// Get all available rule IDs
pub fn get_all_rule_ids() -> Vec<String> {
    get_all_rules()
        .iter()
        .map(|rule| rule.rule_id().to_string())
        .collect()
}

/// Get rules filtered by enabled IDs
pub fn get_enabled_rules(enabled_ids: Option<&[String]>) -> Vec<Box<dyn LintRule>> {
    let all_rules = get_all_rules();

    match enabled_ids {
        Some(ids) => all_rules
            .into_iter()
            .filter(|rule| ids.contains(&rule.rule_id().to_string()))
            .collect(),
        None => all_rules,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_all_rules_loaded() {
        let rules = get_all_rules();
        assert_eq!(rules.len(), 23);

        let rule_ids: Vec<_> = rules.iter().map(|r| r.rule_id()).collect();
        assert!(rule_ids.contains(&"DOEFF001"));
        assert!(rule_ids.contains(&"DOEFF010"));
        assert!(rule_ids.contains(&"DOEFF011"));
        assert!(rule_ids.contains(&"DOEFF012"));
        assert!(rule_ids.contains(&"DOEFF013"));
        assert!(rule_ids.contains(&"DOEFF014"));
        assert!(rule_ids.contains(&"DOEFF015"));
        assert!(rule_ids.contains(&"DOEFF016"));
        assert!(rule_ids.contains(&"DOEFF017"));
        assert!(rule_ids.contains(&"DOEFF018"));
        assert!(rule_ids.contains(&"DOEFF019"));
        assert!(rule_ids.contains(&"DOEFF020"));
        assert!(rule_ids.contains(&"DOEFF021"));
        assert!(rule_ids.contains(&"DOEFF022"));
        assert!(rule_ids.contains(&"DOEFF023"));
    }

    #[test]
    fn test_get_enabled_rules() {
        let enabled = vec!["DOEFF001".to_string(), "DOEFF002".to_string()];
        let rules = get_enabled_rules(Some(&enabled));
        assert_eq!(rules.len(), 2);
    }
}



