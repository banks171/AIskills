---
name: sql-expert
description: SQL database expert for schema design, query optimization, and database operations across MySQL, ClickHouse, PostgreSQL, and SQLite
---

# SQL Expert Skill

Expert guidance for SQL database design, query optimization, and data operations.

## Supported Databases

| Database | Specialization |
|----------|---------------|
| **MySQL** | OLTP, relational data, transactions |
| **ClickHouse** | OLAP, time-series, analytics |
| **PostgreSQL** | Advanced features, JSON, full-text search |
| **SQLite** | Embedded, local development |

## Core Capabilities

### 1. Schema Design

**Principles**:
- Normalize to 3NF for OLTP, denormalize for OLAP
- Choose appropriate data types (smallest sufficient size)
- Design indexes based on query patterns
- Use foreign keys for referential integrity (MySQL/PostgreSQL)

**ClickHouse Specific**:
```sql
-- MergeTree engine with proper ORDER BY
CREATE TABLE events (
    event_date Date,
    event_time DateTime,
    user_id UInt64,
    event_type LowCardinality(String),
    value Float64
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(event_date)
ORDER BY (event_type, user_id, event_time)
TTL event_date + INTERVAL 90 DAY;
```

**MySQL Specific**:
```sql
-- InnoDB with proper indexes
CREATE TABLE orders (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT UNSIGNED NOT NULL,
    status ENUM('pending', 'paid', 'shipped', 'completed') NOT NULL,
    total DECIMAL(10,2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_status (user_id, status),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 2. Query Optimization

**Analysis Steps**:
1. Use EXPLAIN/EXPLAIN ANALYZE to understand execution plan
2. Identify full table scans, missing indexes
3. Check join order and join types
4. Evaluate subquery vs JOIN performance

**Common Optimizations**:
```sql
-- Bad: Subquery in SELECT
SELECT id, (SELECT name FROM users WHERE users.id = orders.user_id) as user_name
FROM orders;

-- Good: JOIN
SELECT o.id, u.name as user_name
FROM orders o
JOIN users u ON u.id = o.user_id;

-- Bad: OR conditions preventing index use
SELECT * FROM orders WHERE user_id = 1 OR status = 'pending';

-- Good: UNION for separate index scans
SELECT * FROM orders WHERE user_id = 1
UNION
SELECT * FROM orders WHERE status = 'pending';
```

**ClickHouse Query Tips**:
```sql
-- Use PREWHERE for filtering before reading columns
SELECT * FROM events
PREWHERE event_date >= today() - 7
WHERE event_type = 'click';

-- Use sampling for approximate results
SELECT count() * 10 FROM events SAMPLE 0.1
WHERE event_type = 'view';
```

### 3. Index Strategy

**MySQL/PostgreSQL**:
| Index Type | Use Case |
|------------|----------|
| B-Tree | Range queries, sorting, equality |
| Hash | Equality only (PostgreSQL) |
| Full-text | Text search |
| Composite | Multi-column queries (leftmost prefix) |

**ClickHouse**:
| Feature | Purpose |
|---------|---------|
| ORDER BY | Primary index, determines data layout |
| PARTITION BY | Data pruning, TTL management |
| Skipping Index | Bloom filter, minmax, set |

### 4. Performance Patterns

**Pagination**:
```sql
-- Bad: OFFSET for large pages
SELECT * FROM orders ORDER BY id LIMIT 10 OFFSET 10000;

-- Good: Keyset pagination
SELECT * FROM orders WHERE id > 10000 ORDER BY id LIMIT 10;
```

**Aggregation**:
```sql
-- ClickHouse: Use materialized views for pre-aggregation
CREATE MATERIALIZED VIEW daily_stats
ENGINE = SummingMergeTree()
ORDER BY (date, event_type)
AS SELECT
    toDate(event_time) as date,
    event_type,
    count() as cnt,
    sum(value) as total
FROM events
GROUP BY date, event_type;
```

**Batch Operations**:
```sql
-- MySQL: Batch inserts
INSERT INTO orders (user_id, status, total) VALUES
(1, 'pending', 100.00),
(2, 'pending', 200.00),
(3, 'pending', 150.00);

-- ClickHouse: Async inserts for high throughput
SET async_insert = 1;
SET wait_for_async_insert = 0;
```

### 5. Data Migration

**Safe Migration Pattern**:
1. Create new structure alongside old
2. Dual-write to both structures
3. Backfill historical data
4. Switch reads to new structure
5. Remove dual-write, drop old structure

```sql
-- Add column with default (MySQL 8.0+ instant)
ALTER TABLE orders ADD COLUMN metadata JSON DEFAULT NULL;

-- ClickHouse: Async mutation
ALTER TABLE events UPDATE metadata = '{}' WHERE metadata IS NULL;
```

## Trigger Phrases

- "design database schema"
- "optimize SQL query"
- "create index for"
- "ClickHouse table design"
- "MySQL performance tuning"
- "database migration"
- "query execution plan"

## References

- [MySQL 8.0 Documentation](https://dev.mysql.com/doc/refman/8.0/en/)
- [ClickHouse Documentation](https://clickhouse.com/docs)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [Use The Index, Luke](https://use-the-index-luke.com/)
