import json


class DependencyInstallCapability:
    def dependency_install(self, packages, timeout_seconds=1800):
        fingerprint = json.dumps({"install": sorted(packages)}, sort_keys=True)
        return self._start("install", {"kind": "install", "packages": packages, "timeout_seconds": timeout_seconds, "network": "bridge"}, fingerprint)
