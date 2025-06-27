import unittest
import os
import psycopg2
import sys
import pg_xcopy
from contextlib import contextmanager
from psycopg2.extensions import connection as Connection

from .test_config import (
    SOURCE_DB_URL,
    TARGET_DB_URL,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))


class PgXCopyIntegrationTestBase(unittest.TestCase):
    source_conn: Connection
    target_conn: Connection

    @classmethod
    def setUpClass(cls):
        """Connects to the databases which are now guaranteed to exist."""
        cls.source_conn = psycopg2.connect(SOURCE_DB_URL)
        cls.target_conn = psycopg2.connect(TARGET_DB_URL)

    @classmethod
    def tearDownClass(cls):
        """Close connections once per test class."""
        if hasattr(cls, "source_conn") and cls.source_conn:
            cls.source_conn.close()
        if hasattr(cls, "target_conn") and cls.target_conn:
            cls.target_conn.close()

    def setUp(self):
        """Prepare a clean slate before each test method."""
        self.setup_source_data()

    def tearDown(self):
        """Clean up all user-created schemas after each test method."""
        self._cleanup_schemas(self.source_conn, "source")
        self._cleanup_schemas(self.target_conn, "target")

    def _cleanup_schemas(self, conn, db_name):
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT nspname FROM pg_catalog.pg_namespace
                WHERE nspname NOT IN ('public', 'information_schema') AND nspname NOT LIKE 'pg_%';
            """)
            for row in cursor.fetchall():
                cursor.execute(f"DROP SCHEMA IF EXISTS {row[0]} CASCADE;")
        conn.commit()

    def setup_source_data(self):
        raise NotImplementedError("Subclasses must implement setup_source_data()")

    def run_job(self, job_name, jobs_config):
        pg_xcopy.run_jobs(job_name, jobs_config)

    @contextmanager
    def get_target_cursor(self):
        with self.target_conn.cursor() as cursor:
            yield cursor

    def assertSchemaExists(self, schema_name, msg=None):
        with self.target_conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s;",
                (schema_name,),
            )
            self.assertIsNotNone(
                cursor.fetchone(),
                msg or f"Schema '{schema_name}' should exist but doesn't.",
            )

    def assertTableExists(self, schema_name, table_name, msg=None):
        with self.target_conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s;",
                (schema_name, table_name),
            )
            self.assertIsNotNone(
                cursor.fetchone(),
                msg or f"Table '{schema_name}.{table_name}' should exist but doesn't.",
            )

    def assertIndexExists(self, schema_name, table_name, index_name, msg=None):
        with self.target_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM pg_indexes
                WHERE schemaname = %s AND tablename = %s AND indexname = %s;
            """,
                (schema_name, table_name, index_name),
            )
            self.assertIsNotNone(
                cursor.fetchone(),
                msg
                or f"Index '{index_name}' on table '{schema_name}.{table_name}' should exist but doesn't.",
            )

    def assertTableRowCount(self, schema_name, table_name, expected_count, msg=None):
        safe_table_name = f'"{schema_name}"."{table_name}"'
        with self.target_conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {safe_table_name};")
            count_result = cursor.fetchone()
            # The unittest assertion gives a nice test failure message.
            self.assertIsNotNone(
                count_result,
                f"Query for row count on '{safe_table_name}' returned no rows.",
            )
            # This plain assert helps the static type checker understand the type is now non-nullable.
            assert count_result is not None
            count = count_result[0]
            self.assertEqual(
                count,
                expected_count,
                msg or f"Table '{safe_table_name}' has wrong row count.",
            )

    def get_table_columns(self, schema_name, table_name, conn=None):
        connection = conn or self.target_conn
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY column_name;
            """,
                (schema_name, table_name),
            )
            return {row[0] for row in cursor.fetchall()}

    def get_row_where(
        self, schema_name, table_name, select_columns, where_conditions, conn=None
    ):
        connection = conn or self.target_conn
        safe_select_cols = ", ".join(f'"{col}"' for col in select_columns)
        safe_table_name = f'"{schema_name}"."{table_name}"'
        where_clause_parts = [f'"{key}" = %s' for key in where_conditions.keys()]
        where_clause = " AND ".join(where_clause_parts)
        query = (
            f"SELECT {safe_select_cols} FROM {safe_table_name} WHERE {where_clause};"
        )
        params = tuple(where_conditions.values())
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()
