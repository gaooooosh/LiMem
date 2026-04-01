# -*- coding: utf-8 -*-
import unittest

from limem.builder.extractor import TwoStageExtractor
from limem.config import normalize_dashscope_base_url
from limem.evolution.dynamic_engine import DynamicEvolutionConfig


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

    def test_two_stage_extractor_normalizes_constructor_base_url(self):
        extractor = TwoStageExtractor(
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


if __name__ == "__main__":
    unittest.main()
