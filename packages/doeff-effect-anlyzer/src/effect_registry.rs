pub struct EffectRegistry;

impl EffectRegistry {
    pub fn default() -> Self {
        Self
    }

    pub fn classify_call(&self, call_text: &str) -> Option<String> {
        let trimmed = call_text.trim();

        if let Some(arg) = Self::match_prefix_literal(trimmed, "ask") {
            return Some(format!("ask:{arg}"));
        }

        if let Some(arg) = Self::match_prefix_literal(trimmed, "emit") {
            return Some(format!("emit:{arg}"));
        }

        if let Some(arg) = Self::match_prefix_literal(trimmed, "log") {
            return Some(format!("log:{arg}"));
        }

        if trimmed.starts_with("get_state(") {
            return Some("state:get".to_string());
        }

        if let Some(arg) = Self::match_prefix_literal(trimmed, "set_state") {
            return Some(format!("state:set:{arg}"));
        }

        if trimmed.starts_with("read(") {
            return Some("io:read".to_string());
        }

        if trimmed.starts_with("write(") {
            return Some("io:write".to_string());
        }

        None
    }

    fn match_prefix_literal(text: &str, prefix: &str) -> Option<String> {
        if !text.starts_with(prefix) {
            return None;
        }

        let remainder = text.strip_prefix(prefix)?;
        let remainder = remainder.trim_start();
        if !remainder.starts_with('(') {
            return None;
        }

        let inside = remainder.strip_prefix('(')?.trim_start();
        if let Some(rest) = inside.strip_prefix('"') {
            let end_quote = rest.find('"')?;
            return Some(rest[..end_quote].to_string());
        }

        None
    }
}
