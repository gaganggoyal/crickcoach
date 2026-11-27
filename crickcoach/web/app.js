const EDGES = [
  [11, 12], [11, 13], [13, 15], [12, 14], [14, 16],
  [11, 23], [12, 24], [23, 24],
  [23, 25], [25, 27], [24, 26], [26, 28],
  [0, 11], [0, 12],
];

const state = {
  role: "bowling",
  person: "largest",
  landmarker: null,
  poseClock: 0,
  streaming: false,
  liveSamples: [],
  sampleTimer: 0,
  raf: 0,
  session: null,
  deliveryId: null,
  reviewCamera: "side",
  calib: null,
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

async function api(url, options) {
  const response = await fetch(url, options);
  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { detail: text };
    }
  }
  if (!response.ok) {
    const detail = body && body.detail;
    const message = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => item.msg).join(" ")
        : "Request failed";
    throw new Error(message);
  }
  return body;
}

function renderCues(cues) {
  const root = $("cues");
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

function showView(name) {
  for (const id of ["lane", "sessions", "machine"]) {
    $(`view-${id}`).hidden = id !== name;
  }
  document.querySelectorAll("nav button").forEach((button) => {
    button.classList.toggle("on", button.dataset.view === name);
  });
  if (name === "sessions") refreshSessions();
  if (name === "machine") refreshMachine();
}

document.querySelectorAll("nav button").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.view));
});

document.querySelectorAll("#role-switch button").forEach((button) => {
  button.addEventListener("click", () => {
    state.role = button.dataset.role;
    document.querySelectorAll("#role-switch button").forEach((item) => {
      item.classList.toggle("on", item === button);
    });
    const sample = state.role === "batting" ? "batting-clean" : "bowling-clean";
    $("sample-clean").textContent = state.role === "batting" ? "Sample stance" : "Sample action";
    $("sample-faults").textContent = state.role === "batting" ? "Sample batting faults" : "Sample faults";
    $("stage-caption").textContent = state.role === "batting"
      ? "Front-on for the stance."
      : "Side-on for the bowling action.";
    if (!state.streaming) loadSample(sample);
  });
});

$("person").addEventListener("change", (event) => {
  state.person = event.target.value;
});

async function loadPose() {
  setStatus("Loading the pose model…");
  try {
    const vision = await import("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21");
    const files = await vision.FilesetResolver.forVisionTasks(
      "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21/wasm",
    );
    const model = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task";
    try {
      state.landmarker = await vision.PoseLandmarker.createFromOptions(files, poseOptions(model, "GPU"));
    } catch {
      state.landmarker = await vision.PoseLandmarker.createFromOptions(files, poseOptions(model, "CPU"));
    }
    setStatus("Pose model ready");
  } catch {
    setStatus("Pose model did not load. Samples and uploaded clips still work.");
  }
}

function poseOptions(model, delegate) {
  return {
    baseOptions: { modelAssetPath: model, delegate },
    runningMode: "VIDEO",
    numPoses: 2,
  };
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
  return points.map((point) => ({
    x: point.x,
    y: point.y,
    visibility: point.visibility ?? 1,
  }));
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
    const scale = Math.min(width / video.videoWidth, height / video.videoHeight);
    frame = {
      w: video.videoWidth * scale,
      h: video.videoHeight * scale,
      x: (width - video.videoWidth * scale) / 2,
      y: (height - video.videoHeight * scale) / 2,
    };
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

async function loadSample(name) {
  window.clearInterval(state.sampleTimer);
  try {
    const sample = await api(`/api/samples/${name}`);
    state.role = sample.role;
    renderCues(sample.cues);
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

$("sample-clean").addEventListener("click", () => {
  loadSample(state.role === "batting" ? "batting-clean" : "bowling-clean");
});
$("sample-faults").addEventListener("click", () => {
  loadSample(state.role === "batting" ? "batting-faults" : "bowling-faults");
});

async function listCameras() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  const devices = await navigator.mediaDevices.enumerateDevices();
  const select = $("devices");
  for (const device of devices.filter((item) => item.kind === "videoinput")) {
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
      const cutoff = now - 2.4;
      state.liveSamples = state.liveSamples.filter((frame) => frame.t >= cutoff);
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
    const result = await api("/api/live/cues", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: state.role, frames }),
    });
    renderCues(result.cues);
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
  if (state.liveSamples.length < 2) return;
  const first = state.liveSamples[0].t;
  const frames = state.liveSamples.map((frame) => ({ ...frame, t: +(frame.t - first).toFixed(3) }));
  try {
    const session = await api("/api/snapshots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: $("player-name").value, role: state.role, frames }),
    });
    state.session = session;
    state.deliveryId = session.deliveries[0].id;
    setStatus("Saved to the session log");
    showView("sessions");
    renderReview();
  } catch (error) {
    setStatus(error.message);
  }
});

async function refreshSessions() {
  const progress = await api("/api/progress");
  const progressLine = $("progress");
  if (progressLine) {
    const pace = progress.mean_pace_kph ? ` · mean ${progress.mean_pace_kph} km/h` : "";
    progressLine.textContent = progress.balls
      ? `${progress.balls} balls logged${pace}. ${progress.next_drill}`
      : "No balls logged yet.";
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
    card.innerHTML = `<div><strong>${esc(session.name)}</strong><span>${esc(session.role)} · ${session.deliveries} balls · ${esc(session.status)}</span></div>`;
    const open = document.createElement("button");
    open.type = "button";
    open.textContent = "Open";
    open.addEventListener("click", () => openSession(session.id));
    card.appendChild(open);
    root.appendChild(card);
  }
}

async function openSession(id) {
  state.session = await api(`/api/sessions/${id}`);
  state.deliveryId = state.session.deliveries[0]?.id || null;
  state.reviewCamera = "side";
  renderReview();
}

function renderReview() {
  const session = state.session;
  const root = $("review");
  if (!session) {
    root.innerHTML = '<p class="empty">Choose a session.</p>';
    return;
  }
  const delivery = session.deliveries.find((item) => item.id === state.deliveryId) || session.deliveries[0];
  const hasVideo = session.cameras && session.cameras[state.reviewCamera];
  root.innerHTML = `
    <div class="panel-head">
      <h2>${esc(session.name)}</h2>
      <span class="note">${esc(session.role)} · ${esc(session.status)}</span>
    </div>
    ${session.error ? `<p class="note">${esc(session.error)}</p>` : ""}
    ${hasVideo ? `<div class="video-box"><video id="review-video" controls playsinline src="/api/sessions/${session.id}/media/${state.reviewCamera}"></video><canvas id="track-overlay"></canvas></div>` : `<p class="empty">Saved from the live lane. This ball has cues and no video.</p>`}
    <div class="delivery-row" id="delivery-row"></div>
    <div id="review-cues"></div>
    <div class="pitch-wrap">
      <svg id="pitch" viewBox="0 0 80 200" role="img" aria-label="Pitch map">
        <rect x="8" y="6" width="64" height="188" rx="2" fill="#c6a15b"></rect>
        <rect x="14" y="92" width="52" height="42" fill="#d4b56e"></rect>
        <line x1="14" y1="22" x2="66" y2="22" stroke="#f4ecd8" stroke-width="1"></line>
        <line x1="14" y1="176" x2="66" y2="176" stroke="#f4ecd8" stroke-width="1"></line>
        <circle id="pitch-mark" cx="40" cy="100" r="4" fill="#e36b4a" visibility="hidden"></circle>
      </svg>
      <div class="pitch-copy">
        <p>Click where the ball pitched. The top is the bowler, the bottom is the batter. Off is on the left for a right-hander.</p>
        <p id="pitch-text" class="measure"></p>
        <label>Pace km/h <input id="pace-input" type="number" min="40" max="180" step="1"></label>
        <div class="delivery-row" id="outcomes"></div>
        <div class="buttons">
          <button type="button" id="read-action">Read the action</button>
          <button type="button" id="track-ball">Track the ball</button>
          <button type="button" id="calibrate">Calibrate camera</button>
          <button type="button" id="apply-radar">Apply radar</button>
          <button type="button" id="save-pace">Save pace</button>
          <button type="button" id="send-machine">Send to the machine</button>
          <button type="button" id="queue-this-drill">Queue the next drill</button>
          ${session.cameras && session.cameras.behind ? '<button type="button" id="swap-camera">Swap camera</button>' : ""}
          <button type="button" id="delete-session" class="ghost">Delete session</button>
        </div>
        <p id="review-note" class="note"></p>
      </div>
    </div>`;
  const row = $("delivery-row");
  for (const item of session.deliveries) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `Ball ${item.index}`;
    if (delivery && item.id === delivery.id) button.classList.add("on");
    button.addEventListener("click", () => {
      state.deliveryId = item.id;
      renderReview();
      playDelivery(item);
    });
    row.appendChild(button);
  }
  const cueRoot = document.createElement("div");
  cueRoot.className = "cues";
  if (delivery) {
    if (delivery.cues?.length) {
      for (const cue of delivery.cues) {
        const card = document.createElement("article");
        card.className = `cue ${cue.severity}`;
        card.innerHTML = `<p class="kicker">${esc(cue.severity)}</p><h3>${esc(cue.title)}</h3><p>${esc(cue.text)}</p>${cue.measure ? `<p class="measure">${esc(cue.measure)}</p>` : ""}`;
        cueRoot.appendChild(card);
      }
    } else {
      cueRoot.innerHTML = '<p class="empty">Read the action to measure this ball.</p>';
    }
    $("pitch-text").textContent = delivery.pitch ? delivery.pitch.text : "No pitch mark yet.";
    if (delivery.pitch && delivery.pitch.x != null) {
      const mark = $("pitch-mark");
      mark.setAttribute("cx", String(8 + delivery.pitch.x * 64));
      mark.setAttribute("cy", String(6 + delivery.pitch.y * 188));
      mark.setAttribute("visibility", "visible");
    }
    if (delivery.pace_kph) $("pace-input").value = delivery.pace_kph;
    const notes = [delivery.cut_note, delivery.pose_note, delivery.tracking?.note, delivery.outcome ? `Shot: ${delivery.outcome}` : ""].filter(Boolean).join(" ");
    $("review-note").textContent = notes;
    const outcomes = $("outcomes");
    for (const name of ["left", "defended", "attacked", "missed", "edged", "hit"]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = name;
      if (delivery.outcome === name) button.classList.add("on");
      button.addEventListener("click", () => markOutcome(delivery, name, button));
      outcomes.appendChild(button);
    }
  }
  $("review-cues").replaceWith(cueRoot);
  cueRoot.id = "review-cues";
  $("pitch").addEventListener("click", (event) => markPitch(event, delivery));
  $("read-action").addEventListener("click", () => readAction(delivery));
  $("track-ball").addEventListener("click", () => trackBall(delivery));
  $("calibrate").addEventListener("click", () => startCalibration());
  $("apply-radar").addEventListener("click", () => applyRadar(delivery));
  $("save-pace").addEventListener("click", () => savePace(delivery));
  $("send-machine").addEventListener("click", () => sendMachine());
  $("queue-this-drill").addEventListener("click", () => queueDrill());
  $("delete-session").addEventListener("click", () => deleteSession(session.id));
  const swap = $("swap-camera");
  if (swap) {
    swap.addEventListener("click", () => {
      state.reviewCamera = state.reviewCamera === "side" ? "behind" : "side";
      renderReview();
    });
  }
  const reviewVideo = $("review-video");
  if (reviewVideo) reviewVideo.addEventListener("click", (event) => onVideoClick(event, reviewVideo));
  if (delivery && hasVideo) {
    playDelivery(delivery);
    const overlay = $("track-overlay");
    const draw = () => drawTrack(delivery);
    reviewVideo.addEventListener("loadedmetadata", draw);
    if (reviewVideo.readyState >= 1) draw();
  }
}

function playDelivery(delivery) {
  const video = $("review-video");
  if (!video || delivery.camera === "live") return;
  const start = () => {
    video.currentTime = delivery.t0;
    video.play().catch(() => {});
  };
  video.onended = null;
  video.ontimeupdate = () => {
    if (video.currentTime >= delivery.t1) video.pause();
  };
  if (video.readyState >= 1) start();
  else video.onloadedmetadata = start;
}

async function markPitch(event, delivery) {
  if (!delivery || !state.session) return;
  const svg = $("pitch");
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const mapped = point.matrixTransform(svg.getScreenCTM().inverse());
  const x = Math.min(1, Math.max(0, (mapped.x - 8) / 64));
  const y = Math.min(1, Math.max(0, (mapped.y - 6) / 188));
  try {
    const updated = await api(`/api/sessions/${state.session.id}/deliveries/${delivery.id}/pitch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ x, y }),
    });
    rememberDelivery(updated);
    $("pitch-text").textContent = updated.pitch.text;
    const mark = $("pitch-mark");
    mark.setAttribute("cx", String(8 + updated.pitch.x * 64));
    mark.setAttribute("cy", String(6 + updated.pitch.y * 188));
    mark.setAttribute("visibility", "visible");
    setStatus(updated.pitch.text);
  } catch (error) {
    setStatus(error.message);
  }
}

async function savePace(delivery) {
  if (!delivery) return;
  const value = $("pace-input").value;
  try {
    const updated = await api(`/api/sessions/${state.session.id}/deliveries/${delivery.id}/pace`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pace_kph: value === "" ? null : Number(value) }),
    });
    rememberDelivery(updated);
    setStatus(updated.pace_kph ? `${updated.pace_kph} km/h saved` : "Pace cleared");
  } catch (error) {
    setStatus(error.message);
  }
}

function currentDelivery() {
  if (!state.session) return null;
  return state.session.deliveries.find((item) => item.id === state.deliveryId) || null;
}

async function sendMachine() {
  const delivery = currentDelivery();
  if (!delivery?.pitch?.line) {
    setStatus("Set the line on the pitch map. A side camera measures length only.");
    return;
  }
  const pace = $("pace-input").value;
  try {
    await api("/api/machine", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pace_kph: pace === "" ? null : Number(pace),
        line: delivery.pitch.line,
        length: delivery.pitch.length,
        note: state.session.name,
        session_id: state.session.id,
        delivery_id: delivery.id,
      }),
    });
    setStatus("Ball added to the machine queue");
    showView("machine");
  } catch (error) {
    setStatus(error.message);
  }
}

function rememberDelivery(updated) {
  state.session.deliveries = state.session.deliveries.map((item) => item.id === updated.id ? updated : item);
}

function replaceDelivery(updated) {
  rememberDelivery(updated);
  renderReview();
}

async function readAction(delivery) {
  const video = $("review-video");
  if (!video) {
    setStatus("This ball has no video to read.");
    return;
  }
  if (!state.landmarker) {
    setStatus("The pose model is not available in this browser.");
    return;
  }
  setStatus("Reading the action…");
  const frames = [];
  const count = 8;
  const span = Math.max(0.05, delivery.t1 - delivery.t0);
  for (let index = 0; index < count; index += 1) {
    const moment = delivery.t0 + (span * index) / (count - 1);
    await seekVideo(video, moment);
    state.poseClock += 40;
    const result = state.landmarker.detectForVideo(video, state.poseClock);
    const pose = pickPose(result.landmarks);
    frames.push({ t: moment, points: pose ? toPayload(pose) : [] });
  }
  try {
    const updated = await api(`/api/sessions/${state.session.id}/deliveries/${delivery.id}/pose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frames }),
    });
    replaceDelivery(updated);
    setStatus(updated.pose_note || "Action read");
  } catch (error) {
    setStatus(error.message);
  }
}

function seekVideo(video, time) {
  return new Promise((resolve) => {
    const target = Math.min(Math.max(time, 0), Math.max((video.duration || time) - 0.04, 0));
    if (Math.abs(video.currentTime - target) < 0.03) {
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

const SIDE_STEPS = [
  { key: "bowling", text: "Click the bowling crease where it meets the pitch." },
  { key: "batting", text: "Click the batting crease where it meets the pitch." },
  { key: "stump_top", text: "Click the top of the off stump." },
];
const BEHIND_STEPS = [
  { key: "c0", text: "Click the off-side corner at the bowler's end." },
  { key: "c1", text: "Click the leg-side corner at the bowler's end." },
  { key: "c2", text: "Click the leg-side corner at the batter's end." },
  { key: "c3", text: "Click the off-side corner at the batter's end." },
];

function startCalibration() {
  const video = $("review-video");
  if (!video) {
    setStatus("Calibration needs a video.");
    return;
  }
  video.pause();
  const behind = state.reviewCamera === "behind";
  state.calib = { camera: state.reviewCamera, steps: behind ? BEHIND_STEPS : SIDE_STEPS, index: 0, points: {} };
  setStatus(state.calib.steps[0].text);
}

function onVideoClick(event, video) {
  if (!state.calib) return;
  event.preventDefault();
  const rect = video.getBoundingClientRect();
  const point = {
    x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
    y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
  };
  const step = state.calib.steps[state.calib.index];
  state.calib.points[step.key] = point;
  state.calib.index += 1;
  if (state.calib.index < state.calib.steps.length) {
    setStatus(state.calib.steps[state.calib.index].text);
    return;
  }
  submitCalibration();
}

async function submitCalibration() {
  const calib = state.calib;
  state.calib = null;
  const body = { kind: calib.camera === "behind" ? "behind" : "side" };
  if (body.kind === "behind") {
    body.corners = [calib.points.c0, calib.points.c1, calib.points.c2, calib.points.c3];
  } else {
    body.bowling = calib.points.bowling;
    body.batting = calib.points.batting;
    body.stump_top = calib.points.stump_top;
  }
  try {
    state.session = await api(`/api/sessions/${state.session.id}/calibration/${calib.camera}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setStatus("Camera calibrated. Track the ball.");
  } catch (error) {
    setStatus(error.message);
  }
}

async function trackBall(delivery) {
  if (!delivery) return;
  setStatus("Tracking the ball…");
  try {
    const updated = await api(`/api/sessions/${state.session.id}/deliveries/${delivery.id}/track?camera=${state.reviewCamera}`, { method: "POST" });
    replaceDelivery(updated);
    const pace = updated.tracking?.pace_kph ? `${updated.tracking.pace_kph} km/h` : "pace not read";
    setStatus(`${updated.tracking?.pitch?.text || "Ball tracked"} · ${pace}`);
  } catch (error) {
    setStatus(error.message);
  }
}

async function markOutcome(delivery, name, button) {
  try {
    const updated = await api(`/api/sessions/${state.session.id}/deliveries/${delivery.id}/outcome`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outcome: name }),
    });
    rememberDelivery(updated);
    document.querySelectorAll("#outcomes button").forEach((item) => item.classList.toggle("on", item === button));
    setStatus(`Marked ${name}`);
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
    await savePace(delivery);
  } catch (error) {
    setStatus(error.message);
  }
}

async function queueDrill() {
  try {
    const plan = await api("/api/drill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.session ? state.session.id : null }),
    });
    setStatus(plan.reason);
    showView("machine");
  } catch (error) {
    setStatus(error.message);
  }
}

function drawTrack(delivery) {
  const canvas = $("track-overlay");
  const video = $("review-video");
  const points = delivery?.tracking?.points || [];
  if (!canvas || !video || !points.length) return;
  const rect = video.getBoundingClientRect();
  canvas.width = Math.max(2, rect.width);
  canvas.height = Math.max(2, rect.height);
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#f4ecd8";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = point.x * canvas.width;
    const y = point.y * canvas.height;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  const last = points[points.length - 1];
  ctx.fillStyle = "#e36b4a";
  ctx.beginPath();
  ctx.arc(last.x * canvas.width, last.y * canvas.height, 5, 0, Math.PI * 2);
  ctx.fill();
}

async function deleteSession(id) {
  if (!window.confirm("Delete this session and its video?")) return;
  await api(`/api/sessions/${id}`, { method: "DELETE" });
  state.session = null;
  state.deliveryId = null;
  $("review").innerHTML = '<p class="empty">Session deleted.</p>';
  refreshSessions();
}

$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const body = new FormData(form);
  setStatus("Cutting the video into deliveries…");
  try {
    state.session = await api("/api/sessions", { method: "POST", body });
    state.deliveryId = state.session.deliveries[0]?.id || null;
    setStatus(state.session.status === "ready" ? `${state.session.deliveries.length} deliveries` : state.session.error);
    renderReview();
    refreshSessions();
  } catch (error) {
    setStatus(error.message);
  }
});

$("camera-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const payload = {
    name: form.get("name"),
    role: form.get("role"),
    side_url: form.get("side_url"),
    behind_url: form.get("behind_url") || null,
    seconds: Number(form.get("seconds")),
  };
  try {
    const session = await api("/api/cameras", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setStatus("Recording the cameras…");
    await watchRecording(session.id);
  } catch (error) {
    setStatus(error.message);
  }
});

async function watchRecording(id) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const session = await api(`/api/sessions/${id}`);
    if (session.status === "ready" || session.status === "error") {
      state.session = session;
      state.deliveryId = session.deliveries[0]?.id || null;
      setStatus(session.status === "ready" ? `${session.deliveries.length} deliveries` : session.error);
      renderReview();
      refreshSessions();
      return;
    }
    setStatus(session.status === "recording" ? "Recording the cameras…" : "Cutting the deliveries…");
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  setStatus("The recording is still running. Open it from the list in a moment.");
}

$("demo-tracked").addEventListener("click", () => loadRaw("/api/demo/tracked", "side"));
$("demo-behind").addEventListener("click", () => loadRaw("/api/demo/behind", "behind"));

async function loadRaw(url, camera) {
  setStatus("Reading the raw reel…");
  try {
    state.session = await api(url, { method: "POST" });
    state.deliveryId = state.session.deliveries[0]?.id || null;
    state.reviewCamera = camera;
    const delivery = state.session.deliveries[0];
    setStatus(delivery?.tracking?.pitch?.text || delivery?.cut_note || state.session.error || "Reel ready");
    renderReview();
    refreshSessions();
  } catch (error) {
    setStatus(error.message);
  }
}

$("demo-session").addEventListener("click", async () => {
  setStatus("Cutting the raw side reel…");
  try {
    state.session = await api("/api/demo/session", { method: "POST" });
    state.deliveryId = state.session.deliveries[0]?.id || null;
    state.reviewCamera = "side";
    setStatus(`${state.session.deliveries.length} deliveries in the raw side reel`);
    renderReview();
    refreshSessions();
  } catch (error) {
    setStatus(error.message);
  }
});

async function refreshMachine() {
  const data = await api("/api/machine");
  const radar = $("radar-line");
  if (radar) {
    const pace = data.radar?.pace_kph ? `${data.radar.pace_kph} km/h` : "no reading";
    radar.textContent = `Machine: ${data.mode}. Radar: ${pace}.`;
  }
  const root = $("machine-list");
  root.replaceChildren();
  if (!data.items.length) {
    root.innerHTML = '<p class="empty">The queue is empty.</p>';
  }
  for (const item of data.items) {
    const row = document.createElement("div");
    row.className = "ball-row";
    const pace = item.pace_kph ? `${item.pace_kph} km/h` : "pace open";
    row.innerHTML = `<div><strong>${esc(item.length)} on ${esc(item.line)}</strong><span>${esc(pace)}${item.note ? ` · ${esc(item.note)}` : ""}</span></div>`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", async () => {
      await api(`/api/machine/${item.id}`, { method: "DELETE" });
      refreshMachine();
    });
    row.appendChild(remove);
    root.appendChild(row);
  }
  const bowled = $("bowled-list");
  if (!bowled) return;
  bowled.replaceChildren();
  if (!data.bowled?.length) {
    bowled.innerHTML = '<p class="empty">Nothing bowled yet.</p>';
    return;
  }
  for (const item of data.bowled) {
    const row = document.createElement("div");
    row.className = "ball-row";
    const pace = item.pace_kph ? `${item.pace_kph} km/h` : "pace open";
    row.innerHTML = `<div><strong>${esc(item.length)} on ${esc(item.line)}</strong><span>${esc(pace)} · ${esc(item.via || "")}</span></div>`;
    bowled.appendChild(row);
  }
}

$("bowl-next").addEventListener("click", async () => {
  try {
    const bowled = await api("/api/machine/bowl", { method: "POST" });
    setStatus(`Bowled ${bowled.length} on ${bowled.line} via ${bowled.via}`);
    refreshMachine();
  } catch (error) {
    setStatus(error.message);
  }
});

$("bowl-all").addEventListener("click", async () => {
  try {
    const result = await api("/api/machine/bowl-all", { method: "POST" });
    setStatus(`Bowled ${result.bowled.length} balls`);
    refreshMachine();
  } catch (error) {
    setStatus(error.message);
  }
});

$("queue-drill").addEventListener("click", () => queueDrill());

$("machine-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const pace = form.get("pace_kph");
  try {
    await api("/api/machine", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pace_kph: pace === "" ? null : Number(pace),
        line: form.get("line"),
        length: form.get("length"),
        note: form.get("note"),
      }),
    });
    event.target.reset();
    refreshMachine();
    setStatus("Ball queued");
  } catch (error) {
    setStatus(error.message);
  }
});

loadPose();
loadSample("bowling-clean");
