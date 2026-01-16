---
name: kafka-streaming
description: Apache Kafka messaging patterns for event streaming, topic design, and reliable message processing. Use when designing Kafka topics, implementing producers/consumers, or building event-driven architectures.
---

# Kafka Streaming Patterns

Production patterns for Apache Kafka event streaming with reliability, partitioning, and schema management.

## When to Use

Triggers: "Kafka", "Topic", "消息队列", "event streaming", "producer", "consumer", "分流", "Schema Registry"

## Core Patterns

### 1. Topic Naming Convention

```
<domain>.<entity>.<event-type>.<version>

Examples:
- market.stock.price-update.v1
- news.article.published.v1
- order.trade.executed.v1
```

| Component | Description | Example |
|-----------|-------------|---------|
| domain | Business domain | market, news, order |
| entity | Core entity | stock, article, trade |
| event-type | What happened | price-update, published |
| version | Schema version | v1, v2 |

### 2. Topic Configuration

```python
from confluent_kafka.admin import AdminClient, NewTopic

def create_topic(
    name: str,
    partitions: int = 6,
    replication: int = 3,
    retention_ms: int = 7 * 24 * 60 * 60 * 1000,  # 7 days
    cleanup_policy: str = "delete"
):
    admin = AdminClient({"bootstrap.servers": KAFKA_BROKERS})

    topic = NewTopic(
        name,
        num_partitions=partitions,
        replication_factor=replication,
        config={
            "retention.ms": str(retention_ms),
            "cleanup.policy": cleanup_policy,
            "compression.type": "lz4",
            "min.insync.replicas": "2",
        }
    )
    admin.create_topics([topic])
```

**Partition Guidelines**:
| Message Rate | Partitions | Reason |
|--------------|------------|--------|
| < 1K/s | 3-6 | Basic parallelism |
| 1K-10K/s | 6-12 | Consumer scaling |
| > 10K/s | 12-24+ | High throughput |

### 3. Producer Patterns

```python
from confluent_kafka import Producer
import json

class KafkaProducer:
    def __init__(self, bootstrap_servers: str):
        self.producer = Producer({
            "bootstrap.servers": bootstrap_servers,
            "acks": "all",                    # Wait for all replicas
            "retries": 3,
            "retry.backoff.ms": 100,
            "enable.idempotence": True,       # Exactly-once semantics
            "compression.type": "lz4",
            "linger.ms": 5,                   # Batch for 5ms
            "batch.size": 16384,
        })

    def send(
        self,
        topic: str,
        key: str,
        value: dict,
        headers: dict = None
    ):
        def delivery_callback(err, msg):
            if err:
                logger.error(f"Delivery failed: {err}")
            else:
                logger.debug(f"Delivered to {msg.topic()}[{msg.partition()}]")

        self.producer.produce(
            topic=topic,
            key=key.encode("utf-8"),
            value=json.dumps(value).encode("utf-8"),
            headers=[(k, v.encode()) for k, v in (headers or {}).items()],
            callback=delivery_callback
        )
        self.producer.poll(0)  # Trigger callbacks

    def flush(self, timeout: float = 10.0):
        self.producer.flush(timeout)
```

### 4. Consumer Patterns

```python
from confluent_kafka import Consumer, KafkaError
import json

class KafkaConsumer:
    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        topics: list[str],
        auto_commit: bool = False
    ):
        self.consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": auto_commit,
            "max.poll.interval.ms": 300000,   # 5 min processing time
            "session.timeout.ms": 45000,
            "heartbeat.interval.ms": 15000,
        })
        self.consumer.subscribe(topics)

    def consume(self, timeout: float = 1.0):
        msg = self.consumer.poll(timeout)
        if msg is None:
            return None
        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                return None
            raise KafkaException(msg.error())

        return {
            "key": msg.key().decode("utf-8") if msg.key() else None,
            "value": json.loads(msg.value().decode("utf-8")),
            "topic": msg.topic(),
            "partition": msg.partition(),
            "offset": msg.offset(),
            "timestamp": msg.timestamp()[1],
            "headers": dict(msg.headers() or []),
        }

    def commit(self):
        """Manual commit after successful processing"""
        self.consumer.commit()

    def close(self):
        self.consumer.close()
```

### 5. Idempotent Processing

```python
class IdempotentProcessor:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.ttl = 7 * 24 * 60 * 60  # 7 days

    def process_message(self, msg: dict, handler: Callable):
        # Generate idempotency key
        idempotency_key = f"kafka:processed:{msg['topic']}:{msg['partition']}:{msg['offset']}"

        # Check if already processed
        if self.redis.exists(idempotency_key):
            logger.info(f"Skipping duplicate: {idempotency_key}")
            return

        # Process message
        try:
            handler(msg["value"])
            # Mark as processed
            self.redis.setex(idempotency_key, self.ttl, "1")
        except Exception as e:
            logger.error(f"Processing failed: {e}")
            raise
```

### 6. Dead Letter Queue (DLQ)

```python
class DLQHandler:
    def __init__(self, producer: KafkaProducer):
        self.producer = producer

    def send_to_dlq(
        self,
        original_topic: str,
        message: dict,
        error: Exception
    ):
        dlq_topic = f"{original_topic}.dlq"

        self.producer.send(
            topic=dlq_topic,
            key=message.get("key", "unknown"),
            value={
                "original_message": message["value"],
                "error": str(error),
                "error_type": type(error).__name__,
                "original_topic": original_topic,
                "original_partition": message["partition"],
                "original_offset": message["offset"],
                "failed_at": datetime.utcnow().isoformat(),
            },
            headers={"error_type": type(error).__name__}
        )
```

### 7. Priority Routing (分流)

```python
class PriorityRouter:
    """Route messages to urgent/standard queues based on score"""

    URGENT_THRESHOLD = 90

    def __init__(self, producer: KafkaProducer):
        self.producer = producer

    def route(self, message: dict, score: float):
        if score >= self.URGENT_THRESHOLD:
            topic = "news.article.urgent.v1"
        else:
            topic = "news.article.standard.v1"

        self.producer.send(
            topic=topic,
            key=message["id"],
            value=message,
            headers={"score": str(score)}
        )
```

### 8. Schema Registry (Avro)

```python
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer, AvroDeserializer

# Schema definition
STOCK_PRICE_SCHEMA = """
{
    "type": "record",
    "name": "StockPrice",
    "namespace": "com.market.stock",
    "fields": [
        {"name": "symbol", "type": "string"},
        {"name": "price", "type": "double"},
        {"name": "volume", "type": "long"},
        {"name": "timestamp", "type": "long", "logicalType": "timestamp-millis"}
    ]
}
"""

# Setup
schema_registry = SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL})
serializer = AvroSerializer(schema_registry, STOCK_PRICE_SCHEMA)
deserializer = AvroDeserializer(schema_registry)

# Produce with schema
producer.produce(
    topic="market.stock.price-update.v1",
    key=symbol,
    value=serializer({"symbol": "AAPL", "price": 150.0, "volume": 1000000, "timestamp": ts})
)
```

## Topic Design Matrix

| Use Case | Partitions | Retention | Cleanup | Compression |
|----------|------------|-----------|---------|-------------|
| Real-time prices | 12+ | 1 day | delete | lz4 |
| News articles | 6 | 7 days | delete | gzip |
| Audit logs | 6 | 90 days | delete | gzip |
| State changelog | 6 | forever | compact | lz4 |

## Best Practices

1. **Key design** - Use meaningful keys for ordering guarantees
2. **Idempotence** - Enable producer idempotence for exactly-once
3. **Manual commits** - Commit after successful processing
4. **DLQ** - Always have a dead letter queue strategy
5. **Monitoring** - Track lag, throughput, and error rates
6. **Schema evolution** - Use Schema Registry for compatibility

## References

- [Confluent Kafka Python](https://docs.confluent.io/kafka-clients/python/current/overview.html)
- [Apache Kafka Documentation](https://kafka.apache.org/documentation/)
- [Schema Registry](https://docs.confluent.io/platform/current/schema-registry/)
