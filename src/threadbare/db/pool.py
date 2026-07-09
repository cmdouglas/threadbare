from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def create_pool(dsn: str, *, max_size: int = 10) -> AsyncConnectionPool:
    """Build the shared async connection pool.

    open=False: callers must `await pool.open()` explicitly (and `await
    pool.close()` on shutdown) rather than relying on implicit open-on-first-use,
    so pool lifecycle is explicit in both the sync worker's startup/shutdown
    and in tests.

    max_size=10 (psycopg_pool's own default is a fixed 4, i.e. min_size with
    no growth): backfill_guild() holds one connection per channel being
    backfilled concurrently (default cap 3) for that channel's entire
    backfill, not just one batch, plus reconciliation and the heartbeat loop
    each need a connection around the same time — 4 is tight enough to
    serialize more than intended.
    """
    return AsyncConnectionPool(
        dsn,
        open=False,
        max_size=max_size,
        kwargs={"row_factory": dict_row},
    )
