# -*- coding: utf-8 -*-
import unittest

from limem.builder.extractor import AdaptiveExtractor, ExtractionResult
from limem.builder.input_classifier import InputClassifier, StructureLevel
from limem.builder.memory_builder import BuilderConfig, MemoryBuilder
from limem.builder.relationship_inferrer import RelationshipInferrer
from limem.builder.semi_structured_extractor import SemiStructuredExtractor
from limem.builder.structured_mapper import StructuredFieldMapper
from limem.builder.unstructured_extractor import UnstructuredExtractor
from limem.core.episode import Episode
from limem.core.event import Event, EventRelation


class _FallbackExtractor:
    def __init__(self):
        self.called = False

    def extract(self, text):
        self.called = True
        return ExtractionResult(
            event_data={"summary": "Fallback event", "action": "fallback action", "causality": ""},
            events_data=[{"summary": "Fallback event", "action": "fallback action", "causality": ""}],
            entities=["Fallback"],
            confidence=0.4,
        )


class _FakeExtractor:
    def __init__(self):
        self.received_metadata = None

    def extract(self, text, metadata=None):
        self.received_metadata = metadata
        events = [
            {
                "summary": "User submits order",
                "action": "submit order",
                "causality": "",
                "participants": [{"role": "user", "seat": ""}],
                "time_range": {"start": 100, "end": 100, "display_time_bucket": ""},
            },
            {
                "summary": "System confirms order",
                "action": "confirm order",
                "causality": "after submit order",
                "participants": [{"role": "system", "seat": ""}],
                "time_range": {"start": 120, "end": 120, "display_time_bucket": ""},
            },
        ]
        return ExtractionResult(
            event_data=events[0],
            events_data=events,
            entities=["order"],
            confidence=1.0,
        )


class _FakeConsolidator:
    pass


class _FakeStore:
    def __init__(self):
        self.events = {}
        self.episodes = {}
        self.entities = set()
        self.involves = {}
        self.links = []
        self.event_relations = []

    def save_episode(self, episode):
        self.episodes[episode.id] = episode

    def save_event(self, event):
        self.events[event.id] = event

    def get_event(self, event_id):
        return self.events.get(event_id)

    def update_event(self, event):
        self.events[event.id] = event

    def link_event_to_episode(self, event_id, episode_id):
        self.links.append((event_id, episode_id))

    def ensure_entity(self, entity_name, entity_type="UNKNOWN"):
        before = len(self.entities)
        self.entities.add(entity_name)
        return len(self.entities) > before

    def get_involves_relation(self, event_id, entity_id):
        return self.involves.get((event_id, entity_id))

    def create_involves_relation(
        self,
        event_id,
        entity_id,
        t_created,
        t_valid,
        c_valid=1,
        t_expired=None,
        t_invalid=None,
    ):
        self.involves[(event_id, entity_id)] = EventRelation(
            event_id=event_id,
            entity_id=entity_id,
            t_created=t_created,
            t_valid=t_valid,
            c_valid=c_valid,
            t_expired=t_expired,
            t_invalid=t_invalid,
        )

    def update_involves_relation(self, relation):
        self.involves[(relation.event_id, relation.entity_id)] = relation

    def promote_permanent_trait(self, user_id, event_id, t_created):
        return None

    def upsert_event_relation(
        self,
        from_event_id,
        to_event_id,
        relation_type,
        description,
        confidence,
        evidence_span,
        source_episode_id,
        source_session_id,
        timestamp,
    ):
        self.event_relations.append(
            {
                "from": from_event_id,
                "to": to_event_id,
                "type": relation_type,
                "description": description,
                "confidence": confidence,
                "evidence_span": evidence_span,
                "source_episode_id": source_episode_id,
                "source_session_id": source_session_id,
                "timestamp": timestamp,
            }
        )


class TestAdaptiveExtractor(unittest.TestCase):
    def test_input_classifier_routes_structured_semi_and_unstructured(self):
        classifier = InputClassifier()

        structured = classifier.classify('{"user_query":"Open Atlas","assistant_response":"Opened Atlas"}')
        self.assertEqual(structured.level, StructureLevel.STRUCTURED)
        self.assertEqual(structured.parsed_json["user_query"], "Open Atlas")

        semi = classifier.classify("2026-03-31 10:30:00 | User: start Atlas")
        self.assertEqual(semi.level, StructureLevel.SEMI_STRUCTURED)
        self.assertIn("dialogue", semi.detected_patterns)
        self.assertIn("timestamp", semi.detected_patterns)

        unstructured = classifier.classify("A free-form paragraph about product strategy.")
        self.assertEqual(unstructured.level, StructureLevel.UNSTRUCTURED)

    def test_structured_mapper_extracts_action_and_entities_without_llm(self):
        mapper = StructuredFieldMapper()
        result = mapper.extract(
            {
                "user": "Alice",
                "user_query": 'Play "Roadtrip Mix" on Spotify',
                "assistant_response": "Started playback",
                "timestamp": "2026-03-31 09:15:00",
                "app_name": "Spotify",
                "title": "Roadtrip Mix",
            },
            source_text="",
        )

        self.assertEqual(result.event_data["participants"], [{"role": "Alice", "seat": ""}])
        self.assertEqual(result.event_data["action"], 'Play "Roadtrip Mix" on Spotify')
        self.assertEqual(result.event_data["causality"], "Started playback")
        self.assertIn("Spotify", result.entities)
        self.assertIn("Roadtrip Mix", result.entities)

    def test_semi_structured_extractor_prefers_rules(self):
        fallback = _FallbackExtractor()
        extractor = SemiStructuredExtractor(fallback_extractor=fallback)
        result = extractor.extract(
            '2026-03-31 10:30:00 | User: Book room "Atlas" -> Assistant: Reserved room "Atlas"'
        )

        self.assertFalse(fallback.called)
        self.assertEqual(len(result.events_data), 2)
        self.assertEqual(result.events_data[0]["participants"], [{"role": "User", "seat": ""}])
        self.assertIn("Atlas", result.entities)

    def test_semi_structured_extractor_falls_back_when_rules_fail(self):
        fallback = _FallbackExtractor()
        extractor = SemiStructuredExtractor(fallback_extractor=fallback)
        result = extractor.extract("A paragraph with no timestamps, turns, or key value pairs.")

        self.assertTrue(fallback.called)
        self.assertEqual(result.event_data["summary"], "Fallback event")

    def test_unstructured_extractor_uses_single_llm_call(self):
        calls = []

        def fake_llm(system_prompt, user_message, default):
            calls.append((system_prompt, user_message, default))
            return {
                "events": [
                    {
                        "summary": "Team plans Acme Summit launch",
                        "action": "plan launch",
                        "participants": [{"role": "team"}],
                        "causality": "",
                    }
                ],
                "entities": ["Acme Summit", "Berlin"],
            }

        extractor = UnstructuredExtractor(
            llm_caller=fake_llm,
            system_prompt="SYSTEM",
            user_prompt="{episode_text}",
        )
        result = extractor.extract("Team discussed the Acme Summit launch in Berlin.")

        self.assertEqual(len(calls), 1)
        self.assertEqual(result.event_data["summary"], "Team plans Acme Summit launch")
        self.assertEqual(result.entities, ["Acme Summit", "Berlin"])

    def test_unstructured_extractor_ignores_list_payload_for_entities(self):
        def fake_llm(system_prompt, user_message, default):
            del system_prompt, user_message, default
            return [
                {
                    "id": "evt-1",
                    "summary": "Team reviews launch plan",
                    "action": "review launch plan",
                    "participants": [{"role": "team"}],
                    "causality": "",
                }
            ]

        extractor = UnstructuredExtractor(
            llm_caller=fake_llm,
            system_prompt="SYSTEM",
            user_prompt="{episode_text}",
        )
        result = extractor.extract("The team reviewed the launch plan.")

        self.assertEqual(result.event_data["summary"], "Team reviews launch plan")
        self.assertEqual(result.entities, [])

    def test_adaptive_extractor_routes_without_extra_llm_calls(self):
        llm_calls = []

        def fake_llm(system_prompt, user_message, default):
            llm_calls.append((system_prompt, user_message))
            return {
                "events": [
                    {
                        "summary": "Sam schedules quarterly review",
                        "action": "schedule quarterly review",
                        "participants": [{"role": "Sam"}],
                    }
                ],
                "entities": ["quarterly review"],
            }

        extractor = AdaptiveExtractor(llm_caller=fake_llm)

        structured_result = extractor.extract(
            '{"user":"Sam","user_query":"Open Atlas","assistant_response":"Opened Atlas","app":"Atlas"}'
        )
        self.assertEqual(structured_result.event_data["action"], "Open Atlas")
        self.assertEqual(len(llm_calls), 0)

        semi_result = extractor.extract("2026-03-31 10:30:00 | User: Start Atlas")
        self.assertTrue(semi_result.events_data)
        self.assertEqual(len(llm_calls), 0)

        unstructured_result = extractor.extract(
            "Sam mentioned the quarterly review and asked to plan it for next week."
        )
        self.assertEqual(unstructured_result.event_data["summary"], "Sam schedules quarterly review")
        self.assertEqual(len(llm_calls), 1)

    def test_relationship_inferrer_detects_temporal_causal_and_parallel_edges(self):
        inferrer = RelationshipInferrer()
        events = [
            Event(
                id="a",
                summary="User submits order",
                action="submit order",
                time_range={"start": 100, "end": 100},
                participants=[{"role": "user", "seat": ""}],
                timestamp=100,
                last_active=100,
            ),
            Event(
                id="b",
                summary="System confirms order",
                action="confirm order",
                causality="after submit order",
                time_range={"start": 120, "end": 120},
                participants=[{"role": "system", "seat": ""}],
                timestamp=120,
                last_active=120,
            ),
            Event(
                id="c",
                summary="Courier starts delivery",
                action="start delivery",
                time_range={"start": 150, "end": 220},
                participants=[{"role": "courier", "seat": ""}],
                timestamp=150,
                last_active=150,
            ),
            Event(
                id="d",
                summary="Support chats with customer",
                action="chat with customer",
                time_range={"start": 160, "end": 200},
                participants=[{"role": "support", "seat": ""}],
                timestamp=160,
                last_active=160,
            ),
        ]

        relations = inferrer.infer(events)
        relation_signatures = {(item.from_event_id, item.to_event_id, item.relation_type) for item in relations}

        self.assertIn(("a", "b", "temporal_next"), relation_signatures)
        self.assertIn(("a", "b", "causality"), relation_signatures)
        self.assertIn(("c", "d", "parallel"), relation_signatures)

    def test_relationship_inferrer_skips_causality_without_timestamps(self):
        inferrer = RelationshipInferrer()
        events = [
            Event(
                id="a",
                summary="User submits order",
                action="submit order",
                participants=[{"role": "user", "seat": ""}],
            ),
            Event(
                id="b",
                summary="System confirms order",
                action="confirm order",
                causality="after submit order",
                participants=[{"role": "system", "seat": ""}],
            ),
        ]

        relations = inferrer.infer(events)
        relation_signatures = {(item.from_event_id, item.to_event_id, item.relation_type) for item in relations}

        self.assertIn(("a", "b", "temporal_next"), relation_signatures)
        self.assertNotIn(("a", "b", "causality"), relation_signatures)

    def test_adaptive_extractor_handles_english_chat_log(self):
        extractor = AdaptiveExtractor()
        result = extractor.extract(
            '2026-03-31 09:00 | Alice: Can you book "Atlas" for tomorrow\'s standup? -> Bob: Yes, I booked "Atlas".'
        )

        self.assertEqual(len(result.events_data), 2)
        self.assertEqual(result.events_data[0]["participants"], [{"role": "Alice", "seat": ""}])
        self.assertEqual(result.events_data[1]["participants"], [{"role": "Bob", "seat": ""}])
        self.assertIn("Atlas", result.entities)

    def test_adaptive_extractor_handles_chinese_meeting_minutes(self):
        extractor = AdaptiveExtractor()
        result = extractor.extract(
            "2026-03-31 14:00\n王敏: 讨论Q2路线图，决定优先做搜索优化。\n李雷: 下周补充用户画像方案。"
        )

        self.assertEqual(len(result.events_data), 2)
        self.assertEqual(result.events_data[0]["participants"], [{"role": "王敏", "seat": ""}])
        self.assertEqual(result.events_data[1]["participants"], [{"role": "李雷", "seat": ""}])
        self.assertIn("搜索优化", result.events_data[0]["action"])

    def test_adaptive_extractor_handles_iot_json_record(self):
        extractor = AdaptiveExtractor()
        result = extractor.extract(
            '{"product":"Greenhouse Sensor A1","operation":"report temperature spike","result":"fan turned on","timestamp":"2026-03-31 08:15:00","location":"Zone 3"}'
        )

        self.assertEqual(result.event_data["action"], "report temperature spike")
        self.assertEqual(result.event_data["causality"], "fan turned on")
        self.assertIn("Greenhouse Sensor A1", result.entities)
        self.assertIn("Zone 3", result.entities)

    def test_adaptive_extractor_handles_mixed_language_dialogue(self):
        extractor = AdaptiveExtractor()
        result = extractor.extract(
            '2026-03-31 16:30 | Alice: 请帮我 reserve "Atlas" for tomorrow -> System: Atlas booked for tomorrow standup'
        )

        self.assertEqual(len(result.events_data), 2)
        self.assertEqual(result.events_data[0]["participants"], [{"role": "Alice", "seat": ""}])
        self.assertEqual(result.events_data[1]["participants"], [{"role": "System", "seat": ""}])
        self.assertIn("Atlas", result.entities)

    def test_adaptive_extractor_handles_nested_json_records(self):
        extractor = AdaptiveExtractor()
        result = extractor.extract(
            '{"envelope":{"payload":{"actor":"OpsBot","operation":"restart search service","result":"service recovered","product":"Search API","location":"cluster-a","timestamp":"2026-03-31 11:20:00"}}}'
        )

        self.assertEqual(result.event_data["participants"], [{"role": "OpsBot", "seat": ""}])
        self.assertEqual(result.event_data["action"], "restart search service")
        self.assertEqual(result.event_data["causality"], "service recovered")
        self.assertIn("Search API", result.entities)
        self.assertIn("cluster-a", result.entities)

    def test_adaptive_extractor_handles_noise_and_edge_inputs(self):
        extractor = AdaptiveExtractor()
        cases = {
            "": ([], []),
            "7": ([], []),
            "lorem ipsum " * 2000: ([], []),
        }

        for text, expected in cases.items():
            with self.subTest(text=text[:20]):
                result = extractor.extract(text)
                self.assertEqual(result.events_data, expected[0])
                self.assertEqual(result.entities, expected[1])

    def test_memory_builder_passes_metadata_and_persists_rule_relations(self):
        extractor = _FakeExtractor()
        store = _FakeStore()
        builder = MemoryBuilder(
            extractor=extractor,
            consolidator=_FakeConsolidator(),
            store=store,
            config=BuilderConfig(append_first_mode=True),
        )
        builder._get_embedding = lambda text: [0.0, 1.0]

        episode = Episode(
            content="User submits order and system confirms it.",
            timestamp=500,
            metadata={"session_id": "session-1", "trace_id": "trace-9"},
        )
        result = builder.build(episode)

        self.assertEqual(extractor.received_metadata, {"session_id": "session-1", "trace_id": "trace-9"})
        self.assertEqual(len(result.events), 2)
        self.assertTrue(any(item["type"] == "temporal_next" for item in store.event_relations))
        self.assertTrue(any(item["type"] == "causality" for item in store.event_relations))


if __name__ == "__main__":
    unittest.main()
