"""Job cancellation that distinguishes user intent from Runtime housekeeping."""

from ...storage import AssetError

INSTALL_KIND = "install"


class JobCancelCapability:
    def job_cancel(self, job_id, user_initiated=None):
        try:
            record = self.records.get(job_id)
        except AssetError:
            return self._domain_job_data("cancel_job", {"job_id": job_id})
        if (
            record["kind"] == INSTALL_KIND
            and user_initiated is not True
            and not self._install_cancel_permitted()
        ):
            # Freeing an execution slot is not a reason to kill a healthy install:
            # the dependency directory would be left half-written and every later
            # execution against it would be unverifiable.
            return {
                "ok": False,
                "outcome_ok": False,
                "error": (
                    "A dependency installation is running. The Runtime does not cancel "
                    "installations to free an execution slot."
                ),
                "failure": {
                    "stage": "preconditions",
                    "code": "install_cancel_refused",
                    "operation_started": False,
                    "side_effects": "none",
                    "job_id": job_id,
                    "job_type": INSTALL_KIND,
                    "retryable": False,
                    "guidance": (
                        "Wait for the installation with job_wait. Cancel it only as an "
                        "explicit user decision, or when it has failed or exceeded its "
                        "own timeout."
                    ),
                },
            }
        result = self.workspaces.bridge.job("cancel", {"job_id": job_id, "offset": 0})
        self.records.update(job_id, "canceled")
        result["job_type"] = record["kind"] if record["kind"] == "install" else "code"
        return result

    @staticmethod
    def _install_cancel_permitted() -> bool:
        import os

        return os.getenv("ALLOW_INSTALL_CANCEL", "false").lower() == "true"
