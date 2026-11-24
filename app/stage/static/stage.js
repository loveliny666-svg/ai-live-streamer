/* ============================================================
   舞台控制：WebSocket 指令、字幕、弹幕、礼物、语音、场景切换
   ============================================================ */
(function () {
  const $ = (id) => document.getElementById(id);
  const body = document.body;
  const IS_SHOT = location.search.indexOf("shot=1") >= 0;

  /* ---------------- 等比缩放 ---------------- */
  function fit() {
    const stage = $("stage");
    const s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    stage.style.transform = `scale(${s})`;
    if (window.PvZ && window.PvZ.setResolution) {
      window.PvZ.setResolution(s * (window.devicePixelRatio || 1));
    }
  }
  window.addEventListener("resize", fit);

  /* ---------------- 尘埃 ---------------- */
  function makeDust() {
    const box = $("dust");
    if (!box) return;
    for (let i = 0; i < 26; i++) {
      const d = document.createElement("i");
      d.style.left = Math.random() * 100 + "%";
      d.style.top = 40 + Math.random() * 55 + "%";
      d.style.animationDuration = 9 + Math.random() * 12 + "s";
      d.style.animationDelay = -Math.random() * 12 + "s";
      d.style.opacity = 0.2 + Math.random() * 0.6;
      const sz = 2 + Math.random() * 3;
      d.style.width = sz + "px";
      d.style.height = sz + "px";
      box.appendChild(d);
    }
  }

  /* ---------------- 语音队列 ---------------- */
  const audioState = { queue: [], playing: false, current: null };

  function enqueueSpeech(item) {
    if (audioState.queue.length >= 4) audioState.queue.shift();
    audioState.queue.push(item);
    if (!audioState.playing) playNext();
  }

  function playNext() {
    const item = audioState.queue.shift();
    if (!item) {
      audioState.playing = false;
      body.classList.remove("speaking");
      voiceSignal("idle");
      return;
    }
    audioState.playing = true;
    body.classList.add("speaking");
    voiceSignal("start");
    bgm.ducked = true;
    bgmFade(bgm.cur, bgmWant(), 240);
    showSubtitle(item.text, item.holdMs);

    let finished = false;
    let timer = null;
    const done = () => {
      if (finished) return;
      finished = true;
      if (timer) clearTimeout(timer);
      body.classList.remove("speaking");
      bgm.ducked = false;
      bgmFade(bgm.cur, bgmWant(), 620);
      setTimeout(playNext, 180);
    };

    if (item.audio) {
      const audio = new Audio(item.audio);
      audio.volume = 1;
      audioState.current = audio;
      audio.onended = done;
      audio.onerror = () => {
        if (timer) clearTimeout(timer);
        done();
      };
      audio.play().catch(() => {
        // 播放被拦（没加 autoplay 参数时），退回按字数计时
        if (timer) clearTimeout(timer);
        timer = setTimeout(done, item.holdMs);
      });
      // 兜底：音频卡住不超过 40 秒
      timer = setTimeout(done, 40000);
    } else {
      timer = setTimeout(done, item.holdMs);
    }
  }

  /* ---------------- 背景音乐 ----------------
     没互动的时候靠它填满声音。两个 Audio 交替做交叉淡入，
     说话时压低（duck），场景切换时换曲。 */
  const BGM_DEFAULT = {
    enabled: true,
    volume: 0.3,
    tracks: {
      coding: "/static/bgm/calm.mp3",
      slack: "/static/bgm/playful.mp3",
      boss: "",
    },
  };
  const bgm = {
    ready: false, vol: 0.3, ducked: false,
    a: null, b: null, cur: null, nxt: null,
    tracks: {}, src: "",
  };

  function bgmWant() {
    return Math.max(0, Math.min(1, bgm.vol * (bgm.ducked ? 0.26 : 1)));
  }

  function bgmFade(el, to, ms) {
    if (!el) return;
    const from = el.volume;
    if (Math.abs(from - to) < 0.004) {
      el.volume = to;
      return;
    }
    const t0 = performance.now();
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / Math.max(1, ms));
      el.volume = Math.max(0, Math.min(1, from + (to - from) * k));
      if (k < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function bgmInit(cfg) {
    if (!cfg) return;
    bgm.vol = Math.max(0, Math.min(1, Number(cfg.volume) || 0.3));
    bgm.tracks = cfg.tracks || bgm.tracks || {};
    if (cfg.enabled === false) {
      if (bgm.cur) bgm.cur.pause();
      bgm.ready = false;
      return;
    }
    if (bgm.ready) {
      bgmPlay(body.dataset.scene || "coding", 500);
      return;
    }
    bgm.a = new Audio();
    bgm.b = new Audio();
    [bgm.a, bgm.b].forEach((el) => {
      el.loop = true;
      el.preload = "auto";
      el.volume = 0;
    });
    bgm.cur = bgm.a;
    bgm.nxt = bgm.b;
    bgm.ready = true;
    bgmPlay(body.dataset.scene || "coding", 0);
  }

  function bgmPlay(scene, fadeMs) {
    if (!bgm.ready) return;
    const ms = fadeMs == null ? 700 : fadeMs;
    const src = (bgm.tracks && bgm.tracks[scene]) || "";
    if (!src) {
      // 该场景不放音乐（比如老板来了）
      if (bgm.cur && !bgm.cur.paused) {
        bgmFade(bgm.cur, 0, Math.min(480, ms || 480));
        setTimeout(() => {
          if (bgm.cur && bgm.cur.volume < 0.012) bgm.cur.pause();
        }, 520);
      }
      bgm.src = "";
      return;
    }
    if (bgm.src === src && bgm.cur && !bgm.cur.paused) {
      bgmFade(bgm.cur, bgmWant(), Math.min(400, ms || 400));
      return;
    }
    bgm.src = src;
    const prev = bgm.cur;
    const next = bgm.nxt;
    if (!next) return;
    next.src = src;
    next.volume = 0;
    const started = () => {
      bgm.cur = next;
      bgm.nxt = prev;
      bgmFade(next, bgmWant(), ms);
      bgmFade(prev, 0, ms);
      setTimeout(() => {
        if (prev && prev !== bgm.cur) prev.pause();
      }, ms + 140);
    };
    const p = next.play();
    if (p && p.then) p.then(started).catch(() => {});
    else started();
  }

  window.__bgm = bgm;   // 便于自检脚本检查

  /* ---------------- 字幕 ---------------- */
  let subTimer = null;
  function showSubtitle(text, holdMs) {
    const box = $("subtitle");
    const el = $("subText");
    if (!box || !el) return;
    el.textContent = text;
    box.classList.add("show");
    const tl = $("taskline");
    if (tl) tl.classList.add("hide");
    if (subTimer) clearTimeout(subTimer);
    subTimer = setTimeout(() => {
      box.classList.remove("show");
      if (tl) tl.classList.remove("hide");
    }, Math.max(1400, holdMs || 4000));
  }

  /* ---------------- 弹幕 ---------------- */
  const DM_MAX = 4;
  function pushDanmaku(d) {
    const layer = $("dmLayer");
    if (!layer) return;
    const el = document.createElement("div");
    el.className = "dm " + (d.kind || "");
    let inner = `<span class="u">${escapeHtml(d.user || "观众")}</span>`;
    if (d.kind === "gift") {
      inner += `<span>送出 ${escapeHtml(d.gift || "礼物")}${d.count > 1 ? " ×" + d.count : ""}</span>`;
    } else if (d.kind === "follow") {
      inner += `<span>关注了主播</span>`;
    } else if (d.kind === "enter") {
      inner += `<span>来了</span>`;
    } else {
      inner += `<span>${escapeHtml(d.text || "")}</span>`;
    }
    el.innerHTML = inner;
    // column-reverse：插到最前面 = 视觉上贴在最下面（最新在下）
    layer.insertBefore(el, layer.firstChild);
    while (layer.children.length > DM_MAX) {
      const last = layer.lastElementChild;
      if (!last) break;
      last.classList.add("out");
      layer.removeChild(last);
    }
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ---------------- 角色气泡 ---------------- */
  let bubbleTimer = null;
  function showBubble(src) {
    const box = $("bubble");
    if (!box) return;
    $("bubUser").textContent = src.user || "观众";
    $("bubText").textContent = src.text || "";
    box.classList.add("show");
    if (bubbleTimer) clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => box.classList.remove("show"), 8000);
  }

  /* ---------------- 礼物：爱心 & 蹦一下 ---------------- */
  const SPARKS = ["💗", "✨", "💫", "🩵", "⭐"];
  function giftBurst() {
    body.classList.remove("react");
    void body.offsetWidth;
    body.classList.add("react");
    setTimeout(() => body.classList.remove("react"), 520);
    const wrap = $("charWrap");
    if (!wrap) return;
    for (let i = 0; i < 12; i++) {
      const s = document.createElement("span");
      s.className = "spark";
      s.textContent = SPARKS[i % SPARKS.length];
      // 撒在她头顶上方和两侧，避开深色衣服，才看得清
      s.style.left = 60 + Math.random() * 600 + "px";
      s.style.top = 40 + Math.random() * 300 + "px";
      s.style.setProperty("--dx", (Math.random() * 90 - 45).toFixed(0) + "px");
      s.style.animationDelay = (Math.random() * 0.4).toFixed(2) + "s";
      s.style.fontSize = (26 + Math.random() * 18).toFixed(0) + "px";
      wrap.appendChild(s);
      setTimeout(() => s.remove(), 2100);
    }
  }

  /* ---------------- 进场欢迎 ----------------
     直播间的门面：每个观众进来，画面左边立刻弹一条。
     原则是「消息到就上屏」—— 不排队、不等模型、不跟字幕抢队列，
     所以这里没有任何排队逻辑，只有一次同步的 DOM 插入。 */
  const WELCOME = { enabled: true, sound: true, duration: 2800, max: 5 };

  function welcomeConfig(cfg) {
    if (!cfg) return;
    if (cfg.enabled != null) WELCOME.enabled = !!cfg.enabled;
    if (cfg.sound != null) WELCOME.sound = !!cfg.sound;
    const d = Number(cfg.duration_ms);
    if (d > 0) WELCOME.duration = Math.max(900, d);
    const n = Number(cfg.max_visible);
    if (n > 0) WELCOME.max = Math.max(1, Math.min(12, n));
  }

  let wcActx = null;
  let wcChimeAt = 0;
  function welcomeChime() {
    if (!WELCOME.sound || audioState.playing) return;   // 主播正在说话，别抢人声
    const now = Date.now();
    if (now - wcChimeAt < 400) return;                  // 一群人一起进，只叮一下
    wcChimeAt = now;
    try {
      if (!wcActx) wcActx = new (window.AudioContext || window.webkitAudioContext)();
      if (wcActx.state === "suspended") wcActx.resume();
      const t0 = wcActx.currentTime;
      [[1046.5, 0], [1568, 0.045]].forEach(([f, d]) => {
        const o = wcActx.createOscillator();
        const g = wcActx.createGain();
        o.type = "sine";
        o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, t0 + d);
        g.gain.exponentialRampToValueAtTime(0.05, t0 + d + 0.012);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + d + 0.24);
        o.connect(g);
        g.connect(wcActx.destination);
        o.start(t0 + d);
        o.stop(t0 + d + 0.26);
      });
    } catch (e) { /* 音频上下文不可用就算了，画面照样有 */ }
  }

  function showWelcome(user, at) {
    if (!WELCOME.enabled) return;
    const layer = $("welcomeLayer");
    if (!layer) return;
    const name = String(user == null ? "" : user).trim() || "观众";

    // 自检用：从「页面里出现这条消息」到「真正画上去」过了多久
    if (at) window.__welcomeLag = Date.now() - at;
    window.__welcomeCount = (window.__welcomeCount || 0) + 1;

    const el = document.createElement("div");
    el.className = "wc";
    // 昵称首字做头像；用 Array.from 取，避免 emoji 昵称被切成半个字符
    const initial = (Array.from(name)[0] || "观").toUpperCase();
    el.innerHTML =
      '<span class="wc-ring"></span>' +
      `<span class="wc-ava">${escapeHtml(initial)}</span>` +
      `<span class="wc-txt">欢迎 <b>${escapeHtml(name)}</b> 来到直播间</span>` +
      '<span class="wc-star">✦</span>';
    for (let i = 0; i < 5; i++) {
      const s = document.createElement("span");
      s.className = "wc-spark";
      s.style.setProperty("--dx", (10 + Math.random() * 46).toFixed(0) + "px");
      s.style.setProperty("--dy", (Math.random() * 46 - 23).toFixed(0) + "px");
      s.style.setProperty("--d", (Math.random() * 0.12).toFixed(2) + "s");
      el.appendChild(s);
    }

    // column-reverse：插到最前面 = 贴在最下面；后来的在最底下，旧的往上顶
    layer.insertBefore(el, layer.firstChild);

    // 人太多就挤掉最旧的，别让欢迎条把画面糊住（最新的一定在）
    while (layer.children.length > WELCOME.max) {
      layer.removeChild(layer.lastElementChild);
    }

    setTimeout(() => {
      el.classList.add("out");
      setTimeout(() => el.remove(), 420);
    }, WELCOME.duration);

    welcomeChime();
  }

  /* ---------------- 右下事件条 ---------------- */
  function pushEvent(kind, user, main, buff) {
    const col = $("eventCol");
    if (!col) return;
    const el = document.createElement("div");
    el.className = "ev " + (kind || "");
    const ic = kind === "gift" ? "🎁" : kind === "follow" ? "✨" : "💬";
    el.innerHTML =
      `<span class="ic">${ic}</span><span class="u">${escapeHtml(user)}</span>` +
      `<span>${escapeHtml(main)}</span>` +
      (buff ? `<span class="b">${escapeHtml(buff)}</span>` : "");
    col.insertBefore(el, col.firstChild);
    while (col.children.length > 3) col.removeChild(col.lastElementChild);
    setTimeout(() => {
      el.classList.add("out");
      setTimeout(() => el.remove(), 380);
    }, 9000);
  }

  /* ---------------- 礼物横幅 / 特效 ---------------- */
  let giftTimer = null;
  function showGift(g) {
    const box = $("giftBanner");
    if (!box) return;
    $("giftIco").textContent = giftIcon(g.gift);
    $("giftName").textContent = (g.gift || "礼物") + (g.count > 1 ? " ×" + g.count : "");
    $("giftUser").textContent = `${g.user || "观众"} 送出了礼物`;
    box.classList.add("show");
    if (giftTimer) clearTimeout(giftTimer);
    giftTimer = setTimeout(() => box.classList.remove("show"), 3800);
  }

  function giftIcon(name) {
    const n = String(name || "");
    if (/火箭|嘉年华|城堡|跑车|飞机/.test(n)) return "🚀";
    if (/玫瑰|花/.test(n)) return "🌹";
    if (/啤酒|干杯/.test(n)) return "🍺";
    if (/心|爱心|喜欢/.test(n)) return "💗";
    if (/棒棒糖|糖/.test(n)) return "🍭";
    if (/蛋糕|甜甜圈|炸酱面|面/.test(n)) return "🍰";
    return "🎁";
  }

  let flashTimer = null;
  function showFlash(big, sub, kind) {
    const box = $("flash");
    if (!box) return;
    // 全屏大字优先，横幅先收起来，避免叠字
    const banner = $("giftBanner");
    if (banner) banner.classList.remove("show");
    if (giftTimer) clearTimeout(giftTimer);
    $("flashBig").textContent = big || "";
    $("flashSub").textContent = sub || "";
    box.className = "flash " + (kind || "");
    void box.offsetWidth;
    box.classList.add("on");
    if (flashTimer) clearTimeout(flashTimer);
    flashTimer = setTimeout(() => box.classList.remove("on"), 3400);
  }

  /* ---------------- 任务行 ---------------- */
  let reworkCount = 3;
  window.onCodeFileChange = (name) => {
    const el = $("taskText");
    if (el) el.innerHTML = `正在写 <b>${escapeHtml(name)}</b>`;
    const tab = $("tabNow");
    if (tab) tab.textContent = name;
    reworkCount += 1;
    const ex = $("taskExtra");
    if (ex && body.dataset.scene === "coding") ex.textContent = `已返工 ${reworkCount} 次`;
  };

  /* ---------------- 场景 ---------------- */
  const SCENE_TEXT = {
    coding: "写代码中",
    slack: "摸鱼中 · 打小游戏",
    boss: "老板来了！",
  };

  /* ---------------- 摸鱼小游戏轮换 ----------------
     后端每次进 slack 随机指定一个（scene 消息带 game 字段），
     舞台只负责切画布、启停，死亡由各游戏自己重开。 */
  const GAMES = {
    pvz:    { cv: "pvzCanvas",       name: "植物大战僵尸", api: () => window.PvZ },
    snake:  { cv: "snakeCanvas",     name: "贪吃蛇",       api: () => window.SnakeGame },
    tetris: { cv: "tetrisCanvas",    name: "俄罗斯方块",   api: () => window.TetrisGame },
    g2048:  { cv: "game2048Canvas",  name: "2048",         api: () => window.Game2048 },
    tank:   { cv: "tankCanvas",      name: "坦克大战",     api: () => window.TankGame },
    cs:     { cv: "csCanvas",        name: "CS 靶场",      api: () => window.CsGame },
  };
  let curGame = "pvz";

  function switchGame(id) {
    if (!GAMES[id]) id = "pvz";
    window.__lastGame = id;
    const old = GAMES[curGame];
    if (old && old.cv !== GAMES[id].cv) {
      const oldApi = old.api();
      if (oldApi && oldApi.pause) oldApi.pause();
      const oldCv = document.getElementById(old.cv);
      if (oldCv) oldCv.style.display = "none";
    }
    curGame = id;
    const cv = document.getElementById(GAMES[id].cv);
    if (cv) cv.style.display = "block";
    const api = GAMES[id].api();
    if (api) {
      if (api.reset) api.reset();
      if (api.start) api.start();
    }
    const moodEl = $("stMood");
    if (moodEl) moodEl.textContent = "🐟 摸鱼中 · " + GAMES[id].name;
    const t = $("sceneText");
    if (t) t.textContent = "摸鱼中 · 打" + GAMES[id].name;
  }

  function stopGame() {
    const api = GAMES[curGame] && GAMES[curGame].api();
    if (api && api.pause) api.pause();
  }

  window.MiniGames = {
    play: switchGame,
    stop: stopGame,
    name: () => (GAMES[curGame] ? GAMES[curGame].name : "小游戏"),
  };

  const MOOD = {
    coding: { text: "⌨ 专心工作中", cls: "" },
    slack: { text: "🐟 带薪摸鱼", cls: "rest" },
    boss: { text: "😰 假装很忙", cls: "panic" },
  };
  let lastScene = "coding";
  let slackSeconds = 0;

  function setScene(scene, reason, game, project) {
    if (!scene) return;
    // 编辑器内容：按后端指定的文件名找（游戏源码或工作文件），boss 回写需求
    if (project && window.typer && window.EditorFiles) {
      const f =
        window.EditorFiles.GAME_FILES[project] ||
        window.EditorFiles.WORK_FILES.find((w) => w.name === project);
      if (f) window.typer.loadFile({ name: project, lang: f.lang, lines: f.lines });
    } else if (scene === "boss" && window.typer && window.EditorFiles) {
      const bf = window.EditorFiles.WORK_FILES.find((w) => w.name === "boss_requirement.py");
      if (bf) window.typer.loadFile(bf);
    }
    if (scene !== lastScene) {
      const fl = $("cutFlash");
      if (fl) {
        fl.classList.remove("on");
        void fl.offsetWidth;
        fl.classList.add("on");
      }
      lastScene = scene;
    }
    body.dataset.scene = scene;
    bgmPlay(scene);
    const m = MOOD[scene];
    const moodEl = $("stMood");
    if (m && moodEl) {
      moodEl.textContent = m.text;
      moodEl.className = "chip mood" + (m.cls ? " " + m.cls : "");
    }
    const t = $("sceneText");
    if (t) t.textContent = SCENE_TEXT[scene] || scene;
    if (scene === "slack") slackSeconds = 0;
    if (scene === "coding") slackSeconds = 0;
    if (window.MiniGames) {
      if (scene === "coding" || scene === "boss") {
        window.MiniGames.stop();
      } else {
        // 每次摸鱼都开新局；后端随 scene 指定游戏，重连时 init 也会带当前游戏
        window.MiniGames.play(game || window.__lastGame || "pvz");
      }
    }
    const ex = $("taskExtra");
    if (ex) {
      if (scene === "slack") ex.textContent = "摸鱼中，键盘声是假的";
      else if (scene === "boss") ex.textContent = "假装很忙";
      else ex.textContent = `已返工 ${reworkCount} 次`;
    }
  }

  setInterval(() => {
    if (body.dataset.scene === "slack") {
      slackSeconds++;
      const mm = String(Math.floor(slackSeconds / 60)).padStart(2, "0");
      const ss = String(slackSeconds % 60).padStart(2, "0");
      const el = $("slackTimer");
      if (el) el.textContent = `${mm}:${ss}`;
    }
  }, 1000);

  /* ---------------- HUD ---------------- */
  function fmtUptime(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = String(Math.floor(sec / 3600)).padStart(2, "0");
    const m = String(Math.floor((sec % 3600) / 60)).padStart(2, "0");
    const s = String(sec % 60).padStart(2, "0");
    return `${h}:${m}:${s}`;
  }
  function fmtNum(n) {
    n = Math.max(0, Number(n) || 0);
    if (n >= 10000) return (n / 10000).toFixed(1) + "w";
    return String(n);
  }

  function setStats(st) {
    if (!st) return;
    if (st.focus_text) $("hudFocus").textContent = st.focus_text;
    else if (st.uptime != null) $("hudFocus").textContent = fmtUptime(st.uptime);
    if (st.online != null) $("hudOnline").textContent = st.online;
    if (st.likes != null) $("hudLikes").textContent = fmtNum(st.likes);
    if (st.gifts != null) $("hudGifts").textContent = fmtNum(st.gifts);

    if (st.slack != null) $("stSlack").textContent = st.slack;
    if (st.caught != null) $("stCaught").textContent = st.caught;
    if (st.lines != null) $("stLines").textContent = fmtNum(st.lines);
    if (st.speed != null) $("stSpeed").textContent = Number(st.speed).toFixed(1);

    // 手速 buff 直接影响敲代码的速度，看得见
    if (st.speed && window.typer) window.typer.speed = 26 * Number(st.speed);

    // 绩效条
    const lines = Number(st.lines || 0);
    const goal = Number(st.goal || 2400) || 1;
    const pct = Math.max(0, Math.min(100, Math.round((lines / goal) * 100)));
    const fill = $("perfFill");
    if (fill) fill.style.width = pct + "%";
    $("perfPct").textContent = pct + "%";
    $("perfNow").textContent = lines.toLocaleString("en-US");
    $("perfGoal").textContent = goal.toLocaleString("en-US");
    const card = document.querySelector(".perf");
    if (card) card.classList.toggle("over", pct >= 100);

    // 摸鱼倒计时
    const cd = Number(st.countdown || 0);
    const mm = String(Math.floor(cd / 60)).padStart(2, "0");
    const ss = String(cd % 60).padStart(2, "0");
    const nt = $("stNoticeText");
    if (nt) {
      if (cd <= 0) nt.textContent = st.typing ? "摸鱼须知 · 马上就能休息了" : "摸鱼中 · 老板随时可能来";
      else if (st.typing) nt.textContent = `摸鱼须知 · 再工作 ${mm}:${ss} 可以休息`;
      else nt.textContent = `摸鱼中 · 还剩 ${mm}:${ss}，老板随时可能来`;
    }
  }

  /* ---------------- 投票 ---------------- */
  function setPoll(p) {
    const box = $("poll");
    if (!box) return;
    if (!p || !p.options || !p.options.length) {
      box.classList.remove("show");
      return;
    }
    $("pollQ").textContent = p.question || "投票";
    const total = p.options.reduce((a, b) => a + (b.votes || 0), 0) || 1;
    const wrap = $("pollOpts");
    wrap.innerHTML = "";
    p.options.forEach((o) => {
      const pct = Math.round(((o.votes || 0) / total) * 100);
      const row = document.createElement("div");
      row.className = "opt";
      row.innerHTML = `<span>${escapeHtml(o.label)}</span><span class="bar"><i style="width:${pct}%"></i></span><span class="p">${pct}%</span>`;
      wrap.appendChild(row);
    });
    box.classList.add("show");
  }

  /* ---------------- WebSocket ---------------- */
  let ws = null;
  let retry = 0;
  let hbTimer = null;
  let lastMsg = Date.now();

  /* 把「真的开始出声 / 队列播完了」告诉后端，后端据此压低、恢复外部音乐。
     断线时静默跳过，后端还有按字数的兜底计时，不会把音乐一直压着。 */
  function voiceSignal(state) {
    if (ws && ws.readyState === 1) {
      try { ws.send(JSON.stringify({ type: "voice", state: state, t: Date.now() })); }
      catch (e) { /* WebSocket 刚好断开，忽略 */ }
    }
  }

  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}/ws/stage`;
  }

  function connect() {
    try {
      ws = new WebSocket(wsUrl());
    } catch (e) {
      setTimeout(connect, 1500);
      return;
    }
    ws.onopen = () => {
      retry = 0;
      $("offlineTip").classList.remove("show");
      ws.send(JSON.stringify({ type: "hello", ua: navigator.userAgent }));
      if (hbTimer) clearInterval(hbTimer);
      hbTimer = setInterval(() => {
        if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "hb", t: Date.now() }));
      }, 2000);
      setTimeout(() => $("bootCover").classList.add("hide"), 500);
    };
    ws.onmessage = (ev) => {
      lastMsg = Date.now();
      let m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      handle(m);
    };
    ws.onclose = () => {
      if (hbTimer) clearInterval(hbTimer);
      $("offlineTip").classList.add("show");
      retry++;
      setTimeout(connect, Math.min(8000, 700 * retry));
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }

  /* 右下角「正在放的歌」+ 逐行歌词：playing=false 或读不到就隐藏，换歌时闪一下边框 */
  function setNowPlaying(m) {
    const box = $("npBox"), ly = $("npLyric");
    if (!m || !m.playing || !m.title) { box.classList.remove("show"); ly.classList.remove("show"); return; }
    const key = (m.title || "") + "|" + (m.artist || "");
    if (window.__npKey !== key) {
      window.__npKey = key;
      window.__npLyric = "";
      $("npTitle").textContent = m.title;
      $("npArtist").textContent = m.artist || "";
      $("npLyricCur").textContent = "";
      $("npLyricNext").textContent = "";
      box.classList.remove("show", "np-flash");
      void box.offsetWidth;   // 强制 reflow，重新触发进场动画
      box.classList.add("np-flash");
    }
    box.classList.add("show");
    const cur = m.lyric || "", nxt = m.lyric_next || "";
    if (cur || nxt) {
      const lkey = cur + "|" + nxt;
      if (window.__npLyric !== lkey) {
        window.__npLyric = lkey;
        $("npLyricCur").textContent = cur;
        $("npLyricNext").textContent = nxt;
      }
      ly.classList.add("show");
    } else {
      ly.classList.remove("show");
    }
  }

  function handle(m) {
    switch (m.type) {      case "init":
        if (m.title) $("titlebar").innerHTML = m.title;
        if (m.persona_name) $("subWho").textContent = m.persona_name;
        bgmInit(m.bgm || BGM_DEFAULT);
        setScene(m.scene || "coding", "", m.game, m.project);
        setStats(m.stats);
        setNowPlaying(m.song || null);
        welcomeConfig(m.welcome);
        break;
      case "scene":
        setScene(m.scene, m.reason, m.game, m.project);
        break;
      case "welcome":
        showWelcome(m.user, m.at);
        break;
      case "welcome_cfg":
        welcomeConfig(m);
        break;
      case "say": {
        window.__lastSay = { text: m.text, audio: m.audio || null, at: Date.now() };
        if (m.reply_to && m.reply_to.text) showBubble(m.reply_to);
        const chars = (m.text || "").length;
        const hold = m.hold_ms || Math.min(9000, 1200 + chars * 190);
        enqueueSpeech({
          text: m.text,
          audio: m.audio ? `${m.audio}${m.audio.includes("?") ? "&" : "?"}t=${Date.now()}` : null,
          holdMs: hold,
        });
        break;
      }
      case "now_playing":
        setNowPlaying(m);
        break;
      case "dm":
        pushDanmaku(m);
        if (m.kind === "gift") {
          const cnt = m.count > 1 ? " ×" + m.count : "";
          pushEvent("gift", m.user, "送出 " + (m.gift || "礼物") + cnt, "手速 +0.1×");
          giftBurst();
        } else if (m.kind === "follow") {
          pushEvent("follow", m.user, "关注了主播", "");
        }
        break;
      case "gift":
        showGift(m);
        break;
      case "flash":
        showFlash(m.big, m.sub, m.kind);
        break;
      case "stats":
        setStats(m);
        break;
      case "poll":
        setPoll(m);
        break;
      case "reload":
        location.reload();
        break;
      default:
        break;
    }
  }

  // 每 8 秒上报一次"敲了多少字"，后台累计成今日绩效
  setInterval(() => {
    const n = window.__typed || 0;
    if (n > 0 && ws && ws.readyState === 1) {
      window.__typed = 0;
      ws.send(JSON.stringify({ type: "code", chars: n }));
    }
  }, 8000);

  // 后台若长时间没消息（>45s），主动重连一次
  setInterval(() => {
    if (ws && ws.readyState === 1 && Date.now() - lastMsg > 45000) {
      try { ws.close(); } catch (e) {}
    }
  }, 15000);

  /* ---------------- 启动 ---------------- */
  window.addEventListener("DOMContentLoaded", () => {
    fit();
    makeDust();
    connect();
    if (IS_SHOT) {
      const bc = $("bootCover");
      if (bc) bc.style.display = "none";
    }
    // 兜底：3.5 秒还没连上就先掀开遮罩，避免黑屏
    setTimeout(() => $("bootCover").classList.add("hide"), 3500);
    // 每秒自检缩放（窗口被拖动/切换分辨率时）
    setInterval(fit, 2000);
  });

  window.Stage = { setScene, showSubtitle, pushDanmaku, showGift, showFlash, setStats, giftBurst, showWelcome, welcomeConfig, handle };
})();
