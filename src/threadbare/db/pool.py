from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def create_pool(dsn: str) -> AsyncConnectionPool:
    """Build the shared async connection pool.

    open_=False: callers must `await pool.open()` explicitly (and `await
    pool.close()` on shutdown) rather than relying on implicit open-on-first-use,
    so pool lifecycle is explicit in both the sync worker's startup/shutdown
    and in tests.
    """
    return AsyncConnectionPool(
        dsn,
        open=False,
        kwargs={"row_factory": dict_row},
    )
