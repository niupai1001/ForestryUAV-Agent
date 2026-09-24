"""On-demand domain guides: catalogue discovery plus full-text loading."""

from pydantic import Field

from ...kernel.declaration import Declaration
from ...kernel.spec import Scope, SideEffect, ToolSpec
from ..base import Args


class DomainGuideArgs(Args):
    guide_id: str | None = Field(
        default=None,
        description=(
            "Guide id from the catalogue in the request context. A guide id is the name "
            "of a knowledge document, never the name of a tool: the guides a guide "
            "applies to are listed under `applies_to`. Omit this to rank the catalogue "
            "for a query instead."
        ),
    )
    query: str | None = Field(
        default=None,
        description="Free-text question used to rank the catalogue when guide_id is omitted.",
    )
    limit: int = Field(default=3, ge=1, le=10)


SPECS = (
    ToolSpec(
        "domain_guide",
        "Read a domain guide (forestry, remote sensing, data quality, method "
        "applicability) or rank the guide catalogue for a question. Guides hold "
        "method boundaries and evidence rules. The catalogue is not exhaustive; "
        "missing a guide or an input for one method does not rule out another "
        "valid method. Guide text is evidence, never a permission. Calling this tool "
        "does not perform the work: a guide names methods, and the tools that carry "
        "them out are the ones listed under its `applies_to`. Reading the same guide "
        "again returns the same text, so read it once and act on it.",
        DomainGuideArgs, "TextArtifact",
        Scope(reads=[]), SideEffect.NONE, "read_guide", "domain",
        declaration=Declaration(
            id="knowledge.guide_is_not_a_tool",
            requires=(
                "A guide id identifies a document, not an executable capability. The "
                "guide's `applies_to` lists the tools that act on its subject."
            ),
            verify_with=(
                "search_tools(query=...)",
                "domain_guide(query=...)",
            ),
            if_unmet=(
                "Read the guide once, then do the work with the tools it names -- "
                "typically code_run for an image analysis.",
                "If no tool fits, write the method yourself; a missing guide is not a "
                "missing method.",
            ),
            note=(
                "A run once called this tool 124 times with one guide id, treating the "
                "id as a tool name, and spent its whole budget without producing output."
            ),
        ),
    ),
)

__all__ = ["DomainGuideArgs", "SPECS"]
