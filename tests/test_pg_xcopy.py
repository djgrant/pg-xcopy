import unittest
from .test_base import PgXCopyIntegrationTestBase
from .test_config import SOURCE_DB_URL, TARGET_DB_URL


class TestPgXCopy(PgXCopyIntegrationTestBase):
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
            # Add a standalone index to test replication
            cursor.execute("CREATE INDEX cust_email_idx ON sales.customers (email);")

            cursor.execute(
                "INSERT INTO sales.customers (name, email, is_active) VALUES (%s, %s, %s), (%s, %s, %s), (%s, %s, %s);",
                (
                    "Alice",
                    "alice@example.com",
                    True,
                    "Bob",
                    "BOB@EXAMPLE.COM",
                    True,
                    "Charlie",
                    "charlie@example.com",
                    False,
                ),
            )
            # Add a second table for wildcard testing
            cursor.execute("""
                CREATE TABLE sales.orders (
                    order_id SERIAL PRIMARY KEY,
                    customer_id INTEGER,
                    amount DECIMAL(10, 2),
                    is_active BOOLEAN DEFAULT true,
                    order_notes TEXT
                );
            """)
            cursor.execute(
                "INSERT INTO sales.orders (customer_id, amount, is_active, order_notes) VALUES (%s, %s, %s, %s), (%s, %s, %s, %s);",
                (
                    1,
                    100.50,
                    True,
                    "Note for active order",
                    3,
                    25.00,
                    False,
                    "Note for inactive order",
                ),
            )
        self.source_conn.commit()

    def test_full_data_transfer(self):
        """Tests transferring a table with no config, mirroring it completely."""
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
        self.assertEqual(
            columns,
            {"id", "name", "email", "is_active"},
            "All columns should be mirrored.",
        )
        # Verify that the primary key (constraint) and standalone index were replicated
        self.assertIndexExists("reporting", "customers", "customers_pkey")
        self.assertIndexExists("reporting", "customers", "cust_email_idx")

    def test_column_omission_and_transform(self):
        """Tests creating a subset of a table by omitting and transforming columns."""
        jobs_config = {
            "selective_transfer": {
                "source": {"database": SOURCE_DB_URL, "schema": "sales"},
                "target": {"database": TARGET_DB_URL, "schema": "reporting"},
                "tables": {
                    "customers": {
                        "transform": {
                            "name": "UPPER(name)",
                            "email": "LOWER(email)",
                            "id": None,  # Omit this column
                            "is_active": None,  # Omit this column
                        },
                        "where": "is_active = true",
                    },
                },
            }
        }
        self.run_job("selective_transfer", jobs_config)

        self.assertTableRowCount("reporting", "customers", 2)
        columns = self.get_table_columns("reporting", "customers")
        self.assertEqual(
            columns,
            {"name", "email"},
            "Target table should only contain non-omitted columns.",
        )

        row = self.get_row_where(
            "reporting", "customers", ("name", "email"), {"name": "ALICE"}
        )
        self.assertIsNotNone(row, "Query for ALICE should return a row.")
        assert row is not None
        self.assertEqual(row, ("ALICE", "alice@example.com"))

    def test_transform_keeps_other_columns(self):
        """Tests that transforming one column keeps the others as-is."""
        jobs_config = {
            "transform_job": {
                "source": {"database": SOURCE_DB_URL, "schema": "sales"},
                "target": {"database": TARGET_DB_URL, "schema": "reporting"},
                "tables": {"customers": {"transform": {"email": "LOWER(email)"}}},
            }
        }
        self.run_job("transform_job", jobs_config)

        self.assertTableRowCount("reporting", "customers", 3)
        columns = self.get_table_columns("reporting", "customers")
        self.assertEqual(
            columns,
            {"id", "name", "email", "is_active"},
            "All columns should be present.",
        )

        bob_row = self.get_row_where(
            "reporting", "customers", ("email",), {"name": "Bob"}
        )
        self.assertIsNotNone(bob_row, "Query for Bob should return a row.")
        assert bob_row is not None
        self.assertEqual(
            bob_row[0], "bob@example.com", "Email should have been lower-cased."
        )

        alice_row = self.get_row_where(
            "reporting", "customers", ("name",), {"name": "Alice"}
        )
        self.assertIsNotNone(alice_row, "Query for Alice should return a row.")
        assert alice_row is not None
        self.assertEqual(alice_row[0], "Alice", "Name should be unchanged.")

    def test_transform_and_omit(self):
        """Tests transforming one column and omitting another."""
        jobs_config = {
            "omit_job": {
                "source": {"database": SOURCE_DB_URL, "schema": "sales"},
                "target": {"database": TARGET_DB_URL, "schema": "reporting"},
                "tables": {
                    "customers": {
                        "transform": {"name": "UPPER(name)", "is_active": None},
                    },
                },
            }
        }
        self.run_job("omit_job", jobs_config)

        self.assertTableRowCount("reporting", "customers", 3)
        columns = self.get_table_columns("reporting", "customers")
        self.assertEqual(
            columns, {"id", "name", "email"}, "is_active column should be excluded."
        )

        alice_row = self.get_row_where(
            "reporting", "customers", ("name",), {"email": "alice@example.com"}
        )
        self.assertIsNotNone(
            alice_row, "Query for Alice's transformed name should return a row."
        )
        assert alice_row is not None
        self.assertEqual(alice_row[0], "ALICE", "Name should have been upper-cased.")

    def test_wildcard_table_processing(self):
        """Tests using '*' as a table name to process all tables in a schema."""
        jobs_config = {
            "wildcard_job": {
                "source": {"database": SOURCE_DB_URL, "schema": "sales"},
                "target": {"database": TARGET_DB_URL, "schema": "reporting"},
                "tables": {
                    "*": {
                        "where": "is_active = true",
                        # Exclude notes and email from any table that has them
                        "transform": {"order_notes": None, "email": None},
                    }
                },
            }
        }
        self.run_job("wildcard_job", jobs_config)

        self.assertSchemaExists("reporting")

        # Verify 'customers' table was processed by the wildcard
        self.assertTableExists("reporting", "customers")
        self.assertTableRowCount(
            "reporting", "customers", 2, "Should only transfer active customers."
        )
        customer_cols = self.get_table_columns("reporting", "customers")
        self.assertEqual(
            customer_cols,
            {"id", "name", "is_active"},
            "Email column should be excluded from customers.",
        )

        # Verify 'orders' table was also processed by the wildcard
        self.assertTableExists("reporting", "orders")
        self.assertTableRowCount(
            "reporting", "orders", 1, "Should only transfer active orders."
        )
        order_cols = self.get_table_columns("reporting", "orders")
        self.assertEqual(
            order_cols,
            {"order_id", "customer_id", "amount", "is_active"},
            "order_notes column should be excluded from orders.",
        )


if __name__ == "__main__":
    unittest.main()
