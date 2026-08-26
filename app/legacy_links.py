"""Write barrier for links to removed legacy artifact types.

Scripts and Windows Scheduled Tasks are no longer tracked artifact types
(Flows replaced them). Links that already exist in the database may survive
an edit unchanged or be removed, but no new legacy link can be created.
"""

from fastapi import HTTPException

LEGACY_LINK_TYPES = {"script", "scheduled_task"}


def reject_new_legacy_links(requested, existing_pairs: set[tuple[str, int]]):
    """Raise 400 if `requested` adds a legacy link that isn't in `existing_pairs`.

    `requested` is any iterable of objects with entity_type/entity_id;
    `existing_pairs` is the set of (entity_type, entity_id) currently stored.
    """
    for link in requested:
        etype = link.entity_type
        if etype in LEGACY_LINK_TYPES and (etype, link.entity_id) not in existing_pairs:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot create new links to '{etype}' entities - this artifact "
                    "type is no longer tracked (use Flows instead). Existing legacy "
                    "links can only be kept or removed."
                ),
            )
