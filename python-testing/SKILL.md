---
name: python-testing
description: Python testing patterns with pytest for unit tests, integration tests, fixtures, mocking, and test organization. Use when writing tests, setting up test infrastructure, or debugging test failures.
---

# Python Testing Patterns

Production patterns for pytest-based testing with fixtures, mocking, parametrization, and async support.

## When to Use

Triggers: "pytest", "test", "测试", "unit test", "integration test", "mock", "fixture", "coverage"

## Core Patterns

### 1. Test Structure

```python
# tests/conftest.py - Shared fixtures
import pytest
from unittest.mock import AsyncMock

@pytest.fixture
def sample_data():
    """Provide sample test data"""
    return {"id": 1, "name": "test", "value": 100}

@pytest.fixture
async def async_client():
    """Async HTTP client for API tests"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

# tests/unit/test_calculator.py
class TestCalculator:
    """Group related tests in classes"""

    def test_add_positive_numbers(self):
        assert add(2, 3) == 5

    def test_add_negative_numbers(self):
        assert add(-2, -3) == -5

    def test_add_raises_on_invalid_input(self):
        with pytest.raises(TypeError):
            add("a", "b")
```

### 2. Fixtures

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Scope: function (default), class, module, session
@pytest.fixture(scope="module")
def db_engine():
    """Create database engine for module"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()

@pytest.fixture
def db_session(db_engine):
    """Create fresh session per test"""
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.rollback()
    session.close()

# Fixture with cleanup
@pytest.fixture
def temp_file(tmp_path):
    """Create temporary file with cleanup"""
    file_path = tmp_path / "test.txt"
    file_path.write_text("test content")
    yield file_path
    # Cleanup happens automatically via tmp_path

# Fixture factory
@pytest.fixture
def make_user(db_session):
    """Factory fixture for creating users"""
    created = []

    def _make_user(name: str, email: str = None):
        user = User(name=name, email=email or f"{name}@test.com")
        db_session.add(user)
        db_session.commit()
        created.append(user)
        return user

    yield _make_user

    # Cleanup
    for user in created:
        db_session.delete(user)
    db_session.commit()
```

### 3. Parametrization

```python
import pytest

# Simple parametrize
@pytest.mark.parametrize("input,expected", [
    (1, 2),
    (2, 4),
    (3, 6),
    (0, 0),
    (-1, -2),
])
def test_double(input, expected):
    assert double(input) == expected

# Multiple parameters
@pytest.mark.parametrize("a,b,expected", [
    (1, 2, 3),
    (0, 0, 0),
    (-1, 1, 0),
])
def test_add(a, b, expected):
    assert add(a, b) == expected

# Parametrize with IDs
@pytest.mark.parametrize("status_code,expected_error", [
    pytest.param(400, "BadRequest", id="bad_request"),
    pytest.param(401, "Unauthorized", id="unauthorized"),
    pytest.param(404, "NotFound", id="not_found"),
    pytest.param(500, "InternalError", id="server_error"),
])
def test_error_handling(status_code, expected_error):
    error = handle_error(status_code)
    assert error.type == expected_error

# Combine parametrize decorators (cartesian product)
@pytest.mark.parametrize("x", [1, 2])
@pytest.mark.parametrize("y", [10, 20])
def test_multiply(x, y):
    # Runs: (1,10), (1,20), (2,10), (2,20)
    assert multiply(x, y) == x * y
```

### 4. Mocking

```python
from unittest.mock import Mock, MagicMock, AsyncMock, patch

# Basic mock
def test_with_mock():
    mock_service = Mock()
    mock_service.get_data.return_value = {"key": "value"}

    result = process_data(mock_service)

    mock_service.get_data.assert_called_once()
    assert result == "processed"

# Patch decorator
@patch("mymodule.external_api")
def test_with_patch(mock_api):
    mock_api.fetch.return_value = {"data": [1, 2, 3]}

    result = get_external_data()

    assert result == [1, 2, 3]
    mock_api.fetch.assert_called_once_with(timeout=30)

# Patch as context manager
def test_with_context_patch():
    with patch("mymodule.database") as mock_db:
        mock_db.query.return_value = []
        result = fetch_records()
        assert result == []

# Async mock
@pytest.mark.asyncio
async def test_async_mock():
    mock_client = AsyncMock()
    mock_client.get.return_value = {"status": "ok"}

    result = await fetch_async(mock_client)

    mock_client.get.assert_awaited_once()

# Mock with side_effect
def test_mock_side_effect():
    mock = Mock()
    # Return different values on successive calls
    mock.side_effect = [1, 2, 3]
    assert mock() == 1
    assert mock() == 2
    assert mock() == 3

    # Raise exception
    mock.side_effect = ValueError("error")
    with pytest.raises(ValueError):
        mock()
```

### 5. Async Testing

```python
import pytest
import asyncio

# Mark async test
@pytest.mark.asyncio
async def test_async_function():
    result = await async_fetch_data()
    assert result is not None

# Async fixture
@pytest.fixture
async def async_db():
    db = await create_async_connection()
    yield db
    await db.close()

@pytest.mark.asyncio
async def test_with_async_fixture(async_db):
    result = await async_db.query("SELECT 1")
    assert result == 1

# Test concurrent operations
@pytest.mark.asyncio
async def test_concurrent_requests():
    results = await asyncio.gather(
        fetch_data("a"),
        fetch_data("b"),
        fetch_data("c"),
    )
    assert len(results) == 3

# Timeout for async tests
@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_with_timeout():
    await slow_operation()
```

### 6. Exception Testing

```python
import pytest

# Basic exception test
def test_raises_value_error():
    with pytest.raises(ValueError):
        validate_input(-1)

# Check exception message
def test_exception_message():
    with pytest.raises(ValueError, match="must be positive"):
        validate_input(-1)

# Check exception attributes
def test_exception_details():
    with pytest.raises(CustomError) as exc_info:
        process_invalid_data()

    assert exc_info.value.code == "INVALID_DATA"
    assert "field_name" in exc_info.value.details

# Expected failure
@pytest.mark.xfail(reason="Known bug #123")
def test_known_bug():
    assert buggy_function() == expected_result
```

### 7. Test Markers

```python
import pytest
import sys

# Skip test
@pytest.mark.skip(reason="Not implemented yet")
def test_future_feature():
    pass

# Conditional skip
@pytest.mark.skipif(sys.version_info < (3, 10), reason="Requires Python 3.10+")
def test_python310_feature():
    pass

# Custom markers (define in pytest.ini)
@pytest.mark.slow
def test_slow_operation():
    pass

@pytest.mark.integration
def test_database_connection():
    pass

# Run specific markers: pytest -m "slow" or pytest -m "not slow"
```

```ini
# pytest.ini
[pytest]
markers =
    slow: marks tests as slow
    integration: marks tests as integration tests
    unit: marks tests as unit tests
```

### 8. Test Organization

```
tests/
├── conftest.py              # Shared fixtures
├── pytest.ini               # Pytest configuration
├── unit/
│   ├── conftest.py          # Unit test fixtures
│   ├── test_models.py
│   └── test_services.py
├── integration/
│   ├── conftest.py          # Integration fixtures
│   ├── test_api.py
│   └── test_database.py
└── e2e/
    ├── conftest.py          # E2E fixtures
    └── test_workflows.py
```

### 9. Coverage Configuration

```ini
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --tb=short"

[tool.coverage.run]
source = ["src"]
branch = true
omit = ["*/tests/*", "*/__init__.py"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
]
fail_under = 80
```

```bash
# Run with coverage
pytest --cov=src --cov-report=html --cov-report=term-missing

# Run specific tests
pytest tests/unit/ -v
pytest -k "test_user" -v
pytest -m "not slow" -v
```

## Fixture Scopes

| Scope | Lifecycle | Use Case |
|-------|-----------|----------|
| `function` | Per test (default) | Isolated test data |
| `class` | Per test class | Shared class setup |
| `module` | Per module | Expensive setup |
| `session` | Per test run | One-time setup |

## Best Practices

1. **Arrange-Act-Assert** - Structure tests clearly
2. **One assertion per test** - Focus on single behavior
3. **Descriptive names** - `test_user_creation_with_invalid_email_raises_error`
4. **Isolate tests** - No shared state, no order dependency
5. **Fast unit tests** - Mock external dependencies
6. **Minimal fixtures** - Only what's needed for the test
7. **Test edge cases** - Empty, null, boundaries, errors

## References

- [pytest Documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [Coverage.py](https://coverage.readthedocs.io/)
