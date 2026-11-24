/* ============================================================
   摸鱼小游戏 · CS 靶场特训（准星自动索敌、弹孔、命中统计）
   画布 1084×634：靶子随机出现 → 准星甩过去 → 开枪 → 计分
   ============================================================ */
(function () {
  const W = 1084, H = 634;
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

  class CsGame {
    constructor(cv) {
      this.cv = cv;
      this.c = cv.getContext("2d");
      this.running = false;
      this.raf = 0;
      this.last = 0;
      this.acc = 0;
      this.tick = 0.016;
      this.startedAt = performance.now();
      this.reset();
    }

    reset() {
      this.targets = [];       // {x,y,r,t,alive}
      this.holes = [];         // 弹孔 {x,y,t}
      this.flashes = [];       // 枪口焰 {x,y,t}
      this.hits = 0;
      this.shots = 0;
      this.headshots = 0;
      this.x = W / 2;          // 准星位置
      this.y = H / 2;
      this.spawnT = 0.6;
      this.msg = "";           // 击杀提示
      this.msgT = 0;
      this.startedAt = performance.now();
    }

    spawnTarget() {
      // 避开当前准星太近的位置，制造甩枪感
      let x, y, tries = 0;
      do {
        x = rand(140, W - 140);
        y = rand(140, H - 150);
        tries++;
      } while (Math.hypot(x - this.x, y - this.y) < 260 && tries < 20);
      const head = Math.random() < 0.3;   // 三成是小头靶
      this.targets.push({ x, y, r: head ? 26 : 44, t: rand(1.4, 2.3), head, alive: true, born: performance.now() });
    }

    shoot() {
      this.shots++;
      this.flashes.push({ x: this.x, y: this.y, t: 0.09 });
      this.holes.push({ x: this.x + rand(-3, 3), y: this.y + rand(-3, 3), t: 6 });
      if (this.holes.length > 60) this.holes.shift();
      let hit = null;
      for (const t of this.targets) {
        if (!t.alive) continue;
        if (Math.hypot(t.x - this.x, t.y - this.y) <= t.r) { hit = t; break; }
      }
        if (hit) {
        hit.alive = false;
        this.hits++;
        if (hit.head) {
          this.headshots++;
          this.msg = "HEADSHOT!";
        } else {
          this.msg = pick(["NICE SHOT", "命中!", "+1"]);
        }
        this.msgT = 0.9;
      }
    }

    step() {
      // 出靶
      this.spawnT -= this.tick;
      if (this.spawnT <= 0) {
        this.spawnTarget();
        this.spawnT = rand(0.5, 1.0);
      }
      for (const t of this.targets) {
        t.t -= this.tick;
        if (t.t <= 0) t.alive = false;
      }
      this.targets = this.targets.filter((t) => t.alive);
      // 准星：甩向最近的靶，带一点手抖
      const live = this.targets.filter((t) => t.alive);
      if (live.length) {
        const t = live.sort(
          (a, b) => Math.hypot(a.x - this.x, a.y - this.y) - Math.hypot(b.x - this.x, b.y - this.y)
        )[0];
        const k = 0.10;
        this.x += (t.x - this.x) * k + rand(-2.4, 2.4);
        this.y += (t.y - this.y) * k + rand(-2.4, 2.4);
        if (Math.hypot(t.x - this.x, t.y - this.y) < 14) {
          // 距离够近：扣扳机
          this.shoot();
          // 击中后甩向下一个
          const rest = live.filter((o) => o !== t);
          if (rest.length) {
            const n = rest[0];
            this.x += (n.x - this.x) * 0.3 + rand(-30, 30);
            this.y += (n.y - this.y) * 0.3 + rand(-30, 30);
          }
        }
      } else {
        // 没靶时缓缓回到屏幕中心待命
        this.x += (W / 2 - this.x) * 0.02;
        this.y += (H / 2 - this.y) * 0.02;
      }
      for (const f of this.flashes) f.t -= this.tick;
      this.flashes = this.flashes.filter((f) => f.t > 0);
      for (const h of this.holes) h.t -= this.tick;
      this.holes = this.holes.filter((h) => h.t > 0);
      if (this.msgT > 0) this.msgT -= this.tick;
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

    draw() {
      const c = this.c;
      const g = c.createLinearGradient(0, 0, 0, H);
      g.addColorStop(0, "#171c26");
      g.addColorStop(1, "#0e1118");
      c.fillStyle = g;
      c.fillRect(0, 0, W, H);
      // 靶场地面透视线
      c.strokeStyle = "rgba(255,255,255,0.05)";
      for (let i = 1; i < 6; i++) {
        const y = H - 40 - i * 90;
        c.beginPath(); c.moveTo(0, y); c.lineTo(W, y); c.stroke();
      }
      // 靶子：外白中红心
      for (const t of this.targets) {
        const a = Math.min(1, (performance.now() - t.born) / 160);
        c.globalAlpha = a;
        c.fillStyle = "#e8e8e8";
        c.beginPath(); c.arc(t.x, t.y, t.r, 0, 6.28); c.fill();
        c.fillStyle = "#d64545";
        c.beginPath(); c.arc(t.x, t.y, t.r * 0.62, 0, 6.28); c.fill();
        c.fillStyle = "#e8e8e8";
        c.beginPath(); c.arc(t.x, t.y, t.r * 0.3, 0, 6.28); c.fill();
        c.fillStyle = "#d64545";
        c.beginPath(); c.arc(t.x, t.y, t.r * 0.12, 0, 6.28); c.fill();
        c.globalAlpha = 1;
      }
      // 弹孔
      for (const h of this.holes) {
        c.fillStyle = `rgba(10,10,10,${Math.min(0.9, h.t)})`;
        c.beginPath(); c.arc(h.x, h.y, 5, 0, 6.28); c.fill();
        c.strokeStyle = "rgba(255,255,255,0.14)";
        c.beginPath(); c.arc(h.x, h.y, 7, 0, 6.28); c.stroke();
      }
      // 枪口焰
      for (const f of this.flashes) {
        c.fillStyle = `rgba(255,220,140,${f.t / 0.09})`;
        c.beginPath(); c.arc(f.x, f.y, 16, 0, 6.28); c.fill();
      }
      // 准星
      c.strokeStyle = "#7CFC9A";
      c.lineWidth = 2;
      c.beginPath(); c.arc(this.x, this.y, 15, 0, 6.28); c.stroke();
      c.beginPath();
      c.moveTo(this.x - 22, this.y); c.lineTo(this.x - 6, this.y);
      c.moveTo(this.x + 6, this.y); c.lineTo(this.x + 22, this.y);
      c.moveTo(this.x, this.y - 22); c.lineTo(this.x, this.y - 6);
      c.moveTo(this.x, this.y + 6); c.lineTo(this.x, this.y + 22);
      c.stroke();
      c.lineWidth = 1;
      // 顶栏
      const acc = this.shots ? Math.round((this.hits / this.shots) * 100) : 0;
      c.fillStyle = "rgba(9,14,26,0.78)";
      c.fillRect(0, 0, W, 56);
      c.fillStyle = "#7CFC9A";
      c.font = "700 22px 'Microsoft YaHei', sans-serif";
      c.textAlign = "left";
      c.fillText(`靶场特训 · 命中 ${this.hits}/${this.shots} · ${acc}% · 爆头 ${this.headshots}`, 28, 37);
      // 顶栏右侧原来有一行「自动瞄准挂机中」，观众不想看这种自曝，已去掉
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
      if (this.msgT > 0) {
        c.fillStyle = `rgba(124,252,154,${Math.min(1, this.msgT / 0.9)})`;
        c.font = "800 44px 'Microsoft YaHei', sans-serif";
        c.textAlign = "center";
        c.fillText(this.msg, W / 2, H / 2 - 120);
      }
    }
  }

  window.addEventListener("DOMContentLoaded", () => {
    const cv = document.getElementById("csCanvas");
    if (!cv) return;
    window.CsGame = new CsGame(cv);
  });

  window.CsGameClass = CsGame;
})();
