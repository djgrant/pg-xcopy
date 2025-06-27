import sys
import subprocess
import re
from typing import List
import psycopg2
from psycopg2.extras import DictCursor


def quote_sql_identifier(identifier):
    """Safely quotes a SQL identifier (schema, table, column) to handle special characters."""
    return f'"{identifier}"'


def get_db_connection(database_url, db_name_for_error=""):
    """Establishes a database connection from a URL."""
    if not database_url:
        print(
            f"Error: Database connection URL for '{db_name_for_error}' is not configured.",
            file=sys.stderr,
        )
        print(
            "Please set the corresponding DATABASE_URL environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        conn = psycopg2.connect(dsn=database_url)
        return conn
    except psycopg2.OperationalError as e:
        print(
            f"Error connecting to '{db_name_for_error}' database: {e}", file=sys.stderr
        )
        sys.exit(1)


def get_tables_for_schema(conn, schema_name):
    """Gets a list of all table names in a given schema."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE';
        """,
            (schema_name,),
        )
        return [row[0] for row in cur.fetchall()]


def get_table_columns(conn, schema_name, table_name):
    """
    Gets column names and their full data types for a given table,
    correctly formatting array types.
    """
    with conn.cursor(cursor_factory=DictCursor) as cur:
        sql = """
            SELECT
                a.attname AS column_name,
                pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type
            FROM
                pg_catalog.pg_attribute a
            JOIN
                pg_catalog.pg_class c ON a.attrelid = c.oid
            JOIN
                pg_catalog.pg_namespace n ON c.relnamespace = n.oid
            WHERE
                n.nspname = %s
                AND c.relname = %s
                AND a.attnum > 0
                AND NOT a.attisdropped
            ORDER BY
                a.attnum;
        """
        cur.execute(sql, (schema_name, table_name))
        return cur.fetchall()


def redact_command(command_array):
    """Redacts passwords from database URLs in a command list for safe logging."""
    redacted_cmd = []
    pattern = re.compile(r"(postgresql://[^:]+:)([^@]+)(@)")
    for part in command_array:
        redacted_part = pattern.sub(r"\1<redacted>\3", part)
        redacted_cmd.append(redacted_part)
    return redacted_cmd


def run_command(command_array, stdin_pipe=None, check=True):
    """Helper to run a subprocess command."""
    try:
        process = subprocess.run(
            command_array, stdin=stdin_pipe, capture_output=True, text=True, check=check
        )
        return process
    except subprocess.CalledProcessError as e:
        redacted_cmd_str = " ".join(redact_command(e.cmd))
        print(f"Error executing command: {redacted_cmd_str}", file=sys.stderr)
        print(f"Stderr: {e.stderr}", file=sys.stderr)
        sys.exit(1)


def teardown_and_create_schemas(conn, schemas):
    """Drops and recreates a list of schemas in the target database."""
    print(f"Tearing down and recreating schemas: {', '.join(schemas)}")
    with conn.cursor() as cur:
        for schema in schemas:
            q_schema = quote_sql_identifier(schema)
            print(f"  - Processing schema: {schema}")
            cur.execute(f"DROP SCHEMA IF EXISTS {q_schema} CASCADE;")
            cur.execute(f"CREATE SCHEMA {q_schema};")
    conn.commit()
    print("Schemas created successfully.")


def create_local_table_structure(
    source_conn,
    target_conn,
    source_schema,
    target_schema,
    table_name,
    columns_to_create: List[str],
    verbose=False,
):
    """
    Creates a local table structure in the target DB using an explicit list of columns.
    The data types are looked up from the source table.
    """
    q_target_schema = quote_sql_identifier(target_schema)
    q_table_name = quote_sql_identifier(table_name)

    if not columns_to_create:
        print(
            f"  - INFO: No columns selected for table {target_schema}.{table_name}. Skipping creation."
        )
        return

    source_column_info = {
        c["column_name"]: c["data_type"]
        for c in get_table_columns(source_conn, source_schema, table_name)
    }

    column_defs = []
    for col_name in columns_to_create:
        data_type = source_column_info.get(col_name)
        if not data_type:
            print(
                f"  - ERROR: Could not find data type for column '{col_name}' in source table {source_schema}.{table_name}. Skipping table creation.",
                file=sys.stderr,
            )
            return
        column_defs.append(f"{quote_sql_identifier(col_name)} {data_type}")

    create_sql = (
        f"CREATE TABLE {q_target_schema}.{q_table_name} ({', '.join(column_defs)})"
    )
    if verbose:
        print(f"  - Executing: {create_sql}")

    with target_conn.cursor() as cur:
        cur.execute(create_sql)
    target_conn.commit()


def get_all_relation_names(conn, schema_name):
    """
    Gets a list of all table and foreign table names in a given schema.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = %s;
        """,
            (schema_name,),
        )
        return [row[0] for row in cur.fetchall()]


def replicate_constraints(source_conn, target_conn, source_schema, target_schema):
    """
    Replicates constraints (PKs, FKs, etc.) and standalone indexes from a source schema
    to a target schema.
    """
    print(f"Replicating constraints and indexes: {source_schema} -> {target_schema}")

    ddl_statements = []
    with source_conn.cursor() as cur:
        # Get constraints (PKs, FKs, CHECKs, etc.)
        cur.execute(
            """
            SELECT 'ALTER TABLE ' || quote_ident(nspname) || '.' || quote_ident(relname) || ' ADD CONSTRAINT ' || quote_ident(conname) || ' ' || pg_get_constraintdef(pg_constraint.oid) || ';' as sql
            FROM pg_constraint
            JOIN pg_class ON conrelid = pg_class.oid
            JOIN pg_namespace ON pg_class.relnamespace = pg_namespace.oid
            WHERE nspname = %s
        """,
            (source_schema,),
        )
        ddl_statements.extend([row[0] for row in cur.fetchall()])

        # Get standalone indexes (indexes not created as part of a constraint)
        cur.execute(
            """
            SELECT pg_get_indexdef(i.indexrelid) || ';' as sql
            FROM pg_index i
            JOIN pg_class c_table ON c_table.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c_table.relnamespace
            LEFT JOIN pg_constraint con ON con.conindid = i.indexrelid
            WHERE n.nspname = %s AND con.conindid IS NULL
        """,
            (source_schema,),
        )
        ddl_statements.extend([row[0] for row in cur.fetchall()])

    with target_conn.cursor() as cur:
        for ddl in ddl_statements:
            # This will fail if the columns/tables for constraints/indexes don't exist, which is expected
            # when using a select transformation. This part of the logic might need to be skipped
            # in such cases, but for now, we let it fail if a definition is invalid.
            try:
                # The DDL generated by pg_get_... functions includes the source schema name.
                # We replace it with the target schema name. This is a simple but effective approach.
                cur.execute(ddl.replace(source_schema, target_schema))
            except psycopg2.Error as e:
                # Safely get the error message
                pg_error_msg = e.pgerror.strip() if e.pgerror else str(e)
                print(
                    f"  - INFO: Could not replicate constraint or index. This may be expected if columns were transformed. Error: {pg_error_msg}",
                    file=sys.stderr,
                )
                target_conn.rollback()  # Rollback the failed DDL transaction
            else:
                target_conn.commit()

    print("Constraint and index replication complete.")
