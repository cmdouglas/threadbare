from threadbare.db.pool import create_pool


async def test_pool_connection_can_query(test_database_url):
    pool = create_pool(test_database_url)
    await pool.open()
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1 AS one")
                assert await cur.fetchone() == {"one": 1}
    finally:
        await pool.close()
