"""弹幕探测器：先把「能不能抓到」这件事单独验证清楚，再谈开播。

用法：
    python tools/probe_danmaku.py                         # 用 config.yaml 的 room_url
    python tools/probe_danmaku.py --url https://live.douyin.com/123456
    python tools/probe_danmaku.py --dump                  # 不监听，只导出页面结构（抓不到时用这个）
    python tools/probe_danmaku.py --seconds 90            # 跑 90 秒后自动结束

它会：开一个真窗口的 Chrome（不登录、不点任何按钮、只读 DOM）→ 注入监听 → 把抓到的事件
一条条打印出来。跑完会给出判定：抓到了 / 页面没动静 / 选择器没命中。
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

from app.browser.cdp import ChromeCDP  # noqa: E402
from app.danmaku.dom_source import INJECT_JS, DRAIN_JS, STATUS_JS  # noqa: E402
from app.models import guess_gift_value  # noqa: E402

# 把页面里「可能是弹幕容器」的东西全找出来，抓不到时用它定位真实 class
DUMP_JS = r"""
(function () {
  var out = { url: location.href, title: document.title, bodyLen: 0, cands: [], chatRoot: null };
  try { out.bodyLen = document.body ? document.body.innerText.length : 0; } catch (e) {}
  var KEY = /chat|danmaku|comment|message|msg|barrage|gift|live-?chat|webcast/i;
  var seen = 0;
  document.querySelectorAll('div,li,ul,section').forEach(function (el) {
    if (seen > 400) return;
    var cls = (el.className && el.className.toString) ? el.className.toString() : '';
    if (!cls || !KEY.test(cls)) return;
    var kids = el.children.length;
    var txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!txt || txt.length > 200) return;
    // 只收「看起来像一条消息」的：子元素不多、文字不长、class 命中关键词
    if (kids > 14) return;
    seen++;
    out.cands.push({
      cls: cls.slice(0, 110),
      tag: el.tagName.toLowerCase(),
      kids: kids,
      depth: (function (n) { var d = 0; while (n && n !== document.body) { n = n.parentElement; d++; } return d; })(el),
      txt: txt.slice(0, 70)
    });
  });
  out.cands.sort(function (a, b) { return a.depth - b.depth; });
  out.cands = out.cands.slice(0, 40);
  // 找最像「聊天列表」的父容器：子元素多、每个子元素文字都不长
  var best = null, bestScore = 0;
  document.querySelectorAll('div,ul').forEach(function (el) {
    var k = el.children.length;
    if (k < 3 || k > 120) return;
    var ok = 0;
    for (var i = 0; i < Math.min(k, 12); i++) {
      var t = (el.children[i].textContent || '').trim();
      if (t.length > 2 && t.length < 160) ok++;
    }
    if (ok / Math.min(k, 12) < 0.8) return;
    var cls = (el.className && el.className.toString) ? el.className.toString() : '';
    var score = k + (KEY.test(cls) ? 40 : 0);
    if (score > bestScore) { bestScore = score; best = { cls: cls.slice(0, 120), kids: k, score: score }; }
  });
  out.chatRoot = best;
  return out;
})()
"""

KIND_LABEL = {
    "comment": "💬 评论",
    "gift": "🎁 礼物",
    "follow": "✨ 关注",
    "enter": "🚪 进场",
    "like": "👍 点赞",
}


async def do_login(profile: Path) -> None:
    """打开一个可见窗口，让用户扫码。登录态会自动写进 profile。"""
    print("正在打开抖音登录页…窗口会出现在屏幕中间，请用抖音 App 扫码。")
    cdp = ChromeCDP(
        "https://live.douyin.com/",
        headless=False,
        width=1180,
        height=860,
        position=(120, 80),
        user_data_dir=profile,
        extra_args=["--disable-notifications"],
    )
    await cdp.start()
    print("窗口已打开。扫码后在手机上点「确认登录」，这里会自动检测。\n")

    deadline = time.time() + 300
    last_note = 0.0
    while time.time() < deadline:
        await asyncio.sleep(3)
        try:
            # sessionid 是 httpOnly，document.cookie 读不到，必须走 CDP
            jar = await cdp.call("Network.getAllCookies", timeout=8)
            names = {c.get("name") for c in (jar.get("cookies") or [])}
            logged = bool({"sessionid", "sessionid_ss", "sid_tt"} & names)
        except Exception:  # noqa: BLE001
            continue
        if logged:
            print("\n✓ 登录成功，登录态已保存到", profile)
            print("  之后跑 python tools/probe_danmaku.py 就能读到观众弹幕了。")
            await asyncio.sleep(1.5)
            await cdp.close()
            return
        left = int(deadline - time.time())
        if time.time() - last_note > 20:
            last_note = time.time()
            print(f"  …等待扫码（还剩 {left} 秒）")

    print("\n超时未检测到登录。窗口留着，你可以继续扫；或者重跑这个命令。")
    await cdp.close()


async def dump(cdp: ChromeCDP) -> None:
    print("\n=== 页面结构导出 ===")
    info = await cdp.eval(DUMP_JS)
    if not info:
        print("拿不到页面信息")
        return
    print(f"url     : {info.get('url')}")
    print(f"title   : {info.get('title')}")
    print(f"文本长度: {info.get('bodyLen')}")
    root = info.get("chatRoot")
    print(f"\n最像聊天列表的容器: {json.dumps(root, ensure_ascii=False) if root else '没找到'}")
    cands = info.get("cands") or []
    print(f"\nclass 命中关键词的候选元素（{len(cands)} 个，按层级从浅到深）:")
    for c in cands[:24]:
        print(f"  d{c['depth']:<2} {c['tag']:<6} kids={c['kids']:<2} .{c['cls']}")
        print(f"       「{c['txt']}」")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    ap.add_argument("--seconds", type=int, default=75, help="监听多久（默认 75 秒）")
    ap.add_argument("--dump", action="store_true", help="只导出 DOM 结构，不监听")
    ap.add_argument("--visible", action="store_true", help="窗口放在屏幕内，方便你看")
    ap.add_argument("--login", action="store_true", help="打开窗口扫码登录抖音，登录态存进应用用的 profile")
    args = ap.parse_args()

    import yaml

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    url = args.url or str((cfg.get("danmaku") or {}).get("room_url") or "").strip()

    # 应用和探测器共用同一个 profile，登录一次两边都生效
    profile = ROOT / "data" / "chrome-live"

    if args.login:
        await do_login(profile)
        return
    if not url:
        print("没有直播间地址。")
        print("  1) 在 config.yaml 的 danmaku.room_url 填上，或")
        print("  2) 加参数：python tools/probe_danmaku.py --url https://live.douyin.com/123456")
        return

    pos = (0, 40) if args.visible else (-2600, 40)
    print(f"打开：{url}")
    print(f"模式：{'导出结构' if args.dump else f'监听 {args.seconds} 秒'}  窗口：{'可见' if args.visible else '移到屏幕外'}\n")

    cdp = ChromeCDP(
        url,
        headless=False,
        width=1280,
        height=900,
        position=pos,
        user_data_dir=profile,
        extra_args=["--mute-audio", "--disable-notifications"],
    )
    await cdp.start()
    await asyncio.sleep(4.0)  # 等抖音前端渲染

    try:
        if args.dump:
            await dump(cdp)
            return

        res = await cdp.eval(INJECT_JS)
        print(f"注入结果: {res}")
        st = await cdp.eval(STATUS_JS)
        print(f"页面状态: title={st.get('title')!r} 文本长度={st.get('bodyLen')} "
              f"已结束={st.get('ended')} 已挂钩={st.get('hooked')}\n")

        got: list[dict] = []
        t_end = time.time() + args.seconds
        tick = 0
        while time.time() < t_end:
            await asyncio.sleep(1.0)
            tick += 1
            try:
                batch = await cdp.eval(DRAIN_JS)
            except Exception as exc:  # noqa: BLE001
                print(f"读取失败：{exc}")
                batch = None
            if batch is None:
                print("队列不见了（页面可能刷新），重新注入")
                await cdp.eval(INJECT_JS)
                continue
            for item in batch:
                got.append(item)
                kind = item.get("kind")
                label = KIND_LABEL.get(kind, kind)
                if kind == "gift":
                    val = guess_gift_value(item.get("gift") or "", int(item.get("count") or 1))
                    print(f"  {label}  {item.get('user')}  ×{item.get('count')}  "
                          f"「{item.get('gift')}」 估值 {val} 抖币")
                elif kind == "comment":
                    print(f"  {label}  {item.get('user')}：{item.get('text')}")
                else:
                    print(f"  {label}  {item.get('user')}")
            if tick % 20 == 0:
                print(f"  … 已跑 {tick}s，累计 {len(got)} 条")

        try:
            jar = await cdp.call("Network.getAllCookies", timeout=8)
            logged = bool({"sessionid", "sessionid_ss", "sid_tt"} & {c.get("name") for c in (jar.get("cookies") or [])})
        except Exception:  # noqa: BLE001
            logged = False
        if not logged:
            print("\n⚠ 当前未登录抖音。未登录时抖音不返回观众弹幕，只能看到系统公告。")
            print("  先跑这个扫码登录（只需一次）：")
            print("    python tools/probe_danmaku.py --login")

        print("\n=== 判定 ===")
        if got:
            kinds = {}
            for g in got:
                kinds[g.get("kind")] = kinds.get(g.get("kind"), 0) + 1
            print(f"✓ 抓到 {len(got)} 条：{kinds}")
            print("  这个房间的弹幕能正常采集，可以开播。")
        else:
            st = await cdp.eval(STATUS_JS)
            print("✗ 一条都没抓到。按可能性排：")
            print("  1) 直播间没人说话（换个热门直播间再试，或自己发一条）")
            print("  2) 抖音的弹幕 DOM 结构跟我的选择器对不上")
            print(f"  3) 页面没加载出来（title={st.get('title')!r}，文本长度={st.get('bodyLen')}）")
            print("\n  下一步跑这个，把输出发我，我按真实 class 改选择器：")
            print("    python tools/probe_danmaku.py --dump")
    finally:
        await cdp.close()


if __name__ == "__main__":
    asyncio.run(main())
