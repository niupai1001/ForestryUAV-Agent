"""Persistence adapters that record facts without making policy decisions."""

from .executions import ExecutionRecords, snapshot_workspace

__all__ = ["ExecutionRecords", "snapshot_workspace"]
