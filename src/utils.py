import sys
import subprocess
import re
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
    source_conn, target_conn, source_schema, target_schema, table_name, verbose=False
):
    """Creates a local table structure in the target DB based on the source DB's definition."""
    q_target_schema = quote_sql_identifier(target_schema)
    q_table_name = quote_sql_identifier(table_name)

    columns = get_table_columns(source_conn, source_schema, table_name)
    if not columns:
        print(
            f"  - WARNING: Could not get column info for {source_schema}.{table_name}. Skipping."
        )
        return

    column_defs = [
        f"{quote_sql_identifier(c['column_name'])} {c['data_type']}" for c in columns
    ]
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


def replicate_constraints_local(
    source_db_url, target_db_url, source_schema, target_schema
):
    """
    Replicates constraints (PKs, FKs, indexes, etc.) from a source schema
    to a target schema using pg_dump.
    """
    print(f"Replicating constraints: {source_schema} -> {target_schema}")

    pg_dump_cmd = [
        "pg_dump",
        "--section=post-data",
        "-d",
        source_db_url,
        "--schema",
        source_schema,
    ]

    sed_script = (
        rf"s/SET search_path = {source_schema},/SET search_path = {quote_sql_identifier(target_schema)},/g; "
        rf"s/ALTER TABLE ONLY {source_schema}\\. /ALTER TABLE ONLY {quote_sql_identifier(target_schema)}./g"
    )

    sed_cmd = ["sed", "-E", sed_script]
    psql_cmd = ["psql", "-v", "ON_ERROR_STOP=1", "-d", target_db_url]

    dumper = subprocess.Popen(pg_dump_cmd, stdout=subprocess.PIPE)
    sed = subprocess.Popen(sed_cmd, stdin=dumper.stdout, stdout=subprocess.PIPE)
    run_command(psql_cmd, stdin_pipe=sed.stdout)
    dumper.wait()
    print("Constraint replication complete.")
