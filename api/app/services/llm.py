"""LLM access. Production: Amazon Bedrock (Converse API, Anthropic models). Local/tests: a mock whose
`is_mock` flag tells callers to use their deterministic rule-based path, so the whole product demos offline."""

from __future__ import annotations

import json
import re


class MockLLM:
    name = "mock-rules"
    is_mock = True

    def complete(self, system: str, prompt: str, *, max_tokens: int = 600) -> str:
        raise NotImplementedError(
            "MockLLM has no generative capability; callers use their rule-based path"
        )


class BedrockLLM:  # pragma: no cover - needs AWS
    is_mock = False

    def __init__(self, model_id: str, region: str):
        import boto3

        self.name, self.client = model_id, boto3.client("bedrock-runtime", region_name=region)

    def complete(self, system: str, prompt: str, *, max_tokens: int = 600) -> str:
        r = self.client.converse(
            modelId=self.name,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.1},
        )
        return r["output"]["message"]["content"][0]["text"]


def extract_json(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def build_llm(settings):
    return (
        BedrockLLM(settings.bedrock_model_id, settings.aws_region)
        if settings.llm_backend == "bedrock"
        else MockLLM()
    )
