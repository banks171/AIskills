---
name: websocket-realtime
description: WebSocket real-time communication patterns for bidirectional messaging, subscriptions, and live data streaming. Use when implementing push notifications, live updates, or real-time data feeds.
---

# WebSocket Real-time Patterns

Production patterns for WebSocket servers with subscription management, heartbeat, and message routing.

## When to Use

Triggers: "WebSocket", "实时推送", "subscription", "live update", "CDC", "双向通信", "push notification"

## Core Patterns

### 1. FastAPI WebSocket Server

```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import Dict, Set
import asyncio
import json

app = FastAPI()

class ConnectionManager:
    def __init__(self):
        # websocket -> set of subscribed topics
        self.active_connections: Dict[WebSocket, Set[str]] = {}
        # topic -> set of websockets
        self.topic_subscribers: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[websocket] = set()

    def disconnect(self, websocket: WebSocket):
        # Remove from all topics
        for topic in self.active_connections.get(websocket, set()):
            self.topic_subscribers.get(topic, set()).discard(websocket)
        # Remove connection
        self.active_connections.pop(websocket, None)

    def subscribe(self, websocket: WebSocket, topic: str):
        """Subscribe websocket to topic"""
        if websocket in self.active_connections:
            self.active_connections[websocket].add(topic)
            if topic not in self.topic_subscribers:
                self.topic_subscribers[topic] = set()
            self.topic_subscribers[topic].add(websocket)

    def unsubscribe(self, websocket: WebSocket, topic: str):
        """Unsubscribe websocket from topic"""
        if websocket in self.active_connections:
            self.active_connections[websocket].discard(topic)
            self.topic_subscribers.get(topic, set()).discard(websocket)

    async def send_personal(self, websocket: WebSocket, message: dict):
        """Send to specific connection"""
        try:
            await websocket.send_json(message)
        except Exception:
            self.disconnect(websocket)

    async def broadcast(self, message: dict):
        """Send to all connections"""
        disconnected = []
        for websocket in self.active_connections:
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.append(websocket)
        for ws in disconnected:
            self.disconnect(ws)

    async def publish(self, topic: str, message: dict):
        """Send to all subscribers of topic"""
        subscribers = self.topic_subscribers.get(topic, set()).copy()
        disconnected = []
        for websocket in subscribers:
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.append(websocket)
        for ws in disconnected:
            self.disconnect(ws)

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            await handle_message(websocket, data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

async def handle_message(websocket: WebSocket, data: dict):
    """Route incoming messages"""
    action = data.get("action")

    if action == "subscribe":
        topic = data.get("topic")
        manager.subscribe(websocket, topic)
        await manager.send_personal(websocket, {
            "type": "subscribed",
            "topic": topic
        })

    elif action == "unsubscribe":
        topic = data.get("topic")
        manager.unsubscribe(websocket, topic)
        await manager.send_personal(websocket, {
            "type": "unsubscribed",
            "topic": topic
        })

    elif action == "ping":
        await manager.send_personal(websocket, {"type": "pong"})
```

### 2. Message Protocol

```python
from pydantic import BaseModel
from typing import Literal, Any
from datetime import datetime

# Client -> Server messages
class SubscribeMessage(BaseModel):
    action: Literal["subscribe"]
    topic: str
    filters: dict = {}

class UnsubscribeMessage(BaseModel):
    action: Literal["unsubscribe"]
    topic: str

class PingMessage(BaseModel):
    action: Literal["ping"]
    timestamp: int

# Server -> Client messages
class DataMessage(BaseModel):
    type: Literal["data"]
    topic: str
    payload: Any
    timestamp: datetime

class ErrorMessage(BaseModel):
    type: Literal["error"]
    code: str
    message: str

class AckMessage(BaseModel):
    type: Literal["ack"]
    action: str
    topic: str | None = None

# Message routing
async def handle_message(websocket: WebSocket, raw_data: dict):
    action = raw_data.get("action")

    handlers = {
        "subscribe": handle_subscribe,
        "unsubscribe": handle_unsubscribe,
        "ping": handle_ping,
    }

    handler = handlers.get(action)
    if handler:
        await handler(websocket, raw_data)
    else:
        await manager.send_personal(websocket, {
            "type": "error",
            "code": "UNKNOWN_ACTION",
            "message": f"Unknown action: {action}"
        })
```

### 3. Heartbeat & Health Check

```python
import asyncio
from datetime import datetime, timedelta

class HeartbeatManager:
    def __init__(self, connection_manager: ConnectionManager,
                 ping_interval: int = 30,
                 timeout: int = 60):
        self.manager = connection_manager
        self.ping_interval = ping_interval
        self.timeout = timeout
        self.last_pong: Dict[WebSocket, datetime] = {}

    async def start(self):
        """Start heartbeat loop"""
        while True:
            await self._check_connections()
            await asyncio.sleep(self.ping_interval)

    async def _check_connections(self):
        """Ping all connections and remove stale ones"""
        now = datetime.utcnow()
        stale = []

        for websocket in list(self.manager.active_connections.keys()):
            # Check if connection is stale
            last = self.last_pong.get(websocket)
            if last and (now - last).seconds > self.timeout:
                stale.append(websocket)
                continue

            # Send ping
            try:
                await websocket.send_json({
                    "type": "ping",
                    "timestamp": now.isoformat()
                })
            except Exception:
                stale.append(websocket)

        # Remove stale connections
        for ws in stale:
            self.manager.disconnect(ws)
            self.last_pong.pop(ws, None)

    def record_pong(self, websocket: WebSocket):
        """Record pong response"""
        self.last_pong[websocket] = datetime.utcnow()


# Integration
heartbeat = HeartbeatManager(manager)

@app.on_event("startup")
async def startup():
    asyncio.create_task(heartbeat.start())

# In message handler
async def handle_pong(websocket: WebSocket, data: dict):
    heartbeat.record_pong(websocket)
```

### 4. Topic-based Subscriptions

```python
from typing import Callable, Awaitable
from dataclasses import dataclass

@dataclass
class Subscription:
    websocket: WebSocket
    topic: str
    filters: dict = None

class TopicManager:
    def __init__(self):
        self.subscriptions: Dict[str, list[Subscription]] = {}
        self.topic_handlers: Dict[str, Callable] = {}

    def register_topic(self, topic_pattern: str,
                       handler: Callable[[dict], Awaitable[bool]] = None):
        """Register topic with optional filter handler"""
        self.topic_handlers[topic_pattern] = handler

    def subscribe(self, websocket: WebSocket, topic: str, filters: dict = None):
        """Add subscription with optional filters"""
        if topic not in self.subscriptions:
            self.subscriptions[topic] = []

        sub = Subscription(websocket=websocket, topic=topic, filters=filters)
        self.subscriptions[topic].append(sub)

    def unsubscribe(self, websocket: WebSocket, topic: str = None):
        """Remove subscription(s)"""
        if topic:
            self.subscriptions[topic] = [
                s for s in self.subscriptions.get(topic, [])
                if s.websocket != websocket
            ]
        else:
            # Remove from all topics
            for t in self.subscriptions:
                self.subscriptions[t] = [
                    s for s in self.subscriptions[t]
                    if s.websocket != websocket
                ]

    async def publish(self, topic: str, data: dict):
        """Publish to topic subscribers with filter matching"""
        message = {
            "type": "data",
            "topic": topic,
            "payload": data,
            "timestamp": datetime.utcnow().isoformat()
        }

        for sub in self.subscriptions.get(topic, []):
            # Apply filters if present
            if sub.filters and not self._match_filters(data, sub.filters):
                continue

            try:
                await sub.websocket.send_json(message)
            except Exception:
                pass  # Handle in heartbeat cleanup

    def _match_filters(self, data: dict, filters: dict) -> bool:
        """Check if data matches subscription filters"""
        for key, value in filters.items():
            if key not in data:
                return False
            if isinstance(value, list):
                if data[key] not in value:
                    return False
            elif data[key] != value:
                return False
        return True


# Usage
topic_manager = TopicManager()

# Subscribe with filters
topic_manager.subscribe(
    websocket,
    topic="market.quotes",
    filters={"symbol": ["AAPL", "GOOGL"]}
)

# Publish - only matching subscribers receive
await topic_manager.publish("market.quotes", {
    "symbol": "AAPL",
    "price": 150.25
})
```

### 5. Room-based Architecture

```python
class Room:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.members: Set[WebSocket] = set()
        self.metadata: dict = {}

    async def join(self, websocket: WebSocket):
        self.members.add(websocket)
        await self.broadcast({
            "type": "room.joined",
            "room_id": self.room_id,
            "member_count": len(self.members)
        }, exclude=websocket)

    async def leave(self, websocket: WebSocket):
        self.members.discard(websocket)
        await self.broadcast({
            "type": "room.left",
            "room_id": self.room_id,
            "member_count": len(self.members)
        })

    async def broadcast(self, message: dict, exclude: WebSocket = None):
        for ws in self.members:
            if ws != exclude:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass


class RoomManager:
    def __init__(self):
        self.rooms: Dict[str, Room] = {}
        self.user_rooms: Dict[WebSocket, Set[str]] = {}

    def get_or_create_room(self, room_id: str) -> Room:
        if room_id not in self.rooms:
            self.rooms[room_id] = Room(room_id)
        return self.rooms[room_id]

    async def join_room(self, websocket: WebSocket, room_id: str):
        room = self.get_or_create_room(room_id)
        await room.join(websocket)

        if websocket not in self.user_rooms:
            self.user_rooms[websocket] = set()
        self.user_rooms[websocket].add(room_id)

    async def leave_room(self, websocket: WebSocket, room_id: str):
        if room_id in self.rooms:
            await self.rooms[room_id].leave(websocket)

        if websocket in self.user_rooms:
            self.user_rooms[websocket].discard(room_id)

    async def leave_all(self, websocket: WebSocket):
        for room_id in list(self.user_rooms.get(websocket, [])):
            await self.leave_room(websocket, room_id)
        self.user_rooms.pop(websocket, None)
```

### 6. CDC Event Streaming

```python
from kafka import KafkaConsumer
import asyncio
import json

class CDCEventStreamer:
    """Stream CDC events from Kafka to WebSocket subscribers"""

    def __init__(self, topic_manager: TopicManager,
                 kafka_brokers: str,
                 kafka_topics: list[str]):
        self.topic_manager = topic_manager
        self.consumer = KafkaConsumer(
            *kafka_topics,
            bootstrap_servers=kafka_brokers,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            group_id="websocket-streamer",
            auto_offset_reset="latest"
        )
        self._running = False

    async def start(self):
        """Start consuming CDC events"""
        self._running = True
        loop = asyncio.get_event_loop()

        while self._running:
            # Non-blocking poll
            messages = await loop.run_in_executor(
                None,
                lambda: self.consumer.poll(timeout_ms=100)
            )

            for topic_partition, records in messages.items():
                for record in records:
                    await self._handle_cdc_event(record.value)

            await asyncio.sleep(0.01)  # Yield to event loop

    async def _handle_cdc_event(self, event: dict):
        """Route CDC event to WebSocket subscribers"""
        entity_type = event.get("entity_type")  # e.g., "company", "event"
        entity_id = event.get("entity_id")
        operation = event.get("operation")  # insert, update, delete

        # Publish to entity-specific topic
        ws_topic = f"cdc.{entity_type}"
        await self.topic_manager.publish(ws_topic, {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "operation": operation,
            "data": event.get("data"),
            "timestamp": event.get("timestamp")
        })

        # Publish to entity-id specific topic
        ws_topic_specific = f"cdc.{entity_type}.{entity_id}"
        await self.topic_manager.publish(ws_topic_specific, event)

    def stop(self):
        self._running = False
        self.consumer.close()


# Startup
cdc_streamer = CDCEventStreamer(
    topic_manager,
    kafka_brokers="localhost:9092",
    kafka_topics=["graph_cdc_events"]
)

@app.on_event("startup")
async def startup():
    asyncio.create_task(cdc_streamer.start())

@app.on_event("shutdown")
async def shutdown():
    cdc_streamer.stop()
```

### 7. Client Implementation

```javascript
// JavaScript WebSocket client
class RealtimeClient {
    constructor(url) {
        this.url = url;
        this.ws = null;
        this.subscriptions = new Map();
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 30000;
        this.pingInterval = null;
    }

    connect() {
        this.ws = new WebSocket(this.url);

        this.ws.onopen = () => {
            console.log('Connected');
            this.reconnectDelay = 1000;
            this._startPing();
            // Resubscribe
            this.subscriptions.forEach((_, topic) => {
                this._send({ action: 'subscribe', topic });
            });
        };

        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this._handleMessage(data);
        };

        this.ws.onclose = () => {
            console.log('Disconnected');
            this._stopPing();
            this._reconnect();
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }

    subscribe(topic, callback) {
        this.subscriptions.set(topic, callback);
        if (this.ws?.readyState === WebSocket.OPEN) {
            this._send({ action: 'subscribe', topic });
        }
    }

    unsubscribe(topic) {
        this.subscriptions.delete(topic);
        if (this.ws?.readyState === WebSocket.OPEN) {
            this._send({ action: 'unsubscribe', topic });
        }
    }

    _handleMessage(data) {
        if (data.type === 'data') {
            const callback = this.subscriptions.get(data.topic);
            if (callback) callback(data.payload);
        } else if (data.type === 'ping') {
            this._send({ action: 'pong', timestamp: Date.now() });
        }
    }

    _send(data) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(data));
        }
    }

    _startPing() {
        this.pingInterval = setInterval(() => {
            this._send({ action: 'ping', timestamp: Date.now() });
        }, 25000);
    }

    _stopPing() {
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    }

    _reconnect() {
        setTimeout(() => {
            console.log('Reconnecting...');
            this.connect();
            this.reconnectDelay = Math.min(
                this.reconnectDelay * 2,
                this.maxReconnectDelay
            );
        }, this.reconnectDelay);
    }

    disconnect() {
        this._stopPing();
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
}

// Usage
const client = new RealtimeClient('wss://api.example.com/ws');
client.connect();

client.subscribe('market.quotes', (data) => {
    console.log(`${data.symbol}: $${data.price}`);
});

client.subscribe('cdc.company.AAPL', (data) => {
    console.log('AAPL update:', data);
});
```

## Message Types

| Type | Direction | Purpose |
|------|-----------|---------|
| `subscribe` | Client→Server | Subscribe to topic |
| `unsubscribe` | Client→Server | Unsubscribe from topic |
| `ping` | Bidirectional | Heartbeat |
| `pong` | Bidirectional | Heartbeat response |
| `data` | Server→Client | Push data |
| `ack` | Server→Client | Confirmation |
| `error` | Server→Client | Error notification |

## Scaling Patterns

| Pattern | Use Case | Implementation |
|---------|----------|----------------|
| Single server | < 10K connections | In-memory state |
| Redis pub/sub | Multi-server | Redis as message bus |
| Kafka | High throughput | Kafka for persistence |
| Sticky sessions | Stateful | Load balancer affinity |

## Best Practices

1. **Always use ping/pong** - Detect dead connections
2. **Implement reconnection** - Client-side exponential backoff
3. **Message acknowledgment** - For critical data
4. **Topic namespacing** - `domain.entity.action`
5. **Connection limits** - Per-user/per-IP limits
6. **Graceful shutdown** - Drain connections on restart

## References

- [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)
- [WebSocket Protocol RFC 6455](https://tools.ietf.org/html/rfc6455)
- [Starlette WebSockets](https://www.starlette.io/websockets/)
