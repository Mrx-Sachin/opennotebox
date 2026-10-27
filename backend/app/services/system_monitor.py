"""System resource monitor.

Samples CPU, memory, disk, and process-level metrics at a configurable
interval and writes them to the metrics registry. Uses psutil for cross-
platform resource monitoring.
"""
import asyncio
import os
import time

from loguru import logger

from app.services.metrics import get_metrics


def update_system_stats() -> dict:
    """Read current system metrics and write to the registry.

    Returns the stats dict for direct use by callers.
    """
    m = get_metrics()
    try:
        import psutil
    except ImportError:
        return {}

    # CPU
    try:
        cpu_pct = psutil.cpu_percent(interval=0.1)
        m.sys_cpu_percent.set(cpu_pct)
    except Exception:
        pass

    # Memory - process level
    try:
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mem_mb = mem_info.rss / (1024 * 1024)
        m.sys_memory_used_mb.set(mem_mb)

        # System-wide
        vm = psutil.virtual_memory()
        m.sys_memory_percent.set(vm.percent)
    except Exception:
        pass

    # Disk usage for the data directory
    try:
        from app.config import settings
        data_path = os.path.dirname(settings.data_dir) or "/"
        usage = psutil.disk_usage(data_path)
        m.sys_disk_used_pct.set(usage.percent)
    except Exception:
        pass

    # Uptime
    m.sys_uptime_seconds.set(time.time() - m._start_time)

    return {
        "cpu_percent": round(m.sys_cpu_percent.value, 1),
        "memory_used_mb": round(m.sys_memory_used_mb.value, 1),
        "memory_percent": round(m.sys_memory_percent.value, 1),
        "disk_used_percent": round(m.sys_disk_used_pct.value, 1),
        "uptime_seconds": round(m.sys_uptime_seconds.value, 0),
    }


async def run_system_monitor(interval: float = 5.0):
    """Background loop that samples system resources periodically."""
    while True:
        try:
            update_system_stats()
        except Exception as e:
            logger.warning(f"System monitor error: {e}")
        await asyncio.sleep(interval)
