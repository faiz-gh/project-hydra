import asyncio
import time
import logging
from psycopg import AsyncConnection, errors
import numpy as np

logger = logging.getLogger("workload")

class WorkloadGenerator:
    def __init__(self, db_uri: str, concurrency: int = 20):
        # db_uri should contain all node IPs: postgresql://user@ip1,ip2,ip3:26257/defaultdb
        self.db_uri = f"{db_uri}&connect_timeout=3&options=-c%20statement_timeout%3D5000"
        self.concurrency = concurrency
        self.running = False
        self.metrics_queue = asyncio.Queue()
        self.client_id = int(time.time())
        self.max_acked_seq = 0

    async def setup_db(self):
        async with await AsyncConnection.connect(self.db_uri) as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS chaos_events (
                        client_id BIGINT,
                        seq_id BIGINT,
                        payload TEXT,
                        created_at TIMESTAMPTZ DEFAULT now(),
                        PRIMARY KEY (client_id, seq_id)
                    )
                """)
            await conn.commit()
        logger.info("Database schema initialized.")

    async def _worker(self, worker_id: int):
        seq_id = 0
        while self.running:
            seq_id += 1
            start_time = time.perf_counter()
            success = False
            try:
                # Ephemeral connection per transaction to ensure load balancing across cluster
                async with await AsyncConnection.connect(self.db_uri) as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "INSERT INTO chaos_events (client_id, seq_id, payload) VALUES (%s, %s, %s)",
                            (self.client_id, seq_id, f"Worker {worker_id} payload")
                        )
                    await conn.commit()
                success = True
                self.max_acked_seq = max(self.max_acked_seq, seq_id)
            except (errors.OperationalError, errors.QueryCanceled, Exception) as e:
                logger.debug(f"Worker {worker_id} transaction failed: {e}")
                await asyncio.sleep(0.5) # Backoff on failure
            finally:
                latency = time.perf_counter() - start_time
                await self.metrics_queue.put({
                    "timestamp": time.time(),
                    "latency": latency,
                    "success": success
                })

    async def start(self):
        await self.setup_db()
        self.running = True
        self.tasks = [asyncio.create_task(self._worker(i)) for i in range(self.concurrency)]
        logger.info(f"Started workload with {self.concurrency} concurrent workers.")

    async def stop(self):
        self.running = False
        await asyncio.gather(*self.tasks, return_exceptions=True)
        logger.info("Workload stopped.")

    async def get_metrics_stream(self, window_seconds: int = 1):
        """Yields aggregated metrics every `window_seconds`."""
        while self.running or not self.metrics_queue.empty():
            await asyncio.sleep(window_seconds)
            batch = []
            while not self.metrics_queue.empty():
                batch.append(await self.metrics_queue.get())

            if not batch:
                continue

            latencies = [b["latency"] for b in batch if b["success"]]
            errors_count = len([b for b in batch if not b["success"]])
            tps = len(latencies) / window_seconds

            yield {
                "tps": tps,
                "p50": np.percentile(latencies, 50) if latencies else 0.0,
                "p99": np.percentile(latencies, 99) if latencies else 0.0,
                "error_rate": (errors_count / len(batch)) * 100
            }