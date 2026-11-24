"""与厂商无关的 LLM 客户端：OpenAI 兼容 / Anthropic / Gemini / 本地 Ollama / Mock。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("llm")

# 各家的默认地址与默认模型（base_url 留空时使用）
PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "kind": "openai",
    },
    "deepseek": {
        "base": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "kind": "openai",
    },
    "agnes": {
        "base": "https://api.agnes-ai.cn/v1",
        "model": "agnes-3.0-flash",
        "kind": "openai",
    },
    "moonshot": {
        "base": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "kind": "openai",
    },
    "zhipu": {
        "base": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "kind": "openai",
    },
    "qwen": {
        "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "kind": "openai",
    },
    "groq": {
        "base": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "kind": "openai",
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat",
        "kind": "openai",
    },
    "siliconflow": {
        "base": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "kind": "openai",
    },
    "ollama": {
        "base": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "kind": "openai",
    },
    "anthropic": {
        "base": "https://api.anthropic.com/v1",
        "model": "claude-3-5-haiku-latest",
        "kind": "anthropic",
    },
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-2.0-flash",
        "kind": "gemini",
    },
    "mock": {"base": "", "model": "mock", "kind": "mock"},
}


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    s = str(raw).strip()
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            try:
                v = json.loads(m.group())
                return v if isinstance(v, dict) else {}
            except Exception:  # noqa: BLE001
                return {}
    return {}


class LLMError(RuntimeError):
    pass


class LLMClient:
    """一个客户端对应一组配置。

    prefix 决定从配置树的哪一支读参数：默认 "llm"（云端），
    传 "llm.local" 就是本地 Ollama。这样同一份代码可以同时持有多个模型。
    """

    def __init__(self, cfg, prefix: str = "llm"):
        self.cfg = cfg
        self.prefix = prefix
        self.reload()

    def _get(self, key: str, default=None):
        return self.cfg.get(f"{self.prefix}.{key}", default)

    def reload(self) -> None:
        self.provider = str(self._get("provider", "mock")).lower()
        meta = PROVIDERS.get(self.provider, PROVIDERS["mock"])
        self.kind = meta["kind"]
        base = str(self._get("base_url", "") or "").strip()
        self.base_url = (base or meta["base"]).rstrip("/")
        model = str(self._get("model", "") or "").strip()
        self.model = model or meta["model"]
        # 密钥来源，三级兜底：
        #   1. config.yaml 里的 api_key（本地开发图省事可以填，但开源仓库里必须留空）
        #   2. 环境变量（config 的 api_key_env 指定名字，默认 LLM_API_KEY）
        #   3. data/.api_key 文件（setx 之后当前终端读不到环境变量时的兜底；
        #      data/ 整个目录在 .gitignore 里，不会跟着仓库公开）
        key = str(self._get("api_key", "") or "").strip()
        if not key:
            env_name = str(self._get("api_key_env", "") or "").strip()
            if env_name:
                key = os.environ.get(env_name, "").strip()
        if not key:
            try:
                kp = Path(str(self.cfg.data_dir)) / ".api_key"
                if kp.exists():
                    key = kp.read_text(encoding="utf-8").strip()
            except Exception:  # noqa: BLE001
                pass
        self.api_key = key
        self.temperature = float(self._get("temperature", 0.85))
        self.max_tokens = int(self._get("max_tokens", 320))
        self.timeout = float(self._get("timeout_sec", 25))
        self.calls = 0
        self.fails = 0
        self.tokens = 0
        self.last_error = ""
        self.last_ok = 0.0

    @property
    def ready(self) -> bool:
        if self.kind == "mock":
            return False
        if self.provider == "ollama":
            return True
        return bool(self.api_key)

    @property
    def why_not(self) -> str:
        if self.kind == "mock":
            return "配置为 mock 模式（未接真模型）"
        if not self.api_key and self.provider != "ollama":
            return "没有填 API Key"
        return ""

    # ---------------- 对外入口 ----------------
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        if not self.ready:
            raise LLMError(self.why_not or "模型不可用")
        self.calls += 1
        try:
            if self.kind == "openai":
                # Ollama 的 OpenAI 兼容层不认 think / keep_alive，Qwen3 会一直开着
                # 思考模式把 max_tokens 吃光，content 返回空字符串。必须走原生接口。
                if self.provider == "ollama":
                    res = await self._ollama_native(messages, tools, temperature, max_tokens)
                else:
                    res = await self._openai(messages, tools, temperature, max_tokens)
            elif self.kind == "anthropic":
                res = await self._anthropic(messages, tools, temperature, max_tokens)
            elif self.kind == "gemini":
                res = await self._gemini(messages, tools, temperature, max_tokens)
            else:
                res = self._mock(messages, tools)
            self.last_ok = asyncio.get_running_loop().time()
            self.fails = 0
            self.tokens += int(res.usage.get("total_tokens", 0) or 0)
            return res
        except Exception as exc:  # noqa: BLE001
            self.fails += 1
            self.last_error = str(exc)[:300]
            raise

    # ---------------- OpenAI 兼容 ----------------
    async def _openai(self, messages, tools, temperature, max_tokens) -> LLMResult:
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_openai(messages),
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
            payload["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            r = await cli.post(url, json=payload, headers=headers)
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code}: {r.text[:240]}")
            data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = (msg.get("content") or "").strip()
        calls: list[ToolCall] = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            calls.append(
                ToolCall(
                    id=tc.get("id") or f"call_{i}",
                    name=fn.get("name") or "",
                    args=_parse_args(fn.get("arguments")),
                )
            )
        return LLMResult(text=text, tool_calls=calls, usage=data.get("usage") or {}, raw=data)

    # ---------------- Ollama 原生 ----------------
    async def _ollama_native(self, messages, tools, temperature, max_tokens) -> LLMResult:
        """直接打 /api/chat，绕开 OpenAI 兼容层。

        兼容层有两个要命的毛病，实测确认：
        1. 不认 think:false，Qwen3 会一直思考，把 max_tokens 全吃光，
           content 返回空字符串、finish_reason=length。等于本地模型永远说不出话。
        2. 不认 keep_alive，模型空闲五分钟就被踢出显存，下条弹幕等三五秒加载。
        原生接口两个都支持，所以 ollama 一律走这里。
        """
        base = self.base_url
        if base.endswith("/v1"):
            base = base[:-3]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_ollama(messages),
            "stream": False,
            "think": bool(self._get("think", False)),
            "keep_alive": self._get("keep_alive", -1),
            "options": {
                "temperature": self.temperature if temperature is None else temperature,
                "num_predict": self.max_tokens if max_tokens is None else max_tokens,
                # 小模型容易复读上一句，稍微加压一点惩罚
                "repeat_penalty": float(self._get("repeat_penalty", 1.15)),
            },
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            r = await cli.post(f"{base}/api/chat", json=payload)
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code}: {r.text[:240]}")
            data = r.json()
        msg = data.get("message") or {}
        text = (msg.get("content") or "").strip()
        calls: list[ToolCall] = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            calls.append(
                ToolCall(
                    id=tc.get("id") or f"call_{i}",
                    name=fn.get("name") or "",
                    args=_parse_args(fn.get("arguments")),
                )
            )
        pc = int(data.get("prompt_eval_count") or 0)
        ec = int(data.get("eval_count") or 0)
        return LLMResult(
            text=text,
            tool_calls=calls,
            usage={"prompt_tokens": pc, "completion_tokens": ec, "total_tokens": pc + ec},
            raw=data,
        )

    @staticmethod
    def _to_ollama(messages) -> list[dict[str, Any]]:
        """Ollama 原生接口的消息格式：tool 回执的字段名和 OpenAI 不一样。"""
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            if role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "content": str(m.get("content", "")),
                        "tool_name": m.get("name", "") or "tool",
                    }
                )
            elif role == "assistant" and m.get("tool_calls"):
                out.append(
                    {
                        "role": "assistant",
                        "content": m.get("content") or "",
                        "tool_calls": [
                            {"function": {"name": c["name"], "arguments": c["args"]}}
                            for c in m["tool_calls"]
                        ],
                    }
                )
            else:
                out.append({"role": role, "content": m.get("content", "")})
        return out

    @staticmethod
    def _to_openai(messages) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            if role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.get("tool_call_id", ""),
                        "content": str(m.get("content", "")),
                    }
                )
            elif role == "assistant" and m.get("tool_calls"):
                out.append(
                    {
                        "role": "assistant",
                        "content": m.get("content") or "",
                        "tool_calls": [
                            {
                                "id": c["id"],
                                "type": "function",
                                "function": {
                                    "name": c["name"],
                                    "arguments": json.dumps(c["args"], ensure_ascii=False),
                                },
                            }
                            for c in m["tool_calls"]
                        ],
                    }
                )
            else:
                out.append({"role": role, "content": m.get("content", "")})
        return out

    # ---------------- Anthropic ----------------
    async def _anthropic(self, messages, tools, temperature, max_tokens) -> LLMResult:
        system = ""
        conv: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                system = str(m.get("content", ""))
                continue
            if role == "tool":
                conv.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.get("tool_call_id", ""),
                                "content": str(m.get("content", "")),
                            }
                        ],
                    }
                )
            elif role == "assistant" and m.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for c in m["tool_calls"]:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": c["id"],
                            "name": c["name"],
                            "input": c["args"],
                        }
                    )
                conv.append({"role": "assistant", "content": blocks})
            else:
                conv.append({"role": "user" if role == "user" else "assistant", "content": m.get("content", "")})

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
            "messages": conv,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {"name": t["name"], "description": t.get("description", ""), "input_schema": t["parameters"]}
                for t in tools
            ]
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            r = await cli.post(f"{self.base_url}/messages", json=payload, headers=headers)
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code}: {r.text[:240]}")
            data = r.json()
        text_parts, calls = [], []
        for i, blk in enumerate(data.get("content") or []):
            if blk.get("type") == "text":
                text_parts.append(blk.get("text", ""))
            elif blk.get("type") == "tool_use":
                calls.append(ToolCall(id=blk.get("id", f"call_{i}"), name=blk.get("name", ""), args=blk.get("input") or {}))
        usage = data.get("usage") or {}
        return LLMResult(
            text="\n".join(text_parts).strip(),
            tool_calls=calls,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)),
            },
            raw=data,
        )

    # ---------------- Gemini ----------------
    async def _gemini(self, messages, tools, temperature, max_tokens) -> LLMResult:
        system = ""
        contents: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                system = str(m.get("content", ""))
                continue
            if role == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": m.get("name", "tool"),
                                    "response": {"result": str(m.get("content", ""))},
                                }
                            }
                        ],
                    }
                )
            elif role == "assistant" and m.get("tool_calls"):
                parts: list[dict[str, Any]] = []
                if m.get("content"):
                    parts.append({"text": m["content"]})
                for c in m["tool_calls"]:
                    parts.append({"functionCall": {"name": c["name"], "args": c["args"]}})
                contents.append({"role": "model", "parts": parts})
            else:
                contents.append(
                    {"role": "user" if role == "user" else "model", "parts": [{"text": m.get("content", "")}]}
                )

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.temperature if temperature is None else temperature,
                "maxOutputTokens": self.max_tokens if max_tokens is None else max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": t["parameters"],
                        }
                        for t in tools
                    ]
                }
            ]
        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            r = await cli.post(url, json=payload, headers={"Content-Type": "application/json"})
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code}: {r.text[:240]}")
            data = r.json()
        cand = (data.get("candidates") or [{}])[0]
        text_parts, calls = [], []
        for i, part in enumerate((cand.get("content") or {}).get("parts") or []):
            if "text" in part:
                text_parts.append(part["text"])
            if "functionCall" in part:
                fc = part["functionCall"]
                calls.append(ToolCall(id=f"call_{i}", name=fc.get("name", ""), args=fc.get("args") or {}))
        um = data.get("usageMetadata") or {}
        return LLMResult(
            text="\n".join(text_parts).strip(),
            tool_calls=calls,
            usage={
                "prompt_tokens": um.get("promptTokenCount", 0),
                "completion_tokens": um.get("candidatesTokenCount", 0),
                "total_tokens": um.get("totalTokenCount", 0),
            },
            raw=data,
        )

    # ---------------- Mock ----------------
    def _mock(self, messages, tools) -> LLMResult:
        last = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last = str(m.get("content", ""))
                break
        names = [t["name"] for t in (tools or [])]
        if "say" in names:
            return LLMResult(
                tool_calls=[ToolCall(id="mock_1", name="say", args={"text": "（mock 模式）我在呢，等我改完这行。"})]
            )
        return LLMResult(text="（mock 模式）" + last[:40])

    # ---------------- 状态 ----------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "label": self.prefix,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "ready": self.ready,
            "why_not": self.why_not,
            "calls": self.calls,
            "fails": self.fails,
            "tokens": self.tokens,
            "last_error": self.last_error,
        }

    async def health(self) -> bool:
        try:
            res = await self.chat(
                [
                    {"role": "system", "content": "只回答一个字。"},
                    {"role": "user", "content": "在吗"},
                ],
                max_tokens=8,
                temperature=0,
            )
            return bool(res.text or res.tool_calls)
        except Exception as exc:  # noqa: BLE001
            log.debug("健康检查失败：%s", exc)
            return False
