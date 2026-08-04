import logging
from psycopg import AsyncConnection

logger = logging.getLogger("evaluator")

class ResilienceEvaluator:
    def __init__(self, db_uri: str):
        self.db_uri = db_uri

    async def calculate_rpo(self, client_id: int, expected_max_seq: int) -> int:
        """Returns the number of missing transactions (data loss). Should be 0."""
        async with await AsyncConnection.connect(self.db_uri) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM chaos_events WHERE client_id = %s AND seq_id <= %s",
                    (client_id, expected_max_seq)
                )
                actual_count = (await cur.fetchone())[0]
                loss = expected_max_seq - actual_count
                logger.info(f"RPO Check | Expected: {expected_max_seq}, Found: {actual_count} | Data Loss: {loss}")
                return loss

    async def get_leaseholders(self) -> dict:
        """Maps node IDs to the number of range leases they currently hold."""
        query = """
            SELECT node_id, count(*) as lease_count
            FROM crdb_internal.ranges
            WHERE database_name = 'defaultdb' AND table_name = 'chaos_events'
            GROUP BY node_id;
        """
        async with await AsyncConnection.connect(self.db_uri) as conn:
            async with conn.cursor() as cur:
                await cur.execute(query)
                results = await cur.fetchall()
                return {row[0]: row[1] for row in results}