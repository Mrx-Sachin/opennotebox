"""System resource monitor.

Samples CPU, memory, disk, and cgroup-level metrics at a configurable
interval and writes them to the metrics registry. Uses psutil and cgroup v2
for zero-blocking container and host resource monitoring.
"""
from __future__ import annotations

import asyncio
import os
import time

from loguru import logger

from app.services.metrics import get_metrics

# Initialize psutil non-blocking baseline
try:
    import psutil
    psutil.cpu_percent(interval=None)
except Exception:
    pass


def read_cgroup_memory() -> dict:
    """Read Linux cgroup v2 container memory limits and current usage."""
    stats = {
        "cgroup_used_mb": None,
        "cgroup_limit_mb": None,
        "cgroup_percent": None,
    }
    try:
        current_path = "/sys/fs/cgroup/memory.current"
        max_path = "/sys/fs/cgroup/memory.max"
        if os.path.exists(current_path):
            with open(current_path, "r", encoding="utf-8") as f:
                used_bytes = int(f.read().strip())
                stats["cgroup_used_mb"] = round(used_bytes / (1024 * 1024), 1)

            if os.path.exists(max_path):
                with open(max_path, "r", encoding="utf-8") as f:
                    max_val = f.read().strip()
                    if max_val.isdigit():
                        limit_bytes = int(max_val)
                        stats["cgroup_limit_mb"] = round(limit_bytes / (1024 * 1024), 1)
                        if limit_bytes > 0:
                            stats["cgroup_percent"] = round((used_bytes / limit_bytes) * 100.0, 1)
    except Exception:
        pass
    return stats


def update_system_stats() -> dict:
    """Read current system metrics and write to the registry.

    Returns the stats dict for direct use by callers.
    """
    m = get_metrics()
    try:
        import psutil
    except ImportError:
        return {}

    # CPU - non-blocking snapshot since previous check
    try:
        cpu_pct = psutil.cpu_percent(interval=None)
        m.sys_cpu_percent.set(cpu_pct)
    except Exception:
        pass

    # Memory - process level
    try:
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mem_mb = mem_info.rss / (1024 * 1024)
        m.sys_memory_used_mb.set(mem_mb)

        # Cgroup or host memory percentage
        cgroup = read_cgroup_memory()
        if cgroup.get("cgroup_percent") is not None:
            m.sys_memory_percent.set(cgroup["cgroup_percent"])
        else:
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

    cg = read_cgroup_memory()
    result = {
        "cpu_percent": round(m.sys_cpu_percent.value, 1),
        "memory_used_mb": round(m.sys_memory_used_mb.value, 1),
        "memory_percent": round(m.sys_memory_percent.value, 1),
        "disk_used_percent": round(m.sys_disk_used_pct.value, 1),
        "uptime_seconds": round(m.sys_uptime_seconds.value, 0),
    }
    if cg.get("cgroup_used_mb") is not None:
        result["cgroup_used_mb"] = cg["cgroup_used_mb"]
        result["cgroup_limit_mb"] = cg["cgroup_limit_mb"]
        result["cgroup_percent"] = cg["cgroup_percent"]

    return result


async def run_system_monitor(interval: float = 5.0):
    """Background loop that samples system resources periodically."""
    while True:
        try:
            update_system_stats()
        except Exception as e:
            logger.warning(f"System monitor error: {e}")
        await asyncio.sleep(interval)
