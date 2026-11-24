/* ============================================================
   摸鱼小游戏 · 植物大战僵尸玩法（原创美术，自动挂机 AI 托管）
   ============================================================ */
(function () {
  const W = 1084, H = 634;
  const G = { x: 118, y: 112, cw: 104, ch: 92, cols: 9, rows: 5 };
  const LAWN_W = G.cols * G.cw;
  const LAWN_H = G.rows * G.ch;

  const PLANTS = {
    sunflower: { cost: 50, hp: 90, cd: 6, name: "向日葵" },
    peashooter: { cost: 100, hp: 110, cd: 6, name: "豌豆射手" },
    wallnut: { cost: 50, hp: 900, cd: 14, name: "坚果" },
    snowpea: { cost: 175, hp: 110, cd: 8, name: "寒冰射手" },
    repeater: { cost: 200, hp: 120, cd: 10, name: "双发射手" },
    cherry: { cost: 150, hp: 1, cd: 20, name: "樱桃炸弹" },
  };

  const ZOMBIES = {
    basic: { hp: 100, speed: 21, dmg: 22 },
    cone: { hp: 210, speed: 19, dmg: 24 },
    bucket: { hp: 380, speed: 16, dmg: 26 },
  };

  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  class Sun {
    constructor(x, y, targetY, value = 25) {
      this.x = x; this.y = y; this.ty = targetY; this.v = value;
      this.life = 14; this.dead = false; this.phase = Math.random() * 6.28;
    }
    update(dt) {
      if (this.y < this.ty) this.y = Math.min(this.ty, this.y + 62 * dt);
      this.life -= dt;
      if (this.life <= 0) this.dead = true;
    }
    draw(c) {
      const t = performance.now() / 600 + this.phase;
      const r = 25 + Math.sin(t) * 1.6;
      c.save();
      c.translate(this.x, this.y);
      const g = c.createRadialGradient(0, 0, 4, 0, 0, r + 16);
      g.addColorStop(0, "rgba(255,236,150,0.85)");
      g.addColorStop(1, "rgba(255,200,60,0)");
      c.fillStyle = g;
      c.beginPath(); c.arc(0, 0, r + 16, 0, 6.2832); c.fill();
      c.rotate(t * 0.5);
      c.fillStyle = "#ffd94a";
      for (let i = 0; i < 8; i++) {
        c.rotate(Math.PI / 4);
        c.beginPath();
        c.moveTo(-4, -r - 8); c.lineTo(4, -r - 8); c.lineTo(0, -r + 2); c.closePath();
        c.fill();
      }
      c.rotate(-t * 0.5);
      c.beginPath();
      c.arc(0, 0, r * 0.72, 0, 6.2832);
      const g2 = c.createRadialGradient(-6, -8, 2, 0, 0, r);
      g2.addColorStop(0, "#fff6c4"); g2.addColorStop(1, "#ffc93c");
      c.fillStyle = g2; c.fill();
      c.restore();
    }
  }

  class Bullet {
    constructor(x, y, row, dmg, frozen) {
      this.x = x; this.y = y; this.row = row; this.dmg = dmg; this.frozen = frozen;
      this.dead = false;
    }
    update(dt) { this.x += 470 * dt; if (this.x > W + 20) this.dead = true; }
    draw(c) {
      c.save();
      c.translate(this.x, this.y);
      c.fillStyle = this.frozen ? "rgba(159,228,255,0.28)" : "rgba(142,224,106,0.28)";
      c.beginPath(); c.arc(0, 0, 13, 0, 6.2832); c.fill();
      c.fillStyle = this.frozen ? "#9fe4ff" : "#8ee06a";
      c.beginPath(); c.arc(0, 0, 8, 0, 6.2832); c.fill();
      c.restore();
    }
  }

  class Particle {
    constructor(x, y, color) {
      this.x = x; this.y = y; this.color = color;
      this.vx = rand(-90, 90); this.vy = rand(-140, -20);
      this.life = rand(0.4, 0.9); this.dead = false; this.r = rand(2, 5);
    }
    update(dt) {
      this.x += this.vx * dt; this.y += this.vy * dt; this.vy += 380 * dt;
      this.life -= dt; if (this.life <= 0) this.dead = true;
    }
    draw(c) {
      c.globalAlpha = clamp(this.life * 1.4, 0, 1);
      c.fillStyle = this.color;
      c.beginPath(); c.arc(this.x, this.y, this.r, 0, 6.2832); c.fill();
      c.globalAlpha = 1;
    }
  }

  class Plant {
    constructor(type, col, row) {
      this.type = type; this.col = col; this.row = row;
      this.hp = PLANTS[type].hp; this.maxHp = this.hp;
      this.cx = G.x + col * G.cw + G.cw / 2;
      this.cy = G.y + row * G.ch + G.ch / 2;
      this.t = rand(0, 6); this.shootCd = 0; this.sunCd = rand(2, 5);
      this.dead = false;
    }
    update(dt, game) {
      this.t += dt;
      if (this.type === "sunflower") {
        this.sunCd -= dt;
        if (this.sunCd <= 0) {
          this.sunCd = 9.5;
          game.addSun(new Sun(this.cx + rand(-14, 14), this.cy - 26, this.cy + 24));
          game.spawnParticles(this.cx, this.cy - 18, "#ffe066", 4);
        }
      } else if (this.type === "peashooter" || this.type === "snowpea" || this.type === "repeater") {
        const target = game.rowHasZombie(this.row, this.cx);
        this.shootCd -= dt;
        if (target && this.shootCd <= 0) {
          this.shootCd = this.type === "repeater" ? 1.7 : 1.45;
          const frozen = this.type === "snowpea";
          const dmg = this.type === "repeater" ? 20 : this.type === "snowpea" ? 18 : 22;
          const shots = this.type === "repeater" ? 2 : 1;
          for (let i = 0; i < shots; i++) {
            setTimeout(() => {
              if (!this.dead) game.addBullet(new Bullet(this.cx + 30, this.cy - 8, this.row, dmg, frozen));
            }, i * 130);
          }
        }
      } else if (this.type === "cherry") {
        this.fuse = (this.fuse ?? 0.9) - dt;
        if (this.fuse <= 0) this.explode(game);
      }
    }
    explode(game) {
      this.dead = true;
      const R = G.cw * 1.55;
      game.shake = 14;
      game.spawnParticles(this.cx, this.cy, "#ff9d5c", 26);
      game.spawnParticles(this.cx, this.cy, "#ffd166", 18);
      for (const z of game.zombies) {
        if (Math.hypot(z.x - this.cx, z.cy - this.cy) < R) z.hit(1400, false);
      }
    }
    hurt(d) { this.hp -= d; if (this.hp <= 0) this.dead = true; }
    draw(c) {
      const bob = Math.sin(this.t * 2.2) * 2.2;
      c.save();
      c.translate(this.cx, this.cy + bob);
      // 影子
      c.fillStyle = "rgba(0,0,0,0.20)";
      c.beginPath(); c.ellipse(0, 34, 30, 9, 0, 0, 6.2832); c.fill();
      // 茎
      c.strokeStyle = "#3f9e5f"; c.lineWidth = 6; c.lineCap = "round";
      c.beginPath(); c.moveTo(0, 30); c.lineTo(0, 4); c.stroke();

      if (this.type === "sunflower") {
        c.fillStyle = "#ffd23f";
        for (let i = 0; i < 11; i++) {
          const a = (i / 11) * 6.2832 + this.t * 0.35;
          c.beginPath();
          c.ellipse(Math.cos(a) * 25, Math.sin(a) * 25 - 6, 11, 8, a, 0, 6.2832);
          c.fill();
        }
        const g = c.createRadialGradient(-6, -12, 3, 0, -6, 24);
        g.addColorStop(0, "#a8703a"); g.addColorStop(1, "#7a4a1f");
        c.fillStyle = g;
        c.beginPath(); c.arc(0, -6, 20, 0, 6.2832); c.fill();
        c.fillStyle = "#2b1a0d";
        c.beginPath(); c.arc(-7, -9, 3.2, 0, 6.2832); c.fill();
        c.beginPath(); c.arc(7, -9, 3.2, 0, 6.2832); c.fill();
        c.strokeStyle = "#2b1a0d"; c.lineWidth = 2.4;
        c.beginPath(); c.arc(0, -3, 7, 0.25, Math.PI - 0.25); c.stroke();
      } else if (this.type === "wallnut") {
        const g = c.createLinearGradient(0, -34, 0, 34);
        g.addColorStop(0, "#c98f52"); g.addColorStop(1, "#9a6432");
        c.fillStyle = g;
        c.beginPath(); c.ellipse(0, -2, 30, 36, 0, 0, 6.2832); c.fill();
        c.fillStyle = "#2b1a0d";
        c.beginPath(); c.arc(-10, -8, 3.4, 0, 6.2832); c.fill();
        c.beginPath(); c.arc(10, -8, 3.4, 0, 6.2832); c.fill();
        c.strokeStyle = "#2b1a0d"; c.lineWidth = 3;
        c.beginPath(); c.moveTo(-8, 8); c.quadraticCurveTo(0, 2, 8, 8); c.stroke();
        // 血条
        if (this.hp < this.maxHp * 0.5) {
          c.strokeStyle = "#6b3f1e"; c.lineWidth = 2;
          c.beginPath(); c.moveTo(-12, 18); c.lineTo(-4, 22); c.lineTo(4, 16); c.stroke();
        }
      } else if (this.type === "cherry") {
        c.fillStyle = "#e2453f";
        c.beginPath(); c.arc(-11, 0, 19, 0, 6.2832); c.fill();
        c.beginPath(); c.arc(11, 6, 19, 0, 6.2832); c.fill();
        c.fillStyle = "#ff8a80";
        c.beginPath(); c.arc(-16, -7, 5, 0, 6.2832); c.fill();
        c.fillStyle = "#3f9e5f";
        c.beginPath(); c.ellipse(0, -22, 12, 6, -0.4, 0, 6.2832); c.fill();
        const pulse = 1 + Math.sin(this.t * 14) * 0.08;
        c.globalAlpha = 0.35;
        c.fillStyle = "#ff6b5a";
        c.beginPath(); c.arc(0, 0, 34 * pulse, 0, 6.2832); c.fill();
        c.globalAlpha = 1;
      } else {
        // 射手类
        const frozen = this.type === "snowpea";
        const head = frozen ? "#7fd4ff" : "#5fc26e";
        const dark = frozen ? "#3f9fc9" : "#3d9a4f";
        c.fillStyle = head;
        c.beginPath(); c.arc(0, -6, 25, 0, 6.2832); c.fill();
        c.fillStyle = dark;
        c.beginPath(); c.roundRect(14, -14, 22, 16, 7); c.fill();
        if (this.type === "repeater") {
          c.beginPath(); c.roundRect(20, 2, 18, 13, 6); c.fill();
        }
        c.fillStyle = "#1e3d26";
        c.beginPath(); c.arc(2, -10, 3.6, 0, 6.2832); c.fill();
        c.fillStyle = "rgba(255,255,255,0.35)";
        c.beginPath(); c.arc(-7, -15, 6, 0, 6.2832); c.fill();
      }
      c.restore();
    }
  }

  class Zombie {
    constructor(type, row) {
      const cfg = ZOMBIES[type];
      this.type = type; this.row = row;
      this.hp = cfg.hp; this.maxHp = cfg.hp;
      this.speed = cfg.speed * rand(0.92, 1.1);
      this.x = W + rand(10, 90);
      this.cy = G.y + row * G.ch + G.ch / 2 + 6;
      this.dmg = cfg.dmg;
      this.t = rand(0, 6); this.eatCd = 0; this.target = null;
      this.slow = 0; this.dead = false;
      this.armSwing = rand(0, 6.28);
    }
    hit(d, frozen) {
      this.hp -= d;
      if (frozen) this.slow = 3.2;
      if (this.hp <= 0) this.dead = true;
    }
    update(dt, game) {
      this.t += dt;
      this.armSwing += dt * 2.4;
      if (this.slow > 0) this.slow -= dt;
      const spd = this.speed * (this.slow > 0 ? 0.42 : 1);
      // 找当前格子的植物
      const col = Math.floor((this.x - G.x) / G.cw);
      const plant = game.plantAt(col, this.row);
      if (plant && this.x - G.x - col * G.cw < G.cw * 0.62) {
        this.target = plant;
        this.eatCd -= dt;
        if (this.eatCd <= 0) {
          this.eatCd = 0.85;
          plant.hurt(this.dmg);
          game.spawnParticles(this.x - 26, this.cy - 6, "#8ee06a", 3);
        }
      } else {
        this.target = null;
        this.x -= spd * dt;
      }
      if (this.x < G.x - 26) {
        this.dead = true;
        game.onZombieBreach(this.row);
      }
      if (this.dead) game.spawnParticles(this.x, this.cy, "#9aa88f", 8);
    }
    draw(c) {
      const b = Math.sin(this.t * 3.6) * 3;
      c.save();
      c.translate(this.x, this.cy + b);
      c.fillStyle = "rgba(0,0,0,0.22)";
      c.beginPath(); c.ellipse(0, 40, 26, 8, 0, 0, 6.2832); c.fill();

      const frozen = this.slow > 0;
      const skin = frozen ? "#a9c7cf" : this.type === "bucket" ? "#8ea08a" : "#94a98c";
      const dark = frozen ? "#7d9aa3" : "#6c7f66";

      // 腿
      c.strokeStyle = "#3c4a52"; c.lineWidth = 11; c.lineCap = "round";
      const leg = Math.sin(this.armSwing) * 7;
      c.beginPath(); c.moveTo(-6, 20); c.lineTo(-10 + leg, 42); c.stroke();
      c.beginPath(); c.moveTo(6, 20); c.lineTo(10 - leg, 42); c.stroke();
      // 身体
      c.fillStyle = "#5b6b7a";
      c.beginPath(); c.roundRect(-17, -14, 34, 38, 10); c.fill();
      // 手臂前伸
      c.strokeStyle = skin; c.lineWidth = 9;
      c.beginPath();
      c.moveTo(-4, -8);
      c.lineTo(-26 - Math.sin(this.armSwing) * 4, -2);
      c.stroke();
      c.fillStyle = skin;
      c.beginPath(); c.arc(-28 - Math.sin(this.armSwing) * 4, -2, 6, 0, 6.2832); c.fill();
      // 头
      c.fillStyle = skin;
      c.beginPath(); c.ellipse(2, -28, 17, 18, 0, 0, 6.2832); c.fill();
      // 眼
      c.fillStyle = "#2a1414";
      c.beginPath(); c.arc(-4, -31, 3.4, 0, 6.2832); c.fill();
      c.beginPath(); c.arc(6, -31, 3.4, 0, 6.2832); c.fill();
      // 嘴
      c.strokeStyle = "#3b1b1b"; c.lineWidth = 2.6;
      c.beginPath(); c.moveTo(-6, -20); c.quadraticCurveTo(2, -16, 9, -21); c.stroke();
      // 帽子
      if (this.type === "cone") {
        c.fillStyle = "#e08b3c";
        c.beginPath();
        c.moveTo(2, -62); c.lineTo(20, -40); c.lineTo(-16, -40); c.closePath(); c.fill();
        c.fillStyle = "#c97428";
        c.beginPath(); c.ellipse(2, -40, 19, 5, 0, 0, 6.2832); c.fill();
      } else if (this.type === "bucket") {
        c.fillStyle = "#9aa7b5";
        c.beginPath(); c.roundRect(-16, -54, 36, 20, 5); c.fill();
        c.fillStyle = "#c0ccd8";
        c.beginPath(); c.roundRect(-16, -54, 36, 5, 3); c.fill();
        c.strokeStyle = "#7b8895"; c.lineWidth = 2;
        c.beginPath(); c.moveTo(-16, -44); c.lineTo(20, -44); c.stroke();
      }
      // 血条
      if (this.hp < this.maxHp) {
        c.fillStyle = "rgba(0,0,0,0.45)";
        c.beginPath(); c.roundRect(-20, -74, 44, 6, 3); c.fill();
        c.fillStyle = this.hp / this.maxHp > 0.4 ? "#ff7a7a" : "#ff3b3b";
        c.beginPath(); c.roundRect(-20, -74, 44 * clamp(this.hp / this.maxHp, 0, 1), 6, 3); c.fill();
      }
      c.restore();
    }
  }

  class Game {
    constructor(canvas) {
      this.c = canvas;
      this.ctx = canvas.getContext("2d");
      this.reset();
      this.running = false;
      this.last = 0;
      this._loop = this._loop.bind(this);
      document.addEventListener("visibilitychange", () => {
        if (!document.hidden) this.last = 0;
      });
    }

    reset() {
      this.sun = 150;
      this.plants = [];
      this.zombies = [];
      this.bullets = [];
      this.suns = [];
      this.particles = [];
      this.mowers = [true, true, true, true, true];
      this.wave = 1;
      this.waveTimer = 14;
      this.spawnTimer = 5;
      this.skySunTimer = 5;
      this.elapsed = 0;
      this.score = 0;
      this.gameOver = false;
      this.shake = 0;
      this.flash = 0;
      this.cooldowns = {};
      this.banner = { text: "摸鱼开始", t: 2.2 };
    }

    setResolution(k) {
      k = Math.max(1, Math.min(2.5, Number(k) || 1));
      if (Math.abs(k - (this.k || 1)) < 0.02) return;
      this.k = k;
      this.c.width = Math.round(W * k);
      this.c.height = Math.round(H * k);
      if (this.ctx.setTransform) this.ctx.setTransform(k, 0, 0, k, 0, 0);
      this.draw();
    }

    start() {
      if (this.running) return;
      this.running = true;
      this.last = 0;
      requestAnimationFrame(this._loop);
    }
    pause() { this.running = false; }

    addSun(s) { this.suns.push(s); }
    addBullet(b) { this.bullets.push(b); }
    spawnParticles(x, y, color, n) {
      for (let i = 0; i < n; i++) this.particles.push(new Particle(x, y, color));
    }
    plantAt(col, row) {
      return this.plants.find((p) => p.col === col && p.row === row && !p.dead);
    }
    rowHasZombie(row, fromX) {
      return this.zombies.some((z) => z.row === row && !z.dead && z.x > fromX - 40);
    }
    onZombieBreach(row) {
      if (this.mowers[row]) {
        this.mowers[row] = false;
        this.shake = 10;
        this.banner = { text: "小推车！", t: 1.4 };
        for (const z of this.zombies) {
          if (z.row === row && !z.dead) { z.dead = true; this.score += 10; }
        }
      } else {
        this.gameOver = true;
        this.banner = { text: "被抓包了 · 重开", t: 3 };
        setTimeout(() => { this.reset(); this.banner = { text: "重新摸鱼", t: 2 }; }, 2600);
      }
    }

    /* ---------------- 自动挂机 AI ---------------- */
    autoPlay() {
      if (this.gameOver) return;
      // 收阳光
      for (const s of this.suns) {
        if (Math.hypot(s.x - 60, s.y - 50) < 9999) {
          if (s.y >= s.ty - 1) {
            s.dead = true;
            this.sun += s.v;
            this.spawnParticles(s.x, s.y, "#ffe066", 4);
          }
        }
      }
      const now = this.elapsed;
      const canPlant = (t) => this.sun >= PLANTS[t].cost && (this.cooldowns[t] || 0) <= now;

      const zombiesByRow = [0, 0, 0, 0, 0];
      const nearestByRow = [1e9, 1e9, 1e9, 1e9, 1e9];
      for (const z of this.zombies) {
        if (z.dead) continue;
        zombiesByRow[z.row]++;
        nearestByRow[z.row] = Math.min(nearestByRow[z.row], z.x);
      }

      const countType = (t) => this.plants.filter((p) => p.type === t && !p.dead).length;

      const place = (type, col, row) => {
        if (col < 0 || col >= G.cols) return false;
        if (this.plantAt(col, row)) return false;
        if (!canPlant(type)) return false;
        this.sun -= PLANTS[type].cost;
        this.cooldowns[type] = now + PLANTS[type].cd;
        this.plants.push(new Plant(type, col, row));
        this.spawnParticles(G.x + col * G.cw + G.cw / 2, G.y + row * G.ch + G.ch / 2, "#bfe9a0", 5);
        return true;
      };

      // 1. 樱桃炸弹：某行 3 只以上且逼近
      for (let r = 0; r < 5; r++) {
        const near = this.zombies.filter((z) => z.row === r && !z.dead && z.x < G.x + LAWN_W * 0.55);
        if (near.length >= 3 && canPlant("cherry")) {
          const col = clamp(Math.floor((near[0].x - G.x) / G.cw) - 1, 0, G.cols - 1);
          if (place("cherry", col, r)) return;
        }
      }

      // 2. 前两列种向日葵，最多 6 个
      if (countType("sunflower") < 6 && canPlant("sunflower")) {
        for (let r = 0; r < 5; r++) {
          for (const col of [0, 1]) {
            if (place("sunflower", col, r)) return;
          }
        }
      }

      // 3. 危行补射手
      for (let r = 0; r < 5; r++) {
        if (!zombiesByRow[r]) continue;
        const hasShooter = this.plants.some(
          (p) => p.row === r && !p.dead && ["peashooter", "snowpea", "repeater"].includes(p.type)
        );
        if (!hasShooter) {
          for (const col of [2, 3, 1, 4]) {
            if (place("peashooter", col, r)) return;
          }
        }
      }

      // 4. 每行至少两个射手
      for (let r = 0; r < 5; r++) {
        const shooters = this.plants.filter(
          (p) => p.row === r && !p.dead && ["peashooter", "snowpea", "repeater"].includes(p.type)
        ).length;
        if (shooters < 2 && (zombiesByRow[r] || this.elapsed > 30)) {
          for (const col of [2, 3, 4, 5]) {
            if (place("peashooter", col, r)) return;
          }
        }
      }

      // 5. 坚果挡路
      for (let r = 0; r < 5; r++) {
        if (nearestByRow[r] < G.x + LAWN_W * 0.62) {
          if (!this.plants.some((p) => p.row === r && p.type === "wallnut" && !p.dead)) {
            if (place("wallnut", 6, r)) return;
          }
        }
      }

      // 6. 有余粮升级寒冰/双发
      if (this.sun > 260) {
        for (let r = 0; r < 5; r++) {
          if (!zombiesByRow[r]) continue;
          if (place(this.wave >= 4 ? "repeater" : "snowpea", 3, r)) return;
          if (place("snowpea", 4, r)) return;
        }
      }
    }

    /* ---------------- 主循环（固定步长，掉帧也不影响游戏时间）---------------- */
    _loop(ts) {
      if (!this.running) return;
      if (!this.last) this.last = ts;
      let real = (ts - this.last) / 1000;
      this.last = ts;
      real = clamp(real, 0, 0.8);
      this._acc = (this._acc || 0) + real;
      const STEP = 1 / 30;
      let guard = 0;
      while (this._acc >= STEP && guard++ < 24) {
        this.step(STEP);
        this._acc -= STEP;
      }
      this.draw();
      requestAnimationFrame(this._loop);
    }

    step(dt) {
      if (this.shake > 0) this.shake = Math.max(0, this.shake - dt * 40);
      if (this.flash > 0) this.flash = Math.max(0, this.flash - dt);
      if (this.banner.t > 0) this.banner.t -= dt;

      if (this.gameOver) return;

      this.elapsed += dt;
      this.waveTimer -= dt;
      this.spawnTimer -= dt;
      this.skySunTimer -= dt;

      if (this.waveTimer <= 0) {
        this.wave++;
        this.waveTimer = Math.max(15, 26 - this.wave * 1.2);
        this.banner = { text: `第 ${this.wave} 波`, t: 2 };
        this.flash = 0.35;
      }
      if (this.spawnTimer <= 0) {
        this.spawnTimer = Math.max(2.2, 7.5 - this.wave * 0.45) * rand(0.75, 1.25);
        const row = Math.floor(Math.random() * 5);
        let type = "basic";
        const roll = Math.random();
        if (this.wave >= 5 && roll > 0.72) type = "bucket";
        else if (this.wave >= 3 && roll > 0.5) type = "cone";
        this.zombies.push(new Zombie(type, row));
      }
      if (this.skySunTimer <= 0) {
        this.skySunTimer = rand(6, 9.5);
        const x = G.x + rand(30, LAWN_W - 30);
        this.addSun(new Sun(x, -20, G.y + rand(60, LAWN_H - 40)));
      }

      this.autoPlay();

      for (const p of this.plants) p.update(dt, this);
      for (const z of this.zombies) z.update(dt, this);
      for (const b of this.bullets) {
        b.update(dt);
        for (const z of this.zombies) {
          if (z.dead || z.row !== b.row) continue;
          if (Math.abs(z.x - b.x) < 24) {
            z.hit(b.dmg, b.frozen);
            b.dead = true;
            this.spawnParticles(b.x, b.y, b.frozen ? "#9fe4ff" : "#a8e878", 3);
            break;
          }
        }
      }
      for (const s of this.suns) s.update(dt);
      for (const pt of this.particles) pt.update(dt);

      this.plants = this.plants.filter((p) => !p.dead);
      this.zombies = this.zombies.filter((z) => !z.dead);
      this.bullets = this.bullets.filter((b) => !b.dead);
      this.suns = this.suns.filter((s) => !s.dead);
      this.particles = this.particles.filter((p) => !p.dead);
    }

    /* ---------------- 绘制 ---------------- */
    draw() {
      const c = this.ctx;
      c.save();
      c.clearRect(0, 0, W, H);

      // 背景
      const bg = c.createLinearGradient(0, 0, 0, H);
      bg.addColorStop(0, "#12203a");
      bg.addColorStop(0.5, "#0e1a30");
      bg.addColorStop(1, "#0a1424");
      c.fillStyle = bg;
      c.fillRect(0, 0, W, H);

      if (this.shake > 0.2) {
        c.translate(rand(-this.shake, this.shake) * 0.5, rand(-this.shake, this.shake) * 0.5);
      }

      // 草坪
      for (let r = 0; r < G.rows; r++) {
        for (let col = 0; col < G.cols; col++) {
          const x = G.x + col * G.cw;
          const y = G.y + r * G.ch;
          const even = (r + col) % 2 === 0;
          c.fillStyle = even ? "#2f6b3c" : "#356f42";
          c.fillRect(x, y, G.cw, G.ch);
          c.fillStyle = "rgba(255,255,255,0.02)";
          c.fillRect(x, y, G.cw, 3);
        }
      }
      // 草坪外框
      c.strokeStyle = "rgba(140,220,160,0.18)";
      c.lineWidth = 2;
      c.strokeRect(G.x, G.y, LAWN_W, LAWN_H);

      // 左侧房子 + 小推车
      const hg = c.createLinearGradient(0, 0, G.x, 0);
      hg.addColorStop(0, "#1b2740");
      hg.addColorStop(1, "#243453");
      c.fillStyle = hg;
      c.fillRect(0, G.y - 18, G.x, LAWN_H + 36);
      c.fillStyle = "rgba(255,255,255,0.05)";
      c.fillRect(G.x - 6, G.y - 18, 6, LAWN_H + 36);
      for (let r = 0; r < G.rows; r++) {
        const y = G.y + r * G.ch + G.ch / 2;
        if (this.mowers[r]) {
          c.save();
          c.translate(G.x - 34, y + 16);
          c.fillStyle = "#c9403f";
          c.beginPath(); c.roundRect(-26, -20, 52, 26, 6); c.fill();
          c.fillStyle = "#8f2c2b";
          c.beginPath(); c.roundRect(-26, -8, 52, 8, 3); c.fill();
          c.fillStyle = "#2c3547";
          c.beginPath(); c.arc(-14, 8, 7, 0, 6.2832); c.fill();
          c.beginPath(); c.arc(14, 8, 7, 0, 6.2832); c.fill();
          c.fillStyle = "#e8eefc";
          c.beginPath(); c.roundRect(16, -30, 5, 12, 2); c.fill();
          c.restore();
        }
      }

      // 实体
      for (const p of this.plants) p.draw(c);
      for (const z of this.zombies) z.draw(c);
      for (const b of this.bullets) b.draw(c);
      for (const s of this.suns) s.draw(c);
      for (const p of this.particles) p.draw(c);

      c.restore();

      // ---------- HUD ----------
      this.drawHUD(c);

      if (this.flash > 0) {
        c.fillStyle = `rgba(255,255,255,${this.flash * 0.22})`;
        c.fillRect(0, 0, W, H);
      }
      if (this.banner.t > 0) {
        const a = clamp(this.banner.t, 0, 1);
        c.save();
        c.globalAlpha = a;
        c.font = "700 46px 'Microsoft YaHei', sans-serif";
        c.textAlign = "center";
        c.fillStyle = "rgba(0,0,0,0.45)";
        c.fillText(this.banner.text, W / 2 + 2, 300 + 2);
        c.fillStyle = "#ffe9b8";
        c.fillText(this.banner.text, W / 2, 300);
        c.restore();
      }
      if (this.gameOver) {
        c.fillStyle = "rgba(10,6,6,0.55)";
        c.fillRect(0, 0, W, H);
      }
    }

    drawHUD(c) {
      // 顶部条
      const g = c.createLinearGradient(0, 0, 0, 100);
      g.addColorStop(0, "rgba(10,16,28,0.96)");
      g.addColorStop(1, "rgba(10,16,28,0.72)");
      c.fillStyle = g;
      c.fillRect(0, 0, W, 100);

      // 阳光
      c.save();
      c.translate(46, 50);
      const gg = c.createRadialGradient(-6, -6, 3, 0, 0, 26);
      gg.addColorStop(0, "#fff2b0"); gg.addColorStop(1, "#ffc93c");
      c.fillStyle = gg;
      c.beginPath(); c.arc(0, 0, 22, 0, 6.2832); c.fill();
      c.restore();
      c.fillStyle = "#fff4cf";
      c.font = "700 30px Consolas, monospace";
      c.textAlign = "left";
      c.fillText(String(this.sun), 80, 61);

      // 卡片
      const cards = ["sunflower", "peashooter", "snowpea", "wallnut", "cherry", "repeater"];
      let x = 168;
      for (const t of cards) {
        const cfg = PLANTS[t];
        const afford = this.sun >= cfg.cost;
        const cdLeft = Math.max(0, (this.cooldowns[t] || 0) - this.elapsed);
        c.save();
        c.translate(x, 14);
        c.fillStyle = afford && cdLeft <= 0 ? "rgba(60,86,60,0.92)" : "rgba(40,48,62,0.9)";
        c.beginPath(); c.roundRect(0, 0, 78, 72, 10); c.fill();
        c.strokeStyle = afford && cdLeft <= 0 ? "rgba(150,220,140,0.5)" : "rgba(255,255,255,0.08)";
        c.lineWidth = 1.5;
        c.stroke();
        c.globalAlpha = afford && cdLeft <= 0 ? 1 : 0.45;
        c.translate(39, 34);
        c.scale(0.62, 0.62);
        const fake = { type: t, t: 0, hp: 1, maxHp: 1, cx: 0, cy: 0 };
        Plant.prototype.draw.call(fake, c);
        c.restore();
        c.save();
        c.globalAlpha = afford && cdLeft <= 0 ? 1 : 0.5;
        c.fillStyle = "#ffe9a8";
        c.font = "600 13px Consolas, monospace";
        c.textAlign = "center";
        c.fillText(String(cfg.cost), x + 39, 82);
        if (cdLeft > 0) {
          c.fillStyle = "rgba(0,0,0,0.55)";
          c.beginPath(); c.roundRect(x, 14, 78, 72, 10); c.fill();
          c.fillStyle = "#cfd9f5";
          c.font = "700 20px Consolas, monospace";
          c.fillText(cdLeft.toFixed(0), x + 39, 56);
        }
        c.restore();
        x += 86;
      }

      // 波次
      c.fillStyle = "#9fb7e0";
      c.font = "600 15px 'Microsoft YaHei', sans-serif";
      c.textAlign = "right";
      c.fillText(`第 ${this.wave} 波`, W - 28, 40);
      const prog = clamp(1 - Math.min(this.nearestZombieX() / (W + 60), 1), 0, 1);
      c.fillStyle = "rgba(255,255,255,0.10)";
      c.beginPath(); c.roundRect(W - 208, 54, 180, 8, 4); c.fill();
      c.fillStyle = "#ff8a5c";
      c.beginPath(); c.roundRect(W - 208, 54, 180 * prog, 8, 4); c.fill();


      // 底部摸鱼计时
      const mm = String(Math.floor(this.elapsed / 60)).padStart(2, "0");
      const ss = String(Math.floor(this.elapsed % 60)).padStart(2, "0");
      c.fillStyle = "rgba(10,16,28,0.72)";
      c.beginPath(); c.roundRect(16, H - 52, 236, 36, 12); c.fill();
      c.fillStyle = "#8ad4ff";
      c.font = "600 16px Consolas, monospace";
      c.textAlign = "left";
      c.fillText(`摸鱼 ${mm}:${ss}`, 32, H - 27);
      c.fillStyle = "rgba(160,180,215,0.55)";
      c.font = "600 13px 'Microsoft YaHei', sans-serif";
      c.fillText("别被老板发现", 140, H - 27);
    }

    nearestZombieX() {
      let m = W + 60;
      for (const z of this.zombies) if (!z.dead) m = Math.min(m, z.x);
      return m;
    }
  }

  window.addEventListener("DOMContentLoaded", () => {
    const cv = document.getElementById("pvzCanvas");
    if (!cv) return;
    const g = new Game(cv);
    window.PvZ = g;
    g.start();
  });

  window.PvZGame = Game;
})();
