"""本地模型自检：Ollama 通不通、模型拉没拉、一句话要等多久。

三层路由里本地这层最容易出问题——装没装、模型名对不对、思考模式关没关、
显存够不够。这个脚本一次性把这些都查一遍。

用法：
    python tools/check_local_llm.py                # 用 config.yaml 里的 llm.local
    python tools/check_local_llm.py --model qwen3:4b
    python tools/check_local_llm.py --prompt "有观众问你在干嘛"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.config import Config  # noqa: E402

OK, BAD = "  ✓ ", "  ✗ "


def line(ok: bool, text: str, extra: str = "") -> bool:
    print((OK if ok else BAD) + text + (f"   {extra}" if extra else ""))
    return ok


def gpu_used() -> str:
    """当前显存占用，用来确认模型真的压进显卡了。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6,
        )
        if out.returncode == 0 and out.stdout.strip():
            used, total = out.stdout.strip().splitlines()[0].split(",")
            return f"{int(used)} / {int(total)} MiB"
    except Exception:  # noqa: BLE001
        pass
    return "读不到（没装 nvidia-smi？）"


async def get_tags(base: str) -> list[str]:
    async with httpx.AsyncClient(timeout=6) as cli:
        r = await cli.get(f"{base}/api/tags")
        r.raise_for_status()
        data = r.json()
    return [m.get("name", "") for m in (data.get("models") or [])]


async def native_chat(base: str, model: str, messages: list[dict], max_tokens: int) -> dict:
    """走 Ollama 原生接口，能拿到 eval_count / eval_duration 算真实速度。"""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": -1,
        "options": {"temperature": 0.9, "num_predict": max_tokens},
    }
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=120) as cli:
        r = await cli.post(f"{base}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
    data["_wall"] = time.perf_counter() - t0
    return data


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="")
    ap.add_argument("--base", default="")
    ap.add_argument("--prompt", default="有观众问「主播在干嘛呢」")
    args = ap.parse_args()

    cfg = Config()
    base = (args.base or str(cfg.get("llm.local.base_url", "") or "")).rstrip("/")
    if not base:
        base = "http://127.0.0.1:11434"
    # 配置里可能填的是 .../v1（给 OpenAI 兼容端点用的），这里统一退回根路径
    if base.endswith("/v1"):
        base = base[:-3]
    model = args.model or str(cfg.get("llm.local.model", "") or "qwen3:8b")

    print("=" * 52)
    print("  本地模型自检")
    print("=" * 52)
    print(f"  地址：{base}")
    print(f"  模型：{model}")
    print(f"  显存：{gpu_used()}")
    print()

    fails = 0

    # 1) 服务在不在
    try:
        tags = await get_tags(base)
        fails += not line(True, "Ollama 服务在线", f"{len(tags)} 个模型")
    except Exception as exc:  # noqa: BLE001
        line(False, "Ollama 服务连不上", str(exc)[:80])
        print("\n  先装 Ollama 并让它跑起来：https://ollama.com/download")
        print("  装完执行：ollama pull " + model)
        return 1

    # 2) 模型拉没拉
    hit = next((t for t in tags if t == model or t.split(":")[0] == model), "")
    if not hit:
        line(False, f"模型 {model} 不在本地", "已有的：" + (", ".join(tags[:6]) or "（空）"))
        print(f"\n  执行：ollama pull {model}")
        return 1
    line(True, "模型已就位", hit)

    # 3) 原生接口：速度与思考模式
    msgs = [
        {"role": "system", "content": "你是抖音主播，说话口语、短句。只回一句话，不超过 30 字，不要引号。"},
        {"role": "user", "content": args.prompt},
    ]
    try:
        data = await native_chat(base, model, msgs, 80)
    except Exception as exc:  # noqa: BLE001
        line(False, "推理失败", str(exc)[:100])
        return 1

    msg = data.get("message") or {}
    text = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "").strip()
    ev = int(data.get("eval_count") or 0)
    ev_ns = int(data.get("eval_duration") or 0)
    load_ns = int(data.get("load_duration") or 0)
    tok_s = (ev / (ev_ns / 1e9)) if ev and ev_ns else 0.0

    line(bool(text), "生成了回复", repr(text[:40]))
    if thinking:
        line(False, "模型输出了思考内容", f"{len(thinking)} 字，说明 think 没关掉")
        fails += 1
    else:
        line(True, "思考模式已关闭", "没有  thinking 残留")

    line(tok_s >= 20, "生成速度", f"{tok_s:.1f} tok/s（{ev} token）")
    line(data["_wall"] < 5, "端到端耗时", f"{data['_wall']:.2f}s")
    if load_ns > 300_000_000:
        print(f"     注意：本次加载模型花了 {load_ns / 1e9:.1f}s，预热之后不会再有这一下")

    # 4) 项目实际链路：LLMClient（内部走 Ollama 原生接口）
    try:
        from app.llm.client import LLMClient

        if args.model:
            cfg.set("llm.local.model", args.model)
        cli = LLMClient(cfg, "llm.local")
        t0 = time.perf_counter()
        res = await cli.chat(msgs, None, max_tokens=80)
        cost = time.perf_counter() - t0
        line(bool(res.text), "项目链路可用（LLMClient）", f"{cost:.2f}s / {res.text[:30]!r}")
        if not res.text:
            line(False, "项目链路返回空文本", "think 没关掉，或 max_tokens 被思考吃光")
            fails += 1
    except Exception as exc:  # noqa: BLE001
        line(False, "项目链路失败", str(exc)[:100])
        fails += 1

    print()
    print(f"  收尾显存：{gpu_used()}")
    print(f"\n结果：{'全部正常' if fails == 0 else f'{fails} 项异常'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    sys.exit(asyncio.run(main()))
