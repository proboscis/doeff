//! Semgrep hit fixture: wait_for_repl_idle readiness discarded.
//!
//! Reproduces the pre-fix session_launch call shape (issue
//! agentd-session-registration-after-ready-gate): the `?;` statement
//! discards the readiness verdict, so a budget-exhausted launch pastes the
//! prompt into whatever unknown screen blocked startup and never
//! transitions the registered BOOTING row to terminal failed. Guarded by
//! doeff-agentd-repl-ready-wait-must-not-discard-readiness.

fn launch_fixture(config: &Config, pane_id: &str) -> Result<()> {
    wait_for_repl_idle(config, pane_id, config.repl_idle_max_wait)?;
    Ok(())
}
