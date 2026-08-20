import asyncio


async def run_blocking(func, *args, **kwargs):
    """Run blocking I/O without occupying the Telegram event loop."""
    return await asyncio.to_thread(func, *args, **kwargs)
