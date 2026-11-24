/* ============================================================
   摸鱼小游戏 · 俄罗斯方块（评分式 AI 自动挂机）
   画布 1084×634：棋盘居中，两侧 NEXT 与 SCORE
   ============================================================ */
(function () {
  const W = 1084, H = 634;
  const CELL = 30;
  const COLS = 10, ROWS = 20;
  const BX = (W - COLS * CELL) / 2;    // 392
  const BY = (H - ROWS * CELL) / 2;    // 17

  const SHAPES = {
    I: { color: "#5fd3ff", cells: [[0, 1], [1, 1], [2, 1], [3, 1]] },
    O: { color: "#ffd76a", cells: [[0, 0], [1, 0], [0, 1], [1, 1]] },
    T: { color: "#c792ff", cells: [[0, 1], [1, 1], [2, 1], [1, 0]] },
    S: { color: "#7ce38b", cells: [[1, 0], [2, 0], [0, 1], [1, 1]] },
    Z: { color: "#ff7a7a", cells: [[0, 0], [1, 0], [1, 1], [2, 1]] },
    J: { color: "#7aa8ff", cells: [[0, 0], [0, 1], [1, 1], [2, 1]] },
    L: { color: "#ffb066", cells: [[2, 0], [0, 1], [1, 1], [2, 1]] },
  };
  const KEYS = Object.keys(SHAPES);

  function rotCells(cells) {
    return cells.map(([x, y]) => [1 - (y - 1), x]);  // 绕 (1,1) 旋转 90°
  }

  function normCells(cells) {
    const xs = cells.map((c) => c[0]), ys = cells.map((c) => c[1]);
    const mx = Math.min(...xs), my = Math.min(...ys);
    return cells.map(([x, y]) => [x - mx, y - my]);
  }

  function pieceRotations(key) {
    let cells = normCells(SHAPES[key].cells);
    const seen = [];
    for (let r = 0; r < 4; r++) {
      const k = JSON.stringify(cells);
      if (!seen.some((s) => s === k)) seen.push(cells);
      cells = normCells(rotCells(cells));
    }
    return seen;
  }

  class TetrisGame {
    constructor(cv) {
      this.cv = cv;
      this.c = cv.getContext("2d");
      this.running = false;
      this.raf = 0;
      this.last = 0;
      this.acc = 0;
      this.tick = 0.075;
      this.startedAt = performance.now();
      this.reset();
    }

    reset() {
      this.board = Array.from({ length: ROWS }, () => Array(COLS).fill(null));
      this.score = 0;
      this.lines = 0;
      this.dead = false;
      this.deadT = 0;
      this.cur = this.spawn();
      this.plan = [];        // AI 决定好的动作序列
      this.nextKey = KEYS[Math.floor(Math.random() * KEYS.length)];
      this.flashRows = [];
      this.startedAt = performance.now();
    }

    spawn() {
      const key = this.nextKey || KEYS[Math.floor(Math.random() * KEYS.length)];
      this.nextKey = KEYS[Math.floor(Math.random() * KEYS.length)];
      return { key, rot: 0, x: 3, y: -1 };
    }

    cellsOf(p) {
      const rots = pieceRotations(p.key);
      const cells = rots[p.rot % rots.length];
      return cells.map(([cx, cy]) => [p.x + cx, p.y + cy]);
    }

    valid(p) {
      return this.cellsOf(p).every(([x, y]) => {
        if (x < 0 || x >= COLS || y >= ROWS) return false;
        if (y < 0) return true;
        return this.board[y][x] === null;
      });
    }

    /* AI：枚举所有旋转×横移，模拟落底后打分 */
    bestPlan() {
      const p0 = { ...this.cur };
      const rots = pieceRotations(p0.key);
      let best = null, bestScore = -Infinity;
      for (let r = 0; r < rots.length; r++) {
        const rotIdx = r % rots.length;
        for (let x = -2; x < COLS; x++) {
          let p = { key: p0.key, rot: rotIdx, x, y: p0.y };
          if (!this.valid(p)) continue;
          while (this.valid({ ...p, y: p.y + 1 })) p = { ...p, y: p.y + 1 };
          const score = this.evaluate(p, rots[rotIdx]);
          if (score > bestScore) { bestScore = score; best = p; }
        }
      }
      if (!best) { this.dead = true; this.deadT = 0; return; }
      // 动作序列：先旋转到目标姿态，再水平平移到位，最后一路软降到底自动锁定
      const plan = [];
      const rots2 = pieceRotations(best.key);
      const curRot = this.cur.rot % rots2.length;
      const dr = (rots2.length + rotIdxOf(rots2, best) - curRot) % rots2.length;
      for (let i = 0; i < dr; i++) plan.push({ type: "rot" });
      const dx = best.x - this.cur.x;
      for (let i = 0; i < Math.abs(dx); i++) plan.push({ type: "move", dx: Math.sign(dx) });
      for (let i = 0; i < ROWS + 2; i++) plan.push({ type: "drop" });
      plan.push({ type: "lock" });
      this.plan = plan;
    }

    cellsOfPlan(p, rots) {
      const cells = rots[p.rot % rots.length];
      return cells.map(([cx, cy]) => [p.x + cx, p.y + cy]);
    }

    evaluate(p, rotCells) {
      const b = this.board.map((r) => r.slice());
      for (const [x, y] of rotCells.map(([cx, cy]) => [p.x + cx, p.y + cy])) {
        if (y >= 0 && y < ROWS) b[y][x] = p.key;
      }
      let cleared = 0;
      for (let y = ROWS - 1; y >= 0; y--) {
        if (b[y].every((v) => v !== null)) { b.splice(y, 1); b.unshift(Array(COLS).fill(null)); cleared++; y++; }
      }
      let holes = 0, agg = 0, bump = 0;
      const heights = [];
      for (let x = 0; x < COLS; x++) {
        let h = 0, seen = false;
        for (let y = 0; y < ROWS; y++) {
          if (b[y][x] !== null) seen = true;
          if (seen) h++;
          else if (b[y][x] === null) { /* 上方悬空不算 */ }
        }
        for (let y = 0; y < ROWS; y++) {
          if (b[y][x] === null) {
            for (let yy = y - 1; yy >= 0; yy--) {
              if (b[yy][x] !== null) { holes++; break; }
            }
          }
        }
        heights.push(h);
        agg += h;
      }
      for (let x = 1; x < COLS; x++) bump += Math.abs(heights[x] - heights[x - 1]);
      return cleared * 760 - agg * 9 - holes * 320 - bump * 26;
    }

    act(a) {
      if (a.type === "rot") {
        const p = { ...this.cur, rot: this.cur.rot + 1 };
        if (this.valid(p)) this.cur = p;
        else {  // 撞墙就试着往里挪一格再转
          for (const dx of [-1, 1, -2, 2]) {
            const q = { ...p, x: p.x + dx };
            if (this.valid(q)) { this.cur = q; return; }
          }
        }
      } else if (a.type === "move") {
        const p = { ...this.cur, x: this.cur.x + a.dx };
        if (this.valid(p)) this.cur = p;
      } else if (a.type === "drop") {
        const p = { ...this.cur, y: this.cur.y + 1 };
        if (this.valid(p)) this.cur = p;
        else this.lock();     // 落不动就锁定
      } else if (a.type === "lock") {
        this.lock();
      }
    }

    lock() {
      for (const [x, y] of this.cellsOf(this.cur)) {
        if (y < 0) { this.dead = true; this.deadT = 0; this.plan = []; return; }
        this.board[y][x] = this.cur.key;
      }
      let cleared = 0;
      const flashRows = [];
      for (let y = ROWS - 1; y >= 0; y--) {
        if (this.board[y].every((v) => v !== null)) {
          flashRows.push(y);
          this.board.splice(y, 1);
          this.board.unshift(Array(COLS).fill(null));
          cleared++;
          y++;
        }
      }
      if (cleared) {
        this.score += [0, 100, 300, 500, 800][cleared];
        this.lines += cleared;
        this.tick = Math.max(0.045, this.tick - 0.002);
      }
      this.flashRows = flashRows;
      this.flashT = performance.now();
      this.cur = this.spawn();
      this.plan = [];
      // 顶到头 = 死
      if (!this.valid(this.cur)) { this.dead = true; this.deadT = 0; }
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
            if (this.deadT > 1.5) this.reset();
          } else {
            if (!this.plan.length) this.bestPlan();
            if (this.plan.length) this.act(this.plan.shift());
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

    drawCell(c, x, y, color, pad = 2) {
      c.fillStyle = color;
      c.beginPath();
      c.roundRect(BX + x * CELL + pad, BY + y * CELL + pad, CELL - pad * 2, CELL - pad * 2, 6);
      c.fill();
      c.fillStyle = "rgba(255,255,255,0.22)";
      c.fillRect(BX + x * CELL + pad, BY + y * CELL + pad, CELL - pad * 2, 4);
    }

    draw() {
      const c = this.c;
      c.fillStyle = "#0e1424";
      c.fillRect(0, 0, W, H);
      // 棋盘背景与网格
      c.fillStyle = "rgba(16,24,44,0.9)";
      c.fillRect(BX - 6, BY - 6, COLS * CELL + 12, ROWS * CELL + 12);
      c.strokeStyle = "rgba(110,168,254,0.14)";
      for (let x = 1; x < COLS; x++) {
        c.beginPath(); c.moveTo(BX + x * CELL, BY); c.lineTo(BX + x * CELL, BY + ROWS * CELL); c.stroke();
      }
      for (let y = 1; y < ROWS; y++) {
        c.beginPath(); c.moveTo(BX, BY + y * CELL); c.lineTo(BX + COLS * CELL, BY + y * CELL); c.stroke();
      }
      // 已固定的块
      for (let y = 0; y < ROWS; y++) {
        for (let x = 0; x < COLS; x++) {
          if (this.board[y][x]) this.drawCell(c, x, y, SHAPES[this.board[y][x]].color);
        }
      }
      // 消行闪白（200ms 内可见）
      if (this.flashT && performance.now() - this.flashT < 200) {
        c.fillStyle = "rgba(255,255,255,0.75)";
        for (const y of this.flashRows) c.fillRect(BX, BY + y * CELL, COLS * CELL, CELL);
      }
      // 当前块（幽灵提示）
      if (!this.dead) {
        let ghost = { ...this.cur };
        while (this.valid({ ...ghost, y: ghost.y + 1 })) ghost = { ...ghost, y: ghost.y + 1 };
        c.fillStyle = "rgba(255,255,255,0.10)";
        for (const [x, y] of this.cellsOf(ghost)) {
          if (y >= 0) c.fillRect(BX + x * CELL + 3, BY + y * CELL + 3, CELL - 6, CELL - 6);
        }
        for (const [x, y] of this.cellsOf(this.cur)) {
          if (y >= 0) this.drawCell(c, x, y, SHAPES[this.cur.key].color);
        }
      }
      // 侧栏
      c.fillStyle = "rgba(9,14,26,0.78)";
      c.fillRect(0, 0, W, 56);
      c.fillStyle = "#8ad4ff";
      c.font = "700 22px 'Microsoft YaHei', sans-serif";
      c.textAlign = "left";
      c.fillText(`俄罗斯方块 · ${this.score} 分 · ${this.lines} 行`, 28, 37);
      // 顶栏右侧原来有一行「自动挂机中」，观众不想看这种自曝，已去掉
      // NEXT
      const nk = this.nextKey, nc = normCells(SHAPES[nk].cells);
      const nx = 70, ny = 120;
      c.fillStyle = "rgba(255,255,255,0.75)";
      c.font = "600 16px 'Microsoft YaHei', sans-serif";
      c.textAlign = "left";
      c.fillText("NEXT", nx, ny - 14);
      for (const [cx, cy] of nc) {
        c.fillStyle = SHAPES[nk].color;
        c.beginPath();
        c.roundRect(nx + cx * 26, ny + cy * 26, 22, 22, 5);
        c.fill();
      }
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
        c.fillStyle = `rgba(255,80,80,${0.28 * (1 - this.deadT / 1.5)})`;
        c.fillRect(0, 0, W, H);
        c.fillStyle = "rgba(255,255,255,0.92)";
        c.font = "800 46px 'Microsoft YaHei', sans-serif";
        c.textAlign = "center";
        c.fillText("堆到顶了，重开一局", W / 2, H / 2);
      }
    }
  }

  function rotIdxOf(rots, target) {
    const k = JSON.stringify(rots[target.rot % rots.length]);
    for (let i = 0; i < rots.length; i++) if (JSON.stringify(rots[i]) === k) return i;
    return 0;
  }

  window.addEventListener("DOMContentLoaded", () => {
    const cv = document.getElementById("tetrisCanvas");
    if (!cv) return;
    window.TetrisGame = new TetrisGame(cv);
  });

  window.TetrisGameClass = TetrisGame;
})();
