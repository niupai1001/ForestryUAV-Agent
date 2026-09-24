"""Domain guide capability."""

import os

from ...domain_guides import GuideError, find_guide, match_guides


def guides_enabled() -> bool:
    """Whether the domain-knowledge layer is active for this process.

    The controlled experiment turns it off for the A arm while everything else --
    Runtime, tools, prompts, budgets, data -- stays identical.
    """
    return os.getenv("DOMAIN_GUIDES_ENABLED", "true").lower() == "true"


class DomainGuideCapability:
    def domain_guide(self, guide_id=None, query=None, limit=3):
        if not guides_enabled():
            return {
                "guides_available": False,
                "matches": [],
                "note": (
                    "No domain guide library is configured for this Runtime. Work "
                    "from the data, general method knowledge, and the tools available."
                ),
            }
        if guide_id:
            guide = find_guide(guide_id)
            body = guide.body
            truncated = False
            if len(body) > 40_000:
                body = body[:40_000]
                truncated = True
            return {
                "id": guide.id,
                "title": guide.title,
                "version": guide.version,
                "citation": guide.citation,
                "applies_to": list(guide.applies_to),
                "content": body,
                "truncated": truncated,
                "note": (
                    "Guide text is method evidence, not an exhaustive menu of "
                    "methods. A missing input for this method does not rule out "
                    "another route using available data. It does not grant "
                    "permissions or replace inspecting real files and rasters."
                ),
            }
        if not query:
            raise GuideError(
                "Provide guide_id to read a guide, or query to rank the catalogue. "
                "The catalogue itself is already listed in the request context."
            )
        ranked = match_guides(query, limit=limit)
        return {
            "query": query,
            "matches": [guide.catalogue_entry() | {"citation": guide.citation}
                        for guide in ranked],
            "note": (
                "Read a match with domain_guide(guide_id=...). When a match is "
                "directly relevant, use it as method evidence. Also consider "
                "other methods supported by the available inputs."
            ),
        }
