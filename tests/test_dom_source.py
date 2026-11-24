"""弹幕源测试：起一个「假直播间」网页，验证 DOM 注入 + 分类 + 去重全链路。"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import CFG
from app.danmaku.dom_source import DomSource
from app.models import LiveEvent

PASS, FAIL = [], []
PORT = 8777

PAGE = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>测试直播间</title>
<style>body{background:#111;color:#eee;font-family:sans-serif}#chat{width:420px}</style></head>
<body>
<div class="side">互动消息</div>
<!-- 照抖音真实结构搭：chatroom___list > ___item > ___item-wrapper > 昵称/正文 -->
<div class="webcast-chatroom___list" id="chat"></div>
<script>
const box = document.getElementById('chat');
function add(t){
  // 区分「昵称：内容」和「整句式消息（礼物/关注/进场）」
  let nick = '', body = t;
  const m = t.match(/^(.{1,26}?)[：:]\s*([\s\S]+)$/);
  if (m) { nick = m[1]; body = m[2]; }

  const item = document.createElement('div');
  item.className = 'webcast-chatroom___item webcast-chatroom___item_new';
  const wrap = document.createElement('div');
  wrap.className = 'Cl4EfhXg webcast-chatroom___item-wrapper';

  if (nick) {
    const n = document.createElement('span');
    n.className = 'rn' + Math.random().toString(36).slice(2, 6);
    n.textContent = nick + '：';
    const c = document.createElement('span');
    c.className = 'rc' + Math.random().toString(36).slice(2, 6);
    c.textContent = body;
    const ce = document.createElement('span');
    ce.className = 'webcast-chatroom___content-with-emoji-text';
    ce.textContent = body;
    wrap.appendChild(n); wrap.appendChild(c); wrap.appendChild(ce);
  } else {
    const d = document.createElement('div');
    d.className = 'rw' + Math.random().toString(36).slice(2, 6);
    d.textContent = body;
    wrap.appendChild(d);
  }
  item.appendChild(wrap);
  box.appendChild(item);
}
// 模拟持续滚动的弹幕流（每 800ms 一条，内容带序号避免被去重）
const lines = [
  '老张：主播在写什么 #N',
  '夜猫子 送出了 小心心 x3',
  '产品经理本人：这个需求很简单 #N',
  '小美 关注了主播',
  '隔壁老王 来了',
  '秃头程序员：这 bug 我见过 #N',
  '老王 送出了 嘉年华 x1',
  '互动消息',
  '发送',
];
let i = 0;
setInterval(() => {
  const t = lines[i % lines.length];
  add(t.replace('N', String(i)));
  i++;
}, 800);
</script>
</body></html>"""


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra else ""), flush=True)


async def main() -> None:
    app = web.Application()

    async def index(_):
        return web.Response(text=PAGE, content_type="text/html", charset="utf-8")

    app.router.add_get("/", index)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PORT)
    await site.start()
    print(f"假直播间：http://127.0.0.1:{PORT}/")

    got: list[LiveEvent] = []
    got_at: dict[str, float] = {}

    async def emit(ev: LiveEvent) -> None:
        got.append(ev)
        got_at[ev.eid] = time.time()

    CFG.set("danmaku.sources.dom.headless", True)
    CFG.set("danmaku.sources.dom.poll_ms", 400)
    src = DomSource(CFG, emit, f"http://127.0.0.1:{PORT}/")
    src.cfg.set("danmaku.sources.dom.offscreen", False)
    await src.start()

    try:
        for _ in range(40):
            await asyncio.sleep(0.5)
            # 多等几条：假直播间是 9 行轮播，只取 6 条可能正好漏掉「关注」
            if len(got) >= 9:
                break
        print("\n[1] 采集结果")
        for e in got:
            print(f"  · {e.kind:7s} user={e.user!r:14s} text={e.text!r} gift={e.gift_name!r} x{e.gift_count}")

        comments = [e for e in got if e.kind == "comment"]
        gifts = [e for e in got if e.kind == "gift"]
        follows = [e for e in got if e.kind == "follow"]
        enters = [e for e in got if e.kind == "enter"]

        check("收到弹幕", len(comments) >= 2, f"{len(comments)} 条")
        check("收到礼物", len(gifts) >= 1, f"{len(gifts)} 条")
        check("收到关注", len(follows) >= 1)
        check("收到进场", len(enters) >= 1)
        check("昵称解析正确", any(e.user == "老张" for e in comments),
              str([e.user for e in comments]))
        check("礼物名解析正确", any(e.gift_name == "小心心" for e in gifts),
              str([e.gift_name for e in gifts]))
        check("礼物数量解析正确", any(e.gift_count == 3 for e in gifts),
              str([e.gift_count for e in gifts]))
        check("大礼物金额估值", any(e.gift_name == "嘉年华" and e.gift_value >= 30000 for e in gifts),
              str([(e.gift_name, e.gift_value) for e in gifts]))
        check("UI 噪音被过滤", not any(e.text in ("互动消息", "发送") for e in got),
              str([e.text for e in got if e.text in ("互动消息", "发送")]))
        check("无重复事件", len({e.eid for e in got}) == len(got), f"{len(got)} 条 / {len({e.eid for e in got})} 唯一")

        print("\n[2] 采集延迟（消息出现在页面 → Python 收到）")
        check("直推通道已建立", bool(src._bound))
        lags = []
        for e in got:
            at = got_at.get(e.eid)
            if at and e.ts:
                lag = at - e.ts
                if 0 <= lag < 5:      # 页面和本机同一个时钟，离谱的丢掉
                    lags.append(lag * 1000)
        if lags:
            lags.sort()
            p90 = lags[min(len(lags) - 1, int(len(lags) * 0.9))]
            print(f"  {len(lags)} 条样本：中位 {lags[len(lags) // 2]:.0f}ms  p90 {p90:.0f}ms  最大 {max(lags):.0f}ms")
            # 老实现是 700ms 轮询，中位必然 300ms+；直推应该是几十毫秒
            check("中位延迟 < 120ms", lags[len(lags) // 2] < 120, f"{lags[len(lags) // 2]:.0f}ms")
            check("p90 延迟 < 200ms", p90 < 200, f"{p90:.0f}ms")
        else:
            check("拿到延迟样本", False)

        print("\n[3] 停止后浏览器应关闭")
        await src.stop()
        await asyncio.sleep(1.5)
        alive = bool(src.cdp and src.cdp.alive)
        check("CDP 已断开", not alive)
    finally:
        try:
            await src.stop()
        except Exception:  # noqa: BLE001
            pass
        await runner.cleanup()

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
