"""Use current contact evidence to resolve already extracted person targets."""

from limem.memory_store.models import Entity
from limem.raw_data_parser.models import SessionInput

from .models import ExtractionItem, L2Result, ResolvedTargetReference


def _contact_sources(item: ExtractionItem, events):
    for event_id in item.raw_event_ids:
        payload = events[event_id].raw_payload
        tree = payload["device_tree"]
        cabin = tree.get("cabin", {})
        query = payload["trigger"]["payload"].get("cabin.dialogue", {}).get("query")
        if query and cabin.get("dialogue", {}).get("query") == query:
            yield (event_id, query, cabin.get("screen", {}).get("hmi", {}),
                   tree.get("connectivity", {}).get("phone", {}))


def _resolve_item(item: ExtractionItem, sources, people: list[Entity]) -> ExtractionItem:
    bindings = list(item.target_bindings)
    bound = {binding.relation_index for binding in bindings}
    local = dict(item.local_person_aliases)
    for index, relation in enumerate(item.relations):
        target = relation.target
        if index in bound or not target or target not in item.entity_mentions:
            continue
        candidates = [person for person in people if target in (person.name, *person.aliases)]
        matches = [source for source in sources if source[1].count(target) == 1]
        if not candidates or len(matches) != 1:
            continue
        [(event_id, _, screen, phone)] = matches
        description = screen.get("description", "")
        label, _, filter_value = description.partition("：")
        screen_filter = (filter_value.strip() if screen.get("app") == "通讯录"
                         and label == "联系人筛选" else "")
        supported = [
            person for person in candidates
            if screen_filter in (person.name, *person.aliases)
            or (phone.get("call_status") == "dialing"
                and phone.get("contact_name") == target == person.name)
        ]
        if len(supported) == 1:
            bindings.append(ResolvedTargetReference(
                relation_index=index, entity_id=supported[0].id,
                raw_event_ids=[event_id], history_memory_ids=[],
            ))
        elif len(candidates) > 1 and not description and not phone.get("contact_name"):
            local[target] = None

    relations = item.relations
    mentions = item.entity_mentions
    if len(relations) == 2 and not bindings:
        first, second = relations
        if (first.predicate == second.predicate and first.target and second.target
                and first.target != second.target
                and {first.target, second.target} <= set(mentions)):
            matches = []
            for _, query, screen, phone in sources:
                if (screen.get("app") != "通讯录" or screen.get("description") != "新建联系人预览"
                        or phone.get("call_status") != "idle"):
                    continue
                name = phone.get("contact_name")
                if name in (first.target, second.target):
                    kept, duplicate = (first, second) if name == first.target else (second, first)
                    if query.count(duplicate.target) == 1 and name not in query:
                        matches.append((kept, duplicate.target))
            if len(matches) == 1:
                [(kept, alias)] = matches
                relations = [kept]
                mentions = [mention for mention in mentions if mention != alias]
                local.pop(alias, None)
                local[kept.target] = [alias]

    return item.model_copy(update={
        "target_bindings": bindings, "local_person_aliases": local,
        "relations": relations, "entity_mentions": mentions,
    })


def resolve_person_references(
    result: L2Result, session: SessionInput, entities: list[Entity], user_id: str,
) -> L2Result:
    people = [entity for entity in entities
              if entity.id == entity.canonical_entity_id and "Person" in entity.types
              and entity.external_ref != user_id]
    events = {event.raw_event_id: event for event in session.raw_events}
    return L2Result(items=[
        _resolve_item(item, list(_contact_sources(item, events)), people) for item in result.items
    ])
