"""A `pool.connection()`-shaped adapter that opens a fresh connection per
call rather than actually pooling. Confirmed empirically (not a guess):
psycopg_pool.AsyncConnectionPool does not survive Flask's async_to_sync
bridge (flask[async]/asgiref) -- its background maintenance tasks get
orphaned across the thread/event-loop boundary asgiref introduces, and
every connection attempt fails immediately with repeated "error connecting"
from the pool's worker, whether the pool is opened before app.run() or
lazily on first request. A single raw connection, opened and closed within
one async view's own async_to_sync-invoked coroutine, works reliably --
proven by direct experimentation before this module was written. See
DESIGN.md §10 for the tracked cost of this (no connection reuse across
requests, unlike sync_worker's db/pool.py which has no such constraint --
it never crosses an async_to_sync boundary at all).
"""

from contextlib import asynccontextmanager

import psycopg
from psycopg.rows import dict_row


class PerRequestConnectionSource:
    """Same `async with source.connection() as conn:` calling convention as
    db.pool.create_pool()'s AsyncConnectionPool, so db/queries.py and
    rendering/ callers don't need to know the difference.
    """

    def __init__(self, dsn: str):
        self._dsn = dsn

    @asynccontextmanager
    async def connection(self):
        conn = await psycopg.AsyncConnection.connect(self._dsn, row_factory=dict_row)
        try:
            # `async with conn:` (not just try/finally around the yield)
            # applies psycopg's own commit-on-success/rollback-on-exception
            # transaction behavior -- the same "normal connection context
            # behaviour" AsyncConnectionPool.connection() documents and
            # relies on. Without it, every write made through a route is
            # silently discarded on close() rather than committed -- a real
            # bug caught by live-testing the attachment-refresh write path
            # (masked in the test suite, where FakePool shares one
            # already-open, never-closed connection across a whole test, so
            # routes' writes were visible to later assertions via
            # read-your-own-writes within the same uncommitted transaction,
            # without ever actually needing a commit to "look" correct).
            async with conn:
                yield conn
        finally:
            await conn.close()
