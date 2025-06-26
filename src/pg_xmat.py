import argparse
import fnmatch
import subprocess
import importlib.util
import sys
from typing import Any, Dict, Optional, Union, TypeVar
from psycopg2.extensions import connection
from pydantic import ValidationError

import utils
from schemas import Job, Jobs, Query

T = TypeVar('T')

def _build_where_clause(filters: Optional[Dict[str, Any]]) -> str:
    """Builds a SQL WHERE clause from a filter dictionary."""
    if not filters:
        return ""
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


def _build_select_list(
    source_conn: "connection",
    source_schema: str,
    table_name: str,
    select_config: Optional[Dict[str, str]],
) -> str:
    """Builds the SELECT list for a query, applying transformations from config."""
    all_columns = [
        c["column_name"]
        for c in utils.get_table_columns(source_conn, source_schema, table_name)
    ]

    if not select_config:
        return "*"

    select_expressions = []
    processed_columns = set()

    for col_name, expression in select_config.items():
        formatted_expression = expression.format(
            column_name=utils.quote_sql_identifier(col_name)
        )
        select_expressions.append(
            f"{formatted_expression} AS {utils.quote_sql_identifier(col_name)}"
        )
        processed_columns.add(col_name)

    for col_name in all_columns:
        if col_name not in processed_columns:
            select_expressions.append(utils.quote_sql_identifier(col_name))

    return ", ".join(select_expressions)


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

        print(f"\n--- Creating table structures in schema: {target_schema} ---")
        for table_name in tables_to_process:
            utils.create_local_table_structure(
                source_conn,
                target_conn,
                source_schema,
                target_schema,
                table_name,
                verbose,
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
            select_list = _build_select_list(
                source_conn, source_schema, table_name, table_config.select
            )

            select_query = (
                f"SELECT {select_list} FROM {q_source_schema}.{q_table}{where_clause}"
            )
            copy_out_cmd = rf"\copy ({select_query}) TO STDOUT WITH CSV"
            psql_export_cmd = ["psql", "-d", source_db, "-c", copy_out_cmd]

            copy_in_cmd = rf"\copy {q_target_schema}.{q_table} FROM STDIN WITH CSV"
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

            exporter = subprocess.Popen(psql_export_cmd, stdout=subprocess.PIPE)
            utils.run_command(psql_import_cmd, stdin_pipe=exporter.stdout)
            exporter.wait()

        utils.replicate_constraints_local(
            source_db, target_db, source_schema, target_schema
        )
    finally:
        source_conn.close()
        target_conn.close()


def run_jobs(
    job_pattern: str, jobs: Union[Jobs, Dict[str, Any]], verbose: bool = False
):
    """
    Finds and runs all jobs in a configuration dictionary that match a pattern.

    Args:
        job_pattern: A glob-style pattern to match against job names.
        jobs: Either a Jobs object or a dictionary where keys are job names and values are job configs.
        verbose: Enable verbose logging.
    """

    jobs = _validate_schema(jobs, Jobs)

    matched_jobs = {
        name: job
        for name, job in jobs.jobs.items()
        if fnmatch.fnmatch(name, job_pattern)
    }

    if not matched_jobs:
        print(f"Error: No jobs found matching pattern '{job_pattern}'.")
        return

    print(f"--- Running {len(matched_jobs)} jobs matching pattern: {job_pattern} ---")

    for name, job in matched_jobs.items():
        print(f"\n--- Starting Job: {name} ---")
        run_job(job, verbose)

    print(f"\n--- All jobs matching pattern '{job_pattern}' completed successfully ---")


def main():
    """The CLI entry point for pg-xmat."""
    parser = argparse.ArgumentParser(
        description="A tool for cross-database materialization in PostgreSQL."
    )
    parser.add_argument(
        "job_pattern",
        help="The pattern to match job names against (e.g., mat:*, shift:reporting).",
    )
    parser.add_argument(
        "-c", "--config", default="pg_xmat_jobs.py", help="Path to the job configuration file."
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
