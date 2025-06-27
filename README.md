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
                "where": {"user_id": [1, 2, 3, 4]},
                "select": { "inserted_at": "current_date" }
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

1. **Schema Preparation**: Drops and recreates target schema, then replicates table structures from source.
2. **Query Building**: Constructs filtered SELECT queries based on your `where` and `select` configurations.
3. **Streaming Transfer**: Uses PostgreSQL's `COPY` command to stream data directly between databases.
4. **Constraint Replication**: Replicates Primary Keys, Foreign Keys, and other constraints from the source tables to the newly created target tables.

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

#### Raw SQL Filter

For more complex conditions, provide a raw SQL string. The string is used as the body of the `WHERE` clause.

```python
"where": "is_active = true AND (category = 'A' OR name LIKE 'Test%')"
```

### Select Transformations

The `select` config allows you to precisely control the columns in the target table. It has two modes: exclusive (default) and inclusive (using a `*` wildcard).

#### Exclusive Column Selection (Default)
If the `select` dictionary does not contain a `"*"` key, it acts as an **exclusive list**. Only the columns defined in the dictionary will be created in the target table. This is useful for creating a subset of the original table.

```python
"select": {
    # The target table will have only two columns: `name` and `email`.
    "name": "UPPER(name)",               # Transform the 'name' column
    "email": "LOWER(email)"              # Transform the 'email' column
}
```

#### Select *

To transfer all source columns while transforming or excluding a few, add `"*": True` to the `select` dictionary.

```python
"select": {
    # Include all columns from the source table by default
    "*": True,

    # Transform the 'email' column using a SQL function
    "email": "LOWER(email)",

    # Exclude the 'last_login_ip' column completely from the target table
    "last_login_ip": None

    # All other columns (e.g., 'id', 'name') will be copied as-is
}
```

### Wildcard Tables

Process all tables in a schema:

```python
"tables": {
    "*": {
        "where": {"tenant_id": 123}
    }
}
```

### Constraint Replication

After transferring data, pg-xmat attempts to replicate constraints from the source to the target tables.

**What is Replicated:**
- Primary Keys
- Foreign Keys
- Unique Constraints
- Check Constraints

**What is NOT Replicated:**
- Triggers
- Row-Level Security Policies

#### Caveats and Limitations

Constraint replication is **best-effort** and works best when the target table structure is a mirror of the source. The process will fail for a specific constraint if `select` transformations alter the table's structure in a way that makes the constraint invalid.

Common failure scenarios include:
- **Excluding a column** that is part of a constraint (e.g., excluding a primary key column or a foreign key column).
- **Renaming a column** that is referenced in a constraint. The replication attempts to use the original column name, which no longer exists in the target table.
- **Excluding a referenced table**. A foreign key from `orders` to `customers` cannot be created if the `customers` table was not also transferred.

When a constraint fails to apply, `pg-xmat` prints an informational warning and **continues the job**. This is often expected behavior, as a transformed or subsetted table may not be able to support the same constraints as the original.

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
                # Keep all columns except the ones we transform or exclude.
                # This ensures columns like `id` and `created_at` are copied.
                "*": True,
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

### Time-shifted Data

```python
SHIFT_JOB = {
    "source": {"database": PROD_DB_URL, "schema": "events"},
    "target": {"database": TEST_DB_URL, "schema": "events"},
    "tables": {
        # Apply transformations to all tables in the 'events' schema
        "*": {
            "select": {
                # Copy all columns from each source table
                "*": True,
                # Shift timestamp columns if they exist in a table.
                "created_at": "created_at + (CURRENT_DATE - DATE '2023-06-01')",
                "updated_at": "updated_at + (CURRENT_DATE - DATE '2023-06-01')"
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
- PostgreSQL client tools (`psql`)
- Network access between source and target databases

## Security

- Database passwords are automatically redacted in log output
- SQL identifiers are properly quoted to prevent injection
- Environment variables recommended for sensitive connection strings

## License

MIT License
