# pg-xcopy

Postgres `\copy` on steroids.

A lightweight configuration-driven tool for performing powerful, cross-database transfers, supporting:

- **Declarative Transfers**: Orchestrates cross-database transfers from simple configuration
- **Filtering**: Transfers a subset of rows (e.g., `WHERE tenant_id = 123`)
- **Transformation**: Changes the values of columns in flight (e.g., `LOWER(email)`)
- **Repeatability**: Defines jobs in code that can be run on-demand or as part of a larger workflow

Suitable for tasks sucha as:

- Creating anonymized staging environments
- Extracting tenant-specific data into separate databases
- Performing surgical data migrations between microservices
- Time-shifting datasets for stable testing environments

## Quick Start

1. Install

```bash
pip install pg-xcopy
```

2.  Define jobs in a file (e.g., `pg_xcopy_jobs.py`):

```python
import os

SOURCE_DB_URL = os.getenv("SOURCE_DB_URL")
TARGET_DB_URL = os.getenv("TARGET_DB_URL")

JOBS = {
    "export:users": {
        "source": {"database": SOURCE_DB_URL, "schema": "public"},
        "target": {"database": TARGET_DB_URL, "schema": "staging"},
        "tables": {
            "users": {"where": {"active": True}},
            "profiles": {
                "where": {"user_id": [1, 2, 3, 4]},
                "transform": { "inserted_at": "current_date" }
            }
        }
    }
}
```

3.  Run the job:

```bash
pg-xcopy "export:users" -c pg_xcopy_jobs.py

# or run all jobs matching a glob expression
pg-xcopy "export:*" -c pg_xcopy_jobs.py
```

## How it works

pg-xcopy orchestrates psql to build an efficient data pipeline between the source and target databases.

1.  **Schema Preparation**: Drops and recreates the target schema, then replicates table structures from the source
2.  **Query Building**: Constructs filtered `SELECT` queries based on your `where` and `transform` configurations
3.  **Streaming Transfer**: Streams data directly from the source to the target, without writing temporary files to disk
4.  **Constraint Replication**: Replicates primary keys, foreign keys, indexes, and other constraints from the source tables

This architecture allows pg-xcopy to stream data between any two databases the client can connect to across networks without requiring superuser privileges on the database server.

## Comparisons

**1. `pg_dump` and `psql`**

-   **Ideal for:** Creating complete structural and data replicas of a database, schema, or table.
-   **Not ideal for:** Transferring a filtered or transformed subset of data from the source.

**2. Manual `\copy` Scripts**

-   **Ideal for:** Performing a single, specific data transfer with custom logic in an imperative script.
-   **Not ideal for:** Repeatable jobs that include schema and constraint replication.

**3. dbt / Dataform**

-   **Ideal for:** Modeling and transforming data that has already been loaded into a target database.
-   **Not ideal for:** Extracting and loading data from a separate source database.

**4. Foreign Data Wrappers (`postgres_fdw`)**

-   **Ideal for:** Executing live, online queries against tables in a remote database as if they were local.
-   **Not ideal for:** Performing efficient, offline bulk data transfers or jobs that require schema replication.

**5. Airflow / Dagster / Prefect**

-   **Ideal for:** Orchestrating complex, multi-dependency workflows that require scheduling, monitoring, and retries.
-   **Not ideal for:** Simple, point-to-point data transfers that do not require a separate, persistent orchestration infrastructure.

## CLI API

```bash
pg-xcopy [job_pattern] [options]

Arguments:
  job_pattern           Glob pattern to match job names (e.g., "export:*")

Options:
  -c, --config FILE     Path to configuration file (default: pg_xcopy_jobs.py)
  -v, --verbose         Enable verbose logging
  -h, --help            Show help message
```

## Python API

```python
from pg_xcopy import run_job, run_jobs

# Run a single job
job_config = {
    "source": {"database": "postgresql://...", "schema": "public"},
    "target": {"database": "postgresql://...", "schema": "staging"},
    "tables": {"users": {"where": {"active": True}}}
}
run_job(job_config, verbose=True)

# Run multiple jobs with pattern matching
all_jobs = {"export:users": job_config, "export:orders": {...}}
run_jobs("export:*", all_jobs, verbose=True)
```

## Job API

### Schema

```python
JOBS = {
    "job_name": {
        "source": {
            "database": "postgresql://...",  # Source database URL
            "schema": "schema_name"          # Source schema name
        },
        "target": {
            "database": "postgresql://...",  # Target database URL
            "schema": "schema_name"          # Target schema name
        },
        "tables": {
            "table_name": {
                "where": {...},      # Optional: Filter conditions
                "transform": {...}   # Optional: Column transformations/omissions
            }
        }
    }
}
```

### Where filters

Filter data during transfer using the structured dictionary syntax or a raw SQL string.

#### Structured Filters

For common equality, range, and `IN` clauses, use the dictionary format:

```python
"where": {
    # Exact match: "status" = 'active'
    "status": "active",

    # IN clause: "user_id" IN (1, 2, 3, 4)
    "user_id": [1, 2, 3, 4],

    # Range queries: "created_at" >= '2023-01-01'
    "created_at": {"gte": "2023-01-01"},

    # Boolean values: "is_active" = true
    "is_active": True
}
```

#### Raw SQL filter

For more complex conditions, provide a raw SQL string as the body of the `WHERE` clause:

```python
"where": "is_active = true AND (category = 'A' OR name LIKE 'Test%')"
```

### Column transformations

By default, `pg-xcopy` copies all columns from the source table to the target. The `transform` configuration allows you to specify exceptions to this rule.

-   **To transform a column's value**, provide a SQL expression as a string.
-   **To omit a column from the target table**, provide `None` as the value.
-   **To keep a column as-is**, simply do not include it in the `transform` dictionary.

```python
"transform": {
    # Transform the 'email' column using a SQL function
    "email": "LOWER(email)",

    # Exclude the 'last_login_ip' column completely from the target table
    "last_login_ip": None

    # All other columns (e.g., 'id', 'name') will be copied as-is
}
```

### Wildcard tables

Apply a configuration to all tables in a schema:

```python
"tables": {
    "*": {
        "where": {"tenant_id": 123}
    }
}
```

### Constraint replication

After transferring data, `pg-xcopy` attempts to replicate constraints from the source to the target tables.

**What is Replicated:**

-   Primary Keys
-   Foreign Keys
-   Unique Constraints
-   Check Constraints
-   Standalone Indexes

**What is NOT Replicated:**

-   Triggers
-   Row-Level Security Policies

#### Caveats

Constraint replication is **best-effort**. It will fail for a specific constraint if `transform` alters the table's structure in a way that makes the constraint invalid (e.g., omitting a column that is part of a primary key). When a failure occurs, `pg-xcopy` prints a warning and continues the job.

## Examples

### Data migration

```python
MIGRATION_JOB = {
    "source": {"database": PROD_DB_URL, "schema": "public"},
    "target": {"database": STAGING_DB_URL, "schema": "public"},
    "tables": {
        "users": {"where": {"created_at": {"gte": "2023-01-01"}}},
        "orders": {"where": {"status": ["completed", "shipped"]}},
        "products": {"where": {"active": True}}
    }
}
```

### Data anonymisation

```python
ANONYMIZE_JOB = {
    "source": {"database": PROD_DB_URL, "schema": "public"},
    "target": {"database": TEST_DB_URL, "schema": "public"},
    "tables": {
        "users": {
            "transform": {
                # Replace real emails with user1@example.com, user2@example.com, etc.
                "email": "'user' || id || '@example.com'",
                # Exclude personal phone numbers from the test database
                "phone": None,
                # Replace real names with "Test User 1", "Test User 2", etc.
                "name": "'Test User ' || id"
            }
        }
    }
}
```

### Time-shifted data

```python
SHIFT_JOB = {
    "source": {"database": PROD_DB_URL, "schema": "events"},
    "target": {"database": TEST_DB_URL, "schema": "events"},
    "tables": {
        # Apply transformations to all tables in the 'events' schema
        "*": {
            "transform": {
                # Shift timestamp columns if they exist in a table.
                # Other columns will be copied as-is.
                "created_at": "created_at + (CURRENT_DATE - DATE '2023-06-01')",
                "updated_at": "updated_at + (CURRENT_DATE - DATE '2023-06-01')"
            }
        }
    }
}
```

### Multi-tenant data extraction

```python
TENANT_EXPORT = {
    "source": {"database": MAIN_DB_URL, "schema": "public"},
    "target": {"database": TENANT_DB_URL, "schema": "tenant_123"},
    "tables": {
        "users": {"where": {"tenant_id": 123}},
        "orders": {"where": {"tenant_id": 123, "status": ["active", "pending"]}},
        "analytics": {"where": {"tenant_id": 123, "date": {"gte": "2023-01-01"}}}
    }
}
```

## Requirements

- Python 3.7+
- PostgreSQL client tools (`psql`)

## Security

- Database passwords are automatically redacted in log output
- SQL identifiers are quoted to prevent injection

## License

MIT License
