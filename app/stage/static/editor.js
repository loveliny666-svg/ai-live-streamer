/* ============================================================
   代码编辑器：真实逐字敲代码 + 语法高亮 + 偶尔打错再删除
   ============================================================ */
(function () {
  const WORK_FILES = [
    {
      name: "boss_requirement.py",
      lang: "py",
      lines: [
        "# boss_requirement.py",
        "# 需求：首页改得「高级感一点」",
        "# 提需求时间：昨天 23:47",
        "# 已变更次数：7",
        "",
        "from __future__ import annotations",
        "",
        "import asyncio",
        "from dataclasses import dataclass, field",
        "",
        "",
        "@dataclass",
        "class Requirement:",
        '    """老板的一句话需求，无法被形式化。"""',
        "",
        "    title: str",
        '    detail: str = "你看着办"',
        '    deadline: str = "昨天"',
        "    revisions: int = 0",
        "",
        "    @property",
        "    def is_possible(self) -> bool:",
        "        return False",
        "",
        "    def bump(self) -> None:",
        "        self.revisions += 1",
        '        print(f"第 {self.revisions} 次改需求")',
        "",
        "",
        "class Boss:",
        '    """核心业务逻辑：永远不满意。"""',
        "",
        "    def __init__(self) -> None:",
        '        self.mood = "还行"',
        "        self.coffee = 3",
        "",
        "    async def review(self, code: str) -> str:",
        "        await asyncio.sleep(0.12)",
        '        if "高级感" not in code:',
        '            return "再加点高级感"',
        '        if self.coffee <= 0:',
        '            return "明天上线"',
        '        return "还是第一版好"',
        "",
        "    async def standup(self) -> None:",
        "        await asyncio.sleep(0.05)",
        '        print("这个很简单，怎么实现我不管")',
        "",
        "",
        "async def main() -> None:",
        '    req = Requirement(title="首页高级感")',
        "    boss = Boss()",
        "",
        "    while req.is_possible is False:",
        "        req.bump()",
        '        result = await boss.review("button { color: #333 }")',
        "        print(result)",
        "        if req.revisions >= 8:",
        '            print("先摸五分钟鱼，就五分钟")',
        "            await asyncio.sleep(300)",
        "",
        "",
        'if __name__ == "__main__":',
        "    asyncio.run(main())",
      ],
    },
    {
      name: "fix_bug_final_final.js",
      lang: "js",
      lines: [
        "// fix_bug_final_final.js",
        "// 上一个版本叫 fix_bug_final.js",
        "// 上上个版本叫 fix_bug.js",
        "",
        "const cache = new Map();",
        "",
        "export function render(items) {",
        "  if (!Array.isArray(items)) {",
        '    throw new TypeError("items 必须是数组");',
        "  }",
        "",
        "  const visible = items.filter((it) => {",
        "    if (it == null) return false;",
        "    if (it.deleted) return false;",
        "    return it.status !== 'draft';",
        "  });",
        "",
        "  return visible.map((it, i) => ({",
        "    key: `${it.id ?? i}`,",
        "    title: it.title || '未命名',",
        "    weight: Number(it.weight) || 0,",
        "  }));",
        "}",
        "",
        "// 这行不能删，删了就崩",
        "// 为什么？不知道，反正不能删",
        "export const MAGIC = 42;",
        "",
        "export async function load(id) {",
        "  if (cache.has(id)) return cache.get(id);",
        "  const res = await fetch(`/api/item/${id}`);",
        "  if (!res.ok) throw new Error(`HTTP ${res.status}`);",
        "  const data = await res.json();",
        "  cache.set(id, data);",
        "  return data;",
        "}",
        "",
        "// TODO: 这里以后要重构",
        "// TODO: 以后 = 永远",
        "export const sleep = (ms) =>",
        "  new Promise((r) => setTimeout(r, ms));",
      ],
    },
    {
      name: "README.md",
      lang: "md",
      lines: [
        "# 项目说明",
        "",
        "> 能跑就行，别动它。",
        "",
        "## 启动",
        "",
        "```bash",
        "npm install",
        "npm run dev",
        "```",
        "",
        "## 已知问题",
        "",
        "1. 偶发白屏，刷新即可",
        "2. 偶发卡顿，等一会即可",
        "3. 偶发崩溃，重启即可",
        "4. 以上问题均无法复现",
        "",
        "## 环境要求",
        "",
        "- Node >= 18",
        "- 一杯咖啡",
        "- 一个不会催进度的老板（可选）",
        "",
        "## 更新日志",
        "",
        "- v1.0.0 能跑",
        "- v1.0.1 修了点东西",
        "- v1.0.2 又改回去了",
      ],
    },
  ];

  /* ---------------- 自己写的摸鱼小游戏源码（写完就自己玩） ---------------- */
  const GAME_FILES = {
    "snake_ai.js": { lang: "js", lines: [
      "// snake_ai.js — 贪吃蛇，写完自己先玩两把",
      "// 灵感来自观众点单",
      "",
      "const COLS = 36, ROWS = 21;",
      "const DIRS = [[1,0],[-1,0],[0,1],[0,-1]];",
      "",
      "export function nextStep(snake, food) {",
      "  const path = bfs(snake, food);",
      "  if (path.length) return path[0];",
      "  return anySafeDir(snake);   // 没路就苟着",
      "}",
      "",
      "function bfs(snake, food) {",
      "  const blocked = new Set(snake.slice(0, -1).map(key));",
      "  const prev = new Map();",
      "  const q = [snake[0]];",
      "  while (q.length) {",
      "    const cur = q.shift();",
      "    if (same(cur, food)) return backtrack(prev, food, snake[0]);",
      "    for (const [dx, dy] of DIRS) {",
      "      const n = { x: cur.x + dx, y: cur.y + dy };",
      "      if (blocked.has(key(n))) continue;",
      "      blocked.add(key(n));",
      '      prev.set(key(n), cur);',
      "      q.push(n);",
      "    }",
      "  }",
      "  return [];",
      "}",
    ]},
    "tetris_engine.js": { lang: "js", lines: [
      "// tetris_engine.js — 俄罗斯方块，AI 自己玩",
      "",
      "const SHAPES = { I, O, T, S, Z, J, L };",
      "",
      "// 评分：消行多、堆得矮、洞少、表面平",
      "export function bestPlacement(board, piece) {",
      "  let best = null, bestScore = -Infinity;",
      "  for (const rot of rotations(piece)) {",
      "    for (let x = -2; x < COLS; x++) {",
      "      const landed = hardDrop(board, piece, rot, x);",
      "      if (!landed) continue;",
      "      const score =",
      "        clearedLines(landed) * 760 -",
      "        aggregateHeight(landed) * 9 -",
      "        holes(landed) * 320 -",
      "        bumpiness(landed) * 26;",
      "      if (score > bestScore) { bestScore = score; best = landed; }",
      "    }",
      "  }",
      "  return best;",
      "}",
      "",
      "// 能跑就行，别动它",
    ]},
    "game_2048.js": { lang: "js", lines: [
      "// game_2048.js — 2048，写完先打个 1024 再提交",
      "",
      "export function chooseDir(grid) {",
      "  let best = -1, bestScore = -Infinity;",
      "  for (const dir of [0, 1, 2, 3]) {",
      "    const { moved, grid: next } = simulate(grid, dir);",
      "    if (!moved) continue;",
      "    const score =",
      "      emptyCells(next) * 150 +",
      "      monotonicity(next) * 26 +",
      "      cornerBonus(maxTile(next));",
      "    if (score > bestScore) { bestScore = score; best = dir; }",
      "  }",
      "  return best;",
      "}",
      "",
      "function cornerBonus(v) {",
      "  return v >= 512 ? 260 : 0;   // 大块蹲角落，稳",
      "}",
    ]},
    "lawn_defense.js": { lang: "js", lines: [
      "// lawn_defense.js — 植物大战僵尸玩法，致敬一下",
      "",
      "const PLANTS = {",
      '  sunflower: { cost: 50 },',
      '  peashooter: { cost: 100 },',
      '  wallnut: { cost: 50, hp: 900 },',
      "};",
      "",
      "export function tick(state) {",
      "  collectSun(state);",
      "  const zombie = nearestZombie(state);",
      "  if (!zombie) return plantSunflower(state);",
      "  const lane = zombie.row;",
      "  if (sunEnough(state, 100)) {",
      "    return place(state, 'peashooter', 0, lane);",
      "  }",
      "  return place(state, 'wallnut', 5, lane);   // 先挡一下",
      "}",
    ]},
    "tank_battle.js": { lang: "js", lines: [
      "// tank_battle.js — 坦克大战，90 后回忆杀",
      "",
      "const DIRS = [[0,-1],[1,0],[0,1],[-1,0]];",
      "",
      "export class Tank {",
      "  constructor(x, y, color) {",
      "    this.x = x; this.y = y; this.color = color;",
      '    this.dir = 0; this.cool = 0;',
      "  }",
      "",
      "  tick(game) {",
      "    const foe = game.nearest(this);",
      "    if (!foe) return this.patrol(game);",
      "    this.aimAt(foe);",
      "    if (this.cool <= 0) { game.fire(this); this.cool = 42; }",
      "    if (game.passable(this.ahead())) this.advance();",
      "    this.cool--;",
      "  }",
      "}",
    ]},
    "aim_trainer.js": { lang: "js", lines: [
      "// aim_trainer.js — CS 靶场，练枪顺便摸鱼",
      "",
      "export class AimTrainer {",
      "  constructor() {",
      "    this.hits = 0; this.shots = 0; this.hs = 0;",
      "  }",
      "",
      "  tick(state) {",
      "    const t = this.nearestTarget(state);",
      "    if (!t) return;",
      "    this.crosshair.moveTo(t.x, t.y, { shake: 2.4 });",
      "    if (this.crosshair.on(t, 14)) {",
      "      state.fire(this.crosshair.pos);",
      "      this.shots++;",
      "      if (this.hitHead(t)) { this.hs++; state.flash('HEADSHOT!'); }",
      "      else this.hits++;",
      "    }",
      "  }",
      "}",
    ]},
  };

  window.EditorFiles = { WORK_FILES, GAME_FILES };

  const KEYWORDS = new Set([
    "from", "import", "as", "class", "def", "return", "if", "elif", "else",
    "for", "while", "in", "not", "and", "or", "is", "None", "True", "False",
    "async", "await", "with", "try", "except", "finally", "raise", "pass",
    "lambda", "yield", "global", "nonlocal", "assert", "del", "break", "continue",
    "const", "let", "var", "function", "new", "export", "default", "throw",
    "typeof", "instanceof", "null", "undefined", "this", "of", "delete",
  ]);

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /** 极简高亮：只处理注释 / 字符串 / 关键字 / 数字 / 函数名 */
  function highlight(line) {
    let out = "";
    let i = 0;
    const n = line.length;
    while (i < n) {
      const c = line[i];
      // 注释
      if ((c === "#" && line[i - 1] !== '"') || (c === "/" && line[i + 1] === "/") || (c === "/" && line[i+1] === "*")) {
        out += `<span class="tok-cmt">${esc(line.slice(i))}</span>`;
        break;
      }
      // 字符串
      if (c === '"' || c === "'" || c === "`") {
        let j = i + 1;
        while (j < n && line[j] !== c) {
          if (line[j] === "\\") j++;
          j++;
        }
        const seg = line.slice(i, Math.min(j + 1, n));
        out += `<span class="tok-str">${esc(seg)}</span>`;
        i = j + 1;
        continue;
      }
      // 数字
      if (/[0-9]/.test(c) && !/[A-Za-z_]/.test(line[i - 1] || "")) {
        let j = i;
        while (j < n && /[0-9.]/.test(line[j])) j++;
        out += `<span class="tok-num">${esc(line.slice(i, j))}</span>`;
        i = j;
        continue;
      }
      // 标识符
      if (/[A-Za-z_]/.test(c)) {
        let j = i;
        while (j < n && /[A-Za-z0-9_]/.test(line[j])) j++;
        const w = line.slice(i, j);
        const next = line[j];
        if (KEYWORDS.has(w)) out += `<span class="tok-kw">${esc(w)}</span>`;
        else if (next === "(") out += `<span class="tok-fn">${esc(w)}</span>`;
        else if (/^[A-Z]/.test(w)) out += `<span class="tok-cls">${esc(w)}</span>`;
        else if (w === "self" || w === "this") out += `<span class="tok-self">${esc(w)}</span>`;
        else out += esc(w);
        i = j;
        continue;
      }
      out += esc(c);
      i++;
    }
    return out;
  }

  const VISIBLE_LINES = 15;
  const LINE_H = 29;

  class CodeTyper {
    constructor(scrollEl) {
      this.el = scrollEl;
      this.fileIdx = 0;
      this.manualFile = null;   // 后端指定要写的文件（写完回自动轮换）
      this.lineIdx = 0;
      this.charIdx = 0;
      this.lines = [];
      this.done = false;
      this.speed = 26;         // 每秒字符数
      this.acc = 0;
      this.last = 0;
      this.typoChance = 0.012;
      this.pauseUntil = 0;
      this.testRuns = 0;
      // 预填前几行，保证画面永远不是空白
      const seed = Math.min(9, this.curFile.lines.length - 1);
      for (let i = 0; i < seed; i++) this.lines.push(this.curFile.lines[i]);
      this.lineIdx = seed;
      this._render();
      this._timer = setInterval(() => this._tick(), 33);
    }

    /* 后端指定项目：切文件、重置打字进度，写完自动回工作文件轮换 */
    loadFile(file) {
      if (!file || this.curFile.name === file.name) return;
      this.manualFile = file;
      this.lineIdx = 0;
      this.charIdx = 0;
      this.lines = [];
      this.done = false;
      this.pauseUntil = performance.now() + 1600;
      this.el.innerHTML = "";
      const seed = Math.min(6, file.lines.length - 1);
      for (let i = 0; i < seed; i++) this.lines.push(file.lines[i]);
      this.lineIdx = seed;
      this._render();
      if (window.onCodeFileChange) window.onCodeFileChange(file.name);
    }

    get curFile() {
      if (this.manualFile) return this.manualFile;
      return WORK_FILES[this.fileIdx % WORK_FILES.length];
    }

    _switchFile() {
      if (this.manualFile) {
        // 点单的项目写完了：摸鱼时间去玩它，这里回正常工作轮换
        const doneName = this.manualFile.name;
        this.manualFile = null;
        this.fileIdx = (this.fileIdx + 1) % WORK_FILES.length;
        this.lineIdx = 0;
        this.charIdx = 0;
        this.lines = [];
        this.done = false;
        this.pauseUntil = performance.now() + 2200;
        this.el.innerHTML = "";
        if (window.onCodeFileChange) window.onCodeFileChange(this.curFile.name);
        this._runTest();
        if (window.onGameProjectDone) window.onGameProjectDone(doneName);
        return;
      }
      this.fileIdx = (this.fileIdx + 1) % WORK_FILES.length;
      this.lineIdx = 0;
      this.charIdx = 0;
      this.lines = [];
      this.done = false;
      this.pauseUntil = performance.now() + 2200;
      this.el.innerHTML = "";
      if (window.onCodeFileChange) window.onCodeFileChange(this.curFile.name);
      this._runTest();
    }

    _runTest() {
      this.testRuns++;
      const t = document.getElementById("term");
      if (!t) return;
      const passed = 38 + Math.floor(Math.random() * 20);
      t.innerHTML =
        '<div class="term-line"><span class="dim">$</span><span>pytest -q</span></div>' +
        `<div class="term-line"><span class="ok">✓</span><span class="ok">${passed} passed</span>` +
        `<span class="dim">in ${(0.6 + Math.random() * 1.6).toFixed(2)}s</span></div>` +
        '<div class="term-line"><span class="dim">$</span><span class="dim">_</span></div>';
    }

    _render() {
      const out = [];
      for (let i = 0; i < this.lines.length; i++) {
        const isCur = i === this.lines.length - 1 && !this.done;
        out.push(
          `<div class="code-line${isCur ? " cur" : ""}"><span class="ln">${i + 1}</span>` +
            `<span class="txt">${highlight(this.lines[i])}${isCur ? '<i class="caret"></i>' : ""}</span></div>`
        );
      }
      this.el.innerHTML = out.join("");
      const viewH = (this.el.parentElement && this.el.parentElement.clientHeight) || 460;
      const contentH = this.lines.length * LINE_H + 14;
      const off = Math.max(0, contentH - viewH);
      this.el.style.transform = `translateY(${-off}px)`;
    }

    _tick() {
      const now = performance.now();
      if (!this.last) this.last = now;
      const dt = Math.min(1.0, (now - this.last) / 1000);
      this.last = now;

      if (now < this.pauseUntil) return;

      if (this.done) {
        this.pauseUntil = now + 900;
        this._switchFile();
        return;
      }

      this.acc += dt * this.speed * (1 + Math.sin(now / 2600) * 0.25);
      while (this.acc >= 1) {
        this.acc -= 1;
        this._typeOne();
        if (this.done) break;
      }
      this._render();
    }

    _typeOne() {
      const src = this.curFile.lines;
      if (this.lineIdx >= src.length) {
        this.done = true;
        return;
      }
      const target = src[this.lineIdx];
      if (this.charIdx === 0) this.lines.push("");
      // 打错字：偶尔多敲一个字符，然后删掉
      if (Math.random() < this.typoChance && this.charIdx < target.length - 1 && target[this.charIdx] !== " ") {
        const wrong = "qwertyuiasdfghj".charAt(Math.floor(Math.random() * 16));
        this.lines[this.lines.length - 1] = target.slice(0, this.charIdx) + wrong;
        this._render();
        setTimeout(() => {
          this.lines[this.lines.length - 1] = target.slice(0, this.charIdx);
          this._render();
        }, 140);
        return;
      }
      this.charIdx++;
      window.__typed = (window.__typed || 0) + 1;
      this.lines[this.lines.length - 1] = target.slice(0, this.charIdx);
      if (this.charIdx >= target.length) {
        this.lineIdx++;
        this.charIdx = 0;
        // 空行/注释行快一点，代码行慢一点
        const next = src[this.lineIdx] || "";
        if (!next.trim()) this.pauseUntil = performance.now() + 90;
      }
    }
  }

  window.CodeTyper = CodeTyper;

  window.addEventListener("DOMContentLoaded", () => {
    const el = document.getElementById("codeScroll");
    if (el) window.typer = new CodeTyper(el);
  });
})();
