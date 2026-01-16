---
name: fastapi-service
description: FastAPI microservice patterns for building production-ready APIs. Use when creating API endpoints, health checks, middleware, dependency injection, or Pydantic models.
---

# FastAPI Microservice Patterns

Production-ready patterns for FastAPI microservices with async support, health checks, and observability.

## When to Use

Triggers: "FastAPI", "微服务", "API端点", "health check", "Pydantic model", "dependency injection", "middleware"

## Core Patterns

### 1. Application Factory

```python
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize connections
    await db.connect()
    yield
    # Shutdown: cleanup
    await db.disconnect()

def create_app() -> FastAPI:
    app = FastAPI(
        title="Service Name",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(api_router, prefix="/api/v1")
    return app
```

### 2. Health Check Endpoints

```python
from fastapi import APIRouter, status
from pydantic import BaseModel

router = APIRouter(tags=["health"])

class HealthResponse(BaseModel):
    status: str
    version: str
    dependencies: dict[str, str]

@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Liveness probe - is the service running?"""
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        dependencies={}
    )

@router.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check():
    """Readiness probe - can the service handle requests?"""
    # Check critical dependencies
    if not await db.is_connected():
        raise HTTPException(status_code=503, detail="Database unavailable")
    if not await cache.ping():
        raise HTTPException(status_code=503, detail="Cache unavailable")
    return {"status": "ready"}
```

### 3. Dependency Injection

```python
from fastapi import Depends
from typing import Annotated

# Database session
async def get_db():
    async with AsyncSession() as session:
        yield session

DBSession = Annotated[AsyncSession, Depends(get_db)]

# Configuration
def get_settings():
    return Settings()

Config = Annotated[Settings, Depends(get_settings)]

# Usage
@router.get("/items/{item_id}")
async def get_item(item_id: int, db: DBSession, config: Config):
    return await db.get(Item, item_id)
```

### 4. Pydantic Models

```python
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime

class ItemBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=100)
    price: float = Field(..., gt=0)

class ItemCreate(ItemBase):
    pass

class ItemResponse(ItemBase):
    id: int
    created_at: datetime

class ItemList(BaseModel):
    items: list[ItemResponse]
    total: int
    page: int
    page_size: int
```

### 5. Error Handling

```python
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

class AppException(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code

@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
            }
        }
    )

# Usage
raise AppException("ITEM_NOT_FOUND", f"Item {item_id} not found", 404)
```

### 6. Middleware

```python
import time
from fastapi import Request
from prometheus_client import Counter, Histogram

REQUEST_COUNT = Counter(
    'http_requests_total',
    'Total requests',
    ['method', 'endpoint', 'status']
)
REQUEST_LATENCY = Histogram(
    'http_request_duration_seconds',
    'Request latency',
    ['method', 'endpoint']
)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=request.url.path,
        status=response.status_code
    ).inc()
    REQUEST_LATENCY.labels(
        method=request.method,
        endpoint=request.url.path
    ).observe(duration)

    return response
```

### 7. Background Tasks

```python
from fastapi import BackgroundTasks

async def send_notification(user_id: int, message: str):
    # Async notification logic
    await notification_service.send(user_id, message)

@router.post("/orders")
async def create_order(
    order: OrderCreate,
    background_tasks: BackgroundTasks,
    db: DBSession
):
    # Create order
    new_order = await create_order_in_db(db, order)

    # Queue background task
    background_tasks.add_task(
        send_notification,
        order.user_id,
        f"Order {new_order.id} created"
    )

    return new_order
```

### 8. Async Patterns

```python
import asyncio
from typing import AsyncGenerator

# Async generator for streaming
async def stream_data() -> AsyncGenerator[str, None]:
    for i in range(100):
        yield f"data: {i}\n\n"
        await asyncio.sleep(0.1)

@router.get("/stream")
async def stream_endpoint():
    return StreamingResponse(
        stream_data(),
        media_type="text/event-stream"
    )

# Concurrent requests
async def fetch_all(urls: list[str]) -> list[dict]:
    async with httpx.AsyncClient() as client:
        tasks = [client.get(url) for url in urls]
        responses = await asyncio.gather(*tasks)
        return [r.json() for r in responses]
```

## Project Structure

```
service/
├── app/
│   ├── __init__.py
│   ├── main.py              # Application factory
│   ├── config.py            # Settings with pydantic-settings
│   ├── dependencies.py      # Shared dependencies
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── health.py
│   │   │   └── items.py
│   │   └── deps.py          # Route-specific dependencies
│   ├── models/              # Pydantic models
│   ├── services/            # Business logic
│   └── repositories/        # Data access
├── tests/
├── Dockerfile
└── pyproject.toml
```

## Best Practices

1. **Always use async** - FastAPI is async-first
2. **Type everything** - Use type hints for all parameters and returns
3. **Validate early** - Use Pydantic for request/response validation
4. **Health endpoints** - Always include /health and /ready
5. **Structured errors** - Return consistent error format
6. **Metrics** - Export Prometheus metrics
7. **Graceful shutdown** - Handle SIGTERM properly

## References

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Pydantic V2 Documentation](https://docs.pydantic.dev/)
- [Starlette (underlying framework)](https://www.starlette.io/)
