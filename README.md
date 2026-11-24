# AI 打工人直播间 · 无人值守自动开播

一个真能干活的桌面程序：**自己开播、自己看弹幕、自己说话、自己决定什么时候摸鱼。**

直播画面是一个叫「小饭」的蓝发女孩在房间里写代码，写累了会切到显示器上打植物大战僵尸；
观众发弹幕、送礼物，智能体会当场接话；有人送大礼物，它会「老板来了」一秒切回写代码。

```
┌──────────────────────── 一次弹幕的完整旅程 ────────────────────────┐
 观众发弹幕
    ↓ ① 采集   直播间网页的 WebSocket（主，CDP 旁听）/ 直播伴侣面板 OCR（兜底）
    ↓ ② 归一   LiveEvent(kind, user, text, gift, value) + 去重 + 限流
    ↓ ③ 决策   智能体（LLM + 工具调用），没有 Key 就换规则脑
    ↓ ④ 执行   say / switch_scene / boss_key / play_effect / remember
    ↓ ⑤ 呈现   字幕气泡 + 口型 + edge-tts 语音 + 弹幕飘屏
    ↓ ⑥ 推流   直播伴侣「窗口捕获」抓 1920×1080 舞台窗口
 观众看到画面和声音
└────────────────────────────────────────────────────────────────────┘
```

### 采集：旁听直播间的 WebSocket（主源）

直播间网页自己会连一条弹幕 WebSocket。我们用 Chrome DevTools 协议只做旁观
（`Network.webSocketFrameReceived`），不改包、不碰签名、不用登录，拿到的是平台原样的 protobuf：

```
WebSocket 帧
  ↓ ① PushFrame → field 8 是 gzip，解压得到 Response
  ↓ ② Response → repeated Message（method + payload + msg_id）
  ↓ ③ 按 method 分发：
        WebcastMemberMessage   → 进场（昵称、粉丝牌、在线人数）
        WebcastChatMessage     → 弹幕（昵称 + 内容）
        WebcastGiftMessage     → 礼物（名字、数量、抖币）
        WebcastLikeMessage     → 点赞
        WebcastSocialMessage   → 关注/分享
        WebcastRoomUserSeqMessage → 真实在线人数
  ↓ ④ 按 msg_id 去重（平台每条消息一个唯一 ID）
  ↓ ⑤ 进 Router 限流
```

为什么不用 OCR 了：

| | WebSocket | 面板 OCR |
|---|---|---|
| 延迟 | **几十毫秒** | 1～2 秒（一次识别） |
| 昵称 | **自带** | 评论气泡没有昵称，只能记「观众」 |
| 重复 | **msg_id 唯一，天然不重** | 消息挂在面板上，得靠「出现过就不再发」 |
| 在线人数 | **真实值** | 只能拿进场次数估算 |
| CPU | 几乎为零 | 每轮识别约 10 秒 CPU 时间 |

OCR 那条链路没删，作为兜底：`danmaku.sources.ocr.fallback_only: true`，
只有 WebSocket 连续 45 秒收不到帧时它才会接管（比如页面被风控、需要验证码）。

两个已知限制：

- **评论气泡没有昵称**是面板 OCR 的限制，WebSocket 没有这个问题。
- **页面被风控时**（验证码、登录墙）WebSocket 会断，这时自动回落到 OCR，
  并在 90 秒无数据后刷新页面重试。


---

## 环境要求

| 需要 | 说明 |
|---|---|
| **Windows 10 / 11** | 大量调用 Win32 API（找窗口、按会话改音量、截图 OCR）。**不支持 macOS / Linux**，别在别的系统上试。 |
| Python 3.11+ | 开发时用 3.13 验证过 |
| Chrome 或 Edge | 渲染舞台页、旁听直播间。程序会自己找，也可以用 `stage.browser` 指定 |
| 抖音直播伴侣 | 推流靠它。程序只是替你去点它的按钮，不碰推流协议 |
| 可选：Ollama + qwen3:8b | 本地模型，云端额度用完时接管。不装也能跑（退回规则脑） |

## 0. 画面长什么样

它不是一个"静静写代码"的画面，而是一个**游戏化的打工直播间**：

| 位置 | 内容 |
|---|---|
| 左上 | `LIVE · 专注 02:31:07 · 在线 12 · 点赞 1.2w · 礼物 860` |
| 左上下方 | `摸鱼 3 次 · 被抓 1 次 · 今日 1240 行` |
| 状态徽章 | `⚡ 手速 ×1.3`（礼物越多越快）、`⌨ 专心工作中 / 🐟 带薪摸鱼 / 😰 假装很忙` |
| 规则条 | `摸鱼须知 · 再工作 03:12 可以休息`（切到摸鱼后变成"还剩 01:40，老板随时可能来"） |
| 右上 | `今日绩效 1240 / 2400 行` 进度条 —— **数字来自她真实敲的字**，满 2400 行自动涨目标 |
| 角色头顶 | 气泡显示"她在回谁"：`@夜猫子 主播这个需求你打算怎么改` |
| 右下 | 事件条：`🎁 老王 送出 小心心 ×3  手速 +0.1×` |
| 左下 | 进场欢迎条：`欢迎 老王 来到直播间`（每个观众一条，滑入 + 光圈 + 粒子 + 叮一声） |
| 收到礼物 | 角色蹦一下 + 头顶飘爱心星星 + 手速 buff 真的会让打字变快 |

这些数字都不是假的：专注时长按真实时间累计，摸鱼/被抓次数由导演状态机记账，
绩效行数由舞台页每 8 秒上报"刚敲了多少字"（跨天自动清零，重启不清零）。

### 有人进来，不能让他等

进场欢迎是直播间的门面，所以这条链路被单独优化过，一路不许排队：

```
观众进门
  ↓ ① 平台推 WebcastMemberMessage → 几十毫秒内解出昵称，立刻出事件
  ↓ ② Router 按「进场专用」的短去重窗口过滤（默认 3 秒，不是 25 秒）
  ↓ ③ Bus 对进场单独放行（同一个观众隔一会儿再来，不会被 eid 永久判重）
  ↓ ④ runtime._on_event 直接广播 welcome，不经过批处理、不等模型、不等统计
  ↓ ⑤ 舞台页收到就插 DOM，动画首帧就开始跑
画面：卡片滑入 + 头像光圈 + 粒子 + 扫光 + 本地合成的「叮」
```

实测（`tests/test_welcome.py` / `tools/probe_ws_source.py`）：
平台推送 → Python 出事件是几十毫秒级（只剩 protobuf 解析）；
事件进总线 → 舞台页画出来 **1~4ms**。
老实现是 OCR 面板，一次识别 1~2 秒，还要靠「消息挂在面板上」判断有没有重复。

几个容易踩的坑，代码里都已经封住：

- **别用长窗口去重进场**：同一个观众离开再回来，字段完全一样，25 秒窗口会把他第二次进场吞掉；
- **别让欢迎等模型**：模型慢的时候，观众已经在门口站了三秒；
- **别拿「界面上还挂着」当新消息**：面板 OCR 会把同一条进场反复读出来，
  现在按 msg_id 去重，一条消息只会出一次；
- **昵称要洗**：网页里昵称后面带「：」，OCR 会把箭头、粉丝牌数字读进来，不洗就会上屏「欢迎 老王： 来到直播间」。

### 没互动的时候，声音怎么填满

直播间最怕静得能听见电流声。声音分三层：

| 层 | 内容 | 触发 |
|---|---|---|
| 背景音乐 | **默认关闭**，只留 Apple Music 等外部播放器的声音（`stage.bgm.enabled: false`） | —— |
| 人声 | edge-tts 合成的回复 | 有人发弹幕/礼物，或每 32 秒左右她随机找个话题自言自语 |
| 静音 | 没人说话时就是现场原声，不额外垫音乐 | —— |

> 之前内置的两段 numpy lo-fi 还留着，只是默认不放了 —— 直播间现在只用 Apple Music 的声音。
> 想放回来把 `stage.bgm.enabled` 改成 `true` 即可。

说话时如果开了 BGM，会自动压到 26%（duck），说完 0.6 秒内缓回来，不会抢人声。
切到「老板来了」场景时 BGM 直接停——她不敢在老板面前放音乐。

**换成自己的歌**：把 mp3 丢进 `app/stage/static/bgm/`，改 `config.yaml`：

```yaml
stage:
  bgm:
    enabled: false      # 默认关；要用内置 lo-fi 才改 true
    volume: 0.30        # 别超过 0.4，会盖住人声
    coding: "calm.mp3"
    slack: "playful.mp3"
    boss: ""            # 留空 = 这个场景不放
```

想重新生成内置的两段（改 BPM / 和弦再造）：

```bash
python tools/make_bgm.py
```

### 主播说话时，自动压低外部音乐（Apple Music）

上面那套 duck 只管舞台页自己的 BGM。如果你是用 **Apple Music / 网易云 / Spotify**
放歌，声音走的是 Windows 系统混音，直播伴侣采集到的正是这个混音——页面里的
音量旋钮管不到它。所以另有一层系统级闪避：

- 主播开口 → 找到 `AMPLibraryAgent.exe` 等音乐进程的**音频会话**，
  0.25 秒内淡到原音量的 18%；舞台页同时上报「开始出声」续上压制；
- 语音队列播完 → 舞台页上报 `idle`，0.6 秒内淡回原音量，句尾多压 0.4 秒防抖；
- 只动音乐会话，**不动系统主音量，也不动 Chrome**（人声和直播间网页都在 Chrome 里），
  所以音乐变小了，主播的声音还是原来那么响；
- 收播、退出、超时（`max_duck_sec`）都会强制把音量还回去，绝不会把你的音乐一直压着；
  pycaw / COM 万一报错就自动停用这一层，直播照常。

配置在 `config.yaml` 的 `audio.duck`，控制台「运行状态」里能看到「音乐闪避」状态：

```yaml
audio:
  duck:
    enabled: true
    level: 0.18           # 觉得压得太狠就调大，0.3 左右更自然
    targets: [...]        # 换播放器就往这里加进程名
    exclude: ["chrome", ...]   # 这里的一律不压
```

> Windows 版 Apple Music 真正出声的进程叫 `AMPLibraryAgent.exe`，不是主界面
> `AppleMusic.exe`，两个都列上了。压低的是这个会话自己的音量，你在 Apple Music
> 里拖音量条它也会尊重（不会因为恢复被覆盖回去）。

## 1. 架构

```
                    ┌──────────────────────────────────────────────┐
                    │        Orchestrator（单进程 asyncio）          │
                    │                                              │
  ┌──────────┐      │  ┌──────────┐   events   ┌──────────────┐    │
  │ 直播间网页 │─CDP─▶│  │ 采集层    │──────────▶│  事件总线 Bus │    │
  │ (Chrome) │      │  │ DOM 注入  │            └──────┬───────┘    │
  └──────────┘      │  ├──────────┤                   │            │
  ┌──────────┐      │  │ 直播伴侣  │                   ▼            │
  │ 直播伴侣  │─OCR─▶│  │ 面板识别  │            ┌──────────────┐    │
  └────┬─────┘      │  └──────────┘            │   智能体 Agent │    │
       │ 截图/点击   │                            │  LLM + Tools │    │
       │            │  ┌──────────┐   scene      └──────┬───────┘    │
       │            │  │ 导演      │◀──────────────────┘            │
       │            │  │ Director │                                │
       │            │  └────┬─────┘                                │
       │            │       │ WebSocket                            │
       │            │  ┌────▼─────────────────────┐                │
       │            │  │ 舞台页 Stage (Chrome)     │                │
       │            │  │ 角色 / 代码 / PvZ / 字幕  │                │
       │            │  └──────────────────────────┘                │
       │            └──────────────────────────────────────────────┘
       │ 窗口捕获（1920×1080 舞台窗口 + 系统声音）
       ▼
  ┌─────────┐
  │ 抖音直播 │
  └─────────┘
```

**为什么这么切分**：采集、决策、呈现三者互不阻塞。模型慢了不影响画面，画面崩了不影响说话，
采集挂了还有兜底源。任何一环出错都只降级，不中断直播。

**决策层是三层路由**（`app/llm/router.py`），不是单模型：

```
   ┌─────────────┐  额度见底 / 429 / 故障  ┌─────────────┐  连不上  ┌──────────┐
   │  云端 API   │ ─────────────────────▶ │ 本地 Ollama │ ───────▶ │  规则脑  │
   │  会调工具   │                        │  只说一句话 │          │  纯模板  │
   └─────────────┘                        └─────────────┘          └──────────┘
          ▲                                      ▲
          └── 观众点单 / 大礼物 / 六条以上混战 ──────┘ 单条弹幕 / 道谢 / 欢迎 / 自言自语
```

- **云端**：脑子。要动工具的活（写项目、切场景、放特效）、大礼物、一次涌进来六条以上的混战。
- **本地**：嘴。一句话的事，零成本、无限速。不调工具，只输出一句台词。
- **规则脑**：保险丝。前两层都趴下时保证还有声音。

免费用户 10 RPM，一场两小时直播如果全走云端，大半互动都得喂给模板随机数。
本地模型接管这部分，成本是零，质量比模板高一个量级。

**本地为什么走 Ollama 原生接口、不走 OpenAI 兼容端点**：兼容层不认 `think` 和 `keep_alive`。
实测 Qwen3 在兼容端点上会一直开着思考模式，`max_tokens` 全被推理过程吃掉，`content` 返回空字符串
（`finish_reason: length`），等于本地模型永远说不出话，每次都掉回规则脑。原生 `/api/chat` 才认这两个参数。
代码见 `app/llm/client.py` 的 `_ollama_native`。

---

## 2. 模块清单

| 模块 | 文件 | 职责 |
|---|---|---|
| 采集 · 网页 | `app/danmaku/dom_source.py` | 用本机 Chrome 打开直播间，注入 MutationObserver 抓 DOM 新增节点，正则分类成弹幕/礼物/进场/关注；命中即通过 CDP binding 直推给 Python，轮询只做兜底 |
| 采集 · OCR | `app/danmaku/ocr_source.py` | 截图直播伴侣「互动消息」面板 → RapidOCR → 合并行 → 分类。网页源真的抓到过进场时自动跳过省 CPU |
| 采集 · 模拟 | `app/danmaku/simulator.py` | 假弹幕，自测和演示用 |
| 采集 · 路由 | `app/danmaku/base.py` | 多源合并、二次去重（普通 25s / 进场 3s）、限流、事件归一、昵称清洗 |
| 决策 | `app/llm/agent.py` | 批量事件 → 模型 → 工具调用 → 落地动作；带记忆、限速、三层降级 |
| 决策 · 路由 | `app/llm/router.py` | 判断这轮该由云端、本地还是规则脑来说；按任务复杂度 + 云端额度水位分流 |
| 决策 | `app/llm/tools.py` | 10 个工具：say / noop / switch_scene / boss_key / play_effect / remember / recall / start_poll / get_room_stats / read_events |
| 决策 | `app/llm/client.py` | 厂商无关客户端：OpenAI 兼容、Anthropic、Gemini、Ollama、Mock；支持多实例（云端 + 本地各一个） |
| 决策 · 兜底 | `app/llm/fallback.py` | 云端和本地都不可用时的规则脑，保证直播间不冷场 |
| 调度 | `app/director.py` | 场景状态机：写代码 ↔ 摸鱼 ↔ 老板键，带最短停留、冷却、强制轮换 |
| 语音 | `app/tts/engine.py` | edge-tts（免费在线）为主，Windows SAPI（离线）兜底；按情绪调语速，ffmpeg 做音色润色 |
| 画面 | `app/stage/static/*` | 1920×1080 舞台：角色立绘（透明 PNG + 微动效）、逐字敲代码、PvZ 小游戏、字幕/弹幕/礼物特效 |
| 画面 · 窗口 | `app/stage/launcher.py` | 用 Chrome 全屏应用窗口承载舞台，去边框，可远程 reload |
| 开播 | `app/companion/controller.py` | 找窗口 → 截图 → OCR 找「开始直播」→ 点击 → 二次确认 → 验证；带重试与证据截图 |
| 开播 · 底层 | `app/companion/win.py` / `ocr.py` | ctypes 窗口操作、PrintWindow 后台截图、鼠标点击；RapidOCR 封装 |
| 总装 | `app/runtime.py` | 把上面全部接起来，跑批处理/主动搭话/统计/看门狗四条循环 |
| 服务 | `app/stage/server.py` | FastAPI：舞台页、控制台、WebSocket 指令通道、TTS 音频 |

---

## 3. 场景是怎么切的

`导演 Director` 持有唯一权威状态，别人只能「申请」：

```
          ┌──────────── 到点了（随机 4~15 分钟）────────────┐
          ▼                                                │
     ┌─────────┐   智能体 switch_scene("slack")   ┌──────────┴──┐
     │ coding  │─────────────────────────────────▶│    slack    │
     │ 写代码   │◀─────────────────────────────────│  摸鱼打游戏  │
     └────┬────┘   到点了 / 智能体切回            └─────────────┘
          │ boss_key（大礼物 / 观众喊老板 / 智能体决定）
          ▼
     ┌─────────┐  保持 12 秒（可配）后自动回 coding
     │  boss   │
     └─────────┘
```

约束（都在 `director` 配置段）：

- 每次切换至少停留 `min_sec`，否则申请被打回 —— 防止画面抽搐；
- 两次切换最小间隔 `switch_cooldown_sec`；
- `agent_can_switch: false` 时智能体无权切画面，只能导演按时间轮换；
- 老板键是**中断**：无视冷却，直接切，到点自动回写代码。

---

## 4. 五步开播

### 第 0 步：拿代码、装依赖、准备配置

```bash
git clone https://github.com/loveliny666-svg/ai-live-streamer.git
cd ai-live-streamer
pip install -r requirements.txt
```

**把配置模板复制一份出来，改那一份**：

```bash
copy config.example.yaml config.yaml      # Windows
cp config.example.yaml config.yaml       # macOS / Linux
```

`config.yaml` 已写进 `.gitignore` —— 你填的房间号、软件路径、机器相关的东西都不会跟着仓库走，也不会在你哪天提 PR 的时候被带上去。

### 第 1 步：设 API Key + 装本地模型

**Key 绝对不要写进 `config.yaml`。** 那个文件是公开仓库的一部分，写进去等于当着所有人的面把 key 贴出来。

用环境变量：

```bash
# Windows：设完必须重开一个终端（或者重启）才生效
setx LLM_API_KEY "sk-你的key"

# macOS / Linux：想持久化就写进 ~/.bashrc 或 ~/.zshrc
export LLM_API_KEY="sk-你的key"
```

代码读 key 的顺序是：`config.yaml` 的 `api_key` → 环境变量 → `data/.api_key` 文件。
三个都空它会直接说你「没有填 API Key」。想换厂商只改 `provider`：

```yaml
llm:
  provider: "agnes"         # agnes / deepseek / qwen / zhipu / moonshot / openai / gemini / ollama ...
  api_key: ""               # 留空，靠环境变量
  model: "agnes-3.0-flash"
  rpm: 9                    # 免费用户上限 10 次/分钟，留一点余量
```

**本地模型**（额度用完时接管简单回复，不花钱）：

```bash
# 1. 装 Ollama：https://ollama.com/download
#    装完托盘里会有一个羊驼图标，开机自启；没自启就手动开一次
# 2. 拉模型，8B 的 4bit 量化约 5GB，12GB 显存跑得开
ollama pull qwen3:8b
# 3. 确认服务在跑
ollama list
```

配置里已经默认开好了，模型名对得上就不用改：

```yaml
llm:
  local:
    enabled: true
    model: "qwen3:8b"
```

两个都不填也能跑：会自动用规则脑（`app/llm/fallback.py`）顶住互动，画面和语音都正常，只是接话没那么灵。

### 第 2 步：填直播间地址

```yaml
danmaku:
  room_url: "https://live.douyin.com/123456789012"
```

打开抖音网页版、进你自己的直播间，地址栏 `https://live.douyin.com/` 后面那串数字就是房间号。
填错了不会崩，但收不到弹幕（只剩直播伴侣面板 OCR 那条兜底的路）。

### 第 3 步：双击 `run.bat`（会自动请求管理员权限）

直播伴侣的安装清单要求管理员权限，不提权就没法自动启动它、也没法点它的按钮。
脚本会弹一次 UAC，允许即可。

如果你的直播伴侣装在别处，记得把 `config.yaml` 里这一行填上，否则程序不会帮你启动它
（留空 = 你自己先打开直播伴侣，程序只负责开播和收弹幕，一样能用）：

```yaml
companion:
  exe_path: "C:/Program Files/你的路径/直播伴侣 Launcher.exe"
```

启动后：

- 控制台 `http://127.0.0.1:8735/admin`
- 舞台页 `http://127.0.0.1:8735/stage`
- 舞台窗口全屏铺满主显示器，**没有标题栏**，可直接被窗口捕获

### 第 4 步：在直播伴侣里配三个源（只需配一次，之后一直有效）

| 位置 | 设置 |
|---|---|
| 添加素材 → 窗口 | 选窗口标题 `直播舞台`，铺满画布。这是**画面** |
| 添加素材 → 音频 | 选「系统声音 / 扬声器」。这是**声音**（TTS 从舞台页播放出来） |
| 开播设置 | 标题、封面随便；画质按网络选 |

> 舞台是全屏无边框的，窗口捕获拿到的就是纯画面，不用裁剪。
> 如果想让舞台只占一个 1920×1080 窗口（比如同屏干别的），把 `stage.mode` 改成 `window`，
> 此时 Chrome 会带一条约 32px 的自绘标题栏，需要在直播伴侣里对该源做一次裁剪。

### 第 5 步：等它自己开播

程序会：找直播伴侣窗口 → 已在播就不动 → 没在播就点「开始直播」→ 处理二次确认 → 验证是否出现「结束直播」。
全过程截图存到 `data/evidence/`。

---

## 5. 无人值守与容错

| 故障 | 程序行为 |
|---|---|
| 网页弹幕源断线 / 页面被刷掉 | 指数退避重连，自动重新注入监听；90s 无数据视为异常重启浏览器 |
| 网页源彻底不可用 | 自动切到 OCR 直播伴侣「互动消息」面板（礼物也能读） |
| 云端额度用尽 / 429 / 401 | 简单回复立刻交给本地模型，复杂任务继续等云端恢复；两层都不可用才切规则脑 |
| 本地模型没装 / 挂了 | 连续 2 次失败后冷却 60s，期间只用云端；恢复后自动重新接管简单回复 |
| 模型胡说 / 说太长 | 单次最多一句、超长截断、每分钟限流、同用户冷却 |
| edge-tts 不可用 | 自动降级到 Windows 离线语音（SAPI），仍然是同一条播放链路 |
| 语音播不出来 | 字幕照常显示，按字数计时关闭，不卡住队列 |
| 舞台页崩溃 / 白屏 | 心跳超时（默认 8s×4）→ 远程 reload；还不行就重启浏览器 |
| 游戏卡帧 | 固定步长模拟，30fps 与 120fps 下游戏时间一致，不会「卡住不动」 |
| 直播伴侣掉线弹窗 | 巡检发现「重新连接 / 重试 / 确定」→ 自动点击 |
| 直播断了 | 巡检发现看不到「结束直播」→ 冷却 120s 后自动重新开播 |
| 账号没有开播权限 | 识别到「申请开播权限」→ **不乱点**，控制台红字告警并留证据截图 |
| 鼠标没人动、直播伴侣抢焦点 | 只有真正要点击时才把直播伴侣提到前台，点完把舞台放回去 |
| 到了收播时间 | `app.shutdown_at: "03:30"` → 自动点「结束直播」再退出 |

还有两层节奏控制，避免「没人说话就死寂」和「弹幕多了刷屏」：

- `behavior.idle_talk_sec`：安静这么久就主动找话题；
- `behavior.max_replies_per_min` + `same_user_cooldown_sec`：说太多会被限流。

---

## 6. 关键配置项

<details>
<summary>点开看全表</summary>

| 键 | 默认 | 说明 |
|---|---|---|
| `llm.provider` | agnes | 厂商，决定默认地址与模型 |
| `llm.model` | agnes-3.0-flash | 模型名 |
| `llm.min_interval_sec` | 2.5 | 两次模型调用的最小间隔，**控成本的主闸门** |
| `llm.context_events` | 14 | 每次塞进 prompt 的弹幕条数 |
| `llm.rpm` | 9 | 云端每分钟最多调用几次。免费用户上限 10，填 9 留一点余量 |
| `llm.fail_threshold` | 3 | 云端连续失败几次算降级 |
| `llm.local.enabled` | true | 本地模型总开关。关掉就退回「云端 + 规则脑」两层 |
| `llm.local.model` | qwen3:8b | 本地模型名，要先用 `ollama pull` 拉下来 |
| `llm.local.strategy` | spill | `spill` = 云端额度见底才溢出到本地；`split` = 简单任务直接给本地，额度全留给复杂任务 |
| `llm.local.spill_ratio` | 0.4 | spill 模式下，云端剩余额度低于这个比例就把简单任务交给本地 |
| `llm.local.keep_alive` | -1 | -1 = 模型常驻显存。改成 0 会空闲五分钟卸载，下条弹幕要等加载 |
| `llm.local.think` | false | 关掉 Qwen3 的思考模式。开着会先吐一大段推理，正文经常被 token 上限挤掉 |
| `llm.local.repeat_penalty` | 1.15 | 重复惩罚，小模型容易照着上一句复读 |
| `persona.name / role / style` | 小饭 / … | 人设，直接拼进 system prompt |
| `persona.catchphrases` | 若干 | 口头禅，少量随机使用 |
| `danmaku.room_url` | 空 | 直播间网页地址 |
| `danmaku.sources.dom.poll_ms` | 400 | 兜底轮询间隔。正常走直推通道，这条只在绑定失效时兜底 |
| `danmaku.sources.ocr.interval_sec` | 1.6 | OCR 兜底间隔 |
| `danmaku.dedupe_window_sec` | 25 | 多源去重窗口（普通消息） |
| `danmaku.enter_dedupe_sec` | 3 | 进场专用去重窗口。设太大会把「同一个人二次进场」吞掉，欢迎就没了 |
| `stage.welcome.enabled` | true | 进场欢迎特效总开关 |
| `stage.welcome.sound` | true | 进场「叮」一声（页面本地合成，不联网） |
| `stage.welcome.duration_ms` | 2800 | 单条欢迎条停留时长 |
| `stage.welcome.max_visible` | 5 | 同时最多显示几条，超了挤掉最旧的（最新的一定在） |
| `tts.engine` | edge | edge / sapi / none |
| `tts.pitch` | +18Hz | 音高。Xiaoyi 默认约 291Hz，+18Hz 约 333Hz（参考视频那个人声的实测值） |
| `tts.polish` | true | 用 ffmpeg 做一层轻润色（去闷/提亮/压动态），没装 ffmpeg 自动跳过 |
| `tts.voice` | zh-CN-XiaoxiaoNeural | 音色，`edge-tts --list-voices` 可查 |
| `tts.max_chars` | 85 | 单句最长字数 |
| `audio.duck.enabled` | true | 主播说话时自动压低外部音乐（Apple Music 等） |
| `audio.duck.level` | 0.18 | 压到原音量的 18%；调大=压得轻，0 是全静音 |
| `audio.duck.targets` | AMPLibraryAgent / AppleMusic / … | 要压的播放器进程名 |
| `audio.duck.exclude` | chrome / msedge / MediaSDK_Server | 一律不压，人声和采集都在里面 |
| `audio.duck.fade_out_sec` / `fade_in_sec` | 0.25 / 0.60 | 压低 / 淡回的渐变时长 |
| `audio.duck.max_duck_sec` | 30 | 单次最长压制，超时强制恢复 |
| `stage.mode` | fullscreen | fullscreen=全屏无边框；window=普通窗口（Chrome 会带标题栏，需在直播伴侣裁掉顶部约 40px） |
| `stage.window_width` | 1280 | window 模式下客户区宽 |
| `stage.window_height` | 720 | window 模式下客户区高 |
| `stage.window_x` / `window_y` | 320 / 60 | window 模式的屏幕位置 |
| `stage.title` | 小饭的打工日常 · 通宵改需求中 | 画面上方的直播间标题 |
| `stage.bgm.enabled` | true | 背景音乐总开关 |
| `stage.bgm.volume` | 0.30 | BGM 音量，人声会被 duck 到它的 26% |
| `stage.bgm.coding` | calm.mp3 | 写代码时的曲子（在 `static/bgm/` 下） |
| `stage.bgm.slack` | playful.mp3 | 摸鱼时的曲子 |
| `stage.bgm.boss` | 空 | 老板来了放什么；留空=静音 |
| `stage.nowplaying.enabled` | true | 画面右下显示「正在播放」的歌名（读播放器播放条，Apple Music 暂停/没开就自动隐藏） |
| `stage.nowplaying.lyrics` | true | 歌词条上方逐行同步显示歌词（lrclib.net 免费接口，无需 key；查不到歌词的歌只显示歌名） |
| `stage.nowplaying.window_titles` | Apple Music / … | 要读哪个播放器窗口，换播放器往里加窗口标题 |
| `director.coding.min_sec/max_sec` | 240 / 900 | 写代码时长区间 |
| `director.slack.min_sec/max_sec` | 100 / 300 | 摸鱼时长区间 |
| `director.boss_key_hold_sec` | 12 | 老板键保持时长 |
| `behavior.idle_talk_sec` | 32 | 主动搭话间隔（32 秒上下正好够话痨；没人时也随机自言自语，话题池见 `app/llm/prompt.py` 的 IDLE_TOPICS，可用 `behavior.idle_topics` 覆盖） |
| `behavior.max_replies_per_min` | 8 | 每分钟最多说几句 |
| `behavior.big_gift_above` | 50 | 多少抖币算大礼物（走全屏大字，不再叠横幅） |
| `companion.exe_path` | 已填 | 直播伴侣启动器路径 |
| `companion.scene_name` | 场景一 | 开播前切到哪个场景，留空不动 |
| `companion.auto_start_live` | true | 是否自动开播 |
| `companion.restart_cooldown_sec` | 120 | 掉播后重新开播的冷却 |

</details>

改完在控制台点「热重载配置」即可，正在直播不会断。**只有弹幕源相关配置需要重启进程**（要重开浏览器）。

---

## 7. 成本

| 项 | 费用 |
|---|---|
| 弹幕采集（Chrome CDP 注入） | 0 |
| 直播伴侣控制（截图 + OCR + 鼠标） | 0 |
| 语音合成（edge-tts / SAPI） | 0 |
| 画面渲染（本地 Chrome + SVG/Canvas） | 0 |
| OCR（RapidOCR onnxruntime 本地推理） | 0 |
| 植物大战僵尸小游戏（原创美术，非盗用素材） | 0 |
| **模型调用（云端）** | **只花你的 API Key** |
| 模型调用（本地 Ollama） | 0，吃自己的电和显卡 |

云端额度上限是 `llm.rpm: 9`，一小时最多 540 次。超出的简单互动由本地模型接管，不额外花钱；
需要动工具的复杂任务继续留给云端。

不开本地模型的话，每分钟第 10 次之后的互动全都会落到规则脑——同一批模板反复说，
观众两分钟就能听出来。开了本地模型，这部分才真正有内容，而且延迟比云端还低。

---

## 8. 实测结论（不是「应该能行」）

这台机器上已经跑通的：

- ✅ 舞台页 2560×1440 全屏无边框渲染，截图实测无标题栏、无黑边（`data/final_*.png`）
- ✅ 角色立绘自动去背，头发镂空处处理好、无白边，全屏下按 1.333 倍等比缩放不虚
- ✅ 游戏化 HUD 全部接真实数据：绩效行数、专注时长、摸鱼/被抓次数、手速 buff
- ✅ 语音换成 Xiaoyi +18Hz（实测基频 333Hz，和参考视频一致），带情绪语速与音色润色
- ✅ 逐字敲代码 + 语法高亮 + 偶尔打错再删，连续跑 6 小时不重样
- ✅ PvZ 小游戏 AI 全自动托管：种向日葵、补射手、坚果挡路、樱桃炸弹，被抓包自动重开
- ✅ 弹幕/礼物/关注/进场采集（用假直播间验证全链路 11 项）
- ✅ 进场欢迎特效：页面出消息 → Python 收到 **中位 7ms**；进总线 → 舞台画出 **1~4ms**；
  真实舞台截图见 `data/welcome_live.png`（三个观众三条，长昵称自动省略，emoji 昵称头像正常）
- ✅ 弹幕 → 智能体 → 字幕 + 语音（edge-tts 实测 1.6s 出一条语音）
- ✅ 场景切换、老板键、礼物横幅、全屏大字
- ✅ 直播伴侣窗口识别 + 「申请开播权限」识别 + 已在播不重复开播
- ✅ 21 项端到端自检全过，见 `data/selftest/*.png`

**三层模型路由（RTX 5070 12GB 实测）**：

| 指标 | 实测值 |
|---|---|
| 本地模型 | qwen3:8b，Q4 量化，5.2GB |
| 显存占用 | 7.5 / 12.2 GB（含 4096 上下文） |
| 生成速度 | **83~87 tok/s**（原生接口实测） |
| 端到端出话 | **0.14~0.19 s**（模型已预热） |
| 首次加载 | 38 s（`warmup` 在开播前就做掉，观众感知不到） |
| 云端单次 | 0.7 s（agnes-3.0-flash） |

降级链路真跑过，不是推演：
- 额度充足 → 走云端，本地一次不调
- 额度打满 → 自动走本地，云端零调用，照样接住弹幕
- 观众点单「写个贪吃蛇」→ 即使额度只剩两成，仍然保留给云端（要调 `write_project`）
- 云端 429 / 双层都挂 → 依次落到本地、规则脑，直播不断

本地回复质量抽检（连续 5 轮不复读）：
```
凌晨三点了，放的是《夜空中最亮的星》。
你这问题是问对人了，我确实挺累的，但有你在还挺精神的。
再撑一会儿，等老板把需求改完就去睡觉。
腰快断了，眼有点酸，但还得咬牙坚持啊。
再撑会儿就睡了，你先问别的吧。
```

**一个必须说清楚的外部阻塞**：你当前这个抖音号，直播伴侣右下方显示的是「申请开播权限」，
不是「开始直播」。程序已经能准确识别这个状态并明确报错（不会瞎点），
但**权限本身得你去直播伴侣里申请通过**——这不是代码能绕过去的，也不该绕。

---

## 9. 自检

```bash
# 单元：智能体工具循环
python tests/test_agent_loop.py

# 单元：三层模型路由（云端 → 本地 → 规则脑，含配额溢出与故障降级）
python tests/test_llm_router.py

# 单元：云端 RPM 闸门
python tests/test_llm_rpm.py

# 工具：本地模型体检（服务在不在 / 模型拉没拉 / 一句话多快 / 思考模式关没关）
python tools/check_local_llm.py

# 单元：导演状态机（自动轮换 / 冷却 / 老板键）
python tests/test_director.py

# 集成：假直播间 → DOM 采集全链路（含直推通道延迟测量）
python tests/test_dom_source.py

# 集成：进场欢迎全链路（昵称清洗 / 去重窗口 / 事件到舞台的延迟）
python tests/test_welcome.py

# 集成：用真实直播伴侣界面截图验证开播判定
python tests/test_companion_ocr.py

# 单元：系统音频闪避（假会话，不碰真实音量）
python tests/test_audio_duck.py

# 端到端：起服务 + 开浏览器 + 发弹幕 + 切场景 + 截图
python tests/selftest_e2e.py
```

`data/selftest/` 下会留下六张关键截图：写代码、说话中、小礼物、大礼物、摸鱼、老板键。

---

## 10. 目录

```
config.example.yaml         配置模板 —— 复制成 config.yaml 再改（仓库里没有带真实值的 config.yaml）
run.bat                     一键启动（自动提权）
guard.bat                   守护启动：进程挂了 8 秒后自动拉起来
app/
  __main__.py               入口：uvicorn + 运行时 + 舞台窗口
  runtime.py                总装 + 四条循环 + 工具执行
  config.py bus.py models.py director.py
  llm/                      client / agent / tools / prompt / fallback
  danmaku/                  base(路由) / dom_source / ocr_source / simulator
  tts/engine.py
  companion/                controller / win / ocr
  browser/cdp.py            自写 Chrome DevTools Protocol 客户端
  stage/                    server / launcher / static(
                              stage.html, stage.css, stage.js,
                              editor.js, pvz.js, dashboard.html,
                              assets/xiaofan.png  ← 角色立绘
                              bgm/calm.mp3, playful.mp3  ← 合成 BGM，可替换)
tests/                      5 套自检
tools/                      probe_companion / shot / preview_stage 等调试脚本
data/
  logs/app.log              运行日志
  evidence/                 开播证据截图
  audio/                    TTS 缓存
  selftest/                 自检截图
```

---

## 11. 常见问题

**Q：一定要装 OBS 吗？**
不用。舞台就是一个 Chrome 全屏窗口，直播伴侣直接「窗口捕获」它。OBS 只在你想加额外图层时才需要（也不花钱）。

**Q：声音怎么进直播？**
TTS 由舞台页播放 → 走系统扬声器 → 直播伴侣「系统声音」采集。记得把其它软件的提示音关掉。

**Q：能挂 24 小时吗？**
可以。`app.shutdown_at` 留空即不自动收播；所有循环都有异常兜底，日志按 4MB 滚动，TTS 缓存自动清理到 400 个。
建议每天看一眼 `data/logs/app.log`。

**Q：为什么在线人数是估算的？**
网页端拿不到官方在线人数接口（那需要开放平台权限）。这里用「最近 10 分钟出现过的不同用户数」估算，
控台上标的是「约」。点赞和礼物是准的。

**Q：直播伴侣更新后 OCR 认不出按钮了怎么办？**
不用改代码。程序找按钮用的是文字匹配 + 局部放大重试 + 逐字相似度兜底，
真找不到会在 `data/evidence/` 留下当时的截图，看着截图把 `companion.permission_keywords`
或 `START_TEXTS` 补一个词就行。

**Q：为什么欢迎条有时候还是会慢一两秒？**
因为那一条是 OCR 兜底源抓到的，不是网页源。控制台「弹幕源」里看 `dom` 的 `count`：

- `count` 在涨 → 走的是直推通道，毫秒级；
- `count` 一直是 0，只有 `ocr` 在涨 → 这个直播间**网页端根本没在推消息**
  （实测现象：网页里在线人数恒为 0、聊天列表只有一条系统公告，但直播伴侣的
  「互动消息」里礼物和进场都在刷）。这时候进场只能靠 OCR 截图识别，一轮 1~3 秒，
  这是这个源的物理下限，代码里已经把它压到最短了。

想让进场变成毫秒级，得先让网页源活过来：
```bash
python tools/probe_danmaku.py          # 看这个房间能不能抓到弹幕
python tools/probe_danmaku.py --dump   # 抓不到就导出页面结构，看是不是选择器对不上
```
如果是「一条都抓不到」而页面里在线人数也是 0，那多半是这个房间（无人直播 / 录播 / 特殊
房间类型）网页端不推互动数据，换 OCR 以外的办法都没有，只能接受 OCR 的秒级延迟。

**Q：怎么换角色形象？**
把新立绘存成 `app/stage/static/assets/xiaofan.png`（建议透明背景、竖向、约 1200×1200），
刷新舞台页即可。位置和大小在 `app/stage/static/stage.css` 的 `#charWrap` 里调：
改 `width/height/left/bottom` 四个值就够，宽高比保持和图片一致，否则会被 `object-fit` 留白。
背景不透明也行，但会带一块底色，建议先用抠图工具去背。

**Q：怎么知道 key 有没有生效？**
```bash
python tools/check_key.py        # 用 config.yaml 的配置测一次
python tools/check_key.py --all  # 这个 key 挨家试，看它到底属于哪家
```
它会区分「key 无效（401）」「余额不足（403）」「模型名写错（404）」。
换完 key 不用重启进程，控制台点一次「重载配置」就会重新信任模型。

**Q：key 挂了会怎样？**
自动切兜底规则脑继续播，不会中断。401/403/404 这类换 key 才能解决的错误只试一次就停，
不会每来一条弹幕都白打一次请求；之后每 90 秒试探一次，key 恢复了自己切回来。

**Q：为什么 OCR 那个兜底源一直显示「被遮挡」？**
舞台是全屏窗口，会把直播伴侣整个盖住。`PrintWindow` 对被遮挡的窗口不可靠——实测它会
把覆盖在上面的舞台画面画进来，OCR 于是把代码当弹幕（`color #333` 这种）。
现在截图前会先检测遮挡，被盖住就暂停，绝不放垃圾进总线。
**想让 OCR 兜底可用**：把直播伴侣拖到副屏（你有扩展屏），它不被舞台遮挡就能正常读。

**Q：怎么确认弹幕能不能抓到？**
```bash
python tools/probe_danmaku.py            # 开真窗口跑 75 秒，把抓到的事件逐条打印
python tools/probe_danmaku.py --dump     # 抓不到时导出页面结构，我按真实 class 改选择器
```
它只读 DOM，不登录、不点任何按钮。

**Q：会封号吗？**
本程序不碰抖音的任何私有接口、不模拟登录、不刷量，只做「看自己直播间 + 点自己直播伴侣的按钮」，
行为上等价于一个人坐在电脑前。但直播内容合规、账号权限这些仍然是你的责任。


---

## 许可

[MIT License](LICENSE) —— 拿去用、改、商用、闭源二次开发都可以，保留版权声明就行。

几点边界说清楚：

- `app/` 下的全部源码、`app/stage/static/` 里的素材（角色图、BGM）都在这个许可下。
  BGM 是用 numpy 现合成的，没有版权问题；角色图是本项目自带的，一起开源。
- `requirements.txt` 里的第三方库各自遵循自己的许可，不因为套了这个 MIT 就变成我的。
- **抖音、直播伴侣都是第三方服务，不在这份许可范围内。** 用这个程序开播，
  你仍然要遵守它们的用户协议 —— 直播内容合规、账号权限这些是你自己的责任。
- 用这个程序赚了钱、或者改成了别的样子，都不需要告诉我，也不用署名到显眼位置，
  保留 LICENSE 文件里的版权声明即可。
