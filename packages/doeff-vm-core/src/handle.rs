use std::any::Any;
use std::fmt;
use std::hash::{Hash, Hasher};
use std::marker::PhantomData;
use std::sync::Arc;

pub trait HandleToken: fmt::Debug + Send + Sync + 'static {
    fn stable_id(&self) -> u64;
    fn as_any(&self) -> &dyn Any;
    fn into_any(self: Box<Self>) -> Box<dyn Any>;
}

#[derive(Debug)]
struct HandleInner {
    token: Box<dyn HandleToken>,
}

impl HandleInner {
    fn stable_id(&self) -> u64 {
        self.token.stable_id()
    }

    fn downcast_ref<U: 'static>(&self) -> Option<&U> {
        self.token.as_any().downcast_ref::<U>()
    }
}

pub struct Handle<T> {
    inner: Arc<HandleInner>,
    marker: PhantomData<fn(T) -> T>,
}

impl<T> Handle<T> {
    fn new(inner: Arc<HandleInner>) -> Self {
        Self {
            inner,
            marker: PhantomData,
        }
    }

    pub fn from_token(token: impl HandleToken) -> Self {
        Self::new(Arc::new(HandleInner {
            token: Box::new(token),
        }))
    }

    pub fn stable_id(&self) -> u64 {
        self.inner.stable_id()
    }

    pub fn downcast_ref<U: 'static>(&self) -> Option<&U> {
        self.inner.downcast_ref::<U>()
    }

    pub fn retag<U>(&self) -> Handle<U> {
        Handle::new(Arc::clone(&self.inner))
    }

    pub(crate) fn try_unwrap_token(self) -> Result<Box<dyn HandleToken>, Self> {
        match Arc::try_unwrap(self.inner) {
            Ok(inner) => Ok(inner.token),
            Err(inner) => Err(Self::new(inner)),
        }
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
