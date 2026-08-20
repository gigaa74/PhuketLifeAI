import asyncio
import time
import unittest

from async_utils import run_blocking


class AsyncUtilsTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_blocking_calls_do_not_run_sequentially(self):
        started = time.perf_counter()
        await asyncio.gather(
            run_blocking(time.sleep, 0.15),
            run_blocking(time.sleep, 0.15),
        )
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.27)


if __name__ == "__main__":
    unittest.main()
