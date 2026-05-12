# -*- coding: utf-8 -*-
import unittest

from unittest.mock import MagicMock
from unittest.mock import patch

from limem.builder.extractor import UnifiedExtractor
from limem.config import normalize_dashscope_base_url
from limem.evolution.dynamic_engine import DynamicEvolutionConfig
from limem.factory import create_ltm_system
from limem.llm.adapters import LLMRequestAdapter
from limem.llm.client import DashScopeClient


class TestDashScopeBaseUrlNormalization(unittest.TestCase):
    def test_normalizes_native_sdk_url_to_compatible_mode(self):
        self.assertEqual(
            normalize_dashscope_base_url(
                "https://dashscope.aliyuncs.com/api/v1"
            ),
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def test_preserves_compatible_mode_url(self):
        self.assertEqual(
            normalize_dashscope_base_url(
                "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def test_unified_extractor_normalizes_constructor_base_url(self):
        extractor = UnifiedExtractor(
            api_key="test-key",
            base_url="https://dashscope.aliyuncs.com/api/v1",
            generation_model="qwen-plus",
        )
        self.assertEqual(extractor.base_url, "https://dashscope.aliyuncs.com/compatible-mode/v1")

    def test_dynamic_evolution_config_normalizes_constructor_base_url(self):
        config = DynamicEvolutionConfig(
            llm_base_url="https://dashscope.aliyuncs.com/api/v1"
        )
        self.assertEqual(config.llm_base_url, "https://dashscope.aliyuncs.com/compatible-mode/v1")

    def test_factory_can_split_generation_and_embedding_clients(self):
        with patch("limem.factory.KuzuStore") as mock_store:
            store = mock_store.return_value
            store.embedding_client = None
            ltm = create_ltm_system(
                config={
                    "generation_api_key": "generation-key",
                    "generation_base_url": "https://api.deepseek.com",
                    "generation_model": "deepseek-v4-flash",
                    "embedding_api_key": "embedding-key",
                    "embedding_base_url": "https://dashscope.aliyuncs.com/api/v1",
                    "embedding_model": "text-embedding-v4",
                    "enable_dynamic_evolution": False,
                }
            )

        self.assertEqual(ltm.builder.extractor.llm_client.base_url, "https://api.deepseek.com")
        self.assertEqual(ltm.builder.extractor.llm_client.generation_model, "deepseek-v4-flash")
        self.assertEqual(ltm.builder.llm_client.base_url, "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(ltm.builder.llm_client.embedding_model, "text-embedding-v4")
        mock_store.assert_called_once()
        self.assertIs(mock_store.call_args.kwargs["embedding_client"], ltm.builder.llm_client)

    def test_deepseek_v4_disables_thinking_with_provider_specific_body(self):
        client = DashScopeClient(
            api_key="test-key",
            base_url="https://api.deepseek.com",
            generation_model="deepseek-v4-flash",
        )
        mock_openai = MagicMock()
        client._openai_client = mock_openai

        client.call_generation(messages=[{"role": "user", "content": "hi"}])

        mock_openai.chat.completions.create.assert_called_once()
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "disabled"}})

    def test_adapter_normalizes_deepseek_disable_alias(self):
        adapter = LLMRequestAdapter()

        kwargs = adapter.adapt_chat_completion_kwargs(
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
            kwargs={"extra_body": {"thinking": {"type": "disable"}}},
        )

        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "disabled"}})

    def test_adapter_keeps_dashscope_thinking_format(self):
        adapter = LLMRequestAdapter()

        kwargs = adapter.adapt_chat_completion_kwargs(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen3-1.7b",
            kwargs={},
        )

        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})

    def test_adapter_does_not_add_thinking_for_plain_models(self):
        adapter = LLMRequestAdapter()

        kwargs = adapter.adapt_chat_completion_kwargs(
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            kwargs={},
        )

        self.assertNotIn("extra_body", kwargs)


if __name__ == "__main__":
    unittest.main()
