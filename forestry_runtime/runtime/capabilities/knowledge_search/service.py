import os

from ...kernel.protocol import ToolPreconditionError
from ...storage import AssetError


class KnowledgeCapability:
    """Search and read only the explicitly selected project's knowledge."""

    def _selected_project(self) -> dict:
        if self.memory is None:
            raise AssetError("Project knowledge is not configured")
        project = self.memory.selected_project(self.owner, self.chat_id)
        if project is None:
            raise AssetError(
                "No project is selected for this conversation; select one before searching knowledge"
            )
        return project

    def knowledge_search(self, query, limit=6):
        maximum = max(1, int(os.getenv("KNOWLEDGE_SEARCHES_PER_TURN", "3")))
        if len(self._knowledge_queries) >= maximum:
            raise ToolPreconditionError(
                "The per-turn project knowledge search limit has been reached. "
                "Use the evidence already returned, or ask the user if it is insufficient.",
                code="knowledge_search_budget_exhausted",
                completed_queries=list(self._knowledge_queries),
                search_limit=maximum,
            )
        project = self._selected_project()
        result = self.memory.search(self.owner, project["id"], query, limit)
        self._knowledge_queries.append(query)
        return {"project_id": project["id"], "project_name": project["name"], **result}

    def knowledge_read(self, chunk_id, before=1, after=1):
        project = self._selected_project()
        return {
            "project_id": project["id"],
            "project_name": project["name"],
            **self.memory.read_chunks(
                self.owner, project["id"], chunk_id, before, after
            ),
        }
