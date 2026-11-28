// The homepage: plays the ball down the net in the hero, and lets the
// sections rise into view. Without this script the page shows the same
// drawing, finished, with every readout in place.

const root = document.documentElement;
const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
root.classList.add("js");

// Sections rise into view once.
const risers = [...document.querySelectorAll(".reveal")];
if (calm.matches || !("IntersectionObserver" in window)) {
  risers.forEach((el) => el.classList.add("in"));
} else {
  const watcher = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("in");
      watcher.unobserve(entry.target);
    }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });
  risers.forEach((el) => {
    const siblings = [...el.parentElement.children].filter((child) => child.classList.contains("reveal"));
    el.style.setProperty("--delay", `${Math.min(siblings.indexOf(el), 5) * 90}ms`);
    watcher.observe(el);
  });
}

// The ball in the hero starts once the page has loaded, so it never slows the first paint.
const stage = document.querySelector(".stage[data-flight]");
if (stage && !calm.matches) {
  const soon = (task) => ("requestIdleCallback" in window ? window.requestIdleCallback(task, { timeout: 1500 }) : window.setTimeout(task, 300));
  if (document.readyState === "complete") soon(() => play(stage));
  else window.addEventListener("load", () => soon(() => play(stage)), { once: true });
}

function play(stage) {
  const { frames, bounce } = JSON.parse(stage.dataset.flight);
  const ball = stage.querySelector("#hero-ball");
  const disc = ball.querySelector("circle");
  const seam = ball.querySelector(".seam");
  const shadow = stage.querySelector("#hero-shadow");
  const trail = stage.querySelector("#hero-trail");
  const glow = stage.querySelector("#hero-trail-glow");
  const path = stage.querySelector("#hero-path");
  const ring = stage.querySelector("#hero-ring");
  const chips = [...stage.querySelectorAll(".chip")];
  const chip = (step) => chips.find((item) => item.dataset.step === String(step));

  // Seconds on screen: the real ball takes half a second, shown four times slower.
  const BEFORE = 1.5, AFTER = 0.95, HOLD = 3.2, FADE = 0.6;
  const LOOP = BEFORE + AFTER + HOLD + FADE;
  const point = (frame) => `${frame[0]},${frame[1]}`;
  const upTo = (index) => frames.slice(0, index + 1).map(point).join(" ");
  let started = null;
  let running = false;
  let lastStep = -1;
  let lastIndex = -1;
  let lastFade = "1";

  function place(frame) {
    const [x, y, r, sx, sy] = frame;
    ball.setAttribute("transform", `translate(${x} ${y})`);
    disc.setAttribute("r", r);
    seam.setAttribute("d", `M${(-r * 0.25).toFixed(1)} ${(-r * 0.95).toFixed(1)} Q${(r * 0.45).toFixed(1)} 0 ${(-r * 0.25).toFixed(1)} ${(r * 0.95).toFixed(1)}`);
    shadow.setAttribute("cx", sx);
    shadow.setAttribute("cy", sy);
    shadow.setAttribute("rx", (r * 1.1).toFixed(1));
    shadow.setAttribute("ry", (r * 0.38).toFixed(1));
  }

  function show(step) {
    for (const item of chips) item.classList.toggle("on", Number(item.dataset.step) <= step);
    ring.classList.toggle("on", step >= 2);
    lastStep = step;
  }

  function frameAt(t) {
    // Frames are even in time on the real ball, so before and after the
    // bounce each get their own share of the slowed clock.
    if (t <= BEFORE) return Math.min(bounce, Math.floor((t / BEFORE) * bounce));
    const after = frames.length - 1 - bounce;
    return Math.min(frames.length - 1, bounce + Math.floor(((t - BEFORE) / AFTER) * after));
  }

  function tick(now) {
    if (!running) return;
    if (started === null) started = now;
    const t = ((now - started) / 1000) % LOOP;
    const index = frameAt(t);
    // Touch the drawing only when the ball has moved to a new frame.
    if (index !== lastIndex) {
      lastIndex = index;
      place(frames[index]);
      const flown = upTo(Math.min(index, bounce));
      trail.setAttribute("points", flown);
      glow.setAttribute("points", flown);
      path.setAttribute("points", index > bounce ? frames.slice(bounce, index + 1).map(point).join(" ") : "");
    }
    const flying = t < BEFORE + AFTER;
    let step = 3;
    if (t >= LOOP - FADE) step = -1;
    else if (t < BEFORE * 0.45) step = 0;
    else if (t < BEFORE) step = 1;
    else if (flying) step = 2;
    if (step !== lastStep) show(step);
    const fade = t > LOOP - FADE ? (1 - (t - (LOOP - FADE)) / FADE).toFixed(2) : "1";
    if (fade !== lastFade) {
      lastFade = fade;
      stage.style.setProperty("--fade", fade);
    }
    requestAnimationFrame(tick);
  }

  function start() {
    if (running) return;
    running = true;
    started = null;
    lastIndex = -1;
    stage.classList.add("playing");
    requestAnimationFrame(tick);
  }

  function stop() {
    running = false;
  }

  // Play only while the drawing is on screen and the tab is open.
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((entries) => {
      for (const entry of entries) (entry.isIntersecting && !document.hidden ? start : stop)();
    }, { threshold: 0.2 }).observe(stage);
  } else {
    start();
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else if (stage.getBoundingClientRect().bottom > 0) start();
  });
  calm.addEventListener?.("change", (event) => {
    if (!event.matches) return;
    stop();
    stage.classList.remove("playing");
    place(frames[frames.length - 1]);
    trail.setAttribute("points", upTo(bounce));
    glow.setAttribute("points", upTo(bounce));
    path.setAttribute("points", frames.slice(bounce).map(point).join(" "));
    stage.style.removeProperty("--fade");
  });
}
