"""Concrete local measurements and resource budgets for the existing Jobs worker."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import psutil

from .contracts import Failure
from .store import now


def execution_profile(config):
    memory = psutil.virtual_memory()
    profile = {"observed_at": now(), "host_ram_available": memory.available,
               "executor_ram_available": memory.available, "executor_ram_total": memory.total,
               "executor_cpu_threads": psutil.cpu_count() or 1,
               "workspace_disk_free": shutil.disk_usage(config.workspace_root).free,
               "source": "local_host", "vram_request_supported": 0}
    if config.docker_distribution:
        command = [shutil.which("wsl.exe") or "wsl.exe", "--distribution", config.docker_distribution,
                   "--user", "root", "--exec", "/usr/bin/cat", "/proc/meminfo",
                   "/sys/devices/system/cpu/online"]
        clean = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
        try:
            result = subprocess.run(command, capture_output=True, timeout=15, check=False, env=clean)
            if result.returncode or len(result.stdout) > 65536:
                raise ValueError("WSL资源探测未完成")
            fields = {}
            cpu_count = 0
            for line in result.stdout.decode('utf-8').splitlines():
                if ':' in line:
                    key, raw = line.split(':', 1)
                    if key in {'MemAvailable', 'MemTotal'}:
                        value, unit = raw.split()
                        if unit != 'kB':
                            raise ValueError('WSL内存探测单位不支持')
                        fields[key] = int(value) * 1024
                elif line.strip():
                    for group in line.strip().split(','):
                        bounds = group.split('-')
                        cpu_count += int(bounds[-1]) - int(bounds[0]) + 1
            if not cpu_count or not fields.get('MemTotal') or 'MemAvailable' not in fields:
                raise ValueError('WSL资源探测字段缺失')
            profile.update(executor_ram_available=fields['MemAvailable'], executor_ram_total=fields['MemTotal'],
                           executor_cpu_threads=cpu_count, source='host_and_wsl_kernel')
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            raise Failure('EXECUTOR_UNAVAILABLE', '无法核验执行机资源：' + str(error), 503, True) from error
    elif os.name == 'nt':
        # Windows host free RAM does not establish Docker Desktop VM free RAM.
        raise Failure('PRECONDITION_MISSING', '当前资源准入支持实测WSL后端；Docker Desktop虚机资源尚无已验证探测器', 422)
    return profile


def assess(request, profile, config):
    output = request['output_bytes'] if request['output_bytes'] is not None else config.max_code_output_bytes
    required = {'ram_bytes': request['ram_bytes'] + config.code_memory_reserve_bytes,
                'disk_bytes': 2 * output + config.code_disk_reserve_bytes + 16 * 1024 * 1024}
    missing = []
    if profile['host_ram_available'] < required['ram_bytes']:
        missing.append('host_ram')
    if profile['executor_ram_available'] < required['ram_bytes']:
        missing.append('executor_ram')
    if profile['workspace_disk_free'] < required['disk_bytes']:
        missing.append('workspace_disk')
    return {'decision': 'wait' if missing else 'admitted', 'missing': missing, 'required': required,
            'profile': profile, 'effective_cpu_threads': min(request['cpu_threads'], profile['executor_cpu_threads']),
            'policy': {'memory_reserve_bytes': config.code_memory_reserve_bytes,
                       'disk_reserve_bytes': config.code_disk_reserve_bytes}}


def limited_code(prepared, output_bytes):
    # Hard per-file limit is inherited by child processes; aggregate space is monitored separately.
    return ('import resource as _job_resource\n'
            f'_job_resource.setrlimit(_job_resource.RLIMIT_FSIZE, ({output_bytes}, {output_bytes}))\n'
            f'exec(compile({json.dumps(prepared)}, "/run.py", "exec"), '
            '{"__name__": "__main__", "__file__": "/run.py"})\n')
