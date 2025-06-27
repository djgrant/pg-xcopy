import argparse
import fnmatch
import subprocess
import importlib.util
import sys
from typing import Any, Dict, Optional, Union, TypeVar, Tuple, List
from psycopg2.extensions import connection
from pydantic import ValidationError

import utils
from schemas import Job, Query

T = TypeVar("T")


def _build_where_clause(filters: Optional[Union[str, Dict[str, Any]]]) -> str:
    """Builds a SQL WHERE clause from a filter dictionary or a raw string."""
    if not filters:
        return ""

    if isinstance(filters, str):
        return f" WHERE {filters}"

    # If not a string, it must be a dict (due to Pydantic validation)
    conditions = []
    for column, value in filters.items():
        q_column = utils.quote_sql_identifier(column)
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            str_values = ", ".join(
                [f"'{v}'" if isinstance(v, str) else str(v) for v in value]
            )
            conditions.append(f"{q_column} IN ({str_values})")
        elif isinstance(value, dict):
            if "gte" in value:
                conditions.append(f"{q_column} >= '{value['gte']}'")
            if "lte" in value:
                conditions.append(f"{q_column} <= '{value['lte']}'")
        elif isinstance(value, bool):
            conditions.append(f"{q_column} = {str(value).lower()}")
        else:
            conditions.append(f"{q_column} = '{value}'")

    return f" WHERE {' AND '.join(conditions)}" if conditions else ""


def _build_column_lists(
    source_conn: "connection",
    source_schema: str,
    table_name: str,
    transform_config: Optional[Dict[str, Optional[str]]],
) -> Tuple[str, List[str]]:
    """
    Builds the SELECT list and the list of target columns for a transfer.
    By default, all source columns are copied. The transform_config can override this.
    - A string value in the config transforms a column's value.
    - A None value in the config excludes the column from the target.
    """
    source_columns = [
        c["column_name"]
        for c in utils.get_table_columns(source_conn, source_schema, table_name)
    ]

    transform_config = transform_config or {}

    final_target_columns = []
    select_expressions = []

    # Iterate over source columns to maintain order and apply transformations
    for col_name in source_columns:
        # Check if this column is explicitly excluded (e.g., {"col": None})
        if transform_config.get(col_name) is None and col_name in transform_config:
            continue

        final_target_columns.append(col_name)

        # Check if there's a transformation expression for the column
        expression = transform_config.get(col_name)
        if isinstance(expression, str):
            select_expressions.append(
                f"{expression} AS {utils.quote_sql_identifier(col_name)}"
            )
        else:
            # No transformation, just select the column as is
            select_expressions.append(utils.quote_sql_identifier(col_name))

    return ", ".join(select_expressions), final_target_columns


def _validate_schema(data: Union[T, Dict[str, Any]], schema_class: type[T]) -> T:
    """Validate and convert data to schema object."""
    try:
        if isinstance(data, dict):
            return schema_class(**data)
        return data

    except ValidationError as e:
        print(f"Error: Invalid {schema_class.__name__} configuration:")
        for error in e.errors():
            location = " -> ".join(str(loc) for loc in error["loc"])
            field_type = error["type"]
            if field_type == "missing":
                print(f"  {location}: Missing required field")
            else:
                print(f"  {location}: {error['msg']}")
        sys.exit(1)


def run_job(job: Union[Job, Dict[str, Any]], verbose: bool = False):
    """Handles a single data transfer job."""
    job = _validate_schema(job, Job)

    source_conn = utils.get_db_connection(job.source.database, "Source")
    target_conn = utils.get_db_connection(job.target.database, "Target")

    try:
        source_db = job.source.database
        source_schema = job.source.schema_
        target_db = job.target.database
        target_schema = job.target.schema_

        print(f"\n>>> Processing Job: {source_schema} -> {target_schema} <<<")

        utils.teardown_and_create_schemas(target_conn, [target_schema])

        source_tables = utils.get_all_relation_names(source_conn, source_schema)
        config_tables: Dict[str, Query] = job.tables

        tables_to_process = list(config_tables.keys())
        if "*" in config_tables:
            tables_to_process = source_tables

        # Determine target columns and create tables first
        # This mapping will store the final list of columns for each table
        table_column_map: Dict[str, List[str]] = {}

        print(
            f"\n--- Introspecting and creating table structures in schema: {target_schema} ---"
        )
        for table_name in tables_to_process:
            table_config = config_tables.get(table_name, config_tables.get("*"))
            if table_config is None:
                continue

            # We need to know the final columns to create the table correctly
            _, final_columns = _build_column_lists(
                source_conn, source_schema, table_name, table_config.transform
            )
            table_column_map[table_name] = final_columns

            utils.create_local_table_structure(
                source_conn,
                target_conn,
                source_schema,
                target_schema,
                table_name,
                columns_to_create=final_columns,
                verbose=verbose,
            )

        print(f"\n--- Transferring data to schema: {target_schema} ---")
        for table_name in tables_to_process:
            table_config = config_tables.get(table_name, config_tables.get("*"))
            if table_config is None:
                continue

            q_source_schema = utils.quote_sql_identifier(source_schema)
            q_target_schema = utils.quote_sql_identifier(target_schema)
            q_table = utils.quote_sql_identifier(table_name)

            where_clause = _build_where_clause(table_config.where)
            select_list, selected_columns = _build_column_lists(
                source_conn, source_schema, table_name, table_config.transform
            )

            # This should now match what create_local_table_structure used
            assert selected_columns == table_column_map[table_name]

            if not selected_columns:
                print(
                    f"Skipping data transfer for {source_schema}.{table_name}: no columns selected."
                )
                continue

            # --- EXPORT COMMAND ---
            select_query = (
                f"SELECT {select_list} FROM {q_source_schema}.{q_table}{where_clause}"
            )
            copy_out_cmd = rf"\copy ({select_query}) TO STDOUT WITH CSV HEADER"
            psql_export_cmd = ["psql", "-d", source_db, "-c", copy_out_cmd]

            # --- IMPORT COMMAND (with explicit columns) ---
            q_selected_columns = [
                utils.quote_sql_identifier(c) for c in selected_columns
            ]
            copy_in_columns = f"({', '.join(q_selected_columns)})"
            copy_in_cmd = rf"\copy {q_target_schema}.{q_table} {copy_in_columns} FROM STDIN WITH CSV HEADER"
            psql_import_cmd = [
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-d",
                target_db,
                "-c",
                copy_in_cmd,
            ]

            print(
                f"Transferring data for: {source_schema}.{table_name} -> {target_schema}.{table_name}"
            )
            if verbose:
                print(f"  - SELECT query: {select_query}")

            # Use a with statement to ensure the subprocess pipe is closed
            with subprocess.Popen(psql_export_cmd, stdout=subprocess.PIPE) as exporter:
                if exporter.stdout:
                    utils.run_command(psql_import_cmd, stdin_pipe=exporter.stdout)
                # Popen.wait() is called automatically on exit of the 'with' block
                if exporter.returncode and exporter.returncode != 0:
                    print(
                        f"Error: The export process failed with code {exporter.returncode}"
                    )
                    # You might want to handle this error more gracefully
                    sys.exit(1)

        utils.replicate_constraints(
            source_conn, target_conn, source_schema, target_schema
        )
    finally:
        source_conn.close()
        target_conn.close()


def run_jobs(job_pattern: str, jobs_config: Dict[str, Any], verbose: bool = False):
    """
    Finds and runs all jobs in a configuration dictionary that match a pattern.

    Args:
        job_pattern: A glob-style pattern to match against job names.
        jobs_config: A dictionary where keys are job names and values are job configs.
        verbose: Enable verbose logging.
    """
    matched_jobs = {
        name: job
        for name, job in jobs_config.items()
        if fnmatch.fnmatch(name, job_pattern)
    }

    if not matched_jobs:
        print(f"Error: No jobs found matching pattern '{job_pattern}'.")
        return

    print(f"--- Running {len(matched_jobs)} jobs matching pattern: {job_pattern} ---")

    for name, job_config in matched_jobs.items():
        print(f"\n--- Starting Job: {name} ---")
        run_job(job_config, verbose)

    print(f"\n--- All jobs matching pattern '{job_pattern}' completed successfully ---")


def main():
    """The CLI entry point for pg-xcopy."""
    parser = argparse.ArgumentParser(
        description="A tool for cross-database materialization in PostgreSQL."
    )
    parser.add_argument(
        "job_pattern",
        help="The pattern to match job names against (e.g., mat:*, shift:reporting).",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="pg_xcopy_jobs.py",
        help="Path to the job configuration file.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging."
    )
    args = parser.parse_args()

    try:
        spec = importlib.util.spec_from_file_location("config", args.config)
        if spec is None or spec.loader is None:
            print(f"Error: Could not load configuration file '{args.config}'")
            sys.exit(1)
        config_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config_module)
        JOBS = config_module.JOBS

    except FileNotFoundError:
        print(f"Error: Configuration file not found at '{args.config}'")
        sys.exit(1)

    except AttributeError:
        print(f"Error: 'JOBS' dictionary not found in '{args.config}'")
        sys.exit(1)

    run_jobs(args.job_pattern, JOBS, args.verbose)
