/* TV canvas layer: slow floating diyas, twinkles, occasional fireworks and confetti bursts.
 * The canvas renders at devicePixelRatio 1 on purpose: at 4K a full-DPR canvas
 * would quadruple the fill cost for an effect that sits softly behind content. */
(function () {
  'use strict';

  const DIYA_COUNT = 9;
  const TWINKLE_COUNT = 70;
  const FIREWORK_MIN_GAP_MS = 7000;
  const FIREWORK_MAX_GAP_MS = 16000;
  const FIREWORK_PARTICLES = 70;
  const CONFETTI_PIECES = 220;
  const GRAVITY = 0.035;
  const FIREWORK_COLORS = ['#F3D27A', '#D4A017', '#F59E0B', '#E0457B', '#FFF6E5', '#FF8A3D', '#7FD1AE'];
  const CONFETTI_COLORS = ['#D4A017', '#F3D27A', '#F59E0B', '#E0457B', '#FFF6E5', '#2E9E5B', '#E07B00'];

  let canvas;
  let ctx;
  let width = 0;
  let height = 0;
  let unit = 1;
  let diyas = [];
  let twinkles = [];
  let sparks = [];
  let confetti = [];
  let nextFireworkAt = 0;
  let ambientFireworks = true;

  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (list) => list[Math.floor(Math.random() * list.length)];

  function resize() {
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = width;
    canvas.height = height;
    unit = Math.min(width / 100, height / 56.25);
  }

  function makeDiya(initial) {
    return {
      x: rand(0.03, 0.97) * width,
      y: initial ? rand(0.1, 1.1) * height : height + rand(4, 12) * unit,
      size: rand(1.4, 2.6) * unit,
      speed: rand(0.04, 0.09) * unit,
      sway: rand(0.4, 1.2) * unit,
      phase: rand(0, Math.PI * 2),
      flicker: rand(0, 100),
      alpha: rand(0.28, 0.5),
    };
  }

  function makeTwinkle() {
    return { x: rand(0, width), y: rand(0, height), r: rand(0.05, 0.16) * unit, phase: rand(0, Math.PI * 2), speed: rand(0.6, 1.6) };
  }

  function drawDiya(d, t) {
    const s = d.size;
    const x = d.x + Math.sin(t / 4000 + d.phase) * d.sway;
    const y = d.y;
    const flick = 0.85 + 0.15 * Math.sin(t / 90 + d.flicker) * Math.sin(t / 37 + d.flicker * 2);
    ctx.save();
    ctx.globalAlpha = d.alpha;

    const glow = ctx.createRadialGradient(x, y - s * 0.6, 0, x, y - s * 0.6, s * 2.6 * flick);
    glow.addColorStop(0, 'rgba(255, 196, 80, 0.55)');
    glow.addColorStop(1, 'rgba(255, 140, 20, 0)');
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(x, y - s * 0.6, s * 2.6 * flick, 0, Math.PI * 2);
    ctx.fill();

    const bowl = ctx.createLinearGradient(x, y - s * 0.2, x, y + s * 0.55);
    bowl.addColorStop(0, '#E07B00');
    bowl.addColorStop(1, '#7A2A10');
    ctx.fillStyle = bowl;
    ctx.beginPath();
    ctx.moveTo(x - s, y - s * 0.15);
    ctx.quadraticCurveTo(x, y + s * 0.9, x + s, y - s * 0.15);
    ctx.quadraticCurveTo(x + s * 0.6, y - s * 0.05, x + s * 0.25, y - s * 0.25);
    ctx.lineTo(x - s * 0.25, y - s * 0.25);
    ctx.quadraticCurveTo(x - s * 0.6, y - s * 0.05, x - s, y - s * 0.15);
    ctx.fill();
    ctx.fillStyle = 'rgba(243, 210, 122, 0.8)';
    ctx.fillRect(x - s * 0.55, y + s * 0.02, s * 1.1, s * 0.07);

    const fh = s * 1.15 * flick;
    const flame = ctx.createRadialGradient(x, y - s * 0.45, 0, x, y - s * 0.5, fh);
    flame.addColorStop(0, '#FFFBE6');
    flame.addColorStop(0.35, '#FFD45A');
    flame.addColorStop(1, 'rgba(245, 120, 10, 0.1)');
    ctx.fillStyle = flame;
    ctx.beginPath();
    ctx.moveTo(x, y - s * 0.25 - fh);
    ctx.quadraticCurveTo(x + s * 0.38, y - s * 0.45, x, y - s * 0.22);
    ctx.quadraticCurveTo(x - s * 0.38, y - s * 0.45, x, y - s * 0.25 - fh);
    ctx.fill();
    ctx.restore();
  }

  function drawTwinkle(tw, t) {
    const a = 0.12 + 0.3 * (0.5 + 0.5 * Math.sin(t / 1000 * tw.speed + tw.phase));
    ctx.fillStyle = `rgba(243, 210, 122, ${a.toFixed(3)})`;
    ctx.beginPath();
    ctx.arc(tw.x, tw.y, tw.r, 0, Math.PI * 2);
    ctx.fill();
  }

  /** A firework burst at (x, y) in CSS pixels; defaults to a random spot in the upper sky. */
  function firework(x = rand(0.15, 0.85) * width, y = rand(0.12, 0.45) * height, { scale = 1, count = FIREWORK_PARTICLES } = {}) {
    const color = pick(FIREWORK_COLORS);
    const accent = pick(FIREWORK_COLORS);
    for (let i = 0; i < count; i += 1) {
      const angle = (Math.PI * 2 * i) / count + rand(-0.05, 0.05);
      const speed = rand(0.18, 0.42) * unit * scale;
      sparks.push({
        x, y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
        life: 1, decay: rand(0.008, 0.014), r: rand(0.1, 0.2) * unit * scale, color: i % 4 === 0 ? accent : color,
      });
    }
  }

  /** Confetti shower from the top of the screen (reveal, trophy, fx). */
  function confettiBurst(pieces = CONFETTI_PIECES) {
    for (let i = 0; i < pieces; i += 1) {
      confetti.push({
        x: rand(0, width), y: rand(-height * 0.4, -unit), vx: rand(-0.15, 0.15) * unit, vy: rand(0.15, 0.4) * unit,
        w: rand(0.5, 0.9) * unit, h: rand(0.25, 0.45) * unit, rot: rand(0, Math.PI), vr: rand(-0.12, 0.12),
        wobble: rand(0, Math.PI * 2), color: pick(CONFETTI_COLORS),
      });
    }
  }

  function stepSparks() {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    sparks = sparks.filter((p) => p.life > 0);
    for (const p of sparks) {
      p.vx *= 0.985;
      p.vy = p.vy * 0.985 + GRAVITY * unit * 0.1;
      p.x += p.vx;
      p.y += p.vy;
      p.life -= p.decay;
      ctx.globalAlpha = Math.max(0, p.life) * 0.85;
      ctx.fillStyle = p.color;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  function stepConfetti() {
    confetti = confetti.filter((c) => c.y < height + 4 * unit);
    for (const c of confetti) {
      c.wobble += 0.06;
      c.x += c.vx + Math.sin(c.wobble) * 0.08 * unit;
      c.y += c.vy;
      c.rot += c.vr;
      ctx.save();
      ctx.translate(c.x, c.y);
      ctx.rotate(c.rot);
      ctx.scale(1, Math.abs(Math.cos(c.wobble)) * 0.8 + 0.2);
      ctx.fillStyle = c.color;
      ctx.fillRect(-c.w / 2, -c.h / 2, c.w, c.h);
      ctx.restore();
    }
  }

  function frame(t) {
    ctx.clearRect(0, 0, width, height);
    for (const tw of twinkles) drawTwinkle(tw, t);
    for (let i = 0; i < diyas.length; i += 1) {
      const d = diyas[i];
      d.y -= d.speed;
      if (d.y < -6 * unit) diyas[i] = makeDiya(false);
      drawDiya(d, t);
    }
    if (ambientFireworks && t > nextFireworkAt) {
      firework(undefined, undefined, { scale: rand(0.7, 1.1), count: Math.round(FIREWORK_PARTICLES * 0.8) });
      nextFireworkAt = t + rand(FIREWORK_MIN_GAP_MS, FIREWORK_MAX_GAP_MS);
    }
    if (sparks.length) stepSparks();
    if (confetti.length) stepConfetti();
    requestAnimationFrame(frame);
  }

  function start(el) {
    canvas = el;
    ctx = canvas.getContext('2d');
    resize();
    diyas = Array.from({ length: DIYA_COUNT }, () => makeDiya(true));
    twinkles = Array.from({ length: TWINKLE_COUNT }, makeTwinkle);
    nextFireworkAt = performance.now() + 3000;
    window.addEventListener('resize', () => {
      resize();
      twinkles = Array.from({ length: TWINKLE_COUNT }, makeTwinkle);
    });
    requestAnimationFrame(frame);
  }

  /** Ambient fireworks are turned off behind full-bleed photos so they never compete with them. */
  function setAmbientFireworks(on) {
    ambientFireworks = on;
  }

  /** A cluster of bursts for celebratory moments. */
  function celebrate() {
    firework(width * 0.2, height * 0.3, { scale: 1.3 });
    setTimeout(() => firework(width * 0.8, height * 0.25, { scale: 1.3 }), 350);
    setTimeout(() => firework(width * 0.5, height * 0.18, { scale: 1.5 }), 700);
  }

  window.TvFx = { start, firework, confetti: confettiBurst, celebrate, setAmbientFireworks };
})();
