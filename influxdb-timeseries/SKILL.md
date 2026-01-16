---
name: influxdb-timeseries
description: InfluxDB time-series database patterns for metrics, IoT data, and financial market data. Use when storing time-series data, writing Flux queries, or designing retention policies.
---

# InfluxDB Time-Series Patterns

Production patterns for InfluxDB time-series database with Flux queries, schema design, and performance optimization.

## When to Use

Triggers: "InfluxDB", "时序数据", "Flux", "行情缓存", "metrics", "time-series", "retention policy"

## Core Patterns

### 1. Data Model

```
InfluxDB Data Model:
┌─────────────────────────────────────────────────────────────┐
│ Measurement: stock_quotes                                   │
├─────────────────────────────────────────────────────────────┤
│ Tags (indexed):     symbol=AAPL, exchange=NASDAQ            │
│ Fields (values):    price=150.25, volume=1000000            │
│ Timestamp:          2024-01-15T09:30:00Z                    │
└─────────────────────────────────────────────────────────────┘

Line Protocol:
stock_quotes,symbol=AAPL,exchange=NASDAQ price=150.25,volume=1000000 1705311000000000000
```

**Key Concepts**:
- **Measurement**: Like a table (e.g., `stock_quotes`, `order_book`)
- **Tags**: Indexed metadata for filtering (e.g., `symbol`, `exchange`)
- **Fields**: Actual values, not indexed (e.g., `price`, `volume`)
- **Timestamp**: Nanosecond precision, always present

### 2. Schema Design

```python
# Line protocol format: measurement,tags fields timestamp

# Stock quotes
"""
stock_quotes,symbol=AAPL,exchange=NASDAQ bid=149.50,ask=150.00,last=149.75,volume=1000000 1705311000000000000
stock_quotes,symbol=TSLA,exchange=NASDAQ bid=248.00,ask=248.50,last=248.25,volume=500000 1705311000000000000
"""

# Order book snapshots
"""
order_book,symbol=AAPL,side=bid,level=1 price=149.50,size=1000 1705311000000000000
order_book,symbol=AAPL,side=bid,level=2 price=149.45,size=2000 1705311000000000000
order_book,symbol=AAPL,side=ask,level=1 price=150.00,size=1500 1705311000000000000
"""

# OHLCV candles
"""
ohlcv,symbol=AAPL,interval=1m open=149.50,high=150.25,low=149.25,close=150.00,volume=50000 1705311000000000000
ohlcv,symbol=AAPL,interval=5m open=149.00,high=150.50,low=148.75,close=150.25,volume=250000 1705311000000000000
"""

# Technical indicators
"""
indicators,symbol=AAPL,indicator=RSI value=65.5 1705311000000000000
indicators,symbol=AAPL,indicator=MACD value=2.35,signal=1.80,histogram=0.55 1705311000000000000
"""
```

### 3. Flux Query Basics

```flux
// Basic query structure
from(bucket: "market_data")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "stock_quotes")
  |> filter(fn: (r) => r.symbol == "AAPL")
  |> filter(fn: (r) => r._field == "last")

// Multiple fields
from(bucket: "market_data")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "stock_quotes")
  |> filter(fn: (r) => r.symbol == "AAPL")
  |> filter(fn: (r) => r._field == "bid" or r._field == "ask" or r._field == "last")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")

// Time range with specific timestamps
from(bucket: "market_data")
  |> range(start: 2024-01-15T09:30:00Z, stop: 2024-01-15T16:00:00Z)
  |> filter(fn: (r) => r._measurement == "stock_quotes")

// Last value
from(bucket: "market_data")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "stock_quotes")
  |> filter(fn: (r) => r.symbol == "AAPL")
  |> last()
```

### 4. Aggregations

```flux
// Window aggregation (OHLCV from ticks)
from(bucket: "market_data")
  |> range(start: -1d)
  |> filter(fn: (r) => r._measurement == "stock_quotes")
  |> filter(fn: (r) => r.symbol == "AAPL")
  |> filter(fn: (r) => r._field == "last")
  |> aggregateWindow(
      every: 1m,
      fn: (tables=<-, column) => tables
        |> reduce(
            identity: {open: 0.0, high: -999999.0, low: 999999.0, close: 0.0, count: 0},
            fn: (r, accumulator) => ({
                open: if accumulator.count == 0 then r._value else accumulator.open,
                high: if r._value > accumulator.high then r._value else accumulator.high,
                low: if r._value < accumulator.low then r._value else accumulator.low,
                close: r._value,
                count: accumulator.count + 1
            })
        )
  )

// Simpler: use built-in aggregations
from(bucket: "market_data")
  |> range(start: -1d)
  |> filter(fn: (r) => r._measurement == "stock_quotes")
  |> filter(fn: (r) => r.symbol == "AAPL")
  |> filter(fn: (r) => r._field == "last")
  |> aggregateWindow(every: 5m, fn: mean, createEmpty: false)

// Multiple aggregations
from(bucket: "market_data")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "stock_quotes")
  |> filter(fn: (r) => r._field == "volume")
  |> group(columns: ["symbol"])
  |> sum()

// Moving average
from(bucket: "market_data")
  |> range(start: -1d)
  |> filter(fn: (r) => r._measurement == "stock_quotes")
  |> filter(fn: (r) => r._field == "last")
  |> movingAverage(n: 20)
```

### 5. Downsampling & Retention

```flux
// Create downsampled bucket
// Run via InfluxDB Tasks

option task = {name: "downsample_1m_to_1h", every: 1h}

from(bucket: "market_data")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "ohlcv")
  |> filter(fn: (r) => r.interval == "1m")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> set(key: "interval", value: "1h")
  |> to(bucket: "market_data_hourly", org: "myorg")
```

**Retention Policy Setup** (via CLI):
```bash
# Create bucket with retention
influx bucket create \
  --name market_data \
  --retention 7d \
  --org myorg

# Create long-term storage
influx bucket create \
  --name market_data_archive \
  --retention 365d \
  --org myorg
```

### 6. Python Client

```python
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from datetime import datetime, timezone
import pandas as pd

class InfluxDBService:
    def __init__(self, url: str, token: str, org: str, bucket: str):
        self.client = InfluxDBClient(url=url, token=token, org=org)
        self.bucket = bucket
        self.org = org
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()

    def write_quote(self, symbol: str, exchange: str,
                    bid: float, ask: float, last: float, volume: int,
                    timestamp: datetime = None):
        """Write single quote"""
        point = (
            Point("stock_quotes")
            .tag("symbol", symbol)
            .tag("exchange", exchange)
            .field("bid", bid)
            .field("ask", ask)
            .field("last", last)
            .field("volume", volume)
            .time(timestamp or datetime.now(timezone.utc), WritePrecision.NS)
        )
        self.write_api.write(bucket=self.bucket, org=self.org, record=point)

    def write_batch(self, points: list[Point]):
        """Write batch of points"""
        self.write_api.write(bucket=self.bucket, org=self.org, record=points)

    def get_latest_quote(self, symbol: str) -> dict | None:
        """Get latest quote for symbol"""
        query = f'''
        from(bucket: "{self.bucket}")
          |> range(start: -1h)
          |> filter(fn: (r) => r._measurement == "stock_quotes")
          |> filter(fn: (r) => r.symbol == "{symbol}")
          |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> last()
        '''
        tables = self.query_api.query(query, org=self.org)

        for table in tables:
            for record in table.records:
                return {
                    "symbol": record["symbol"],
                    "bid": record.get("bid"),
                    "ask": record.get("ask"),
                    "last": record.get("last"),
                    "volume": record.get("volume"),
                    "time": record.get_time()
                }
        return None

    def get_ohlcv(self, symbol: str, interval: str = "1m",
                  start: str = "-1d") -> pd.DataFrame:
        """Get OHLCV data as DataFrame"""
        query = f'''
        from(bucket: "{self.bucket}")
          |> range(start: {start})
          |> filter(fn: (r) => r._measurement == "ohlcv")
          |> filter(fn: (r) => r.symbol == "{symbol}")
          |> filter(fn: (r) => r.interval == "{interval}")
          |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        '''
        return self.query_api.query_data_frame(query, org=self.org)

    def get_price_series(self, symbol: str, field: str = "last",
                         start: str = "-1d", window: str = "1m") -> pd.DataFrame:
        """Get aggregated price series"""
        query = f'''
        from(bucket: "{self.bucket}")
          |> range(start: {start})
          |> filter(fn: (r) => r._measurement == "stock_quotes")
          |> filter(fn: (r) => r.symbol == "{symbol}")
          |> filter(fn: (r) => r._field == "{field}")
          |> aggregateWindow(every: {window}, fn: last, createEmpty: false)
        '''
        return self.query_api.query_data_frame(query, org=self.org)

    def close(self):
        self.client.close()


# Usage
service = InfluxDBService(
    url="http://localhost:8086",
    token="my-token",
    org="myorg",
    bucket="market_data"
)

# Write quote
service.write_quote("AAPL", "NASDAQ", 149.50, 150.00, 149.75, 1000000)

# Get latest
quote = service.get_latest_quote("AAPL")
print(f"AAPL: ${quote['last']}")

# Get OHLCV DataFrame
df = service.get_ohlcv("AAPL", interval="5m", start="-4h")
print(df.head())

service.close()
```

### 7. Batch Writing

```python
from influxdb_client.client.write_api import ASYNCHRONOUS, WriteOptions

class InfluxBatchWriter:
    def __init__(self, client: InfluxDBClient, bucket: str, org: str):
        self.bucket = bucket
        self.org = org
        # Batch configuration
        self.write_api = client.write_api(
            write_options=WriteOptions(
                batch_size=5000,
                flush_interval=1000,  # ms
                jitter_interval=500,
                retry_interval=5000,
                max_retries=3,
                max_retry_delay=30000,
                exponential_base=2
            )
        )

    def write_quotes_batch(self, quotes: list[dict]):
        """Write batch of quotes"""
        points = []
        for q in quotes:
            point = (
                Point("stock_quotes")
                .tag("symbol", q["symbol"])
                .tag("exchange", q.get("exchange", "UNKNOWN"))
                .field("bid", q["bid"])
                .field("ask", q["ask"])
                .field("last", q["last"])
                .field("volume", q["volume"])
                .time(q.get("timestamp", datetime.now(timezone.utc)))
            )
            points.append(point)

        self.write_api.write(bucket=self.bucket, org=self.org, record=points)

    def write_line_protocol(self, lines: list[str]):
        """Write raw line protocol"""
        self.write_api.write(bucket=self.bucket, org=self.org, record=lines)

    def flush(self):
        """Flush pending writes"""
        self.write_api.flush()

    def close(self):
        self.write_api.close()
```

### 8. Real-time Subscriptions

```python
from influxdb_client import InfluxDBClient
import asyncio

async def subscribe_quotes(client: InfluxDBClient, symbol: str,
                           callback, poll_interval: float = 1.0):
    """Poll for new quotes (InfluxDB doesn't have native pub/sub)"""
    query_api = client.query_api()
    last_time = None

    while True:
        query = f'''
        from(bucket: "market_data")
          |> range(start: -10s)
          |> filter(fn: (r) => r._measurement == "stock_quotes")
          |> filter(fn: (r) => r.symbol == "{symbol}")
          |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["_time"], desc: true)
          |> limit(n: 1)
        '''

        tables = query_api.query(query)
        for table in tables:
            for record in table.records:
                record_time = record.get_time()
                if last_time is None or record_time > last_time:
                    last_time = record_time
                    await callback({
                        "symbol": record["symbol"],
                        "last": record.get("last"),
                        "time": record_time
                    })

        await asyncio.sleep(poll_interval)


# Usage with async callback
async def on_quote(quote):
    print(f"New quote: {quote['symbol']} @ ${quote['last']}")

# asyncio.run(subscribe_quotes(client, "AAPL", on_quote))
```

## Tag vs Field Guidelines

| Use Case | Type | Reason |
|----------|------|--------|
| Symbol/Ticker | Tag | High cardinality but needed for filtering |
| Exchange | Tag | Low cardinality, filter/group by |
| Interval | Tag | Low cardinality, filter/group by |
| Price values | Field | Numeric, not for filtering |
| Volume | Field | Numeric, aggregation target |
| Indicator name | Tag | Used for filtering |
| Indicator value | Field | Numeric value |

## Retention Strategy

| Data Type | Retention | Bucket |
|-----------|-----------|--------|
| Tick data | 7 days | `market_realtime` |
| 1-min OHLCV | 30 days | `market_data` |
| 1-hour OHLCV | 1 year | `market_hourly` |
| Daily OHLCV | Forever | `market_daily` |

## Best Practices

1. **Tags for filtering** - Only index what you filter on
2. **Avoid high cardinality tags** - Don't use unique IDs as tags
3. **Batch writes** - Use batch size 1000-5000
4. **Downsample** - Use tasks for automatic aggregation
5. **Retention policies** - Set appropriate TTL per bucket
6. **Query time ranges** - Always specify reasonable ranges

## References

- [InfluxDB Documentation](https://docs.influxdata.com/influxdb/v2/)
- [Flux Language](https://docs.influxdata.com/flux/)
- [Python Client](https://github.com/influxdata/influxdb-client-python)
