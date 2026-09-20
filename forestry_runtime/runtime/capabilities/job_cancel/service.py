from ...storage import AssetError


class JobCancelCapability:
    def job_cancel(self, job_id):
        try:
            self.records.get(job_id)
        except AssetError:
            return self._domain_job_data("cancel_job", {"job_id": job_id})
        result = self.workspaces.bridge.job("cancel", {"job_id": job_id, "offset": 0})
        self.records.update(job_id, "canceled")
        result["job_type"] = "code"
        return result
