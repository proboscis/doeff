//! Handler types for effect handling.

use pyo3::prelude::*;

use crate::continuation::Continuation;
use crate::effect::Effect;
use crate::ids::SegmentId;
use crate::step::{HandlerContext, PythonCall};
use crate::value::Value;
use crate::vm::RustStore;

#[derive(Debug, Clone)]
pub enum Handler {
    Stdlib(StdlibHandler),
    Python(Py<PyAny>),
}

#[derive(Debug, Clone)]
pub enum StdlibHandler {
    State,
    Reader,
    Writer,
}

#[derive(Debug, Clone)]
pub struct HandlerEntry {
    pub handler: Handler,
    pub prompt_seg_id: SegmentId,
}

impl HandlerEntry {
    pub fn new(handler: Handler, prompt_seg_id: SegmentId) -> Self {
        HandlerEntry {
            handler,
            prompt_seg_id,
        }
    }
}

pub enum HandlerAction {
    Resume {
        k: Continuation,
        value: Value,
    },
    Transfer {
        k: Continuation,
        value: Value,
    },
    Return {
        value: Value,
    },
    NeedsPython {
        handler: StdlibHandler,
        call: PythonCall,
        k: Continuation,
        context: HandlerContext,
    },
}

impl StdlibHandler {
    pub fn can_handle(&self, effect: &Effect) -> bool {
        match (self, effect) {
            (StdlibHandler::State, Effect::Get { .. }) => true,
            (StdlibHandler::State, Effect::Put { .. }) => true,
            (StdlibHandler::State, Effect::Modify { .. }) => true,
            (StdlibHandler::Reader, Effect::Ask { .. }) => true,
            (StdlibHandler::Writer, Effect::Tell { .. }) => true,
            _ => false,
        }
    }

    pub fn handle(&self, effect: &Effect, k: Continuation, store: &mut RustStore) -> HandlerAction {
        match self {
            StdlibHandler::State => handle_state_effect(effect, k, store),
            StdlibHandler::Reader => handle_reader_effect(effect, k, store),
            StdlibHandler::Writer => handle_writer_effect(effect, k, store),
        }
    }

    pub fn continue_after_python(
        &self,
        result: Value,
        context: HandlerContext,
        k: Continuation,
        store: &mut RustStore,
    ) -> HandlerAction {
        match context {
            HandlerContext::ModifyPending { key, old_value } => {
                store.put(key, result);
                HandlerAction::Resume {
                    k,
                    value: old_value,
                }
            }
        }
    }
}

fn handle_state_effect(effect: &Effect, k: Continuation, store: &mut RustStore) -> HandlerAction {
    match effect {
        Effect::Get { key } => {
            let value = store.get(key).cloned().unwrap_or(Value::None);
            HandlerAction::Resume { k, value }
        }

        Effect::Put { key, value } => {
            store.put(key.clone(), value.clone());
            HandlerAction::Resume {
                k,
                value: Value::Unit,
            }
        }

        Effect::Modify { key, modifier } => {
            let old_value = store.get(key).cloned().unwrap_or(Value::None);
            HandlerAction::NeedsPython {
                handler: StdlibHandler::State,
                call: PythonCall::CallFunc {
                    func: modifier.clone(),
                    args: vec![old_value.clone()],
                },
                k,
                context: HandlerContext::ModifyPending {
                    key: key.clone(),
                    old_value,
                },
            }
        }

        _ => panic!("State handler cannot handle {:?}", effect),
    }
}

fn handle_reader_effect(effect: &Effect, k: Continuation, store: &mut RustStore) -> HandlerAction {
    match effect {
        Effect::Ask { key } => {
            let value = store.ask(key).cloned().unwrap_or(Value::None);
            HandlerAction::Resume { k, value }
        }
        _ => panic!("Reader handler cannot handle {:?}", effect),
    }
}

fn handle_writer_effect(effect: &Effect, k: Continuation, store: &mut RustStore) -> HandlerAction {
    match effect {
        Effect::Tell { message } => {
            store.tell(message.clone());
            HandlerAction::Resume {
                k,
                value: Value::Unit,
            }
        }
        _ => panic!("Writer handler cannot handle {:?}", effect),
    }
}

impl Handler {
    pub fn can_handle(&self, effect: &Effect) -> bool {
        match self {
            Handler::Stdlib(h) => h.can_handle(effect),
            Handler::Python(_) => true,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::Marker;
    use crate::segment::Segment;

    fn make_test_continuation() -> Continuation {
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![marker]);
        let seg_id = SegmentId::from_index(0);
        Continuation::capture(&seg, seg_id, None)
    }

    #[test]
    fn test_handler_entry_creation() {
        let handler = Handler::Stdlib(StdlibHandler::State);
        let prompt_seg_id = SegmentId::from_index(5);
        let entry = HandlerEntry::new(handler, prompt_seg_id);

        assert_eq!(entry.prompt_seg_id, prompt_seg_id);
        assert!(matches!(
            entry.handler,
            Handler::Stdlib(StdlibHandler::State)
        ));
    }

    #[test]
    fn test_state_handler_get() {
        let mut store = RustStore::new();
        store.put("key".to_string(), Value::Int(42));

        let k = make_test_continuation();
        let effect = Effect::Get {
            key: "key".to_string(),
        };

        let action = StdlibHandler::State.handle(&effect, k, &mut store);
        match action {
            HandlerAction::Resume { value, .. } => {
                assert_eq!(value.as_int(), Some(42));
            }
            _ => panic!("Expected Resume"),
        }
    }

    #[test]
    fn test_state_handler_put() {
        let mut store = RustStore::new();
        let k = make_test_continuation();
        let effect = Effect::Put {
            key: "key".to_string(),
            value: Value::Int(99),
        };

        let action = StdlibHandler::State.handle(&effect, k, &mut store);
        assert!(matches!(
            action,
            HandlerAction::Resume {
                value: Value::Unit,
                ..
            }
        ));
        assert_eq!(store.get("key").unwrap().as_int(), Some(99));
    }

    #[test]
    fn test_reader_handler_ask() {
        let mut store = RustStore::new();
        store
            .env
            .insert("config".to_string(), Value::String("value".to_string()));

        let k = make_test_continuation();
        let effect = Effect::Ask {
            key: "config".to_string(),
        };

        let action = StdlibHandler::Reader.handle(&effect, k, &mut store);
        match action {
            HandlerAction::Resume { value, .. } => {
                assert_eq!(value.as_str(), Some("value"));
            }
            _ => panic!("Expected Resume"),
        }
    }

    #[test]
    fn test_writer_handler_tell() {
        let mut store = RustStore::new();
        let k = make_test_continuation();
        let effect = Effect::Tell {
            message: Value::String("log".to_string()),
        };

        let action = StdlibHandler::Writer.handle(&effect, k, &mut store);
        assert!(matches!(
            action,
            HandlerAction::Resume {
                value: Value::Unit,
                ..
            }
        ));
        assert_eq!(store.logs().len(), 1);
    }

    #[test]
    fn test_handler_can_handle() {
        assert!(StdlibHandler::State.can_handle(&Effect::Get {
            key: "x".to_string()
        }));
        assert!(StdlibHandler::State.can_handle(&Effect::Put {
            key: "x".to_string(),
            value: Value::Unit
        }));
        assert!(StdlibHandler::Reader.can_handle(&Effect::Ask {
            key: "x".to_string()
        }));
        assert!(StdlibHandler::Writer.can_handle(&Effect::Tell {
            message: Value::Unit
        }));

        assert!(!StdlibHandler::State.can_handle(&Effect::Ask {
            key: "x".to_string()
        }));
        assert!(!StdlibHandler::Reader.can_handle(&Effect::Get {
            key: "x".to_string()
        }));
    }
}
