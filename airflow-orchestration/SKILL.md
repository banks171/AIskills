---
name: airflow-orchestration
description: Apache Airflow workflow orchestration patterns for data pipelines, DAG design, and task scheduling. Use when creating DAGs, managing dependencies, or building data workflows.
---

# Airflow Orchestration Patterns

Production patterns for Apache Airflow DAGs with proper dependency management, error handling, and monitoring.

## When to Use

Triggers: "Airflow", "DAG", "workflow", "调度", "ETL", "data pipeline", "task scheduling"

## Core Patterns

### 1. DAG Structure

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.empty import EmptyOperator
from airflow.utils.dates import days_ago
from datetime import timedelta

default_args = {
    "owner": "data-team",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
}

with DAG(
    dag_id="example_pipeline",
    default_args=default_args,
    description="Example data pipeline",
    schedule_interval="0 */6 * * *",  # Every 6 hours
    start_date=days_ago(1),
    catchup=False,
    tags=["example", "etl"],
    max_active_runs=1,
) as dag:

    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    # Define tasks here
    start >> end
```

### 2. Task Patterns

```python
from airflow.decorators import task
from airflow.models import Variable

# TaskFlow API (recommended for Python tasks)
@task
def extract_data(source: str) -> dict:
    """Extract data from source"""
    # XCom automatically handles return value
    return {"records": 100, "source": source}

@task
def transform_data(data: dict) -> dict:
    """Transform extracted data"""
    return {"processed": data["records"], "status": "ok"}

@task
def load_data(data: dict) -> None:
    """Load data to destination"""
    print(f"Loaded {data['processed']} records")

# Usage in DAG
with DAG(...) as dag:
    raw = extract_data("api")
    transformed = transform_data(raw)
    load_data(transformed)
```

### 3. Sensor Patterns

```python
from airflow.sensors.filesystem import FileSensor
from airflow.sensors.external_task import ExternalTaskSensor
from airflow.sensors.python import PythonSensor
from datetime import timedelta

# File sensor - wait for file
wait_for_file = FileSensor(
    task_id="wait_for_file",
    filepath="/data/input/{{ ds }}.csv",
    poke_interval=60,  # Check every 60 seconds
    timeout=3600,      # Timeout after 1 hour
    mode="reschedule", # Free up worker slot while waiting
)

# External task sensor - wait for another DAG
wait_for_upstream = ExternalTaskSensor(
    task_id="wait_for_upstream",
    external_dag_id="upstream_dag",
    external_task_id="final_task",
    execution_delta=timedelta(hours=1),
    mode="reschedule",
)

# Custom sensor
def check_api_ready():
    import requests
    resp = requests.get("http://api/health")
    return resp.status_code == 200

wait_for_api = PythonSensor(
    task_id="wait_for_api",
    python_callable=check_api_ready,
    poke_interval=30,
    timeout=600,
    mode="reschedule",
)
```

### 4. Branching & Conditionals

```python
from airflow.operators.python import BranchPythonOperator
from airflow.operators.empty import EmptyOperator

def choose_branch(**context):
    """Choose processing path based on data volume"""
    ti = context["ti"]
    record_count = ti.xcom_pull(task_ids="extract", key="count")

    if record_count > 10000:
        return "heavy_processing"
    elif record_count > 0:
        return "light_processing"
    else:
        return "skip_processing"

branch = BranchPythonOperator(
    task_id="branch_by_volume",
    python_callable=choose_branch,
)

heavy = EmptyOperator(task_id="heavy_processing")
light = EmptyOperator(task_id="light_processing")
skip = EmptyOperator(task_id="skip_processing")

# Join branches (trigger_rule is key)
join = EmptyOperator(
    task_id="join",
    trigger_rule="none_failed_min_one_success",
)

branch >> [heavy, light, skip] >> join
```

### 5. Dynamic Task Generation

```python
from airflow.decorators import task, dag
from airflow.utils.task_group import TaskGroup

# Dynamic task mapping (Airflow 2.3+)
@task
def get_partitions() -> list[str]:
    return ["partition_a", "partition_b", "partition_c"]

@task
def process_partition(partition: str) -> dict:
    return {"partition": partition, "status": "done"}

with DAG(...) as dag:
    partitions = get_partitions()
    # Creates tasks dynamically based on partitions
    results = process_partition.expand(partition=partitions)

# Task Groups for organization
with DAG(...) as dag:
    with TaskGroup("extract_group") as extract:
        extract_a = PythonOperator(...)
        extract_b = PythonOperator(...)

    with TaskGroup("transform_group") as transform:
        transform_a = PythonOperator(...)
        transform_b = PythonOperator(...)

    extract >> transform
```

### 6. Error Handling & Callbacks

```python
from airflow.operators.python import PythonOperator

def on_failure_callback(context):
    """Handle task failure"""
    task_instance = context["task_instance"]
    exception = context.get("exception")

    # Send alert
    send_alert(
        title=f"Task Failed: {task_instance.task_id}",
        message=str(exception),
        dag_id=task_instance.dag_id,
        execution_date=context["execution_date"],
    )

def on_success_callback(context):
    """Handle task success"""
    # Log metrics, update status, etc.
    pass

def on_retry_callback(context):
    """Handle task retry"""
    # Log retry attempt
    pass

task_with_callbacks = PythonOperator(
    task_id="critical_task",
    python_callable=process_data,
    on_failure_callback=on_failure_callback,
    on_success_callback=on_success_callback,
    on_retry_callback=on_retry_callback,
)

# DAG-level callbacks
with DAG(
    ...,
    on_failure_callback=dag_failure_callback,
    on_success_callback=dag_success_callback,
) as dag:
    pass
```

### 7. Connections & Variables

```python
from airflow.hooks.base import BaseHook
from airflow.models import Variable

# Get connection (stored in Airflow metadata DB)
def get_database_conn():
    conn = BaseHook.get_connection("mysql_default")
    return {
        "host": conn.host,
        "port": conn.port,
        "user": conn.login,
        "password": conn.password,
        "database": conn.schema,
    }

# Get variables (with default and JSON parsing)
config = Variable.get("pipeline_config", deserialize_json=True)
api_key = Variable.get("api_key", default_var="")

# Template variables in operators
task = BashOperator(
    task_id="templated_task",
    bash_command="echo {{ var.value.my_var }} {{ ds }} {{ ts }}",
)
```

### 8. Pool & Queue Management

```python
# Limit concurrent tasks with pools
heavy_task = PythonOperator(
    task_id="heavy_task",
    python_callable=heavy_processing,
    pool="heavy_processing_pool",  # Max 2 concurrent
    pool_slots=1,
)

# Route to specific queue/worker
gpu_task = PythonOperator(
    task_id="gpu_task",
    python_callable=gpu_processing,
    queue="gpu_workers",
)

# Priority weight (higher = sooner)
urgent_task = PythonOperator(
    task_id="urgent_task",
    python_callable=urgent_processing,
    priority_weight=10,
)
```

## DAG Design Matrix

| Use Case | Schedule | Catchup | Max Active Runs |
|----------|----------|---------|-----------------|
| Real-time ETL | `*/5 * * * *` | False | 1 |
| Daily reports | `0 6 * * *` | True | 1 |
| Event-driven | `None` | False | 3 |
| Backfill jobs | `@once` | False | 1 |

## Trigger Rules

| Rule | Description |
|------|-------------|
| `all_success` | All parents succeeded (default) |
| `all_failed` | All parents failed |
| `all_done` | All parents completed |
| `one_success` | At least one parent succeeded |
| `one_failed` | At least one parent failed |
| `none_failed` | No parent failed (skipped OK) |
| `none_failed_min_one_success` | None failed + at least one success |
| `always` | Run regardless of parent state |

## Best Practices

1. **Idempotency** - Tasks should produce same result on re-run
2. **Atomicity** - Tasks should succeed or fail completely
3. **No side effects** - Avoid modifying external state in sensors
4. **Small XComs** - Don't pass large data through XCom (use external storage)
5. **Catchup carefully** - Disable for real-time, enable for historical
6. **Pool resources** - Use pools to limit concurrent heavy tasks
7. **Monitor SLAs** - Set SLAs for critical pipelines

## References

- [Apache Airflow Documentation](https://airflow.apache.org/docs/)
- [TaskFlow API](https://airflow.apache.org/docs/apache-airflow/stable/tutorial/taskflow.html)
- [Best Practices](https://airflow.apache.org/docs/apache-airflow/stable/best-practices.html)
