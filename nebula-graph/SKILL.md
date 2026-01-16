---
name: nebula-graph
description: NebulaGraph distributed graph database patterns for entity relationships, graph traversal, and nGQL queries. Use when designing graph schemas, writing graph queries, or building knowledge graphs.
---

# NebulaGraph Patterns

Production patterns for NebulaGraph distributed graph database with nGQL queries, schema design, and traversal optimization.

## When to Use

Triggers: "NebulaGraph", "图数据库", "nGQL", "实体关系", "graph traversal", "知识图谱", "关系网络"

## Core Patterns

### 1. Schema Design

```ngql
-- Create graph space
CREATE SPACE IF NOT EXISTS knowledge_graph (
    partition_num = 10,
    replica_factor = 3,
    vid_type = FIXED_STRING(64)
);

USE knowledge_graph;

-- Create Tags (vertex types)
CREATE TAG IF NOT EXISTS Company (
    name string NOT NULL,
    ticker string,
    industry string,
    market_cap double,
    created_at datetime DEFAULT datetime(),
    updated_at datetime DEFAULT datetime()
);

CREATE TAG IF NOT EXISTS Person (
    name string NOT NULL,
    title string,
    email string
);

CREATE TAG IF NOT EXISTS Event (
    type string NOT NULL,
    title string,
    severity int DEFAULT 0,
    occurred_at datetime,
    source string
);

-- Create Edge types
CREATE EDGE IF NOT EXISTS WORKS_AT (
    role string,
    since datetime,
    until datetime
);

CREATE EDGE IF NOT EXISTS SUPPLIES_TO (
    product string,
    volume double,
    contract_start datetime
);

CREATE EDGE IF NOT EXISTS AFFECTS (
    impact_score double,
    confidence double,
    lag_days int DEFAULT 0
);

CREATE EDGE IF NOT EXISTS RELATED_TO (
    relation_type string,
    weight double DEFAULT 1.0
);
```

### 2. Index Design

```ngql
-- Tag indexes for property lookups
CREATE TAG INDEX IF NOT EXISTS idx_company_ticker ON Company(ticker(32));
CREATE TAG INDEX IF NOT EXISTS idx_company_industry ON Company(industry(64));
CREATE TAG INDEX IF NOT EXISTS idx_event_type ON Event(type(32));
CREATE TAG INDEX IF NOT EXISTS idx_event_time ON Event(occurred_at);

-- Edge indexes
CREATE EDGE INDEX IF NOT EXISTS idx_affects_score ON AFFECTS(impact_score);
CREATE EDGE INDEX IF NOT EXISTS idx_supplies_volume ON SUPPLIES_TO(volume);

-- Composite index
CREATE TAG INDEX IF NOT EXISTS idx_company_industry_cap
    ON Company(industry(64), market_cap);

-- Rebuild indexes after data load
REBUILD TAG INDEX idx_company_ticker;
REBUILD EDGE INDEX idx_affects_score;
```

### 3. Basic CRUD Operations

```ngql
-- Insert vertices
INSERT VERTEX Company(name, ticker, industry, market_cap) VALUES
    "company_001":("Apple Inc", "AAPL", "Technology", 3000000000000),
    "company_002":("TSMC", "TSM", "Semiconductors", 500000000000);

INSERT VERTEX Person(name, title) VALUES
    "person_001":("Tim Cook", "CEO"),
    "person_002":("Wei Zhejia", "CEO");

INSERT VERTEX Event(type, title, severity, occurred_at) VALUES
    "event_001":("earnings", "Q4 2024 Earnings", 2, datetime("2024-01-25"));

-- Insert edges
INSERT EDGE WORKS_AT(role, since) VALUES
    "person_001" -> "company_001":("CEO", datetime("2011-08-24")),
    "person_002" -> "company_002":("CEO", datetime("2018-06-05"));

INSERT EDGE SUPPLIES_TO(product, volume) VALUES
    "company_002" -> "company_001":("A16 Chip", 100000000);

INSERT EDGE AFFECTS(impact_score, confidence) VALUES
    "event_001" -> "company_001":(0.85, 0.92);

-- Update vertex
UPDATE VERTEX ON Company "company_001"
SET market_cap = 3100000000000, updated_at = datetime();

-- Update edge
UPDATE EDGE ON SUPPLIES_TO "company_002" -> "company_001"
SET volume = 120000000;

-- Delete (use with caution)
DELETE VERTEX "event_old_001";
DELETE EDGE AFFECTS "event_001" -> "company_001";
```

### 4. Query Patterns

```ngql
-- Fetch vertex by ID
FETCH PROP ON Company "company_001"
YIELD properties(vertex) AS props;

-- Lookup by property (requires index)
LOOKUP ON Company
WHERE Company.industry == "Technology"
YIELD id(vertex) AS vid, Company.name AS name, Company.ticker AS ticker;

-- Match pattern (Cypher-like)
MATCH (c:Company {ticker: "AAPL"})
RETURN c.Company.name AS name, c.Company.market_cap AS market_cap;

-- Find connected vertices
MATCH (p:Person)-[w:WORKS_AT]->(c:Company {ticker: "AAPL"})
RETURN p.Person.name AS person, w.role AS role;

-- Multi-hop traversal
MATCH (c1:Company {ticker: "AAPL"})<-[:SUPPLIES_TO*1..2]-(supplier:Company)
RETURN supplier.Company.name AS supplier_name, supplier.Company.ticker AS ticker;
```

### 5. Graph Traversal

```ngql
-- GO statement (native traversal)
-- Find direct suppliers
GO FROM "company_001" OVER SUPPLIES_TO REVERSELY
YIELD $$.Company.name AS supplier, $$.Company.ticker AS ticker;

-- Multi-hop with depth
GO 1 TO 3 STEPS FROM "company_001" OVER SUPPLIES_TO REVERSELY
YIELD $$.Company.name AS supplier, $$.Company.industry AS industry;

-- Bidirectional traversal
GO FROM "company_001" OVER RELATED_TO BIDIRECT
YIELD $$.Company.name AS related_company;

-- With filtering
GO FROM "event_001" OVER AFFECTS
WHERE $$.Company.market_cap > 1000000000000
YIELD $$.Company.name AS affected, AFFECTS.impact_score AS impact;

-- Path finding
FIND SHORTEST PATH FROM "company_001" TO "company_003"
OVER SUPPLIES_TO, RELATED_TO
YIELD path AS p;

-- All paths with limit
FIND ALL PATH FROM "company_001" TO "company_003"
OVER * BIDIRECT UPTO 5 STEPS
YIELD path AS p | LIMIT 10;
```

### 6. Aggregation & Analytics

```ngql
-- Count and group
MATCH (c:Company)
RETURN c.Company.industry AS industry, count(*) AS count
ORDER BY count DESC;

-- Aggregate edge properties
GO FROM "company_001" OVER SUPPLIES_TO REVERSELY
YIELD SUPPLIES_TO.volume AS vol
| GROUP BY $-.vol
| YIELD sum($-.vol) AS total_volume;

-- Degree centrality
MATCH (c:Company)
RETURN c.Company.name AS name,
       size((c)<-[:SUPPLIES_TO]-()) AS in_degree,
       size((c)-[:SUPPLIES_TO]->()) AS out_degree
ORDER BY in_degree + out_degree DESC
LIMIT 10;

-- Subgraph extraction
GET SUBGRAPH 2 STEPS FROM "company_001"
YIELD VERTICES AS nodes, EDGES AS edges;
```

### 7. Python Client

```python
from nebula3.gclient.net import ConnectionPool
from nebula3.Config import Config
from contextlib import contextmanager

class NebulaClient:
    def __init__(self, hosts: list[tuple], space: str):
        self.space = space
        config = Config()
        config.max_connection_pool_size = 10

        self.pool = ConnectionPool()
        self.pool.init(hosts, config)

    @contextmanager
    def session(self):
        """Get session with automatic cleanup"""
        session = self.pool.get_session("user", "password")
        try:
            session.execute(f"USE {self.space}")
            yield session
        finally:
            session.release()

    def execute(self, query: str) -> list[dict]:
        """Execute query and return results as dicts"""
        with self.session() as session:
            result = session.execute(query)
            if not result.is_succeeded():
                raise Exception(f"Query failed: {result.error_msg()}")

            if result.is_empty():
                return []

            columns = result.keys()
            return [
                dict(zip(columns, [row.values[i].cast() for i in range(len(columns))]))
                for row in result.rows()
            ]

    def get_vertex(self, vid: str, tag: str) -> dict | None:
        """Fetch single vertex by ID"""
        query = f'FETCH PROP ON {tag} "{vid}" YIELD properties(vertex) AS props'
        results = self.execute(query)
        return results[0]["props"] if results else None

    def find_neighbors(
        self,
        vid: str,
        edge_type: str,
        direction: str = "OUT",  # OUT, IN, BOTH
        depth: int = 1
    ) -> list[dict]:
        """Find connected vertices"""
        dir_clause = {
            "OUT": "",
            "IN": "REVERSELY",
            "BOTH": "BIDIRECT"
        }[direction]

        query = f"""
        GO {depth} STEPS FROM "{vid}" OVER {edge_type} {dir_clause}
        YIELD id($$) AS vid, properties($$) AS props
        """
        return self.execute(query)

    def close(self):
        self.pool.close()


# Usage
client = NebulaClient([("localhost", 9669)], "knowledge_graph")

# Query company
company = client.get_vertex("company_001", "Company")
print(f"Company: {company['name']}")

# Find suppliers
suppliers = client.find_neighbors("company_001", "SUPPLIES_TO", direction="IN")
for s in suppliers:
    print(f"Supplier: {s['props']['name']}")

client.close()
```

### 8. Batch Operations

```python
class NebulaBatchWriter:
    def __init__(self, client: NebulaClient, batch_size: int = 1000):
        self.client = client
        self.batch_size = batch_size
        self.vertex_buffer = []
        self.edge_buffer = []

    def add_vertex(self, vid: str, tag: str, props: dict):
        """Buffer vertex for batch insert"""
        prop_str = ", ".join(f'"{v}"' if isinstance(v, str) else str(v)
                            for v in props.values())
        self.vertex_buffer.append(f'"{vid}":({prop_str})')

        if len(self.vertex_buffer) >= self.batch_size:
            self._flush_vertices(tag, list(props.keys()))

    def add_edge(self, src: str, dst: str, edge_type: str, props: dict = None):
        """Buffer edge for batch insert"""
        if props:
            prop_str = ", ".join(f'"{v}"' if isinstance(v, str) else str(v)
                                for v in props.values())
            self.edge_buffer.append(f'"{src}" -> "{dst}":({prop_str})')
        else:
            self.edge_buffer.append(f'"{src}" -> "{dst}":()')

        if len(self.edge_buffer) >= self.batch_size:
            self._flush_edges(edge_type, list((props or {}).keys()))

    def _flush_vertices(self, tag: str, columns: list):
        if not self.vertex_buffer:
            return
        col_str = ", ".join(columns)
        values = ", ".join(self.vertex_buffer)
        query = f"INSERT VERTEX {tag}({col_str}) VALUES {values}"
        self.client.execute(query)
        self.vertex_buffer.clear()

    def _flush_edges(self, edge_type: str, columns: list):
        if not self.edge_buffer:
            return
        col_str = ", ".join(columns) if columns else ""
        values = ", ".join(self.edge_buffer)
        query = f"INSERT EDGE {edge_type}({col_str}) VALUES {values}"
        self.client.execute(query)
        self.edge_buffer.clear()

    def flush_all(self, tag: str = None, edge_type: str = None,
                  vertex_cols: list = None, edge_cols: list = None):
        """Flush all buffers"""
        if self.vertex_buffer and tag:
            self._flush_vertices(tag, vertex_cols or [])
        if self.edge_buffer and edge_type:
            self._flush_edges(edge_type, edge_cols or [])
```

## Schema Design Guidelines

| Entity Type | VID Strategy | Example |
|------------|--------------|---------|
| Company | `{type}_{id}` | `company_001` |
| Person | `{type}_{id}` | `person_001` |
| Event | `{type}_{timestamp}_{hash}` | `event_20240125_abc123` |
| Composite | `{type1}_{id1}_{type2}_{id2}` | `rel_company_001_person_001` |

## Performance Tips

| Scenario | Recommendation |
|----------|----------------|
| Large result sets | Use `LIMIT` and pagination |
| Frequent property lookup | Create tag indexes |
| Edge filtering | Create edge indexes on filter properties |
| Deep traversal | Limit depth, use `SAMPLE` |
| Batch insert | Use batch size 1000-5000 |

## nGQL vs Cypher

| Operation | nGQL | Cypher |
|-----------|------|--------|
| Traverse | `GO FROM vid OVER edge` | `MATCH (n)-[r]->(m)` |
| Lookup | `LOOKUP ON tag WHERE ...` | `MATCH (n:Tag) WHERE ...` |
| Path | `FIND PATH FROM ... TO ...` | `MATCH path = (a)-[*]-(b)` |
| Subgraph | `GET SUBGRAPH n STEPS FROM vid` | N/A |

## Best Practices

1. **VID design** - Use meaningful, stable identifiers
2. **Index first** - Create indexes before bulk loading
3. **Partition wisely** - Match partition_num to cluster size
4. **Batch operations** - Never insert one-by-one
5. **Limit depth** - Cap traversal at 3-5 hops
6. **TTL for events** - Set TTL on time-sensitive data

## References

- [NebulaGraph Documentation](https://docs.nebula-graph.io/)
- [nGQL Manual](https://docs.nebula-graph.io/master/3.ngql-guide/)
- [Python Client](https://github.com/vesoft-inc/nebula-python)
