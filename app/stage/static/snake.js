/* ============================================================
   摸鱼小游戏 · 贪吃蛇（BFS 寻路 AI 自动挂机）
   画布 1084×634，与 PvZ 同一套摸鱼美术：底部摸鱼计时 + 别被老板发现
   ============================================================ */
(function () {
  const W = 1084, H = 634;
  const CELL = 30;
  const COLS = 36, ROWS = 21;           // 1080×630 棋盘
  const OX = (W - COLS * CELL) / 2;     // 2
  const OY = (H - ROWS * CELL) / 2;     // 2

  const DIRS = [
    { x: 1, y: 0 }, { x: -1, y: 0 }, { x: 0, y: 1 }, { x: 0, y: -1 },
  ];

  class SnakeGame {
    constructor(cv) {
      this.cv = cv;
      this.c = cv.getContext("2d");
      this.running = false;
      this.raf = 0;
      this.last = 0;
      this.acc = 0;
      this.tick = 0.095;             // AI 每步间隔（秒）
      this.startedAt = 0;
      this.reset();
    }

    reset() {
      this.snake = [{ x: 8, y: 10 }, { x: 7, y: 10 }, { x: 6, y: 10 }];
      this.dir = { x: 1, y: 0 };
      this.score = 0;
      this.best = this.best || 0;
      this.dead = false;
      this.deadT = 0;
      this.startedAt = performance.now();
      this.food = this.spawnFood();
    }

    occupied(x, y) {
      return this.snake.some((s) => s.x === x && s.y === y);
    }

    spawnFood() {
      for (let i = 0; i < 500; i++) {
        const x = Math.floor(Math.random() * COLS);
        const y = Math.floor(Math.random() * ROWS);
        if (!this.occupied(x, y)) return { x, y };
      }
      return { x: 0, y: 0 };
    }

    /* BFS：从头到目标的最短路径，返回下一步方向；无路返回 null */
    bfsStep(target, body) {
      const blocked = new Set(body.slice(0, -1).map((s) => s.y * COLS + s.x)); // 尾巴会挪走，不算障碍
      const start = this.snake[0];
      const key = (p) => p.y * COLS + p.x;
      const prev = new Map();
      const q = [start];
      blocked.add(key(start));
      while (q.length) {
        const cur = q.shift();
        if (cur.x === target.x && cur.y === target.y) break;
        for (const d of DIRS) {
          const nx = cur.x + d.x, ny = cur.y + d.y;
          if (nx < 0 || ny < 0 || nx >= COLS || ny >= ROWS) continue;
          const k = ny * COLS + nx;
          if (blocked.has(k)) continue;
          blocked.add(k);
          prev.set(k, { x: cur.x, y: cur.y, d });
          q.push({ x: nx, y: ny });
        }
      }
      const tk = target.y * COLS + target.x;
      if (!prev.has(tk)) return null;
      let cur = { x: target.x, y: target.y };
      while (!(prev.get(key(cur)) === undefined)) {
        const p = prev.get(key(cur));
        if (p.x === start.x && p.y === start.y) return p.d;
        cur = { x: p.x, y: p.y };
      }
      return null;
    }

    /* 找一个不会立刻死的方向；都不行就随便（大不了重开） */
    safeDir() {
      const head = this.snake[0];
      const body = this.snake;
      const ok = (d) => {
        const nx = head.x + d.x, ny = head.y + d.y;
        if (nx < 0 || ny < 0 || nx >= COLS || ny >= ROWS) return false;
        for (let i = 0; i < body.length - 1; i++) {
          if (body[i].x === nx && body[i].y === ny) return false;
        }
        return true;
      };
      // 优先离食物更近的
      const good = DIRS.filter(ok).sort((a, b) => {
        const da = Math.abs(head.x + a.x - this.food.x) + Math.abs(head.y + a.y - this.food.y);
        const db = Math.abs(head.x + b.x - this.food.x) + Math.abs(head.y + b.y - this.food.y);
        return da - db;
      });
      if (good.length) return good[0];
      return this.dir;
    }

    step() {
      if (this.dead) return;
      let d = this.bfsStep(this.food, this.snake);
      if (!d) d = this.safeDir();
      this.dir = d;
      const head = this.snake[0];
      const nx = head.x + d.x, ny = head.y + d.y;
      if (nx < 0 || ny < 0 || nx >= COLS || ny >= ROWS || this.occupied(nx, ny)) {
        this.dead = true;
        this.deadT = 0;
        this.best = Math.max(this.best, this.score);
        return;
      }
      this.snake.unshift({ x: nx, y: ny });
      if (nx === this.food.x && ny === this.food.y) {
        this.score += 10;
        this.food = this.spawnFood();
        this.tick = Math.max(0.055, this.tick - 0.0012);  // 越吃越快
      } else {
        this.snake.pop();
      }
    }

    start() {
      if (this.running) return;
      this.running = true;
      this.last = performance.now();
      const loop = (t) => {
        if (!this.running) return;
        const dt = Math.min(0.1, (t - this.last) / 1000);
        this.last = t;
        this.acc += dt;
        while (this.acc >= this.tick) {
          this.acc -= this.tick;
          this.step();
          if (this.dead) {
            this.deadT += this.tick;
            if (this.deadT > 1.2) this.reset();   // 死了一秒多自动开新局
          }
        }
        this.draw();
        this.raf = requestAnimationFrame(loop);
      };
      this.raf = requestAnimationFrame(loop);
    }

    pause() { this.running = false; cancelAnimationFrame(this.raf); }

    mmss() {
      const s = Math.floor((performance.now() - this.startedAt) / 1000);
      return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    }

    draw() {
      const c = this.c;
      c.fillStyle = "#0d1a12";
      c.fillRect(0, 0, W, H);
      // 棋盘格
      for (let y = 0; y < ROWS; y++) {
        for (let x = 0; x < COLS; x++) {
          c.fillStyle = (x + y) % 2 ? "rgba(46,74,58,0.35)" : "rgba(38,62,50,0.35)";
          c.fillRect(OX + x * CELL, OY + y * CELL, CELL, CELL);
        }
      }
      // 食物
      const f = this.food;
      const pulse = 1 + 0.12 * Math.sin(performance.now() / 180);
      c.fillStyle = "#ff5d5d";
      c.beginPath();
      c.arc(OX + f.x * CELL + CELL / 2, OY + f.y * CELL + CELL / 2, (CELL / 2 - 5) * pulse, 0, 6.28);
      c.fill();
      c.fillStyle = "rgba(255,255,255,0.5)";
      c.beginPath();
      c.arc(OX + f.x * CELL + CELL / 2 - 4, OY + f.y * CELL + CELL / 2 - 5, 3.5, 0, 6.28);
      c.fill();
      // 蛇
      this.snake.forEach((s, i) => {
        const t = i / this.snake.length;
        c.fillStyle = i === 0 ? "#b6ff7a" : `rgba(88, 200, 110, ${0.95 - t * 0.55})`;
        const pad = i === 0 ? 3 : 4;
        c.beginPath();
        c.roundRect(OX + s.x * CELL + pad, OY + s.y * CELL + pad, CELL - pad * 2, CELL - pad * 2, 7);
        c.fill();
        if (i === 0) {  // 眼睛
          c.fillStyle = "#12301c";
          c.beginPath();
          c.arc(OX + s.x * CELL + 10, OY + s.y * CELL + 12, 3, 0, 6.28);
          c.arc(OX + s.x * CELL + 20, OY + s.y * CELL + 12, 3, 0, 6.28);
          c.fill();
        }
      });
      // 记分
      c.fillStyle = "rgba(9,14,26,0.78)";
      c.fillRect(0, 0, W, 56);
      c.fillStyle = "#9ff0c4";
      c.font = "700 22px 'Microsoft YaHei', sans-serif";
      c.textAlign = "left";
      c.fillText(`贪吃蛇 · 分数 ${this.score}${this.best ? ` · 最高 ${this.best}` : ""}`, 28, 37);
      // 顶栏右侧原来有一行「BFS 自动挂机中」，观众不想看这种自曝，已去掉
      // 底部
      c.fillStyle = "rgba(9,14,26,0.78)";
      c.fillRect(0, H - 56, W, 56);
      c.fillStyle = "rgba(160,180,215,0.9)";
      c.font = "600 16px 'Microsoft YaHei', sans-serif";
      c.textAlign = "left";
      c.fillText(`摸鱼 ${this.mmss()}`, 32, H - 21);
      c.fillStyle = "rgba(160,180,215,0.55)";
      c.font = "600 13px 'Microsoft YaHei', sans-serif";
      c.fillText("别被老板发现", 140, H - 21);
      // 死亡闪屏
      if (this.dead) {
        c.fillStyle = `rgba(255,60,60,${0.25 * (1 - this.deadT / 1.2)})`;
        c.fillRect(0, 0, W, H);
        c.fillStyle = "rgba(255,255,255,0.92)";
        c.font = "800 46px 'Microsoft YaHei', sans-serif";
        c.textAlign = "center";
        c.fillText("撞死了，重开一局", W / 2, H / 2);
      }
    }
  }

  window.addEventListener("DOMContentLoaded", () => {
    const cv = document.getElementById("snakeCanvas");
    if (!cv) return;
    window.SnakeGame = new SnakeGame(cv);
  });

  window.SnakeGameClass = SnakeGame;
})();
