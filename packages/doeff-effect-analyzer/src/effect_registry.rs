//! The effect vocabulary SEDA recognises, shared by the Python and Hy front ends.
//!
//! The vocabulary follows the constructors doeff exports today
//! (`doeff_core_effects.effects`, re-exported from `doeff`): `Ask(key)`,
//! `Tell(message)`, `Get(key)`, `Put(key, value)` and `slog(msg, **kw)`.
//! Effect keys are `<kind>:<first argument>` when the first argument is a string
//! literal and `<kind>:<dynamic>` when it is not, so a non-literal argument is
//! reported instead of being dropped.

/// How an effect constructor turns into an effect key.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum KeyShape {
    /// `<kind>:<first argument>` (or `<kind>:<dynamic>`).
    FirstArgument,
    /// `<kind>` alone — the arguments are payload, not an identity.
    Bare,
}

/// Constructor name → (effect kind, key shape).
const VOCABULARY: &[(&str, &str, KeyShape)] = &[
    ("Ask", "ask", KeyShape::FirstArgument),
    ("Tell", "tell", KeyShape::FirstArgument),
    ("Get", "get", KeyShape::FirstArgument),
    ("Put", "put", KeyShape::FirstArgument),
    ("slog", "slog", KeyShape::Bare),
    ("Slog", "slog", KeyShape::Bare),
    ("SlogEffect", "slog", KeyShape::Bare),
];

const DYNAMIC: &str = "<dynamic>";

/// The first argument of an effect constructor call, as far as SEDA can read it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FirstArgument<'a> {
    /// A string literal (the literal's contents).
    Literal(&'a str),
    /// Any other expression.
    Dynamic,
    /// The call has no arguments.
    Missing,
}

pub struct EffectRegistry;

impl EffectRegistry {
    pub fn default() -> Self {
        Self
    }

    /// The effect key of a call to `constructor`, or `None` when the name is not
    /// part of the vocabulary.
    pub fn key_for(&self, constructor: &str, first: FirstArgument<'_>) -> Option<String> {
        let (_, kind, shape) = VOCABULARY
            .iter()
            .find(|(name, _, _)| *name == constructor)?;
        Some(match (shape, first) {
            (KeyShape::Bare, _) => (*kind).to_string(),
            (KeyShape::FirstArgument, FirstArgument::Literal(value)) => format!("{kind}:{value}"),
            (KeyShape::FirstArgument, FirstArgument::Dynamic | FirstArgument::Missing) => {
                format!("{kind}:{DYNAMIC}")
            }
        })
    }

    /// Classify the source text of a Python call expression (`Ask("alpha")`).
    pub fn classify_call(&self, call_text: &str) -> Option<String> {
        let trimmed = call_text.trim();
        let open = trimmed.find('(')?;
        let callee = trimmed[..open].trim_end();
        if callee.is_empty() || !callee.chars().all(|c| c.is_alphanumeric() || c == '_') {
            return None;
        }
        let arguments = trimmed[open + 1..].trim_start();
        self.key_for(callee, Self::first_python_argument(arguments))
    }

    fn first_python_argument(arguments: &str) -> FirstArgument<'_> {
        if arguments.starts_with(')') {
            return FirstArgument::Missing;
        }
        for quote in ['"', '\''] {
            if let Some(rest) = arguments.strip_prefix(quote) {
                let Some(end) = rest.find(quote) else {
                    return FirstArgument::Dynamic;
                };
                let after = rest[end + 1..].trim_start();
                // `"a" + x` or `"a".format(...)` is not a literal key.
                if after.starts_with(',') || after.starts_with(')') {
                    return FirstArgument::Literal(&rest[..end]);
                }
                return FirstArgument::Dynamic;
            }
        }
        FirstArgument::Dynamic
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn classify(text: &str) -> Option<String> {
        EffectRegistry::default().classify_call(text)
    }

    #[test]
    fn current_constructors_are_recognised() {
        assert_eq!(classify(r#"Ask("alpha")"#).as_deref(), Some("ask:alpha"));
        assert_eq!(classify(r#"Tell('beta')"#).as_deref(), Some("tell:beta"));
        assert_eq!(classify(r#"Get("counter")"#).as_deref(), Some("get:counter"));
        assert_eq!(classify(r#"Put("counter", 1)"#).as_deref(), Some("put:counter"));
        assert_eq!(classify(r#"slog("msg", level=1)"#).as_deref(), Some("slog"));
    }

    #[test]
    fn non_literal_arguments_are_reported_as_dynamic() {
        assert_eq!(classify("Ask(key)").as_deref(), Some("ask:<dynamic>"));
        assert_eq!(classify(r#"Tell(f"x{y}")"#).as_deref(), Some("tell:<dynamic>"));
        assert_eq!(classify(r#"Tell("a" + b)"#).as_deref(), Some("tell:<dynamic>"));
    }

    #[test]
    fn other_calls_are_not_effects() {
        assert_eq!(classify("helper_alpha()"), None);
        assert_eq!(classify(r#"obj.Ask("alpha")"#), None);
        assert_eq!(classify(r#"ask("alpha")"#), None);
    }
}
