"""弹幕源 · 网页版直播间 DOM 注入（不装插件、不花钱、不碰签名）。"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from ..browser.cdp import ChromeCDP
from ..models import LiveEvent, guess_gift_value
from .base import DanmakuSource, clean_user

log = logging.getLogger("danmaku.dom")

# 页面 → Python 的直推通道名（对应页面里的 window.__dmEmit）
BINDING = "__dmEmit"

INJECT_JS = r"""
(function () {
  if (window.__DMQ) { return 'ok:' + (window.__DM_ROOT || 'cached'); }
  window.__DMQ = [];
  window.__DM_SEEN = new Map();
  window.__DM_ITEMTXT = new WeakMap();   // 同一个 item 处理过一次就不重复
  var MAXQ = 300;

  var NOISE = ['互动消息','在线观众榜','说点什么','添加直播画面','申请开播权限','展示本场',
               '主播中心','直播设置','开播设置','切换场景','添加素材','清空','互动玩法',
               '直播工具','评论互动信息','礼物消息','直播伴侣',
               '欢迎来到直播间','抖音严禁未成年人','理性消费','如主播在直播中',
               '严禁违法违规','原画可试看','立即登录','试看完成','已切换至',
               '送礼人数','排名','小时榜','人气榜','下载抖音精选','私信加载中',
               '日播放量','观看人数','充钻石','更多直播',
               '点亮了','正在加载','加载中','网络开小差'];
  // 界面按钮词：整段完全相等才丢，避免误杀「这个礼物好看」
  var UI_EXACT = ['消息','私信','通知','投稿','我的','首页','精选','推荐','关注',
                  '搜索','客户端','壁纸','充值','全屏','清屏','发送','说点什么',
                  '聊天','礼物','榜单','规则','举报','更多','评论','点赞','分享',
                  '在线观众','观众榜','互动消息','礼物消息','直播设置','观看',
                  '粉丝团','加入粉丝团','发送消息','投喂'];

  function clean(t) { return (t || '').replace(/\s+/g, ' ').trim(); }
  function isNoise(t) {
    if (!t) return true;
    if (UI_EXACT.indexOf(t) >= 0) return true;
    for (var i = 0; i < NOISE.length; i++) { if (t.indexOf(NOISE[i]) >= 0) return true; }
    if (/^\d+$/.test(t)) return true;
    if (/^[\d\s:：.%+\-—]+$/.test(t)) return true;
    return false;
  }
  function push(kind, user, text, gift, count) {
    var key = kind + '|' + user + '|' + text + '|' + gift;
    var t = Date.now();
    var last = window.__DM_SEEN.get(key) || 0;
    if (t - last < 15000) { return; }
    window.__DM_SEEN.set(key, t);
    if (window.__DM_SEEN.size > 700) { window.__DM_SEEN.clear(); }
    var item = { kind: kind, user: user, text: text, gift: gift, count: count || 1, ts: t };
    window.__DMQ.push(item);
    if (window.__DMQ.length > MAXQ) { window.__DMQ.splice(0, window.__DMQ.length - MAXQ); }
    // 直推通道：CDP 绑定的 window.__dmEmit，页面一有消息立刻送到 Python，
    // 不用等轮询。绑定还没挂上时静默跳过，队列照样兜底。
    try { if (typeof window.__dmEmit === 'function') { window.__dmEmit(JSON.stringify(item)); } } catch (e) {}
  }

  var GIFT = /^(.{1,26}?)\s*(?:送出了|送给主播|送出|赠送了?|投喂了?)\s*(.{1,18}?)\s*(?:[x×]\s*(\d+))?$/;
  var FOLLOW = /^(.{1,26}?)\s*(?:关注了你|关注了主播|成为了主播的粉丝|加入了粉丝团)/;
  var ENTER = /^(.{1,26}?)\s*(?:来了|进入了直播间|加入直播间|进入了直播)/;
  var LIKE = /^(.{1,26}?)\s*(?:为主播点赞|点亮了|点了赞)/;
  var COMMENT = /^(.{1,26}?)\s*[：:]\s*([\s\S]{1,140})$/;

  function classify(txt) {
    var m;
    if ((m = GIFT.exec(txt)) && m[2]) { push('gift', m[1], '', m[2], parseInt(m[3] || '1', 10)); return true; }
    if ((m = FOLLOW.exec(txt))) { push('follow', m[1], '', '', 1); return true; }
    if ((m = ENTER.exec(txt))) { push('enter', m[1], '', '', 1); return true; }
    if ((m = LIKE.exec(txt))) { push('like', m[1], '', '', 1); return true; }
    if ((m = COMMENT.exec(txt))) { push('comment', m[1], m[2], '', 1); return true; }
    return false;
  }

  function inChatList(el) {
    var n = el, depth = 0;
    while (n && depth < 16) {
      var c = (n.className && n.className.toString) ? n.className.toString() : '';
      if (c.indexOf('chatroom___list') >= 0) { return true; }
      n = n.parentElement;
      depth++;
    }
    return false;
  }

  // 抖音的 item 内部：昵称 span 带「：」，正文在另一个 span，class 是随机哈希
  function findNick(el) {
    var sels = ['[class*="nickname"]', '[class*="nickName"]', '[class*="user-name"]',
                '[class*="userName"]', '[class*="user_name"]'];
    for (var i = 0; i < sels.length; i++) {
      var n = el.querySelector(sels[i]);
      if (n) {
        var t = clean(n.textContent).replace(/[:：]\s*$/, '');
        if (t && t.length <= 24) { return t; }
      }
    }
    // 退化：找第一个以「：」结尾的短 span
    var spans = el.querySelectorAll('span');
    for (var j = 0; j < spans.length; j++) {
      var s = clean(spans[j].textContent);
      if (s && s.length <= 26 && /[:：]\s*$/.test(s)) { return s.replace(/[:：]\s*$/, ''); }
    }
    return '';
  }
  function findBody(el, nick) {
    var sels = ['[class*="content-with-emoji"]', '[class*="content"]', '[class*="text"]'];
    for (var i = 0; i < sels.length; i++) {
      var n = el.querySelector(sels[i]);
      if (n) {
        var t = clean(n.textContent);
        if (t && t !== nick) { return t; }
      }
    }
    return '';
  }

  function parseItem(el) {
    if (el.querySelector('[class*="room-message"]')) { return false; }
    if (el.className && el.className.toString().indexOf('room-message') >= 0) { return false; }

    var all = clean(el.textContent);
    // 内容是分几步填进来的：同一个 item 内容没变就别重复处理
    if (window.__DM_ITEMTXT.get(el) === all) { return false; }
    window.__DM_ITEMTXT.set(el, all);

    if (!all || all.length > 200) { return false; }
    if (isNoise(all)) { return false; }

    var nick = findNick(el);
    var body = findBody(el, nick);

    if (nick && body && body !== nick) {
      var whole = nick + '：' + body;
      if (classify(whole)) { return true; }
      if (!isNoise(body)) { push('comment', nick, body, '', 1); return true; }
    }
    if (nick && all.indexOf(nick) === 0) {
      var rest = all.slice(nick.length).replace(/^[:：\s]+/, '').trim();
      if (rest.length >= 1 && rest.length <= 140 && !isNoise(rest)) {
        if (classify(rest)) { return true; }
        push('comment', nick, rest, '', 1);
        return true;
      }
    }
    return classify(all);
  }

  function clsOf(el) {
    return (el && el.className && el.className.toString) ? el.className.toString() : '';
  }

  // 找这条新增节点归属的「消息条目」。
  // 抖音会先插一个没有 class 的 div，过一会儿才补上 class，
  // 所以不能只看 class —— 还得认「聊天列表的直接子元素」。
  function itemOf(node) {
    var el = node, depth = 0, c;
    while (el && depth < 16) {
      c = clsOf(el);
      if (c.indexOf('chatroom___item') >= 0) { return el; }
      el = el.parentElement; depth++;
    }
    el = node; depth = 0;
    while (el && el.parentElement && depth < 16) {
      if (clsOf(el.parentElement).indexOf('chatroom___list') >= 0) { return el; }
      el = el.parentElement; depth++;
    }
    return null;
  }

  function handleNode(node) {
    if (!node) { return; }
    if (node.nodeType === 3) { node = node.parentElement; }
    if (!node || node.nodeType !== 1) { return; }
    var item = itemOf(node);
    if (!item) { return; }
    var c = clsOf(item);
    if (c.indexOf('room-message') >= 0) { return; }
    parseItem(item);
  }

  var observer = null;
  function cb(muts) {
    for (var i = 0; i < muts.length; i++) {
      var m = muts[i];
      if (!m.addedNodes || !m.addedNodes.length) {
        // 文本是后填进 span 的情况
        if (m.type === 'characterData' && m.target) {
          handleNode(m.target.parentElement);
        }
        continue;
      }
      for (var j = 0; j < m.addedNodes.length; j++) { handleNode(m.addedNodes[j]); }
    }
  }

  // 必须挂 body：抖音是 React 应用，chatroom___list 这个节点会被整体替换，
  // 挂在它上面观察器随时会失效。挂 body 再加 inChatList 过滤才稳。
  function attach() {
    if (observer) { observer.disconnect(); }
    observer = new MutationObserver(cb);
    try {
      observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    } catch (e) { return false; }
    window.__DM_OBS = observer;
    window.__DM_ROOT = 'body';
    return true;
  }

  attach();
  return 'ok:' + (window.__DM_ROOT || '?');
})()
"""

DRAIN_JS = "(function(){ return window.__DMQ ? window.__DMQ.splice(0, window.__DMQ.length) : null; })()"

STATUS_JS = """
(function(){
  return {
    hooked: !!window.__DMQ,
    url: location.href,
    title: document.title,
    bodyLen: document.body ? document.body.innerText.length : 0,
    ended: /直播已结束|直播结束|回放/.test(document.title) ||
           (document.body && /直播已结束/.test(document.body.innerText.slice(0, 2000)))
  };
})()
"""


class DomSource(DanmakuSource):
    name = "dom"
    label = "网页直播间（DOM 注入）"

    def __init__(self, cfg, emit, room_url: str):
        super().__init__(cfg, emit)
        self.room_url = room_url
        self.cdp: ChromeCDP | None = None
        self._injected_at = 0.0
        self.ended = False
        self.enter_last_at = 0.0   # 最近一次抓到进场的时间（OCR 判断要不要兜底用）
        self._q: asyncio.Queue[dict] = asyncio.Queue(maxsize=200)
        self._bound = False
        self._last_push_at = 0.0
        self._emitted: dict[str, float] = {}

    async def _ensure_browser(self) -> None:
        if self.cdp and self.cdp.alive and self.cdp.ws and not self.cdp.ws.closed:
            return
        if self.cdp:
            try:
                await self.cdp.close()
            except Exception:  # noqa: BLE001
                pass
        headless = bool(self.cfg.get("danmaku.sources.dom.headless", False))
        # 无头模式也能跑，但更容易被风控；默认用真窗口挪到屏幕外
        pos = (-2600, 40) if self.cfg.get("danmaku.sources.dom.offscreen", True) else (0, 0)
        self.cdp = ChromeCDP(
            self.room_url,
            chrome_path=str(self.cfg.get("danmaku.sources.dom.chrome_path", "") or ""),
            headless=headless,
            width=1280,
            height=900,
            position=pos,
            user_data_dir=self.cfg.data_dir / "chrome-live",
            extra_args=[
                "--mute-audio",
                "--disable-notifications",
                # 进程被强杀重启时不弹「要恢复页面吗」，弹窗会盖住直播伴侣干扰 OCR
                "--disable-session-crashed-bubble",
                "--hide-crash-restore-bubble",
            ],
        )
        await self.cdp.start()
        self._bound = False
        self.cdp.on_event("Runtime.bindingCalled", self._on_binding)
        log.info("已打开直播间页面：%s", self.room_url)
        await asyncio.sleep(3.0)

    # ---------- 直推通道 ----------
    def _on_binding(self, params: dict) -> None:
        """页面里的 window.__dmEmit 一被调用就进这里，同步入队，微秒级。"""
        if params.get("name") != BINDING:
            return
        try:
            item = json.loads(params.get("payload") or "{}")
        except Exception:  # noqa: BLE001
            return
        if not isinstance(item, dict):
            return
        self._last_push_at = time.time()
        try:
            self._q.put_nowait(item)
        except asyncio.QueueFull:
            pass

    async def _bind(self) -> None:
        if self._bound or not self.cdp:
            return
        try:
            await self.cdp.add_binding(BINDING)
            self._bound = True
            self._last_push_at = time.time()
            log.info("弹幕直推通道已建立（不再靠轮询）")
        except Exception as exc:  # noqa: BLE001
            log.warning("直推通道建立失败，退回轮询：%s", str(exc)[:120])

    async def _inject(self) -> bool:
        assert self.cdp
        await self._bind()
        try:
            res = await self.cdp.eval(INJECT_JS)
            if isinstance(res, str) and (res == "ok" or res.startswith("ok:") or res == "already"):
                self._injected_at = time.time()
                log.info("弹幕监听已注入")
                return True
            log.warning("注入返回：%s", res)
        except Exception as exc:  # noqa: BLE001
            log.warning("注入失败：%s", exc)
        return False

    async def _emit_item(self, item: dict) -> None:
        try:
            ev = self._to_event(item)
        except Exception:  # noqa: BLE001
            return
        if not ev:
            return
        # 直推和兜底轮询可能拿到同一条，按 (类型,昵称,时间戳) 去重
        key = f"{ev.kind}|{ev.user}|{item.get('ts') or 0}"
        now = time.time()
        if now - self._emitted.get(key, 0) < 60:
            return
        self._emitted[key] = now
        if len(self._emitted) > 800:
            cut = now - 120
            self._emitted = {k: v for k, v in self._emitted.items() if v >= cut}
        if ev.kind == "enter":
            self.enter_last_at = now
        await self._push(ev)

    async def _drain(self) -> None:
        """兜底：直推通道没建立（或断过）时，靠轮询把队列取回来。"""
        if not self.cdp:
            return
        try:
            batch = await self.cdp.eval(DRAIN_JS)
        except Exception as exc:  # noqa: BLE001
            log.debug("读取弹幕失败：%s", exc)
            if self.cdp and not self.cdp.alive:
                raise RuntimeError("浏览器已退出")
            return
        if batch is None:
            # 页面刷新了或注入丢失，重新注入（绑定也跟着失效，要重挂）
            self._injected_at = 0.0
            self._bound = False
            return
        # 绑定还在、但一条都没推过来，而轮询却有数据 —— 说明直推哑了
        # （页面重载、绑定被丢），重挂一次就能恢复毫秒级
        if batch and self._bound and time.time() - self._last_push_at > 90:
            log.info("直推通道静默超过 90 秒，重新挂载")
            self._bound = False
            self._last_push_at = time.time()
        for item in batch:
            await self._emit_item(item)

    async def _run(self) -> None:
        interval = max(0.12, float(self.cfg.get("danmaku.sources.dom.poll_ms", 400)) / 1000)
        nav_checked = 0.0
        while True:
            await self._ensure_browser()
            self.ok = True
            if not self._bound:
                await self._bind()
            if not self._injected_at or time.time() - self._injected_at > 120:
                await self._inject()

            # ① 直推优先：页面一有消息这里就被唤醒，几乎零延迟
            item: dict | None = None
            try:
                item = await asyncio.wait_for(self._q.get(), timeout=interval)
            except asyncio.TimeoutError:
                pass

            if item is not None:
                await self._emit_item(item)
                while True:  # 一次把积压的都发完，不拖到下一轮
                    try:
                        nxt = self._q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    await self._emit_item(nxt)
            else:
                # ② 兜底轮询：直推没建立时靠它，重复项会被 ts 去重挡掉
                await self._drain()

            # 每 20 秒看一眼页面状态
            if time.time() - nav_checked > 20:
                nav_checked = time.time()
                try:
                    st = await self.cdp.eval(STATUS_JS)  # type: ignore[union-attr]
                    if st:
                        if st.get("ended") and not self.ended:
                            self.ended = True
                            log.warning("检测到直播已结束 / 页面是回放：%s", st.get("title"))
                        if not st.get("hooked"):
                            self._injected_at = 0.0
                            self._bound = False
                except Exception:  # noqa: BLE001
                    pass

    def _to_event(self, item: dict) -> LiveEvent | None:
        kind = str(item.get("kind") or "")
        if kind not in ("comment", "gift", "enter", "follow", "like"):
            return None
        gift = str(item.get("gift") or "")
        cnt = int(item.get("count") or 1)
        ev = LiveEvent(
            kind=kind,  # type: ignore[arg-type]
            user=clean_user(item.get("user")),
            text=str(item.get("text") or "")[:120],
            gift_name=gift,
            gift_count=max(1, cnt),
            gift_value=guess_gift_value(gift, cnt) if gift else 0,
            source="dom",
        )
        # 用页面里那一刻的时间戳，后面算「从出现到上屏」的延迟才准
        try:
            t = float(item.get("ts") or 0) / 1000.0
            if t and abs(time.time() - t) < 30:
                ev.ts = t
        except (TypeError, ValueError):
            pass
        return ev

    async def stop(self) -> None:
        await super().stop()
        if self.cdp:
            await self.cdp.close()

    def snapshot(self) -> dict:
        d = super().snapshot()
        d.update({"room_url": self.room_url, "ended": self.ended})
        return d
