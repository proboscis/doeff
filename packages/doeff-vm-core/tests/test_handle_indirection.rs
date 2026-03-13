use std::collections::HashSet;

use doeff_vm_core::handle::{Handle, HandleToken};

#[derive(Debug)]
struct DummyTag;

#[derive(Debug)]
struct OtherTag;

#[derive(Debug)]
struct DummyToken {
    stable_id: u64,
    label: &'static str,
}

impl HandleToken for DummyToken {
    fn stable_id(&self) -> u64 {
        self.stable_id
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }

    fn into_any(self: Box<Self>) -> Box<dyn std::any::Any> {
        self
    }
}

#[test]
fn handle_clone_hash_roundtrip() {
    let handle: Handle<DummyTag> = Handle::from_token(DummyToken {
        stable_id: 7,
        label: "sentinel",
    });
    let cloned = handle.clone();

    assert_eq!(handle, cloned);
    assert_eq!(handle.stable_id(), 7);
    assert_eq!(
        cloned
            .downcast_ref::<DummyToken>()
            .expect("token downcast should succeed")
            .label,
        "sentinel"
    );

    let mut seen = HashSet::new();
    seen.insert(handle);
    assert!(seen.contains(&cloned));
}

#[test]
fn handle_retag_preserves_identity() {
    let handle: Handle<DummyTag> = Handle::from_token(DummyToken {
        stable_id: 11,
        label: "value",
    });
    let retagged: Handle<OtherTag> = handle.retag();

    assert_eq!(retagged.stable_id(), 11);
    assert_eq!(
        retagged
            .downcast_ref::<DummyToken>()
            .expect("retagged token downcast should succeed")
            .label,
        "value"
    );
}
