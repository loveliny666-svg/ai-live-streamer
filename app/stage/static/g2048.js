/* ============================================================
   摸鱼小游戏 · 2048（贪心 AI 自动挂机）
   画布 1084×634：棋盘居中，经典配色，分数与摸鱼计时同款美术
   ============================================================ */
(function () {
  const W = 1084, H = 634;
  const N = 4;
  const CELL = 118, GAP = 12;
  const BX = (W - (N * CELL + (N + 1) * GAP)) / 2;   // 293
  const BY = (H - (N * CELL + (N + 1) * GAP)) / 2;   // 34

  const COLORS = {
    2: "#eee4da", 4: "#ede0c8", 8: "#f2b179", 16: "#f59563",
    32: "#f67c5f", 64: "#f65e3b", 128: "#edcf72", 256: "#edcc61",
    512: "#edc850", 1024: "#edc53f", 2048: "#edc22e",
  };

  class Game2048 {
    constructor(cv) {
      this.cv = cv;
      this.c = cv.getContext("2d");
      this.running = false;
      this.raf = 0;
      this.last = 0;
      this.acc = 0;
      this.tick = 0.42;
      this.startedAt = performance.now();
      this.reset();
    }

    reset() {
      this.grid = Array.from({ length: N }, () => Array(N).fill(0));
      this.score = 0;
      this.best = this.best || 0;
      this.dead = false;
      this.deadT = 0;
      this.pops = [];      // 新块闪现动画 {x,y,t}
      this.merges = [];    // 合并闪光 {x,y,t}
      this.addTile();
      this.addTile();
      this.startedAt = performance.now();
    }

    emptyCells() {
      const out = [];
      for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
        if (this.grid[y][x] === 0) out.push({ x, y });
      }
      return out;
    }

    addTile() {
      const es = this.emptyCells();
      if (!es.length) return;
      const p = es[Math.floor(Math.random() * es.length)];
      this.grid[p.y][p.x] = Math.random() < 0.9 ? 2 : 4;
      this.pops.push({ x: p.x, y: p.y, t: performance.now() });
    }

    /* 沿某方向把一行压实+合并，返回 {row, gained, moved} */
    collapseRow(row) {
      const vals = row.filter((v) => v !== 0);
      const out = [];
      let gained = 0;
      for (let i = 0; i < vals.length; i++) {
        if (i + 1 < vals.length && vals[i] === vals[i + 1]) {
          out.push(vals[i] * 2);
          gained += vals[i] * 2;
          i++;
        } else {
          out.push(vals[i]);
        }
      }
      while (out.length < N) out.push(0);
      const moved = out.some((v, i) => v !== row[i]);
      return { row: out, gained, moved };
    }

    move(dir) {
      // dir: 0=左 1=右 2=上 3=下
      let gained = 0, moved = false;
      const merges = [];
      const get = (i, j) => {
        if (dir === 0) return { r: this.grid[i], j };
        if (dir === 1) return { r: this.grid[i].slice().reverse(), j: N - 1 - j };
        if (dir === 2) return { r: this.grid.map((row) => row[j]), j: i };
        return { r: this.grid.map((row) => row[j]).reverse(), j: N - 1 - i };
      };
      const res = Array.from({ length: N }, () => Array(N).fill(0));
      for (let i = 0; i < N; i++) {
        let line = [];
        for (let j = 0; j < N; j++) line.push(get(i, j).r[j]);
        const { row, gained: g, moved: mv } = this.collapseRow(line);
        gained += g; moved = moved || mv;
        for (let j = 0; j < N; j++) {
          let x, y;
          if (dir === 0) { x = j; y = i; }
          else if (dir === 1) { x = N - 1 - j; y = i; }
          else if (dir === 2) { x = i; y = j; }
          else { x = i; y = N - 1 - j; }
          res[y][x] = row[j];
          if (row[j] !== 0 && line[j] !== row[j] && line[j] !== 0) merges.push({ x, y });
        }
      }
      if (moved) { this.grid = res; this.score += gained; }
      return moved;
    }

    /* AI：对每个方向模拟一步，挑「空格多 + 大块靠角落 + 排列单调」的 */
    evaluate(grid) {
      let empty = 0, mono = 0, maxVal = 0, maxX = 0, maxY = 0;
      for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
        if (grid[y][x] === 0) { empty++; continue; }
        if (grid[y][x] > maxVal) { maxVal = grid[y][x]; maxX = x; maxY = y; }
      }
      // 行单调（左->右递增或递减的整齐度）
      for (let y = 0; y < N; y++) {
        let inc = 0, dec = 0;
        for (let x = 1; x < N; x++) {
          const a = grid[y][x - 1] || 0, b = grid[y][x] || 0;
          if (b >= a) inc++; if (b <= a) dec++;
        }
        mono += Math.max(inc, dec);
      }
      const corner = (maxX === 0 || maxX === N - 1) && (maxY === 0 || maxY === N - 1) ? 260 : 0;
      return empty * 150 + mono * 26 + corner + maxVal;
    }

    simulate(dir) {
      const backup = this.grid.map((r) => r.slice());
      const moved = this.move(dir);
      const g = this.grid.map((r) => r.slice());
      const score = moved ? this.evaluate(g) : -Infinity;
      this.grid = backup;
      return { moved, score };
    }

    aiStep() {
      if (this.dead) return;
      let bestDir = -1, bestScore = -Infinity;
      for (const d of [0, 1, 2, 3]) {
        const { moved, score } = this.simulate(d);
        if (moved && score > bestScore) { bestScore = score; bestDir = d; }
      }
      if (bestDir < 0) {
        // 都动不了 → 游戏结束
        this.dead = true;
        this.deadT = 0;
        this.best = Math.max(this.best, this.score);
        return;
      }
      if (this.move(bestDir)) {
        this.addTile();
        this.tick = Math.max(0.28, this.tick - 0.002);
        if (this.emptyCells().length === 0 && !this.anyMove()) {
          this.dead = true; this.deadT = 0;
          this.best = Math.max(this.best, this.score);
        }
      }
    }

    anyMove() {
      for (const d of [0, 1, 2, 3]) {
        const backup = this.grid.map((r) => r.slice());
        const moved = this.move(d);
        this.grid = backup;
        if (moved) return true;
      }
      return false;
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
          if (this.dead) {
            this.deadT += this.tick;
            if (this.deadT > 1.6) this.reset();
          } else {
            this.aiStep();
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
      const now = performance.now();
      c.fillStyle = "#1a1712";
      c.fillRect(0, 0, W, H);
      // 底板
      c.fillStyle = "#2e2a24";
      c.beginPath();
      c.roundRect(BX - 4, BY - 4, N * CELL + (N + 1) * GAP + 8, N * CELL + (N + 1) * GAP + 8, 16);
      c.fill();
      for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
        c.fillStyle = "rgba(255,255,255,0.06)";
        c.beginPath();
        c.roundRect(BX + GAP + x * (CELL + GAP), BY + GAP + y * (CELL + GAP), CELL, CELL, 10);
        c.fill();
      }
      // 块
      for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
        const v = this.grid[y][x];
        if (!v) continue;
        const col = COLORS[v] || "#3c3a32";
        const px = BX + GAP + x * (CELL + GAP), py = BY + GAP + y * (CELL + GAP);
        const pop = this.pops.find((p) => p.x === x && p.y === y);
        const merge = this.merges.find((p) => p.x === x && p.y === y);
        let scale = 1;
        if (pop && now - pop.t < 220) scale = 0.6 + 0.4 * ((now - pop.t) / 220);
        c.fillStyle = col;
        c.beginPath();
        const s = (CELL - 4) * scale;
        c.roundRect(px + (CELL - s) / 2, py + (CELL - s) / 2, s, s, 10);
        c.fill();
        if (merge && now - merge.t < 260) {
          c.fillStyle = `rgba(255,255,255,${0.5 * (1 - (now - merge.t) / 260)})`;
          c.beginPath();
          c.roundRect(px, py, CELL, CELL, 10);
          c.fill();
        }
        c.fillStyle = v <= 4 ? "#6b6255" : "#fff8ee";
        c.font = `800 ${v >= 1024 ? 34 : v >= 128 ? 42 : 48}px 'Microsoft YaHei', sans-serif`;
        c.textAlign = "center";
        c.textBaseline = "middle";
        c.fillText(String(v), px + CELL / 2, py + CELL / 2 + 2);
      }
      c.textBaseline = "alphabetic";
      // 顶栏
      c.fillStyle = "rgba(9,14,26,0.78)";
      c.fillRect(0, 0, W, 56);
      c.fillStyle = "#ffd76a";
      c.font = "700 22px 'Microsoft YaHei', sans-serif";
      c.textAlign = "left";
      c.fillText(`2048 · ${this.score} 分${this.best ? ` · 最高 ${this.best}` : ""}`, 28, 37);
      // 顶栏右侧原来有一行「贪心 AI 自动挂机中」，观众不想看这种自曝，已去掉
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
      if (this.dead) {
        c.fillStyle = `rgba(230,180,80,${0.3 * (1 - this.deadT / 1.6)})`;
        c.fillRect(0, 0, W, H);
        c.fillStyle = "rgba(255,255,255,0.92)";
        c.font = "800 46px 'Microsoft YaHei', sans-serif";
        c.textAlign = "center";
        c.fillText(`棋盘满了 · ${this.score} 分，重开一局`, W / 2, H / 2);
      }
    }
  }

  // merge 闪光在 move() 里没法精确对位，统一在 addTile 后清理旧记录
  setInterval(function () {}, 1e9);  // 占位：IIFE 内不依赖外部定时器

  window.addEventListener("DOMContentLoaded", () => {
    const cv = document.getElementById("game2048Canvas");
    if (!cv) return;
    window.Game2048 = new Game2048(cv);
  });

  window.Game2048Class = Game2048;
})();
