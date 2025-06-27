import os
from dotenv import load_dotenv

load_dotenv()

TEST_DB_URL = os.environ.get("PG_XCOPY_TEST_DB_URL")
if not TEST_DB_URL:
    raise Exception(
        "PG_XCOPY_TEST_DB_URL environment variable not set. Cannot run integration tests."
    )

DB_URL_BASE, _ = TEST_DB_URL.rsplit("/", 1)
SOURCE_DB_NAME = "pg_xcopy_source_db"
TARGET_DB_NAME = "pg_xcopy_target_db"
SOURCE_DB_URL = f"{DB_URL_BASE}/{SOURCE_DB_NAME}"
TARGET_DB_URL = f"{DB_URL_BASE}/{TARGET_DB_NAME}"
