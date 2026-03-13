use std::any::Any;
use std::fmt;
use std::hash::{Hash, Hasher};
use std::marker::PhantomData;
use std::sync::Arc;

pub trait HandleToken: fmt::Debug + Send + Sync + 'static {
    fn stable_id(&self) -> u64;
    fn as_any(&self) -> &dyn Any;
}

pub struct Handle<T> {
    inner: Arc<dyn HandleToken>,
    marker: PhantomData<fn() -> T>,
}

impl<T> Handle<T> {
    pub fn new(inner: Arc<dyn HandleToken>) -> Self {
        Self {
            inner,
            marker: PhantomData,
        }
    }

    pub fn from_token(token: impl HandleToken) -> Self {
        Self::new(Arc::new(token))
    }

    pub fn stable_id(&self) -> u64 {
        self.inner.stable_id()
    }

    pub fn downcast_ref<U: 'static>(&self) -> Option<&U> {
        self.inner.as_any().downcast_ref::<U>()
    }

    pub fn retag<U>(&self) -> Handle<U> {
        Handle::new(Arc::clone(&self.inner))
    }
}

impl<T> Clone for Handle<T> {
    fn clone(&self) -> Self {
        Self::new(Arc::clone(&self.inner))
    }
}

impl<T> fmt::Debug for Handle<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Handle")
            .field("stable_id", &self.stable_id())
            .finish()
    }
}

impl<T> PartialEq for Handle<T> {
    fn eq(&self, other: &Self) -> bool {
        self.stable_id() == other.stable_id()
    }
}

impl<T> Eq for Handle<T> {}

impl<T> Hash for Handle<T> {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.stable_id().hash(state);
    }
}
