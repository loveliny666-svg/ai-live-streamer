/* ============================================================
   摸鱼小游戏 · 坦克大战（蓝方 AI 巡航索敌 vs 红方坦克）
   画布 1084×634：俯视砖墙地图，炮弹、爆炸粒子、击毁记分
   ============================================================ */
(function () {
  const W = 1084, H = 634;
  const CELL = 46;
  const COLS = 23, ROWS = 12;          // 1058×552 战场，顶部留 56 顶栏
  const OX = (W - COLS * CELL) / 2;    // 13
  const OY = 62;
  const FW = COLS * CELL, FH = ROWS * CELL;

  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

  class TankGame {
    constructor(cv) {
      this.cv = cv;
      this.c = cv.getContext("2d");
      this.running = false;
      this.raf = 0;
      this.last = 0;
      this.acc = 0;
      this.tick = 0.05;
      this.startedAt = performance.now();
      this.reset();
    }

    reset() {
      // 地图：0 空 1 砖 2 钢（不可摧毁）
      this.map = Array.from({ length: ROWS }, () => Array(COLS).fill(0));
      for (let i = 0; i < 26; i++) {
        const x = Math.floor(rand(1, COLS - 1));
        const y = Math.floor(rand(1, ROWS - 1));
        this.map[y][x] = Math.random() < 0.75 ? 1 : 2;
      }
      this.player = this.makeTank(2, ROWS - 2, "#58a6ff", true);
      this.enemies = [
        this.makeTank(Math.floor(COLS / 2), 0, "#ff6b6b", false),
        this.makeTank(1, 0, "#ff6b6b", false),
        this.makeTank(COLS - 2, 0, "#ff6b6b", false),
      ];
      this.bullets = [];
      this.booms = [];
      this.kills = 0;
      this.respawns = 0;
      this.startedAt = performance.now();
    }

    makeTank(x, y, color, isPlayer) {
      return {
        x, y, color, isPlayer,
        dir: isPlayer ? 3 : 1,          // 0上 1右 2下 3左
        cool: rand(0.4, 1.2),
        moveCd: 0,
        alive: true,
      };
    }

    free(x, y) {
      if (x < 0 || y < 0 || x >= COLS || y >= ROWS) return false;
      if (this.map[y][x] !== 0) return false;
      return true;
    }

    tankAt(x, y, except) {
      const all = [this.player, ...this.enemies];
      return all.find((t) => t !== except && t.alive && t.x === x && t.y === y);
    }

    moveTank(t, d) {
      const dx = [0, 1, 0, -1][d], dy = [-1, 0, 1, 0][d];
      const nx = t.x + dx, ny = t.y + dy;
      t.dir = d;
      if (this.free(nx, ny) && !this.tankAt(nx, ny, t)) { t.x = nx; t.y = ny; }
    }

    fire(t) {
      const dx = [0, 1, 0, -1][t.dir], dy = [-1, 0, 1, 0][t.dir];
      this.bullets.push({
        x: t.x * CELL + CELL / 2 + dx * CELL / 2,
        y: OY + t.y * CELL + CELL / 2 + dy * CELL / 2,
        dx, dy, from: t,
      });
    }

    enemyAI(e) {
      // 追玩家：优先缩小距离的方向，走不动就换方向；冷却好了就朝玩家开火
      const p = this.player;
      if (!p.alive) return;
      const dx = Math.sign(p.x - e.x), dy = Math.sign(p.y - e.y);
      const dirs = [];
      if (Math.abs(p.x - e.x) > Math.abs(p.y - e.y)) { if (dx) dirs.push(dx > 0 ? 1 : 3); if (dy) dirs.push(dy > 0 ? 2 : 0); }
      else { if (dy) dirs.push(dy > 0 ? 2 : 0); if (dx) dirs.push(dx > 0 ? 1 : 3); }
      dirs.push(Math.floor(Math.random() * 4));
      e.moveCd -= this.tick;
      if (e.moveCd <= 0) {
        const d0 = dirs[0];
        const nx = e.x + [0, 1, 0, -1][d0], ny = e.y + [-1, 0, 1, 0][d0];
        if (this.free(nx, ny) && !this.tankAt(nx, ny, e)) { this.moveTank(e, d0); }
        else { this.moveTank(e, pick(dirs)); }
        e.moveCd = rand(0.5, 1.1);
      }
      e.cool -= this.tick;
      if (e.cool <= 0) {
        // 大致朝向玩家才开火
        if ((dx && e.dir === (dx > 0 ? 1 : 3)) || (dy && e.dir === (dy > 0 ? 2 : 0))) {
          this.fire(e);
          e.cool = rand(1.2, 2.4);
        }
      }
    }

    playerAI(pl) {
      // 巡逻 + 索敌开火：朝最近的敌人走，同向即开炮
      const es = this.enemies.filter((e) => e.alive);
      if (!es.length) return;
      const t = es.sort(
        (a, b) => Math.abs(a.x - pl.x) + Math.abs(a.y - pl.y) - Math.abs(b.x - pl.x) - Math.abs(b.y - pl.y)
      )[0];
      const dx = Math.sign(t.x - pl.x), dy = Math.sign(t.y - pl.y);
      let aim;
      if (Math.abs(t.x - pl.x) > Math.abs(t.y - pl.y)) aim = dx > 0 ? 1 : 3;
      else aim = dy > 0 ? 2 : 0;
      pl.moveCd -= this.tick;
      if (pl.moveCd <= 0) {
        if (aim === pl.dir || aim === ((pl.dir + 2) % 4)) {
          this.moveTank(pl, aim);
        } else {
          this.moveTank(pl, Math.random() < 0.7 ? aim : Math.floor(Math.random() * 4));
        }
        if (pl.x === undefined) return;
        pl.moveCd = rand(0.35, 0.8);
      }
      pl.cool -= this.tick;
      if (pl.cool <= 0) {
        if ((dx && pl.dir === (dx > 0 ? 1 : 3)) || (dy && pl.dir === (dy > 0 ? 2 : 0)) || Math.random() < 0.25) {
          this.fire(pl);
          pl.cool = rand(0.6, 1.1);
        }
      }
    }

    step() {
      if (this.player.alive) this.playerAI(this.player);
      for (const e of this.enemies) if (e.alive) this.enemyAI(e);
      // 子弹
      for (const b of this.bullets) {
        b.px = b.x; b.py = b.y;
        b.x += b.dx * 9; b.y += b.dy * 9;
        const cx = Math.floor((b.x - OX) / CELL), cy = Math.floor((b.y - OY) / CELL);
        if (cx < 0 || cy < 0 || cx >= COLS || cy >= ROWS) { b.dead = true; continue; }
        const cell = this.map[cy][cx];
        if (cell === 1) { this.map[cy][cx] = 0; b.dead = true; this.boom(b.x, b.y, 0.5); continue; }
        if (cell === 2) { b.dead = true; this.boom(b.x, b.y, 0.35); continue; }
        // 打坦克
        const all = [this.player, ...this.enemies];
        for (const t of all) {
          if (!t.alive || t === b.from) continue;
          if (t.isPlayer === b.from.isPlayer) continue;   // 不打自己人
          const tx = OX + t.x * CELL, ty = OY + t.y * CELL;
          if (b.x > tx && b.x < tx + CELL && b.y > ty && b.y < ty + CELL) {
            b.dead = true;
            t.alive = false;
            this.boom(tx + CELL / 2, ty + CELL / 2, 1.4);
            if (!t.isPlayer) {
              this.kills++;
              const spawnX = pick([1, Math.floor(COLS / 2), COLS - 2]);
              const e2 = this.makeTank(spawnX, 0, "#ff6b6b", false);
              const idx = this.enemies.indexOf(t);
              this.enemies[idx] = e2;
            } else {
              // 玩家被击毁：1.2 秒后重开整局
              this.playerDeadT = 0;
            }
          }
        }
      }
      this.bullets = this.bullets.filter((b) => !b.dead);
      for (const bm of this.booms) bm.t -= this.tick;
      this.booms = this.booms.filter((b) => b.t > 0);
      if (this.player && !this.player.alive) {
        this.playerDeadT = (this.playerDeadT || 0) + this.tick;
        if (this.playerDeadT > 1.2) this.reset();
      }
    }

    boom(x, y, size) {
      for (let i = 0; i < 10 * size + 4; i++) {
        this.booms.push({
          x: x + rand(-14, 14) * size, y: y + rand(-14, 14) * size,
          vx: rand(-70, 70), vy: rand(-70, 70),
          t: rand(0.3, 0.7) * size, size,
        });
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
        while (this.acc >= this.tick) { this.acc -= this.tick; this.step(); }
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

    drawTank(c, t) {
      const x = OX + t.x * CELL, y = OY + t.y * CELL;
      c.fillStyle = t.alive ? t.color : "rgba(80,80,80,0.5)";
      c.beginPath();
      c.roundRect(x + 8, y + 8, CELL - 16, CELL - 16, 6);
      c.fill();
      // 炮塔
      const cx = x + CELL / 2, cy = y + CELL / 2;
      c.fillStyle = "rgba(255,255,255,0.28)";
      c.beginPath(); c.arc(cx, cy, 11, 0, 6.28); c.fill();
      c.strokeStyle = t.color; c.lineWidth = 5;
      const dx = [0, 1, 0, -1][t.dir], dy = [-1, 0, 1, 0][t.dir];
      c.beginPath(); c.moveTo(cx, cy); c.lineTo(cx + dx * 20, cy + dy * 20); c.stroke();
      c.lineWidth = 1;
    }

    draw() {
      const c = this.c;
      c.fillStyle = "#131a10";
      c.fillRect(0, 0, W, H);
      c.fillStyle = "rgba(24,32,20,0.95)";
      c.fillRect(OX, OY, FW, FH);
      // 地图
      for (let y = 0; y < ROWS; y++) for (let x = 0; x < COLS; x++) {
        const v = this.map[y][x];
        if (v === 1) {
          c.fillStyle = "#8a5a3a";
          c.fillRect(OX + x * CELL + 3, OY + y * CELL + 3, CELL - 6, CELL - 6);
          c.strokeStyle = "rgba(0,0,0,0.35)";
          c.strokeRect(OX + x * CELL + 3, OY + y * CELL + 3, CELL - 6, CELL - 6);
        } else if (v === 2) {
          c.fillStyle = "#9aa5b8";
          c.fillRect(OX + x * CELL + 5, OY + y * CELL + 5, CELL - 10, CELL - 10);
        }
      }
      // 坦克
      for (const e of this.enemies) if (e.alive) this.drawTank(c, e);
      if (this.player.alive) this.drawTank(c, this.player);
      // 子弹
      c.fillStyle = "#ffd76a";
      for (const b of this.bullets) {
        c.beginPath(); c.arc(b.x, b.y, 4, 0, 6.28); c.fill();
      }
      // 爆炸
      for (const bm of this.booms) {
        const a = Math.max(0, bm.t / (0.5 * bm.size));
        c.fillStyle = `rgba(255,${140 + Math.floor(60 * a)},60,${a})`;
        c.beginPath(); c.arc(bm.x, bm.y, 8 + (1 - a) * 16 * bm.size, 0, 6.28); c.fill();
      }
      // 顶栏
      c.fillStyle = "rgba(9,14,26,0.78)";
      c.fillRect(0, 0, W, 56);
      c.fillStyle = "#ffb066";
      c.font = "700 22px 'Microsoft YaHei', sans-serif";
      c.textAlign = "left";
      c.fillText(`坦克大战 · 击毁 ${this.kills} 辆`, 28, 37);
      // 顶栏右侧原来有一行「蓝方 AI 自动作战中」，观众不想看这种自曝，已去掉
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
    }
  }

  window.addEventListener("DOMContentLoaded", () => {
    const cv = document.getElementById("tankCanvas");
    if (!cv) return;
    window.TankGame = new TankGame(cv);
  });

  window.TankGameClass = TankGame;
})();
