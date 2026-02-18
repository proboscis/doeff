//! Unified traceback capture and assembly types.

use crate::ids::DispatchId;

/// Unique identifier for a program frame instance.
pub type FrameId = u64;

/// Handler implementation kind for trace output.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HandlerKind {
    Python,
    RustBuiltin,
}

/// Snapshot entry for a handler visible to a dispatch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HandlerSnapshotEntry {
    pub name: String,
    pub handler_idx: usize,
}

/// Per-handler status marker for default traceback rendering.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HandlerStatus {
    Delegated,
    Resumed,
    Threw,
    Transferred,
    Active,
    Pending,
}

/// Handler stack entry included in assembled TraceDispatch payloads.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HandlerDispatchEntry {
    pub name: String,
    pub status: HandlerStatus,
}

/// Final action produced by a handler for a dispatch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HandlerAction {
    Resumed { value_repr: Option<String> },
    Transferred { value_repr: Option<String> },
    Returned { value_repr: Option<String> },
    Threw { exception_repr: Option<String> },
}

/// Low-level control-flow event appended by the VM.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CaptureEvent {
    FrameEntered {
        frame_id: FrameId,
        function_name: String,
        source_file: String,
        source_line: u32,
        args_repr: Option<String>,
    },
    FrameExited {
        function_name: String,
    },
    DispatchStarted {
        dispatch_id: DispatchId,
        effect_repr: String,
        handler_name: String,
        handler_idx: usize,
        handler_kind: HandlerKind,
        handler_source_file: Option<String>,
        handler_source_line: Option<u32>,
        handler_stack: Vec<HandlerSnapshotEntry>,
    },
    Delegated {
        dispatch_id: DispatchId,
        from_handler_name: String,
        to_handler_name: String,
        to_handler_kind: HandlerKind,
        to_handler_source_file: Option<String>,
        to_handler_source_line: Option<u32>,
    },
    HandlerCompleted {
        dispatch_id: DispatchId,
        handler_name: String,
        action: HandlerAction,
    },
    Resumed {
        dispatch_id: DispatchId,
        handler_name: String,
        value_repr: Option<String>,
        resumed_function_name: String,
        source_file: String,
        source_line: u32,
    },
    Transferred {
        dispatch_id: DispatchId,
        handler_name: String,
        value_repr: Option<String>,
        resumed_function_name: String,
        source_file: String,
        source_line: u32,
    },
}

/// Delegation hop for a dispatch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DelegationEntry {
    pub handler_name: String,
    pub handler_kind: HandlerKind,
    pub handler_source_file: Option<String>,
    pub handler_source_line: Option<u32>,
}

/// Dispatch completion status.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DispatchAction {
    Active,
    Resumed,
    Transferred,
    Returned,
    Threw,
}

/// Assembled VM-level trace entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TraceEntry {
    Frame {
        frame_id: FrameId,
        function_name: String,
        source_file: String,
        source_line: u32,
        args_repr: Option<String>,
    },
    Dispatch {
        dispatch_id: DispatchId,
        effect_repr: String,
        handler_name: String,
        handler_kind: HandlerKind,
        handler_source_file: Option<String>,
        handler_source_line: Option<u32>,
        delegation_chain: Vec<DelegationEntry>,
        handler_stack: Vec<HandlerDispatchEntry>,
        action: DispatchAction,
        value_repr: Option<String>,
        exception_repr: Option<String>,
    },
    ResumePoint {
        dispatch_id: DispatchId,
        handler_name: String,
        resumed_function_name: String,
        source_file: String,
        source_line: u32,
        value_repr: Option<String>,
    },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_dispatch_started_captures_full_handler_stack() {
        let dispatch_id = DispatchId::fresh();
        let event = CaptureEvent::DispatchStarted {
            dispatch_id,
            effect_repr: "Ask('x')".to_string(),
            handler_name: "ReaderHandler".to_string(),
            handler_idx: 1,
            handler_kind: HandlerKind::RustBuiltin,
            handler_source_file: None,
            handler_source_line: None,
            handler_stack: vec![
                HandlerSnapshotEntry {
                    name: "LazyAskHandler".to_string(),
                    handler_idx: 0,
                },
                HandlerSnapshotEntry {
                    name: "ReaderHandler".to_string(),
                    handler_idx: 1,
                },
                HandlerSnapshotEntry {
                    name: "sync_await_handler".to_string(),
                    handler_idx: 2,
                },
            ],
        };

        let CaptureEvent::DispatchStarted { handler_stack, .. } = event else {
            panic!("expected DispatchStarted");
        };
        assert_eq!(handler_stack.len(), 3);
        assert_eq!(handler_stack[0].name, "LazyAskHandler");
        assert_eq!(handler_stack[1].name, "ReaderHandler");
        assert_eq!(handler_stack[2].name, "sync_await_handler");
    }

    #[test]
    fn test_handler_status_enum_values() {
        let statuses = [
            HandlerStatus::Delegated,
            HandlerStatus::Resumed,
            HandlerStatus::Threw,
            HandlerStatus::Transferred,
            HandlerStatus::Active,
            HandlerStatus::Pending,
        ];
        assert_eq!(statuses.len(), 6);
    }

    #[test]
    fn test_delegation_entry_has_status() {
        let entry = HandlerDispatchEntry {
            name: "ReaderHandler".to_string(),
            status: HandlerStatus::Resumed,
        };
        assert_eq!(entry.name, "ReaderHandler");
        assert_eq!(entry.status, HandlerStatus::Resumed);
    }
}
