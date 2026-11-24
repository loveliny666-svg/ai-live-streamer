"""Key 连通性自检：一键判断「这个 key 能不能用、是哪家的、错在哪」。

用法：
    python tools/check_key.py                     # 用 config.yaml 里的配置
    python tools/check_key.py --provider openai --key sk-xxx --model gpt-4o-mini
    python tools/check_key.py --all               # 拿 config 里的 key 挨个试常见 provider
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

PRESETS = {
    "agnes": ("https://api.agnes-ai.cn/v1", "agnes-3.0-flash"),
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "moonshot": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    "siliconflow": ("https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.1-8b-instant"),
}


def load_cfg() -> dict:
    import yaml

    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    llm = raw.get("llm") or {}
    import os

    key = str(llm.get("api_key") or "").strip()
    env_name = str(llm.get("api_key_env") or "")
    if not key and env_name:
        key = os.environ.get(env_name, "").strip()
    return {
        "provider": str(llm.get("provider") or "deepseek").lower(),
        "key": key,
        "base_url": str(llm.get("base_url") or ""),
        "model": str(llm.get("model") or ""),
    }


def explain(status: int, body: str) -> str:
    low = body.lower()
    if status == 401 or "authentication" in low or "invalid" in low and "key" in low:
        return "key 无效 / 不是这家的 key / 已被删除"
    if status == 403:
        if "insufficient" in low or "quota" in low or "balance" in low:
            return "余额不足或额度用尽"
        return "无权限（可能需要实名或开通该模型）"
    if status == 404:
        return "model 或 base_url 不存在（模型名写错了？）"
    if status == 429:
        return "被限流，稍后再试（key 本身可能没问题）"
    if status >= 500:
        return "对方服务器故障，与 key 无关"
    return "未知错误"


async def probe(label: str, base: str, key: str, model: str) -> bool:
    url = base.rstrip("/") + "/chat/completions"
    head = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回两个字：可以"}],
        "max_tokens": 8,
        "temperature": 0,
        "stream": False,
    }
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.post(url, headers=head, json=payload)
    except Exception as exc:  # noqa: BLE001
        print(f"  [{label:11s}] 连不上  {type(exc).__name__}: {exc}")
        return False

    dt = time.time() - t0
    if r.status_code == 200:
        try:
            data = r.json()
            txt = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage") or {}
            print(
                f"  [{label:11s}] \033[32m✓ 可用\033[0m  {dt:.1f}s  "
                f"回复={txt.strip()!r}  tokens={usage.get('total_tokens', '?')}"
            )
        except Exception:  # noqa: BLE001
            print(f"  [{label:11s}] 200 但响应不是预期格式：{r.text[:120]}")
        return True

    msg = explain(r.status_code, r.text)
    print(f"  [{label:11s}] \033[31m✗ {r.status_code}\033[0m  {msg}")
    print(f"                原始返回：{r.text[:180]}")
    return False


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="")
    ap.add_argument("--key", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--all", action="store_true", help="用同一个 key 依次试所有常见 provider")
    args = ap.parse_args()

    cfg = load_cfg()
    key = (args.key or cfg["key"]).strip()
    provider = (args.provider or cfg["provider"]).lower()

    if not key:
        print("config.yaml 里 llm.api_key 是空的，也没设 LLM_API_KEY 环境变量。")
        return
    print(f"key: {key[:6]}…{key[-4:]}  长度 {len(key)}")

    if args.all:
        print(f"\n同一个 key 试着连 {len(PRESETS)} 家（只发一次最小请求，每次消耗几个 token）：\n")
        hits = []
        for name, (base, model) in PRESETS.items():
            if await probe(name, base, key, model):
                hits.append(name)
        print()
        if hits:
            print(f"能用的：{hits}")
            if hits[0] != provider:
                b, m = PRESETS[hits[0]]
                print(f"建议改 config.yaml：provider: \"{hits[0]}\"  model: \"{m}\"")
        else:
            print("一家都不认。这个 key 可能来自中转站，需要它自带的 base_url。")
            print("拿到 base_url 后这样测：")
            print("  python tools/check_key.py --base-url https://xxx/v1 --key sk-... --model gpt-4o-mini")
        return

    base = args.base_url or cfg["base_url"] or PRESETS.get(provider, ("", ""))[0]
    model = args.model or cfg["model"] or PRESETS.get(provider, ("", ""))[1]
    if not base or not model:
        print(f"不认识 provider={provider}，请用 --base-url 和 --model 指定")
        return
    print(f"provider={provider}  base_url={base}  model={model}\n")
    ok = await probe(provider, base, key, model)
    if not ok:
        print("\n可以先跑一句排除格式问题：")
        print("  python tools/check_key.py --all")
        print("注意：--all 会把这个 key 依次发给上表里的各家，只在你确认它不该属于某一家时才用。")


if __name__ == "__main__":
    asyncio.run(main())
