import unittest
from .test_base import PgXmatIntegrationTestBase
from .test_config import SOURCE_DB_URL, TARGET_DB_URL

class TestPgXmat(PgXmatIntegrationTestBase):
    def setup_source_data(self):
        with self.source_conn.cursor() as cursor:
            cursor.execute("CREATE SCHEMA IF NOT EXISTS sales;")
            cursor.execute("""
                CREATE TABLE sales.customers (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100),
                    email VARCHAR(100),
                    is_active BOOLEAN DEFAULT true
                );
            """)
            cursor.execute(
                "INSERT INTO sales.customers (name, email, is_active) VALUES (%s, %s, %s), (%s, %s, %s), (%s, %s, %s);",
                ('Alice', 'alice@example.com', True,
                 'Bob', 'BOB@EXAMPLE.COM', True,
                 'Charlie', 'charlie@example.com', False)
            )
        self.source_conn.commit()


    def test_full_data_transfer(self):
        """Tests transferring a table with no 'select' config, mirroring it completely."""
        jobs_config = {
            "sales_reporting": {
                "source": {"database": SOURCE_DB_URL, "schema": "sales"},
                "target": {"database": TARGET_DB_URL, "schema": "reporting"},
                "tables": {"customers": {}},
            }
        }
        self.run_job("sales_reporting", jobs_config)

        self.assertSchemaExists("reporting")
        self.assertTableExists("reporting", "customers")
        self.assertTableRowCount("reporting", "customers", 3)
        columns = self.get_table_columns("reporting", "customers")
        self.assertEqual(columns, {"id", "name", "email", "is_active"}, "All columns should be mirrored.")


    def test_exclusive_select_subsetting(self):
        """Tests the original behavior: if '*' is not present, 'select' is an exclusive list."""
        jobs_config = {
            "selective_transfer": {
                "source": {"database": SOURCE_DB_URL, "schema": "sales"},
                "target": {"database": TARGET_DB_URL, "schema": "reporting"},
                "tables": {
                    "customers": {
                        "select": { "name": "UPPER(name)", "email": "LOWER(email)" },
                        "where": "is_active = true"
                    },
                },
            }
        }
        self.run_job("selective_transfer", jobs_config)

        self.assertTableRowCount("reporting", "customers", 2)
        columns = self.get_table_columns("reporting", "customers")
        self.assertEqual(columns, {"name", "email"}, "Target table should only contain columns from 'select'.")

        row = self.get_row_where("reporting", "customers", ("name", "email"), {"name": "ALICE"})
        self.assertIsNotNone(row, "Query for ALICE should return a row.")
        assert row is not None
        self.assertEqual(row, ('ALICE', 'alice@example.com'))


    def test_splat_transform_and_keep_rest(self):
        """Tests using '*' to transform some columns and keep the rest as-is."""
        jobs_config = {
            "splat_job": {
                "source": {"database": SOURCE_DB_URL, "schema": "sales"},
                "target": {"database": TARGET_DB_URL, "schema": "reporting"},
                "tables": { "customers": { "select": { "email": "LOWER(email)", "*": True } } },
            }
        }
        self.run_job("splat_job", jobs_config)

        self.assertTableRowCount("reporting", "customers", 3)
        columns = self.get_table_columns("reporting", "customers")
        self.assertEqual(columns, {"id", "name", "email", "is_active"}, "All columns should be present.")

        bob_row = self.get_row_where("reporting", "customers", ("email",), {"name": "Bob"})
        self.assertIsNotNone(bob_row, "Query for Bob should return a row.")
        assert bob_row is not None
        self.assertEqual(bob_row[0], 'bob@example.com', "Email should have been lower-cased.")

        alice_row = self.get_row_where("reporting", "customers", ("name",), {"name": "Alice"})
        self.assertIsNotNone(alice_row, "Query for Alice should return a row.")
        assert alice_row is not None
        self.assertEqual(alice_row[0], 'Alice', "Name should be unchanged.")


    def test_splat_transform_and_exclude(self):
        """Tests using '*' to transform some, exclude some (with None), and keep the rest."""
        jobs_config = {
            "exclude_job": {
                "source": {"database": SOURCE_DB_URL, "schema": "sales"},
                "target": {"database": TARGET_DB_URL, "schema": "reporting"},
                "tables": {
                    "customers": {
                        "select": { "name": "UPPER(name)", "is_active": None, "*": True },
                    },
                },
            }
        }
        self.run_job("exclude_job", jobs_config)

        self.assertTableRowCount("reporting", "customers", 3)
        columns = self.get_table_columns("reporting", "customers")
        self.assertEqual(columns, {"id", "name", "email"}, "is_active column should be excluded.")

        alice_row = self.get_row_where("reporting", "customers", ("name",), {"email": "alice@example.com"})
        self.assertIsNotNone(alice_row, "Query for Alice's transformed name should return a row.")
        assert alice_row is not None
        self.assertEqual(alice_row[0], 'ALICE', "Name should have been upper-cased.")


if __name__ == "__main__":
    unittest.main()
