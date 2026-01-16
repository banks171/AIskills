---
name: rust-development
description: Rust development patterns for systems programming, error handling, async code, and performance optimization. Use when writing Rust code, designing APIs, or optimizing performance.
---

# Rust Development Patterns

Production patterns for Rust systems programming with error handling, async, and performance focus.

## When to Use

Triggers: "Rust", "Cargo", "ownership", "借用", "lifetime", "async rust", "tokio", "系统编程"

## Core Patterns

### 1. Project Structure

```
project/
├── Cargo.toml
├── Cargo.lock
├── src/
│   ├── main.rs           # Binary entry
│   ├── lib.rs            # Library root
│   ├── config.rs         # Configuration
│   ├── error.rs          # Error types
│   └── modules/
│       ├── mod.rs
│       └── feature.rs
├── tests/
│   └── integration_test.rs
├── benches/
│   └── benchmark.rs
└── examples/
    └── basic_usage.rs
```

```toml
# Cargo.toml
[package]
name = "myproject"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
anyhow = "1"
thiserror = "1"
tracing = "0.1"

[dev-dependencies]
tokio-test = "0.4"
criterion = "0.5"

[[bench]]
name = "benchmark"
harness = false
```

### 2. Error Handling

```rust
use thiserror::Error;

// Define error types with thiserror
#[derive(Error, Debug)]
pub enum AppError {
    #[error("Database error: {0}")]
    Database(#[from] sqlx::Error),

    #[error("Validation error: {field} - {message}")]
    Validation { field: String, message: String },

    #[error("Not found: {0}")]
    NotFound(String),

    #[error("Internal error")]
    Internal(#[source] anyhow::Error),
}

// Result type alias
pub type Result<T> = std::result::Result<T, AppError>;

// Usage
fn get_user(id: u64) -> Result<User> {
    let user = db.find_user(id)
        .map_err(AppError::Database)?
        .ok_or_else(|| AppError::NotFound(format!("User {id}")))?;

    Ok(user)
}

// With anyhow for applications
use anyhow::{Context, Result};

fn load_config() -> Result<Config> {
    let content = std::fs::read_to_string("config.toml")
        .context("Failed to read config file")?;

    let config: Config = toml::from_str(&content)
        .context("Failed to parse config")?;

    Ok(config)
}
```

### 3. Struct Patterns

```rust
use serde::{Deserialize, Serialize};

// Builder pattern
#[derive(Debug, Clone)]
pub struct Client {
    host: String,
    port: u16,
    timeout: Duration,
    max_retries: u32,
}

#[derive(Default)]
pub struct ClientBuilder {
    host: Option<String>,
    port: Option<u16>,
    timeout: Option<Duration>,
    max_retries: Option<u32>,
}

impl ClientBuilder {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn host(mut self, host: impl Into<String>) -> Self {
        self.host = Some(host.into());
        self
    }

    pub fn port(mut self, port: u16) -> Self {
        self.port = Some(port);
        self
    }

    pub fn timeout(mut self, timeout: Duration) -> Self {
        self.timeout = Some(timeout);
        self
    }

    pub fn build(self) -> Result<Client, &'static str> {
        Ok(Client {
            host: self.host.ok_or("host is required")?,
            port: self.port.unwrap_or(8080),
            timeout: self.timeout.unwrap_or(Duration::from_secs(30)),
            max_retries: self.max_retries.unwrap_or(3),
        })
    }
}

// Usage
let client = ClientBuilder::new()
    .host("localhost")
    .port(9000)
    .build()?;

// Newtype pattern for type safety
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct UserId(u64);

impl UserId {
    pub fn new(id: u64) -> Self {
        Self(id)
    }

    pub fn into_inner(self) -> u64 {
        self.0
    }
}
```

### 4. Traits and Generics

```rust
// Define trait
pub trait Repository<T> {
    type Error;

    fn find(&self, id: u64) -> Result<Option<T>, Self::Error>;
    fn save(&self, item: &T) -> Result<(), Self::Error>;
    fn delete(&self, id: u64) -> Result<(), Self::Error>;
}

// Implement for specific type
impl Repository<User> for PostgresRepo {
    type Error = sqlx::Error;

    fn find(&self, id: u64) -> Result<Option<User>, Self::Error> {
        // Implementation
    }

    fn save(&self, user: &User) -> Result<(), Self::Error> {
        // Implementation
    }

    fn delete(&self, id: u64) -> Result<(), Self::Error> {
        // Implementation
    }
}

// Generic function with trait bounds
fn process<T, R>(repo: &R, id: u64) -> Result<T, R::Error>
where
    R: Repository<T>,
    T: Clone + Debug,
{
    let item = repo.find(id)?
        .ok_or_else(|| /* error */)?;
    Ok(item.clone())
}

// Extension trait
pub trait StringExt {
    fn truncate_words(&self, max_words: usize) -> String;
}

impl StringExt for str {
    fn truncate_words(&self, max_words: usize) -> String {
        self.split_whitespace()
            .take(max_words)
            .collect::<Vec<_>>()
            .join(" ")
    }
}
```

### 5. Async Patterns

```rust
use tokio::sync::{mpsc, oneshot, Mutex};
use std::sync::Arc;

// Async function
async fn fetch_data(url: &str) -> Result<String> {
    let response = reqwest::get(url).await?;
    let body = response.text().await?;
    Ok(body)
}

// Concurrent execution
async fn fetch_all(urls: Vec<String>) -> Vec<Result<String>> {
    let futures: Vec<_> = urls.iter()
        .map(|url| fetch_data(url))
        .collect();

    futures::future::join_all(futures).await
}

// Select pattern
use tokio::select;

async fn timeout_fetch(url: &str, timeout: Duration) -> Result<String> {
    select! {
        result = fetch_data(url) => result,
        _ = tokio::time::sleep(timeout) => {
            Err(anyhow::anyhow!("Request timed out"))
        }
    }
}

// Channel communication
async fn worker_pattern() {
    let (tx, mut rx) = mpsc::channel::<Task>(100);

    // Spawn worker
    tokio::spawn(async move {
        while let Some(task) = rx.recv().await {
            process_task(task).await;
        }
    });

    // Send tasks
    tx.send(Task::new()).await?;
}

// Shared state
struct AppState {
    cache: Mutex<HashMap<String, String>>,
}

async fn with_shared_state(state: Arc<AppState>, key: String) -> Option<String> {
    let cache = state.cache.lock().await;
    cache.get(&key).cloned()
}
```

### 6. Iterators

```rust
// Iterator chain
let result: Vec<_> = items.iter()
    .filter(|item| item.is_active())
    .map(|item| item.transform())
    .take(10)
    .collect();

// Custom iterator
struct Counter {
    count: usize,
    max: usize,
}

impl Iterator for Counter {
    type Item = usize;

    fn next(&mut self) -> Option<Self::Item> {
        if self.count < self.max {
            self.count += 1;
            Some(self.count)
        } else {
            None
        }
    }
}

// Efficient iteration patterns
// Use iter() for borrowing, into_iter() for ownership
for item in &items { }      // Borrows
for item in items { }       // Takes ownership

// Collect with type hint
let map: HashMap<_, _> = pairs.into_iter().collect();

// Parallel iteration with rayon
use rayon::prelude::*;

let results: Vec<_> = items.par_iter()
    .map(|item| expensive_computation(item))
    .collect();
```

### 7. Memory Patterns

```rust
use std::borrow::Cow;

// Copy vs Clone
#[derive(Copy, Clone)]  // For small, stack-allocated types
struct Point { x: f64, y: f64 }

#[derive(Clone)]  // For heap-allocated types
struct Buffer { data: Vec<u8> }

// Cow (Clone on Write)
fn process_string(input: &str) -> Cow<str> {
    if input.contains("replace_me") {
        Cow::Owned(input.replace("replace_me", "new_value"))
    } else {
        Cow::Borrowed(input)
    }
}

// Arc for shared ownership
use std::sync::Arc;

let shared = Arc::new(ExpensiveData::new());
let clone1 = Arc::clone(&shared);
let clone2 = Arc::clone(&shared);

// Box for heap allocation
let boxed: Box<dyn Trait> = Box::new(ConcreteType::new());

// Rc for single-threaded shared ownership
use std::rc::Rc;
let shared = Rc::new(data);
```

### 8. Testing

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic() {
        assert_eq!(add(2, 3), 5);
    }

    #[test]
    fn test_result() -> Result<()> {
        let result = parse_config("valid")?;
        assert!(result.is_valid);
        Ok(())
    }

    #[test]
    #[should_panic(expected = "invalid input")]
    fn test_panic() {
        process_invalid_input();
    }

    // Async test
    #[tokio::test]
    async fn test_async() {
        let result = fetch_data("http://test").await;
        assert!(result.is_ok());
    }

    // Parameterized test with macro
    macro_rules! test_cases {
        ($($name:ident: $input:expr => $expected:expr),* $(,)?) => {
            $(
                #[test]
                fn $name() {
                    assert_eq!(process($input), $expected);
                }
            )*
        };
    }

    test_cases! {
        case_1: 1 => 2,
        case_2: 2 => 4,
        case_3: 0 => 0,
    }
}

// Integration test (tests/integration_test.rs)
#[test]
fn integration_test() {
    let app = setup_app();
    let result = app.run();
    assert!(result.is_ok());
}
```

### 9. Performance

```rust
// Avoid allocations
fn process(data: &[u8]) -> &[u8] {
    // Return slice, not Vec
    &data[..10]
}

// Use capacity hints
let mut vec = Vec::with_capacity(1000);

// Inline small functions
#[inline]
fn small_helper(x: u32) -> u32 {
    x * 2
}

// Benchmarking with criterion
use criterion::{black_box, criterion_group, criterion_main, Criterion};

fn benchmark_function(c: &mut Criterion) {
    c.bench_function("my_function", |b| {
        b.iter(|| {
            my_function(black_box(100))
        })
    });
}

criterion_group!(benches, benchmark_function);
criterion_main!(benches);
```

## Ownership Quick Reference

| Operation | Ownership | Example |
|-----------|-----------|---------|
| Move | Transfers | `let b = a;` |
| Borrow (&T) | Temporary read | `fn read(&self)` |
| Mutable borrow (&mut T) | Temporary write | `fn modify(&mut self)` |
| Clone | Deep copy | `let b = a.clone();` |
| Copy | Implicit copy | `let b = a;` (for Copy types) |

## Common Crates

| Category | Crate | Purpose |
|----------|-------|---------|
| Async | `tokio` | Async runtime |
| Error | `anyhow`, `thiserror` | Error handling |
| Serialization | `serde` | Serialize/deserialize |
| HTTP | `reqwest`, `hyper` | HTTP client/server |
| CLI | `clap` | Argument parsing |
| Logging | `tracing` | Structured logging |
| Database | `sqlx`, `diesel` | Database access |

## Best Practices

1. **Prefer borrowing** - Avoid unnecessary clones
2. **Use Result** - No exceptions, explicit error handling
3. **Leverage types** - Newtypes for domain concepts
4. **Write tests** - Rust's type system helps but doesn't replace tests
5. **Profile first** - Don't optimize prematurely
6. **Read clippy** - `cargo clippy` catches common issues

## References

- [The Rust Book](https://doc.rust-lang.org/book/)
- [Rust by Example](https://doc.rust-lang.org/rust-by-example/)
- [Async Book](https://rust-lang.github.io/async-book/)
- [Rustonomicon](https://doc.rust-lang.org/nomicon/)
