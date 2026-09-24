"""Dependency installation that must prove success before it is reported."""
from __future__ import annotations

import json

from ..environment.service import EnvironmentCapability


class DependencyInstallCapability(EnvironmentCapability):
    """Install task dependencies, then verify the outcome.

    Installation is a durable networked job.  It is never reported as successful
    until the container reached a terminal success *and* the requested
    distributions are present in the dependency directory, because code started
    against a half-finished install is the failure this exists to prevent.

    It derives from :class:`EnvironmentCapability` so that the install record, the
    verification records and the "this package set already failed" memory are the
    same object the execution preflight reads.  The inheritance is declaration
    only: no state is created here, and the composed toolbox lists
    ``DependencyInstallCapability`` before ``EnvironmentCapability``, so the install
    tool keeps calling this class's ``_submit_install``.
    """

    def _submit_install(self, packages, timeout_seconds, system_packages=None):
        system_packages = list(system_packages or [])
        fingerprint = json.dumps(
            {"install": sorted(packages), "system": sorted(system_packages)}, sort_keys=True,
        )
        return self._start("install", {
            "kind": "install",
            "packages": list(packages),
            "system_packages": system_packages,
            "timeout_seconds": timeout_seconds,
            "network": "bridge",
        }, fingerprint)

    def dependency_install(self, packages=None, system_packages=None, timeout_seconds=1800):
        return self.environment_install(
            list(packages or []), timeout_seconds=timeout_seconds,
            system_packages=list(system_packages or []),
        )
