"""Confirm the observed driver-seat ventilation function from original actions."""

from collections.abc import Sequence

from limem.memory_store import Entity, EntityIdentityConfirmation, MemoryItem, MemoryStore
from limem.raw_data_parser import VehicleRawDataParser
from limem.raw_data_parser.vehicle import _PAYLOAD_FIELDS


FUNCTION_TEXT = "主驾座椅通风"
FUNCTION_PATH = "seat.row1.left.ventilation_level"


def references(item: MemoryItem) -> list[tuple[str, str]]:
    return [(ref.text, ref.entity_id) for ref in item.entity_refs] + [
        (relation.target, relation.target_entity_id)
        for relation in item.relations
        if relation.target is not None and relation.target_entity_id is not None
    ]


def confirm_ventilation_identity(
    store: MemoryStore,
    entities: Sequence[Entity],
) -> EntityIdentityConfirmation:
    """Prove all uses of these unresolved mentions name one vehicle function.

    The identity is a functional slot on a vehicle, not a hardware serial number.
    Unrelated text, a snapshot alone, or a shared display name is insufficient.
    """
    ids = {entity.id for entity in entities}
    if len(ids) < 2 or any(
        entity.canonical_entity_id is not None
        or entity.external_ref is not None
        or entity.scope_text != FUNCTION_TEXT
        for entity in entities
    ):
        raise ValueError("vehicle identity confirmation requires unresolved function mentions")
    parser = VehicleRawDataParser()
    vehicles: set[str] = set()
    seen: set[str] = set()
    for item in store.list_memory_items():
        uses = [(text, eid) for text, eid in references(item) if eid in ids]
        if item.subject_entity_id in ids:
            raise ValueError("vehicle function is also used as a subject")
        if not uses:
            continue
        if any(text != FUNCTION_TEXT for text, _ in uses):
            raise ValueError("vehicle function has an unverified mention")
        evidence = store.get_direct_evidence(item.id)
        if not evidence:
            raise ValueError("vehicle function reference has no direct evidence")
        for source in evidence:
            session = parser.parse(
                store.get_source_session(source.source_session_id).raw_session_json
            )
            actions = [
                event for event in session.raw_events
                if event.raw_event_id in source.raw_event_ids and event.actor == "驾驶员"
            ]
            if len(actions) != 1:
                raise ValueError("vehicle function evidence must locate one driver action")
            [action] = actions
            if (
                action.action != "seat.setVentilation"
                or action.vehicle_ref is None
                or action.detail is None
                or FUNCTION_TEXT not in action.detail
                or action.payload is None
                or _PAYLOAD_FIELDS[FUNCTION_PATH][0] not in action.payload
            ):
                raise ValueError("vehicle function lacks an explicit ventilation action target")
            # The strict parser renders this key only from FUNCTION_PATH in trigger.payload.
            vehicles.add(action.vehicle_ref)
        seen.update(eid for _, eid in uses)
    if seen != ids or len(vehicles) != 1:
        raise ValueError("vehicle function references do not identify one vehicle")
    return EntityIdentityConfirmation(entity_ids=sorted(ids))
