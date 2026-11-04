"""Minimal client for an OpenAI-compatible API (Ollama, vLLM, llama.cpp, ...): chat and embeddings."""

import json
import re

import httpx

from . import metrics

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 120):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def embed(self, texts: list[str], model: str) -> tuple[list[list[float]], int | None]:
        """One embedding per text, in order, plus the prompt tokens the server reports (if any)."""
        try:
            with metrics.llm_request("embed"):
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/embeddings",
                        json={"model": model, "input": texts},
                        headers=self._headers(),
                    )
                    response.raise_for_status()
                    body = response.json()
            rows = sorted(body["data"], key=lambda row: row["index"])
            vectors = [row["embedding"] for row in rows]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise LLMError(str(exc)) from exc
        if len(vectors) != len(texts):
            raise LLMError(f"Asked for {len(texts)} embeddings, got {len(vectors)}")
        return vectors, (body.get("usage") or {}).get("prompt_tokens")

    async def complete(self, system: str, user: str) -> str:
        headers = self._headers()
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            with metrics.llm_request("chat"):
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions", json=payload, headers=headers
                    )
                    response.raise_for_status()
                    return response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMError(str(exc)) from exc

    async def complete_json(self, system: str, user: str) -> dict:
        """Ask for a JSON object; tolerates models that wrap it in prose or code fences."""
        text = await self.complete(system, user)
        match = _JSON_BLOCK.search(text)
        if not match:
            raise LLMError(f"No JSON object in LLM response: {text[:200]!r}")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError(f"Invalid JSON from LLM: {exc}") from exc
