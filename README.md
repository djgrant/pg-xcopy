# pg-xmat

**Cross-database materialisations for PostgreSQL.**

pg-xmat lets you define jobs for transforming and streaming data between different database servers.

It is a good choice when more than one of the following are requirements:
- one-off/batch jobs
- moving data between database instances
- use-cases not suitable for FDWs
- programmatic use

## Installation

```bash
pip install pg-xmat
```

## Quick Start

1. Define jobs (`pg_xmat_jobs.py`):

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
                "where": {"user_id": [1, 2, 3, 4]}}
                "select": { "inserted_at": "current_date" },
            }
        }
    }
}
```

2. Run the job:

```bash
pg-xmat "export:users"

# or run all jobs matching glob expression
pg-xmat "export:*"
```

## How it works

1. **Schema Preparation**: Drops and recreates target schema, then replicates table structures from source
2. **Query Building**: Constructs filtered SELECT queries based on your `where` and `select` configurations
3. **Streaming Transfer**: Uses PostgreSQL's `COPY` command to stream data directly between databases
4. **Constraint Replication**: Copies indexes, foreign keys, and constraints using `pg_dump --section=post-data`

The process is designed to be fast and maintain data integrity by leveraging PostgreSQL's native bulk operations rather than row-by-row processing.

## CLI Usage

```bash
pg-xmat [job_pattern] [options]

Arguments:
  job_pattern           Glob pattern to match job names (e.g., "export:*", "mat:users")

Options:
  -c, --config FILE     Path to configuration file (default: pg_xmat_jobs.py)
  -v, --verbose         Enable verbose logging
  -h, --help            Show help message
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
                "where": {...},     # Optional: Filter conditions
                "select": {...}     # Optional: Column transformations
            }
        }
    }
}
```

### Where Filters

Filter data during transfer using various condition types:

```python
"where": {
    # Exact match
    "status": "active",

    # IN clause (list/tuple)
    "user_id": [1, 2, 3, 4],
    "category": ("A", "B", "C"),

    # Range queries (dict)
    "created_at": {"gte": "2023-01-01", "lte": "2023-12-31"},
    "price": {"gte": 100},
    "score": {"lte": 90},

    # Boolean values
    "is_active": True,
    "is_deleted": False,
}
```

### Select Transformations

Transform columns during transfer using SQL expressions:

```python
"select": {
    "created_at": "DATE({column_name})",                   # Extract date part
    "full_name": "CONCAT(first_name, ' ', last_name)",     # Concatenate columns
    "shifted_date": "{column_name} + INTERVAL '30 days'",  # Date arithmetic
    "normalized_email": "LOWER(TRIM({column_name}))"       # Text normalization
}
```

The `{column_name}` placeholder is automatically replaced with the properly quoted column name.

### Wildcard Tables

Process all tables in a schema:

```python
"tables": {
    "*": {
        "where": {"tenant_id": 123}  # Applied to all tables that have this column
    }
}
```

## Python API

### Basic Usage

```python
from pg_xmat import run_job, run_jobs

# Run a single job
job_config = {
    "source": {"database": "postgresql://...", "schema": "public"},
    "target": {"database": "postgresql://...", "schema": "staging"},
    "tables": {"users": {"where": {"active": True}}}
}
run_job(job_config, verbose=True)

# Run multiple jobs with pattern matching
jobs_config = {"export:users": job_config, "export:orders": {...}}
run_jobs("export:*", jobs_config, verbose=True)
```


## Examples

### Data Migration

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

### Data Anonymization

```python
ANONYMIZE_JOB = {
    "source": {"database": PROD_DB_URL, "schema": "public"},
    "target": {"database": TEST_DB_URL, "schema": "public"},
    "tables": {
        "users": {
            "select": {
                # Replace real emails with user1@example.com, user2@example.com, etc.
                "email": "'user' || id || '@example.com'",
                # Generate fake phone numbers like +15550000001, +15550000002, etc.
                "phone": "'+1555' || LPAD(id::text, 7, '0')",
                # Replace real names with "Test User 1", "Test User 2", etc.
                "name": "'Test User ' || id"
            }
        }
    }
}
```

### Time-shifted Data

```python
SHIFT_JOB = {
    "source": {"database": PROD_DB_URL, "schema": "events"},
    "target": {"database": TEST_DB_URL, "schema": "events"},
    "tables": {
        "*": {
            "select": {
                "created_at": "{column_name} + (CURRENT_DATE - DATE '2023-06-01')",
                "updated_at": "{column_name} + (CURRENT_DATE - DATE '2023-06-01')"
            }
        }
    }
}
```

### Multi-tenant Data Extraction

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
- PostgreSQL client tools (`psql`, `pg_dump`)
- Network access between source and target databases

## Security

- Database passwords are automatically redacted in log output
- SQL identifiers are properly quoted to prevent injection
- Environment variables recommended for sensitive connection strings

## License

MIT License
