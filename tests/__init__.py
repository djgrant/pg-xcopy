import atexit
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from .test_config import (
    TEST_DB_URL,
    SOURCE_DB_NAME,
    TARGET_DB_NAME,
)

def _setUpDatabases():
    """Creates the databases needed for the entire test suite."""
    print("Creating test databases for the suite...")
    conn = psycopg2.connect(TEST_DB_URL)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cursor:
        cursor.execute(f"DROP DATABASE IF EXISTS {SOURCE_DB_NAME} WITH (FORCE);")
        cursor.execute(f"DROP DATABASE IF EXISTS {TARGET_DB_NAME} WITH (FORCE);")
        cursor.execute(f"CREATE DATABASE {SOURCE_DB_NAME};")
        cursor.execute(f"CREATE DATABASE {TARGET_DB_NAME};")
    conn.close()
    print("Test databases created.")
    # IMPORTANT: Register the teardown to run when the process exits.
    atexit.register(_tearDownDatabases)

def _tearDownDatabases():
    """This function will be registered to run at interpreter exit."""
    print("\n(atexit) Dropping test databases...")
    try:
        conn = psycopg2.connect(TEST_DB_URL)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {SOURCE_DB_NAME} WITH (FORCE);")
            cursor.execute(f"DROP DATABASE IF EXISTS {TARGET_DB_NAME} WITH (FORCE);")
        conn.close()
        print("(atexit) Test databases dropped.")
    except psycopg2.Error as e:
        print(f"(atexit) Could not drop databases: {e}")

_setUpDatabases()
