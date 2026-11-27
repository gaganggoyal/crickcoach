const EDGES = [
  [11, 12], [11, 13], [13, 15], [12, 14], [14, 16],
  [11, 23], [12, 24], [23, 24],
  [23, 25], [25, 27], [24, 26], [26, 28],
  [0, 11], [0, 12],
];
const OUTCOMES = ["left", "defended", "attacked", "missed", "edged", "bowled"];
const OUTCOME_COLOURS = {
  left: "#9fb3c8", defended: "#b6d98a", attacked: "#7fd1c2", missed: "#e2b65c", edged: "#f08d5a", bowled: "#e36b4a",
};
const LENGTH_BANDS = [
  [0, 2, "yorker"], [2, 6, "full"], [6, 8, "good"], [8, 10, "short of length"], [10, 14, "short"],
];
const MAP = { top: 22, bottom: 290, left: 24, right: 186, maxLength: 14, halfWidth: 1.6 };
const POSE_VERSION = "0.10.21";
const POSE_CDN = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${POSE_VERSION}`;
const POSE_MODELS = {
  lite: "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
  full: "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
};

const state = {
  me: { coach: false, auth_required: false, rtsp: false },
  role: "bowling",
  person: "largest",
  vision: null,
  landmarker: null,
  clipLandmarker: null,
  poseClock: 0,
  streaming: false,
  liveSamples: [],
  sampleTimer: 0,
  cueTimer: 0,
  raf: 0,
  session: null,
  deliveryId: null,
  reviewCamera: "side",
  calib: null,
  poll: 0,
  pending: null,
};

const $ = (id) => document.getElementById(id);

function setStatus(text) {
  $("status").textContent = text;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { detail: text };
    }
  }
  if (response.status === 401 && state.me.auth_required) {
    state.me.coach = false;
    renderAccount();
    openLogin();
  }
  if (!response.ok) {
    const detail = body && body.detail;
    const message = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => item.msg).join(" ")
        : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return body;
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function postJSON(url, payload) {
  return api(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
}

// ---------------------------------------------------------------- account

async function loadMe() {
  try {
    state.me = await api("/api/me");
  } catch {
    state.me = { coach: false, auth_required: true, rtsp: false };
  }
  renderAccount();
  $("camera-form").hidden = !state.me.rtsp;
}

function renderAccount() {
  const root = $("account");
  root.replaceChildren();
  if (!state.me.auth_required) return;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ghost";
  if (state.me.coach) {
    button.textContent = "Sign out";
    button.addEventListener("click", async () => {
      await postJSON("/api/logout");
      state.me.coach = false;
      renderAccount();
      setStatus("Signed out");
    });
  } else {
    button.textContent = "Coach sign in";
    button.addEventListener("click", openLogin);
  }
  root.appendChild(button);
}

function openLogin() {
  const dialog = $("login-dialog");
  if (dialog.open) return;
  $("login-error").textContent = "";
  $("login-key").value = "";
  dialog.showModal();
  $("login-key").focus();
}

$("login-cancel").addEventListener("click", () => $("login-dialog").close());
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await postJSON("/api/login", { key: $("login-key").value });
    $("login-dialog").close();
    state.me.coach = true;
    renderAccount();
    setStatus("Signed in as the coach");
    const visible = document.querySelector("nav button.on")?.dataset.view;
    if (visible) showView(visible);
  } catch (error) {
    $("login-error").textContent = error.message;
  }
});

function needCoach() {
  if (state.me.auth_required && !state.me.coach) {
    openLogin();
    return true;
  }
  return false;
}

// ---------------------------------------------------------------- views

function showView(name) {
  for (const id of ["lane", "sessions", "machine"]) {
    $(`view-${id}`).hidden = id !== name;
  }
  document.querySelectorAll("nav button").forEach((button) => {
    button.classList.toggle("on", button.dataset.view === name);
  });
  if (name !== "lane" && needCoach()) return;
  if (name === "sessions") refreshSessions().catch((error) => setStatus(error.message));
  if (name === "machine") refreshMachine().catch((error) => setStatus(error.message));
}

document.querySelectorAll("nav button").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.view));
});

function renderCueCards(root, cues) {
  root.replaceChildren();
  if (!cues || !cues.length) {
    root.innerHTML = '<p class="empty">No cue for this view yet.</p>';
    return;
  }
  for (const cue of cues) {
    const card = document.createElement("article");
    card.className = `cue ${cue.severity}`;
    card.innerHTML = `<p class="kicker">${esc(cue.severity)}</p><h3>${esc(cue.title)}</h3><p>${esc(cue.text)}</p>${cue.measure ? `<p class="measure">${esc(cue.measure)}</p>` : ""}`;
    root.appendChild(card);
  }
}

// ---------------------------------------------------------------- the lane

document.querySelectorAll("#role-switch button").forEach((button) => {
  button.addEventListener("click", () => {
    state.role = button.dataset.role;
    document.querySelectorAll("#role-switch button").forEach((item) => {
      item.classList.toggle("on", item === button);
    });
    $("sample-clean").textContent = state.role === "batting" ? "Sample stance" : "Sample action";
    $("sample-faults").textContent = state.role === "batting" ? "Sample batting faults" : "Sample faults";
    $("stage-caption").textContent = state.role === "batting" ? "Front-on for the stance." : "Side-on for the bowling action.";
    if (!state.streaming) loadSample(state.role === "batting" ? "batting-clean" : "bowling-clean");
  });
});

$("person").addEventListener("change", (event) => {
  state.person = event.target.value;
});

async function visionModule() {
  if (!state.vision) {
    const vision = await import(`${POSE_CDN}/vision_bundle.mjs`);
    const files = await vision.FilesetResolver.forVisionTasks(`${POSE_CDN}/wasm`);
    state.vision = { vision, files };
  }
  return state.vision;
}

async function makeLandmarker(model) {
  const { vision, files } = await visionModule();
  const options = (delegate) => ({
    baseOptions: { modelAssetPath: POSE_MODELS[model], delegate },
    runningMode: "VIDEO",
    numPoses: 2,
  });
  try {
    return await vision.PoseLandmarker.createFromOptions(files, options("GPU"));
  } catch {
    return await vision.PoseLandmarker.createFromOptions(files, options("CPU"));
  }
}

async function loadPose() {
  try {
    state.landmarker = await makeLandmarker("lite");
    setStatus("Pose model ready");
  } catch {
    setStatus("The pose model did not load. Samples and ball tracking still work.");
  }
}

function pickPose(landmarks) {
  if (!landmarks || !landmarks.length) return null;
  const ranked = landmarks.map((points) => {
    const xs = points.map((point) => point.x);
    const ys = points.map((point) => point.y);
    const hip = ((points[23]?.x || 0) + (points[24]?.x || 0)) / 2;
    const area = (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys));
    return { points, hip, area };
  });
  if (state.person === "left") ranked.sort((a, b) => a.hip - b.hip);
  else if (state.person === "right") ranked.sort((a, b) => b.hip - a.hip);
  else ranked.sort((a, b) => b.area - a.area);
  return ranked[0].points;
}

function toPayload(points) {
  return points.map((point) => ({ x: point.x, y: point.y, visibility: point.visibility ?? 1 }));
}

function drawPose(points) {
  const canvas = $("stage");
  const box = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(320, box.width) * ratio;
  canvas.height = Math.max(180, box.height) * ratio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const width = canvas.width / ratio;
  const height = canvas.height / ratio;
  ctx.fillStyle = "#10160e";
  ctx.fillRect(0, 0, width, height);
  const video = $("live-video");
  let frame = { x: 0, y: 0, w: width, h: height };
  if (state.streaming && video.videoWidth) {
    frame = contentRect(width, height, video.videoWidth, video.videoHeight);
    ctx.drawImage(video, frame.x, frame.y, frame.w, frame.h);
  } else {
    ctx.fillStyle = "#1a3320";
    ctx.fillRect(0, height * 0.72, width, height * 0.28);
  }
  if (!points) return;
  const xy = (index) => {
    const point = points[index];
    if (!point || (point.visibility ?? 1) < 0.4) return null;
    return [frame.x + point.x * frame.w, frame.y + point.y * frame.h];
  };
  ctx.lineWidth = 3;
  ctx.strokeStyle = "#f4ecd8";
  ctx.beginPath();
  for (const [a, b] of EDGES) {
    const start = xy(a);
    const end = xy(b);
    if (!start || !end) continue;
    ctx.moveTo(start[0], start[1]);
    ctx.lineTo(end[0], end[1]);
  }
  ctx.stroke();
  ctx.fillStyle = "#e36b4a";
  for (const index of [0, 15, 16, 27, 28]) {
    const point = xy(index);
    if (!point) continue;
    ctx.beginPath();
    ctx.arc(point[0], point[1], 4, 0, Math.PI * 2);
    ctx.fill();
  }
}

function contentRect(width, height, mediaWidth, mediaHeight) {
  const scale = Math.min(width / mediaWidth, height / mediaHeight);
  const w = mediaWidth * scale;
  const h = mediaHeight * scale;
  return { x: (width - w) / 2, y: (height - h) / 2, w, h };
}

async function loadSample(name) {
  window.clearInterval(state.sampleTimer);
  try {
    const sample = await api(`/api/samples/${name}`);
    state.role = sample.role;
    renderCueCards($("cues"), sample.cues);
    let index = 0;
    const tick = () => {
      if (state.streaming) return;
      drawPose(sample.frames[index].points);
      index = (index + 1) % sample.frames.length;
    };
    tick();
    state.sampleTimer = window.setInterval(tick, 180);
    $("stage-caption").textContent = sample.blurb;
    setStatus(sample.title);
  } catch (error) {
    setStatus(error.message);
  }
}

$("sample-clean").addEventListener("click", () => loadSample(state.role === "batting" ? "batting-clean" : "bowling-clean"));
$("sample-faults").addEventListener("click", () => loadSample(state.role === "batting" ? "batting-faults" : "bowling-faults"));

async function listCameras() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  const devices = await navigator.mediaDevices.enumerateDevices();
  const select = $("devices");
  const known = new Set([...select.options].map((option) => option.value));
  for (const device of devices.filter((item) => item.kind === "videoinput")) {
    if (known.has(device.deviceId)) continue;
    const option = document.createElement("option");
    option.value = device.deviceId;
    option.textContent = device.label || "Camera";
    select.appendChild(option);
  }
}

function liveLoop() {
  if (!state.streaming) return;
  const video = $("live-video");
  let pose = null;
  if (state.landmarker && video.readyState >= 2) {
    state.poseClock += 33;
    const result = state.landmarker.detectForVideo(video, state.poseClock);
    pose = pickPose(result.landmarks);
    if (pose) {
      const now = performance.now() / 1000;
      state.liveSamples.push({ t: now, points: toPayload(pose) });
      state.liveSamples = state.liveSamples.filter((frame) => frame.t >= now - 2.4).slice(-200);
    }
  }
  drawPose(pose);
  state.raf = requestAnimationFrame(liveLoop);
}

let cuePost = 0;
async function postLiveCues() {
  if (!state.streaming || state.liveSamples.length < 4) return;
  const started = performance.now();
  if (started - cuePost < 700) return;
  cuePost = started;
  const first = state.liveSamples[0].t;
  const frames = state.liveSamples.map((frame) => ({ ...frame, t: frame.t - first }));
  try {
    const result = await postJSON("/api/live/cues", { role: state.role, frames });
    renderCueCards($("cues"), result.cues);
    $("save-ball").disabled = false;
  } catch (error) {
    setStatus(error.message);
  }
}

$("start-camera").addEventListener("click", async () => {
  if (!navigator.mediaDevices?.getUserMedia) {
    setStatus("This browser has no camera access.");
    return;
  }
  window.clearInterval(state.sampleTimer);
  const deviceId = $("devices").value;
  const video = { width: { ideal: 1280 }, height: { ideal: 720 } };
  if (deviceId) video.deviceId = { exact: deviceId };
  try {
    if (!state.landmarker) await loadPose();
    const stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
    const player = $("live-video");
    player.srcObject = stream;
    await player.play();
    state.streaming = true;
    state.liveSamples = [];
    $("start-camera").disabled = true;
    $("stop-camera").disabled = false;
    setStatus("Camera on");
    await listCameras();
    liveLoop();
    state.cueTimer = window.setInterval(postLiveCues, 250);
  } catch (error) {
    setStatus(error.message || "The camera did not start.");
  }
});

$("stop-camera").addEventListener("click", () => {
  stopCamera();
  setStatus("Camera off");
});

function stopCamera() {
  state.streaming = false;
  cancelAnimationFrame(state.raf);
  window.clearInterval(state.cueTimer);
  const player = $("live-video");
  for (const track of player.srcObject?.getTracks?.() || []) track.stop();
  player.srcObject = null;
  $("start-camera").disabled = false;
  $("stop-camera").disabled = true;
  $("save-ball").disabled = state.liveSamples.length < 2;
}

$("save-ball").addEventListener("click", async () => {
  if (state.liveSamples.length < 2 || needCoach()) return;
  const first = state.liveSamples[0].t;
  const frames = state.liveSamples.map((frame) => ({ ...frame, t: +(frame.t - first).toFixed(3) }));
  try {
    const session = await postJSON("/api/snapshots", { name: $("player-name").value, role: state.role, frames });
    state.session = session;
    state.deliveryId = session.deliveries[0].id;
    setStatus("Saved to the session log");
    showView("sessions");
    renderReview();
  } catch (error) {
    setStatus(error.message);
  }
});

// ---------------------------------------------------------------- sessions

async function refreshSessions() {
  const progress = await api("/api/progress");
  const line = $("progress");
  if (progress.balls) {
    const pace = progress.mean_pace_kph ? ` · mean ${progress.mean_pace_kph} km/h, top ${progress.top_pace_kph}` : "";
    line.textContent = `${plural(progress.balls, "ball")} logged${pace}. ${progress.next_drill}`;
  } else {
    line.textContent = "No balls logged yet.";
  }
  const data = await api("/api/sessions");
  const root = $("session-list");
  root.replaceChildren();
  if (!data.sessions.length) {
    root.innerHTML = '<p class="empty">No sessions yet.</p>';
    return;
  }
  for (const session of data.sessions) {
    const card = document.createElement("div");
    card.className = "session-card";
    if (state.session && state.session.id === session.id) card.classList.add("on");
    const job = session.job ? ` · ${Math.round((session.job.progress || 0) * 100)}%` : "";
    const cams = [session.cameras.side ? "side" : "", session.cameras.behind ? "behind" : ""].filter(Boolean).join(" + ") || "live";
    card.innerHTML = `<div><strong>${esc(session.name)}</strong><span>${esc(session.role)} · ${plural(session.deliveries, "ball")} · ${esc(cams)} · ${esc(session.status)}${esc(job)}</span></div>`;
    const open = document.createElement("button");
    open.type = "button";
    open.textContent = "Open";
    open.addEventListener("click", () => openSession(session.id));
    card.appendChild(open);
    root.appendChild(card);
  }
}

async function openSession(id) {
  try {
    state.session = await api(`/api/sessions/${id}`);
    state.deliveryId = state.session.deliveries[0]?.id || null;
    state.reviewCamera = state.session.cameras.side ? "side" : "behind";
    state.calib = null;
    renderReview();
    watchSession();
  } catch (error) {
    setStatus(error.message);
  }
}

function watchSession() {
  window.clearTimeout(state.poll);
  const session = state.session;
  if (!session || !(session.job || ["processing", "recording"].includes(session.status))) return;
  state.poll = window.setTimeout(async () => {
    try {
      const fresh = await api(`/api/sessions/${session.id}`);
      if (!state.session || state.session.id !== fresh.id) return;
      const finished = !(fresh.job || ["processing", "recording"].includes(fresh.status));
      state.session = fresh;
      if (finished) {
        state.deliveryId = state.deliveryId && fresh.deliveries.some((item) => item.id === state.deliveryId)
          ? state.deliveryId
          : fresh.deliveries[0]?.id || null;
        renderReview();
        refreshSessions().catch(() => {});
        setStatus(fresh.status === "error" ? fresh.error : `${plural(fresh.deliveries.length, "ball")} ready`);
      } else {
        renderJob();
        watchSession();
      }
    } catch (error) {
      setStatus(error.message);
    }
  }, 1200);
}

function renderJob() {
  const box = $("job");
  if (!box || !state.session) return;
  const job = state.session.job;
  if (!job && state.session.status !== "recording") {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const share = job ? job.progress || 0 : 0;
  box.querySelector(".bar span").style.width = `${Math.round(share * 100)}%`;
  box.querySelector("p").textContent = state.session.status === "recording" ? "Recording the cameras…" : job.message;
}

function currentDelivery() {
  if (!state.session) return null;
  return state.session.deliveries.find((item) => item.id === state.deliveryId) || state.session.deliveries[0] || null;
}

function rememberDelivery(updated) {
  state.session.deliveries = state.session.deliveries.map((item) => (item.id === updated.id ? updated : item));
}

function renderReview() {
  const session = state.session;
  const root = $("review");
  if (!session) {
    root.innerHTML = '<p class="empty">Choose a session.</p>';
    return;
  }
  const delivery = currentDelivery();
  state.deliveryId = delivery ? delivery.id : null;
  const camera = state.reviewCamera;
  const hasVideo = Boolean(session.cameras && session.cameras[camera]);
  const both = Boolean(session.cameras.side && session.cameras.behind);
  const calibrated = Boolean((session.calibration || {})[camera]);
  root.innerHTML = `
    <div class="panel-head">
      <div>
        <h2>${esc(session.name)}</h2>
        <span class="note">${esc(session.role)} · ${esc(session.status)}${session.found_by ? ` · balls found by ${esc(session.found_by)}` : ""}${both ? ` · behind camera clock ${session.clock_offset >= 0 ? "+" : ""}${Number(session.clock_offset || 0).toFixed(2)} s (${esc(session.clock_note || "kept")})` : ""}</span>
      </div>
      <div class="switch" id="batter-switch" title="The batter on strike">
        <button type="button" data-batter="right" class="${session.batter !== "left" ? "on" : ""}">Right-hander</button>
        <button type="button" data-batter="left" class="${session.batter === "left" ? "on" : ""}">Left-hander</button>
      </div>
    </div>
    <div id="job" class="job" hidden><div class="bar"><span></span></div><p></p></div>
    ${session.error ? `<p class="error">${esc(session.error)}</p>` : ""}
    ${both ? `<div class="switch" id="camera-switch"><button type="button" data-camera="side" class="${camera === "side" ? "on" : ""}">Side camera</button><button type="button" data-camera="behind" class="${camera === "behind" ? "on" : ""}">Behind the bowler</button></div>` : ""}
    ${hasVideo ? `<div class="video-box"><video id="review-video" controls playsinline preload="auto" src="/api/sessions/${session.id}/media/${camera}"></video><canvas id="track-overlay"></canvas><canvas id="loupe" width="168" height="168" hidden></canvas></div>
      <div id="calib-bar" class="calib-bar" hidden></div>` : `<p class="empty">Saved from the live lane. This ball has cues and no video.</p>`}
    <div class="delivery-row" id="delivery-row"></div>
    <div class="review-grid">
      <div>
        <div id="ball-facts" class="facts"></div>
        <div class="delivery-row" id="outcomes"></div>
        <div id="review-cues" class="cues"></div>
        <p id="review-note" class="note"></p>
        <div class="buttons">
          ${hasVideo ? `<button type="button" id="track-ball" ${calibrated ? "" : "disabled"}>Track this ball</button>
          <button type="button" id="track-all" ${session.deliveries.length ? "" : "disabled"}>Track every ball</button>
          <button type="button" id="find-again">Find the balls again</button>
          <button type="button" id="calibrate">${calibrated ? "Calibrate again" : "Calibrate this camera"}</button>
          <button type="button" id="mark-here">Mark a ball here</button>
          <button type="button" id="read-action">Read the action</button>` : ""}
          ${delivery ? '<button type="button" id="remove-ball" class="ghost">Remove this ball</button>' : ""}
        </div>
        <div class="row pace-row">
          <label>Pace km/h <input id="pace-input" type="number" min="40" max="180" step="0.1"></label>
          <button type="button" id="save-pace">Save pace</button>
          <button type="button" id="apply-radar">Use radar</button>
        </div>
        <div class="buttons">
          <button type="button" id="send-machine">Send this ball to the machine</button>
          <button type="button" id="queue-this-drill">Queue the next drill</button>
          <button type="button" id="delete-session" class="ghost danger">Delete session</button>
        </div>
      </div>
      <div class="pitch-wrap">
        ${pitchMapSvg()}
        <p class="hint">Seen from behind the bowler. The batter's stumps are at the top; ${session.batter === "left" ? "a left-hander's off side is on the right" : "a right-hander's off side is on the left"}. Click to mark where a ball pitched.</p>
        <button type="button" id="clear-mark" class="ghost">Use the cameras' mark</button>
      </div>
    </div>`;
  renderJob();
  renderDeliveryRow(session, delivery);
  renderFacts(delivery, session);
  drawPitchMap(session, delivery);
  wireReview(session, delivery, hasVideo);
}

function renderDeliveryRow(session, delivery) {
  const row = $("delivery-row");
  if (!session.deliveries.length) {
    row.innerHTML = '<p class="empty">No balls yet.</p>';
    return;
  }
  for (const item of session.deliveries) {
    const button = document.createElement("button");
    button.type = "button";
    const pitch = item.pitch;
    const sub = pitch ? `${pitch.length_m.toFixed(1)} m` : "–";
    const pace = item.pace_kph ? ` · ${Math.round(item.pace_kph)}` : "";
    button.innerHTML = `Ball ${item.index}<small>${esc(sub)}${esc(pace)}</small>`;
    if (item.confidence === "low") button.classList.add("unsure");
    if (item.outcome) button.style.borderColor = OUTCOME_COLOURS[item.outcome];
    if (delivery && item.id === delivery.id) button.classList.add("on");
    button.addEventListener("click", () => {
      state.deliveryId = item.id;
      state.calib = null;
      renderReview();
    });
    row.appendChild(button);
  }
}

function renderFacts(delivery, session) {
  const facts = $("ball-facts");
  const cueRoot = $("review-cues");
  if (!delivery) {
    facts.innerHTML = '<p class="empty">Pick a ball.</p>';
    return;
  }
  const pitch = delivery.pitch;
  const stumps = delivery.stumps || {};
  let stumpsText = "Not seen";
  if (stumps.would_hit === true) stumpsText = "Would hit the stumps";
  else if (stumps.would_hit === false) stumpsText = "Would miss the stumps";
  else if (stumps.height_m != null) stumpsText = `Passes at ${stumps.height_m.toFixed(2)} m high; line not seen`;
  const sure = delivery.confidence === "low"
    ? '<span class="badge unsure">check this mark</span>'
    : delivery.confidence === "ok" ? '<span class="badge sure">measured</span>' : "";
  const paceSource = delivery.pace_source ? ` <span class="note">(${esc(delivery.pace_source)})</span>` : "";
  facts.innerHTML = `
    <dl>
      <div><dt>Pitched</dt><dd>${pitch ? esc(pitch.text) : "No mark yet"} ${sure}</dd></div>
      <div><dt>Pace</dt><dd>${delivery.pace_kph ? `${delivery.pace_kph.toFixed(1)} km/h` : "Not measured"}${paceSource}</dd></div>
      <div><dt>Stumps</dt><dd>${esc(stumpsText)}</dd></div>
      <div><dt>Window</dt><dd>${delivery.t0.toFixed(2)}–${delivery.t1.toFixed(2)} s</dd></div>
    </dl>`;
  if (delivery.cues?.length) renderCueCards(cueRoot, delivery.cues);
  else cueRoot.innerHTML = '<p class="empty">Read the action to get coaching cues for this ball.</p>';
  const notes = [delivery.cut_note, delivery.pose_note, ...Object.values(delivery.tracks || {}).map((track) => track?.note)]
    .filter(Boolean);
  $("review-note").textContent = [...new Set(notes)].join(" ");
  const pace = $("pace-input");
  if (pace) pace.value = delivery.pace_kph ?? "";
  const outcomes = $("outcomes");
  for (const name of OUTCOMES) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = name;
    if (delivery.outcome === name) {
      button.classList.add("on");
      button.style.background = OUTCOME_COLOURS[name];
    }
    button.addEventListener("click", () => markOutcome(delivery, delivery.outcome === name ? null : name));
    outcomes.appendChild(button);
  }
}

function wireReview(session, delivery, hasVideo) {
  document.querySelectorAll("#batter-switch button").forEach((button) => {
    button.addEventListener("click", () => setBatter(button.dataset.batter));
  });
  document.querySelectorAll("#camera-switch button").forEach((button) => {
    button.addEventListener("click", () => {
      state.reviewCamera = button.dataset.camera;
      state.calib = null;
      renderReview();
    });
  });
  $("pitch").addEventListener("click", (event) => markPitch(event, delivery));
  $("clear-mark").addEventListener("click", () => clearMark(delivery));
  $("save-pace").addEventListener("click", () => savePace(delivery));
  $("apply-radar").addEventListener("click", () => applyRadar(delivery));
  $("send-machine").addEventListener("click", () => sendMachine(delivery));
  $("queue-this-drill").addEventListener("click", () => queueDrill(session.id));
  $("delete-session").addEventListener("click", () => deleteSession(session.id));
  $("remove-ball")?.addEventListener("click", () => removeBall(delivery));
  if (!hasVideo) return;
  $("track-ball").addEventListener("click", () => trackBall(delivery));
  $("track-all").addEventListener("click", () => sessionJob("track"));
  $("find-again").addEventListener("click", () => sessionJob("find"));
  $("calibrate").addEventListener("click", startCalibration);
  $("mark-here").addEventListener("click", markHere);
  $("read-action").addEventListener("click", () => readAction(delivery));
  const video = $("review-video");
  const overlay = $("track-overlay");
  overlay.addEventListener("click", (event) => onVideoClick(event));
  overlay.addEventListener("mousemove", (event) => onVideoMove(event));
  overlay.addEventListener("mouseleave", () => { $("loupe").hidden = true; });
  const redraw = () => drawOverlay(delivery);
  video.addEventListener("loadedmetadata", () => {
    if (delivery && delivery.camera !== "live") playDelivery(delivery);
    redraw();
  });
  video.addEventListener("seeked", redraw);
  video.addEventListener("timeupdate", redraw);
  window.addEventListener("resize", redraw, { once: true });
}

function playDelivery(delivery) {
  const video = $("review-video");
  if (!video || !delivery) return;
  const offset = state.reviewCamera === "behind" ? Number(state.session.clock_offset || 0) : 0;
  const start = Math.max(0, delivery.t0 + offset);
  const stop = delivery.t1 + offset;
  video.currentTime = start;
  video.ontimeupdate = () => {
    if (video.currentTime >= stop && !video.paused) video.pause();
  };
}

function videoRect() {
  const video = $("review-video");
  const box = video.getBoundingClientRect();
  if (!video.videoWidth) return { box, x: 0, y: 0, w: box.width, h: box.height };
  const inner = contentRect(box.width, box.height, video.videoWidth, video.videoHeight);
  return { box, ...inner };
}

function drawOverlay(delivery) {
  const canvas = $("track-overlay");
  const video = $("review-video");
  if (!canvas || !video) return;
  const rect = videoRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = rect.box.width * ratio;
  canvas.height = rect.box.height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, rect.box.width, rect.box.height);
  const toScreen = (point) => [rect.x + point.x * rect.w, rect.y + point.y * rect.h];
  if (state.calib) {
    drawCalibration(ctx, toScreen);
    return;
  }
  const track = delivery?.tracks?.[state.reviewCamera];
  const points = track?.points || [];
  if (!points.length) return;
  const now = video.currentTime;
  ctx.lineWidth = 2;
  ctx.strokeStyle = "rgba(244, 236, 216, 0.85)";
  ctx.beginPath();
  points.forEach((point, index) => {
    const [x, y] = toScreen(point);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  for (const point of points) {
    const [x, y] = toScreen(point);
    ctx.fillStyle = point.t <= now ? "#f4ecd8" : "rgba(244, 236, 216, 0.35)";
    ctx.beginPath();
    ctx.arc(x, y, 2.5, 0, Math.PI * 2);
    ctx.fill();
  }
  if (track.bounce_t != null) {
    const nearest = points.reduce((best, point) => (Math.abs(point.t - track.bounce_t) < Math.abs(best.t - track.bounce_t) ? point : best), points[0]);
    const [x, y] = toScreen(nearest);
    ctx.strokeStyle = "#e36b4a";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 8, 0, Math.PI * 2);
    ctx.stroke();
  }
}

// ---------------------------------------------------------------- calibration

const SIDE_STEPS = [
  { key: "bowler_base", text: "Click the foot of the stumps at the bowler's end." },
  { key: "bowler_top", text: "Click the top of those stumps." },
  { key: "batter_base", text: "Click the foot of the stumps at the batter's end." },
  { key: "batter_top", text: "Click the top of those stumps." },
];
const BEHIND_STEPS = [
  { key: "c0", text: "Bowler's end: click where the popping crease meets the return crease on the off side." },
  { key: "c1", text: "Bowler's end: the same corner on the leg side." },
  { key: "c2", text: "Batter's end: where the popping crease meets the return crease on the leg side." },
  { key: "c3", text: "Batter's end: the same corner on the off side." },
];

function startCalibration() {
  const video = $("review-video");
  if (!video) return;
  video.pause();
  const behind = state.reviewCamera === "behind";
  state.calib = { camera: state.reviewCamera, steps: behind ? BEHIND_STEPS : SIDE_STEPS, index: 0, points: {} };
  renderCalibBar();
  drawOverlay(currentDelivery());
}

function renderCalibBar() {
  const bar = $("calib-bar");
  if (!bar) return;
  const calib = state.calib;
  if (!calib) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;
  const step = calib.steps[calib.index];
  const side = calib.camera === "side";
  const existing = state.session.calibration?.side?.distance_m ?? "";
  bar.innerHTML = `<p><strong>${calib.index + 1} of ${calib.steps.length}.</strong> ${esc(step ? step.text : "Done.")} Pause on a frame where the pitch is clear. The magnifier follows the pointer.</p>
    <div class="buttons">
      ${side ? `<label class="inline">Camera to stumps line, m <input id="calib-distance" type="number" min="2" max="60" step="0.1" value="${esc(existing)}" placeholder="12"></label>` : ""}
      <button type="button" id="calib-undo" ${calib.index ? "" : "disabled"}>Undo</button>
      <button type="button" id="calib-cancel" class="ghost">Cancel</button>
    </div>`;
  $("calib-undo").addEventListener("click", () => {
    calib.index = Math.max(0, calib.index - 1);
    delete calib.points[calib.steps[calib.index].key];
    renderCalibBar();
    drawOverlay(currentDelivery());
  });
  $("calib-cancel").addEventListener("click", () => {
    state.calib = null;
    renderCalibBar();
    drawOverlay(currentDelivery());
    setStatus("Calibration cancelled");
  });
}

function drawCalibration(ctx, toScreen) {
  const calib = state.calib;
  ctx.font = "12px system-ui, sans-serif";
  calib.steps.forEach((step, index) => {
    const point = calib.points[step.key];
    if (!point) return;
    const [x, y] = toScreen(point);
    ctx.strokeStyle = "#e36b4a";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x - 7, y); ctx.lineTo(x + 7, y);
    ctx.moveTo(x, y - 7); ctx.lineTo(x, y + 7);
    ctx.stroke();
    ctx.fillStyle = "#f4ecd8";
    ctx.fillText(String(index + 1), x + 6, y - 6);
  });
}

function eventToVideo(event) {
  const rect = videoRect();
  const x = (event.clientX - rect.box.left - rect.x) / rect.w;
  const y = (event.clientY - rect.box.top - rect.y) / rect.h;
  return { x, y, inside: x >= 0 && x <= 1 && y >= 0 && y <= 1 };
}

function onVideoMove(event) {
  const loupe = $("loupe");
  if (!state.calib) {
    loupe.hidden = true;
    return;
  }
  const video = $("review-video");
  const spot = eventToVideo(event);
  if (!spot.inside || !video.videoWidth) {
    loupe.hidden = true;
    return;
  }
  loupe.hidden = false;
  const rect = videoRect();
  const left = event.clientX - rect.box.left + 18;
  const top = event.clientY - rect.box.top - 184;
  loupe.style.left = `${Math.min(left, rect.box.width - 172)}px`;
  loupe.style.top = `${Math.max(top, 4)}px`;
  const ctx = loupe.getContext("2d");
  const span = 42;
  const sx = spot.x * video.videoWidth - span / 2;
  const sy = spot.y * video.videoHeight - span / 2;
  ctx.imageSmoothingEnabled = false;
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, 168, 168);
  ctx.drawImage(video, sx, sy, span, span, 0, 0, 168, 168);
  ctx.strokeStyle = "#e36b4a";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(84, 0); ctx.lineTo(84, 168);
  ctx.moveTo(0, 84); ctx.lineTo(168, 84);
  ctx.stroke();
}

function onVideoClick(event) {
  const video = $("review-video");
  if (!state.calib) {
    if (video.paused) video.play().catch(() => {});
    else video.pause();
    return;
  }
  event.preventDefault();
  const spot = eventToVideo(event);
  if (!spot.inside) return;
  const calib = state.calib;
  const step = calib.steps[calib.index];
  calib.points[step.key] = { x: spot.x, y: spot.y };
  calib.index += 1;
  drawOverlay(currentDelivery());
  if (calib.index < calib.steps.length) {
    renderCalibBar();
    return;
  }
  submitCalibration();
}

async function submitCalibration() {
  const calib = state.calib;
  const body = { kind: calib.camera };
  if (calib.camera === "behind") {
    body.corners = ["c0", "c1", "c2", "c3"].map((key) => calib.points[key]);
  } else {
    for (const step of SIDE_STEPS) body[step.key] = calib.points[step.key];
    const distance = Number($("calib-distance")?.value);
    if (distance) body.distance_m = distance;
  }
  try {
    state.session = await postJSON(`/api/sessions/${state.session.id}/calibration/${calib.camera}`, body);
    state.calib = null;
    renderReview();
    setStatus("Camera calibrated. It stays on the lane for the next session. Find the balls again to use it.");
  } catch (error) {
    calib.index -= 1;
    renderCalibBar();
    setStatus(error.message);
  }
}

// ---------------------------------------------------------------- ball actions

async function sessionJob(kind) {
  try {
    state.session = await postJSON(`/api/sessions/${state.session.id}/${kind}`);
    renderReview();
    watchSession();
  } catch (error) {
    setStatus(error.message);
  }
}

async function trackBall(delivery) {
  if (!delivery) return;
  setStatus(`Tracking ball ${delivery.index}…`);
  try {
    const updated = await postJSON(`/api/sessions/${state.session.id}/deliveries/${delivery.id}/track?camera=${state.reviewCamera}`);
    rememberDelivery(updated);
    renderReview();
    const reading = updated.tracks?.[state.reviewCamera];
    setStatus(reading?.confidence === "none" ? reading.note : updated.pitch?.text || "Ball tracked");
  } catch (error) {
    setStatus(error.message);
  }
}

async function markHere() {
  const video = $("review-video");
  const offset = state.reviewCamera === "behind" ? Number(state.session.clock_offset || 0) : 0;
  const moment = Math.max(0, video.currentTime - offset);
  try {
    const added = await postJSON(`/api/sessions/${state.session.id}/deliveries`, {
      t0: Math.max(0, moment - 2.0),
      t1: moment + 1.5,
    });
    state.session = await api(`/api/sessions/${state.session.id}`);
    state.deliveryId = added.id;
    renderReview();
    setStatus(`Ball ${added.index} marked. Track it to measure it.`);
  } catch (error) {
    setStatus(error.message);
  }
}

async function removeBall(delivery) {
  if (!delivery || !window.confirm(`Remove ball ${delivery.index}?`)) return;
  try {
    state.session = await api(`/api/sessions/${state.session.id}/deliveries/${delivery.id}`, { method: "DELETE" });
    state.deliveryId = state.session.deliveries[0]?.id || null;
    renderReview();
  } catch (error) {
    setStatus(error.message);
  }
}

async function setBatter(batter) {
  try {
    state.session = await api(`/api/sessions/${state.session.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batter }),
    });
    renderReview();
  } catch (error) {
    setStatus(error.message);
  }
}

async function markOutcome(delivery, name) {
  try {
    const updated = await postJSON(`/api/sessions/${state.session.id}/deliveries/${delivery.id}/outcome`, { outcome: name });
    rememberDelivery(updated);
    renderReview();
    setStatus(name ? `Marked ${name}` : "Outcome cleared");
  } catch (error) {
    setStatus(error.message);
  }
}

async function savePace(delivery, source = "manual") {
  if (!delivery) return;
  const value = $("pace-input").value;
  try {
    const updated = await postJSON(`/api/sessions/${state.session.id}/deliveries/${delivery.id}/pace`, {
      pace_kph: value === "" ? null : Number(value),
      source,
    });
    rememberDelivery(updated);
    renderReview();
    setStatus(updated.pace_kph ? `${updated.pace_kph} km/h saved` : "Pace back to the cameras");
  } catch (error) {
    setStatus(error.message);
  }
}

async function applyRadar(delivery) {
  try {
    const reading = await api("/api/radar");
    if (!reading.pace_kph) {
      setStatus("No radar reading yet. Post one to /api/radar, or set VANSH_RADAR_PORT.");
      return;
    }
    $("pace-input").value = reading.pace_kph;
    await savePace(delivery, "radar");
  } catch (error) {
    setStatus(error.message);
  }
}

async function sendMachine(delivery) {
  if (!delivery?.pitch) {
    setStatus("Track the ball or mark it on the pitch map first.");
    return;
  }
  const line = delivery.pitch.line || "outside off";
  try {
    await postJSON("/api/machine", {
      pace_kph: delivery.pace_kph ?? null,
      line,
      length: delivery.pitch.length,
      batter: state.session.batter || "right",
      note: `${state.session.name}, ball ${delivery.index}`,
      session_id: state.session.id,
      delivery_id: delivery.id,
    });
    setStatus(delivery.pitch.line ? "Ball added to the machine queue" : "Ball added. No camera saw the line, so it aims just outside off.");
    showView("machine");
  } catch (error) {
    setStatus(error.message);
  }
}

async function queueDrill(sessionId) {
  try {
    const plan = await postJSON("/api/drill", { session_id: sessionId || null });
    setStatus(plan.reason);
    showView("machine");
  } catch (error) {
    setStatus(error.message);
  }
}

async function deleteSession(id) {
  if (!window.confirm("Delete this session and its video?")) return;
  try {
    await api(`/api/sessions/${id}`, { method: "DELETE" });
    state.session = null;
    state.deliveryId = null;
    $("review").innerHTML = '<p class="empty">Session deleted.</p>';
    refreshSessions();
  } catch (error) {
    setStatus(error.message);
  }
}

async function readAction(delivery) {
  const video = $("review-video");
  if (!video || !delivery) return;
  setStatus("Loading the pose model…");
  try {
    if (!state.clipLandmarker) state.clipLandmarker = await makeLandmarker("full");
  } catch {
    setStatus("The pose model is not available in this browser.");
    return;
  }
  const offset = state.reviewCamera === "behind" ? Number(state.session.clock_offset || 0) : 0;
  const start = Math.max(0, delivery.t0 + offset);
  const span = Math.max(0.2, delivery.t1 - delivery.t0);
  const count = Math.min(90, Math.max(12, Math.round(span * 25)));
  const frames = [];
  video.pause();
  for (let index = 0; index < count; index += 1) {
    const moment = start + (span * index) / (count - 1);
    await seekVideo(video, moment);
    state.poseClock += 40;
    const result = state.clipLandmarker.detectForVideo(video, state.poseClock);
    const pose = pickPose(result.landmarks);
    frames.push({ t: +(moment - start).toFixed(3), points: pose ? toPayload(pose) : [] });
    if (index % 6 === 0) setStatus(`Reading the action… ${Math.round((index / count) * 100)}%`);
  }
  try {
    const updated = await postJSON(`/api/sessions/${state.session.id}/deliveries/${delivery.id}/pose`, { frames });
    rememberDelivery(updated);
    renderReview();
    setStatus(updated.pose_note || "Action read");
  } catch (error) {
    setStatus(error.message);
  }
}

function seekVideo(video, time) {
  return new Promise((resolve) => {
    const target = Math.min(Math.max(time, 0), Math.max((video.duration || time) - 0.04, 0));
    if (Math.abs(video.currentTime - target) < 0.01) {
      resolve();
      return;
    }
    const done = () => {
      video.removeEventListener("seeked", done);
      resolve();
    };
    video.addEventListener("seeked", done);
    video.currentTime = target;
  });
}

// ---------------------------------------------------------------- pitch map

function mapX(offset) {
  const mid = (MAP.left + MAP.right) / 2;
  return mid + (offset / MAP.halfWidth) * ((MAP.right - MAP.left) / 2);
}

function mapY(length) {
  return MAP.top + (Math.min(Math.max(length, -0.5), MAP.maxLength) / MAP.maxLength) * (MAP.bottom - MAP.top);
}

function pitchMapSvg() {
  const bands = LENGTH_BANDS.map(([from, to, name], index) => {
    const y0 = mapY(from);
    const y1 = mapY(to);
    return `<rect x="${mapX(-1.525)}" y="${y0}" width="${mapX(1.525) - mapX(-1.525)}" height="${y1 - y0}" fill="${index % 2 ? "#c9a764" : "#d6b875"}"></rect>
      <text x="${MAP.right + 2}" y="${(y0 + y1) / 2 + 3}" class="band">${name}</text>`;
  }).join("");
  const stumps = [-0.0953, 0, 0.0953].map((x) => `<circle cx="${mapX(x)}" cy="${mapY(0)}" r="1.6" fill="#f4ecd8"></circle>`).join("");
  return `<svg id="pitch" viewBox="0 0 262 300" role="img" aria-label="Pitch map seen from behind the bowler">
    <rect x="0" y="0" width="262" height="300" fill="#1e3a24"></rect>
    ${bands}
    <line x1="${mapX(-1.83)}" y1="${mapY(1.22)}" x2="${mapX(1.83)}" y2="${mapY(1.22)}" stroke="#f4ecd8" stroke-width="0.8"></line>
    <line x1="${mapX(-1.32)}" y1="${mapY(0)}" x2="${mapX(1.32)}" y2="${mapY(0)}" stroke="#f4ecd8" stroke-width="0.6"></line>
    <line x1="${mapX(-1.32)}" y1="${mapY(-0.5)}" x2="${mapX(-1.32)}" y2="${mapY(1.22)}" stroke="#f4ecd8" stroke-width="0.6"></line>
    <line x1="${mapX(1.32)}" y1="${mapY(-0.5)}" x2="${mapX(1.32)}" y2="${mapY(1.22)}" stroke="#f4ecd8" stroke-width="0.6"></line>
    <rect x="${mapX(-0.15)}" y="${mapY(0)}" width="${mapX(0.15) - mapX(-0.15)}" height="${mapY(14) - mapY(0)}" fill="rgba(244,236,216,0.10)"></rect>
    ${stumps}
    <g id="pitch-balls"></g>
  </svg>`;
}

function drawPitchMap(session, current) {
  const group = $("pitch-balls");
  if (!group) return;
  const parts = [];
  for (const item of session.deliveries) {
    const pitch = item.pitch;
    if (!pitch) continue;
    const colour = item.outcome ? OUTCOME_COLOURS[item.outcome] : "#f4ecd8";
    const y = mapY(pitch.length_m);
    const isCurrent = current && item.id === current.id;
    if (pitch.offset_m == null) {
      parts.push(`<line x1="${mapX(-1.4)}" y1="${y}" x2="${mapX(1.4)}" y2="${y}" stroke="${colour}" stroke-width="${isCurrent ? 2.4 : 1.2}" stroke-dasharray="4 3" opacity="${isCurrent ? 1 : 0.7}"></line>`);
    } else {
      parts.push(`<circle cx="${mapX(pitch.offset_m)}" cy="${y}" r="${isCurrent ? 5 : 3.4}" fill="${colour}" stroke="${isCurrent ? "#10160e" : "none"}" stroke-width="1.5" opacity="${item.confidence === "low" ? 0.55 : 0.95}"></circle>`);
    }
  }
  group.innerHTML = parts.join("");
}

async function markPitch(event, delivery) {
  if (!delivery || !state.session) return;
  const svg = $("pitch");
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const mapped = point.matrixTransform(svg.getScreenCTM().inverse());
  const length = ((mapped.y - MAP.top) / (MAP.bottom - MAP.top)) * MAP.maxLength;
  const offset = ((mapped.x - (MAP.left + MAP.right) / 2) / ((MAP.right - MAP.left) / 2)) * MAP.halfWidth;
  if (length < -0.5 || length > MAP.maxLength || Math.abs(offset) > 2.5) return;
  try {
    const updated = await postJSON(`/api/sessions/${state.session.id}/deliveries/${delivery.id}/pitch`, {
      length_m: +length.toFixed(2),
      offset_m: +offset.toFixed(3),
    });
    rememberDelivery(updated);
    renderReview();
    setStatus(updated.pitch.text);
  } catch (error) {
    setStatus(error.message);
  }
}

async function clearMark(delivery) {
  if (!delivery) return;
  try {
    const updated = await postJSON(`/api/sessions/${state.session.id}/deliveries/${delivery.id}/pitch`, { clear: true });
    rememberDelivery(updated);
    renderReview();
    setStatus(updated.pitch ? `Back to the cameras: ${updated.pitch.text}` : "No camera mark for this ball yet.");
  } catch (error) {
    setStatus(error.message);
  }
}

// ---------------------------------------------------------------- uploads and reels

$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (needCoach()) return;
  const form = event.target;
  const body = new FormData(form);
  if (!form.side.files.length) body.delete("side");
  if (!form.behind.files.length) body.delete("behind");
  if (!form.side.files.length && !form.behind.files.length) {
    setStatus("Choose a video from at least one camera.");
    return;
  }
  setStatus("Uploading…");
  try {
    state.session = await api("/api/sessions", { method: "POST", body });
    state.deliveryId = null;
    state.reviewCamera = state.session.cameras.side ? "side" : "behind";
    renderReview();
    watchSession();
    refreshSessions();
    setStatus("Uploaded. Finding the balls…");
  } catch (error) {
    setStatus(error.message);
  }
});

$("camera-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (needCoach()) return;
  const form = new FormData(event.target);
  try {
    state.session = await postJSON("/api/cameras", {
      name: form.get("name"),
      side_url: form.get("side_url"),
      behind_url: form.get("behind_url") || null,
      seconds: Number(form.get("seconds")),
    });
    state.reviewCamera = "side";
    renderReview();
    watchSession();
    setStatus("Recording both cameras together…");
  } catch (error) {
    setStatus(error.message);
  }
});

document.querySelectorAll("[data-demo]").forEach((button) => {
  button.addEventListener("click", async () => {
    if (needCoach()) return;
    setStatus("Loading the raw reel…");
    try {
      state.session = await postJSON(`/api/demo/${button.dataset.demo}`);
      state.deliveryId = null;
      state.reviewCamera = state.session.cameras.side ? "side" : "behind";
      renderReview();
      watchSession();
      refreshSessions();
    } catch (error) {
      setStatus(error.message);
    }
  });
});

// ---------------------------------------------------------------- the machine

async function refreshMachine() {
  const data = await api("/api/machine");
  fillSelect($("machine-line"), data.lines, "outside off");
  fillSelect($("machine-length"), data.lengths, "good");
  const modeText = {
    simulator: "Simulator: balls go straight to the bowled list. Set VANSH_MACHINE for a real machine.",
    edge: "Edge: balls wait for the bridge at the nets (python -m crickcoach.edge machine --armed).",
  };
  $("machine-mode").textContent = modeText[data.mode] || `Machine at ${data.mode}.`;
  const pace = data.radar?.pace_kph ? `${data.radar.pace_kph} km/h` : "no reading";
  $("radar-line").textContent = `Radar: ${pace}.`;
  renderBalls($("machine-list"), data.items, "The queue is empty.", true);
  renderBalls($("request-list"), data.requests || [], data.mode === "edge" ? "Nothing waiting at the nets." : "Only used with an edge bridge.", false);
  renderBalls($("bowled-list"), [...(data.bowled || [])].reverse(), "Nothing bowled yet.", false);
}

function fillSelect(select, values, preferred) {
  if (select.options.length === values.length) return;
  select.replaceChildren(...values.map((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    option.selected = value === preferred;
    return option;
  }));
}

function renderBalls(root, items, empty, removable) {
  root.replaceChildren();
  if (!items.length) {
    root.innerHTML = `<p class="empty">${esc(empty)}</p>`;
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "ball-row";
    const pace = item.pace_kph ? `${item.pace_kph} km/h` : "pace open";
    const aim = item.length_m != null ? ` · aim ${Number(item.length_m).toFixed(1)} m, ${Number(item.offset_m ?? 0) >= 0 ? "+" : ""}${Number(item.offset_m ?? 0).toFixed(2)} m` : "";
    const via = item.via ? ` · ${item.via}` : "";
    row.innerHTML = `<div><strong>${esc(item.length)} on ${esc(item.line)}</strong><span>${esc(pace)}${esc(aim)}${esc(via)}${item.note ? ` · ${esc(item.note)}` : ""}</span></div>`;
    if (removable) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Remove";
      remove.addEventListener("click", async () => {
        await api(`/api/machine/${item.id}`, { method: "DELETE" });
        refreshMachine();
      });
      row.appendChild(remove);
    }
    root.appendChild(row);
  }
}

$("bowl-next").addEventListener("click", async () => {
  try {
    const bowled = await postJSON("/api/machine/bowl");
    setStatus(bowled.status === "sent to the nets" ? "Sent to the nets" : `Bowled ${bowled.length} on ${bowled.line} via ${bowled.via}`);
    refreshMachine();
  } catch (error) {
    setStatus(error.message);
  }
});

$("bowl-all").addEventListener("click", async () => {
  try {
    const result = await postJSON("/api/machine/bowl-all");
    setStatus(`${result.bowled.length} balls sent`);
    refreshMachine();
  } catch (error) {
    setStatus(error.message);
  }
});

$("queue-drill").addEventListener("click", () => queueDrill(null));

$("machine-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const pace = form.get("pace_kph");
  try {
    await postJSON("/api/machine", {
      pace_kph: pace === "" ? null : Number(pace),
      line: form.get("line"),
      length: form.get("length"),
      batter: form.get("batter"),
      note: form.get("note"),
    });
    event.target.note.value = "";
    refreshMachine();
    setStatus("Ball queued");
  } catch (error) {
    setStatus(error.message);
  }
});

// ---------------------------------------------------------------- start

loadMe();
loadSample("bowling-clean");
loadPose();
