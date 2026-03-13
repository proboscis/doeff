impl VM {
    fn bad_trace_inline_emit(&mut self, dispatch_id: DispatchId) {
        self.trace_state.emit_dispatch_started(
            dispatch_id,
            "effect".to_string(),
            false,
            None,
            "handler".to_string(),
            HandlerKind::Python,
            None,
            None,
            vec![],
            None,
            None,
            None,
            None,
        );
    }
}
