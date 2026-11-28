// The two tools in the setup guide: where to put a camera, and its RTSP address.
// Both run only in this browser. Nothing typed here is sent anywhere.

const BALL_M = 0.072;          // a cricket ball's diameter
const HALF_PITCH_M = 10.06;    // middle of the pitch to either set of stumps
const SPARE_M = 1.5;           // room to keep beyond each set of stumps
const WORK_WIDTH = 1280;       // VANSH reads pictures at up to 1280 pixels wide
const RAD = Math.PI / 180;

const LENSES = [
  ["2.8 mm, about 100°", 100],
  ["4 mm, about 84°", 84],
  ["6 mm, about 54°", 54],
  ["8 mm, about 40°", 40],
  ["12 mm, about 27°", 27],
];

const DEFAULTS = {
  side: { fov: 84, distance: 13, height: 2.5 },
  behind: { fov: 50, distance: 8, height: 6 },
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

function field(label, input) {
  return el("label", {}, label, input);
}

function numberInput(name, value, min, max, step) {
  return el("input", { name, type: "number", value: String(value), min: String(min), max: String(max), step: String(step) });
}

function select(name, options, chosen) {
  const node = el("select", { name });
  for (const [label, value] of options) {
    const option = el("option", { value: String(value), text: label });
    if (String(value) === String(chosen)) option.selected = true;
    node.append(option);
  }
  return node;
}

function result(tone, title, figure, text) {
  return el("div", { class: `result ${tone}` }, el("b", { text: title }), el("span", { class: "figure", text: figure }), el("span", { text }));
}

// ------------------------------------------------------------------ placement

function sideReport(o) {
  const t = Math.tan((o.fov * RAD) / 2);
  const reach = o.distance * t;
  const spare = reach - HALF_PITCH_M;
  const needDistance = (HALF_PITCH_M + SPARE_M) / t;
  const needFov = (2 * Math.atan((HALF_PITCH_M + SPARE_M) / o.distance)) / RAD;
  const ppm = Math.min(o.width, WORK_WIDTH) / 2 / t / o.distance;
  const out = [];
  if (spare < 0.3) {
    out.push(result("fix", "Both sets of stumps", `${(2 * reach).toFixed(1)} m wide`,
      `They do not both fit. Move back to at least ${needDistance.toFixed(1)} m, or widen the view to ${Math.ceil(needFov)}° or more.`));
  } else if (spare < SPARE_M) {
    out.push(result("watch", "Both sets of stumps", `${spare.toFixed(1)} m spare`,
      `They fit, but keep about 1.5 m beyond each set so the release and the batter stay in view: ${needDistance.toFixed(1)} m back.`));
  } else if (reach > 2 * (HALF_PITCH_M + SPARE_M)) {
    out.push(result("watch", "Both sets of stumps", `${Math.round((100 * HALF_PITCH_M) / reach)}% of the width`,
      `The pitch fills only part of the picture, so the ball is small. Move in to about ${needDistance.toFixed(1)} m, or zoom in.`));
  } else {
    out.push(result("ok", "Both sets of stumps", `${spare.toFixed(1)} m spare`,
      "Both fit, with room for the release and the batter at each end."));
  }
  // The benchmark's side camera saw a ball of about 3.7 px and found the length on 12 of 12.
  out.push(ballSize(BALL_M * ppm, o.width, "along the pitch", 3.5, 2.5));
  out.push(blur(o));
  out.push(frames(o));
  if (o.height < 1.5) {
    out.push(result("watch", "Height", `${o.height} m`, "Low: players and the near netting get in the way. 2 to 3 m is best."));
  } else if (o.height > 4) {
    out.push(result("watch", "Height", `${o.height} m`, "High works, as long as the camera stays square to the middle of the pitch."));
  } else {
    out.push(result("ok", "Height", `${o.height} m`, "Sees over the bottom of the netting without looking down steeply."));
  }
  if (o.fov > 95) {
    out.push(result("watch", "Lens", `${o.fov}°`,
      "Very wide lenses bend straight lines near the edges, which VANSH does not correct yet. 70 to 90° from 12 to 16 m is better."));
  }
  return out;
}

function behindReport(o) {
  const B = o.distance;
  const H = o.height;
  const half = (o.fov * RAD) / 2;
  const tall = 2 * Math.atan(Math.tan(half) * (9 / 16));
  const nearDown = Math.atan(H / (B + 1.22));
  const farDown = Math.atan((H - 0.71) / (B + 20.12));
  const need = (nearDown - farDown) * 1.12;
  const focal = Math.min(o.width, WORK_WIDTH) / 2 / Math.tan(half);
  const out = [];
  if (tall >= need) {
    out.push(result("ok", "Both popping creases", `tilt ${Math.round(((nearDown + farDown) / 2) / RAD)}° down`,
      "Both fit. Aim so the near popping crease sits just above the bottom of the picture and the far stumps near the top."));
  } else {
    const wider = (2 * Math.atan(Math.tan(need / 2) * (16 / 9))) / RAD;
    out.push(result("fix", "Both popping creases", `${Math.round(tall / RAD)}° tall`,
      `The picture needs ${Math.round(need / RAD)}° from top to bottom to hold both creases. Widen the view to about ${Math.ceil(wider)}°, or move further back.`));
  }
  // Where the view line to a short ball, 4 m down the pitch and 1.3 m up, passes over the bowler.
  const clearance = H - ((H - 1.3) * (B + 1.0)) / (B + 4.0);
  // In the simulator, 2.5 m kept every short ball in view; 2.1 m lost some until they pitched.
  if (clearance >= 2.3) {
    out.push(result("ok", "Over the bowler", `${clearance.toFixed(1)} m`,
      "The view to a short ball passes above the bowler's head and follow-through."));
  } else if (clearance >= 2.15) {
    out.push(result("watch", "Over the bowler", `${clearance.toFixed(1)} m`,
      "Just clears the bowler's head. The follow-through can hide short balls; mount higher if you can."));
  } else {
    out.push(result("fix", "Over the bowler", `${clearance.toFixed(1)} m`,
      "The bowler's body will hide short balls before they pitch. Mount higher, or nearer the stumps."));
  }
  const cm = (100 * Math.hypot(B + 13.1, H)) / focal;
  out.push(result(cm <= 2 ? "ok" : cm <= 3.5 ? "watch" : "fix", "Line detail", `${cm.toFixed(1)} cm a pixel`,
    cm <= 3.5 ? "At a good length. The stumps are 23 cm wide, so the line is measured well."
      : "Too coarse for the line. Narrow the view (zoom in) or move closer."));
  out.push(ballSize((BALL_M * focal) / Math.hypot(B + 20.12, H), o.width, "at the batter's end", 3, 2));
  out.push(frames(o));
  return out;
}

function ballSize(px, width, where, good, usable) {
  const note = width > WORK_WIDTH ? " VANSH reads pictures at 1280 pixels wide, so a sharper camera does not make the ball bigger." : "";
  if (px >= good) return result("ok", "Ball size", `${px.toFixed(1)} px`, `Big enough to track ${where}.${note}`);
  if (px >= usable) return result("watch", "Ball size", `${px.toFixed(1)} px`, `Small ${where}. Move closer or zoom in if you can.${note}`);
  return result("fix", "Ball size", `${px.toFixed(1)} px`, `Too small to track ${where}. Move closer or zoom in.${note}`);
}

function blur(o) {
  const metres = (o.speed / 3.6) * o.shutter;
  const widths = metres / BALL_M;
  const text = `At ${o.speed} km/h the ball moves ${Math.round(metres * 100)} cm while the shutter is open, ${widths.toFixed(1)} ball widths.`;
  if (widths <= 1.5) return result("ok", "Blur", `${Math.round(metres * 100)} cm`, `${text} Crisp.`);
  if (widths <= 3) return result("watch", "Blur", `${Math.round(metres * 100)} cm`, `${text} Usable; a faster shutter is crisper.`);
  return result("fix", "Blur", `${Math.round(metres * 100)} cm`, `${text} The ball smears into the grass. Use 1/500 s or faster.`);
}

function frames(o) {
  const count = (o.fps * 18) / (o.speed / 3.6);
  const text = "pictures of the ball between the release and the batter.";
  if (count >= 18) return result("ok", "Frames", `${Math.round(count)}`, `${text} Plenty.`);
  if (count >= 10) return result("ok", "Frames", `${Math.round(count)}`, `${text} Enough; 50 frames a second times pace more closely.`);
  if (count >= 6) return result("watch", "Frames", `${Math.round(count)}`, `${text} Few. Set the camera to 25 frames a second or more.`);
  return result("fix", "Frames", `${Math.round(count)}`, `${text} Too few to follow the ball.`);
}

function planner(root) {
  let kind = "side";
  const switcher = el("div", { class: "switch", role: "group", "aria-label": "Which camera" },
    el("button", { type: "button", "data-kind": "side", class: "on", text: "Side-on camera" }),
    el("button", { type: "button", "data-kind": "behind", text: "Behind the bowler" }));
  const lens = select("lens", [...LENSES, ["My own number", "custom"]], 84);
  const fov = numberInput("fov", 84, 10, 130, 1);
  const distance = numberInput("distance", 13, 2, 40, 0.5);
  const height = numberInput("height", 2.5, 0.5, 12, 0.1);
  const width = select("width", [["1280 × 720 (720p)", 1280], ["1920 × 1080 (1080p)", 1920], ["2560 × 1440 (4 MP)", 2560], ["3840 × 2160 (4K)", 3840]], 1920);
  const fps = select("fps", [["25", 25], ["30", 30], ["50", 50], ["60", 60]], 25);
  const shutter = select("shutter", [["1/50 s", 1 / 50], ["1/100 s", 1 / 100], ["1/250 s", 1 / 250], ["1/500 s", 1 / 500], ["1/1000 s", 1 / 1000], ["1/2000 s", 1 / 2000]], 1 / 500);
  const speed = numberInput("speed", 135, 60, 160, 5);
  const distanceLabel = el("span", { text: "Distance from the stump line (m)" });
  const results = el("div", { class: "results", "aria-live": "polite" });
  root.replaceChildren(
    el("h3", { text: "Placement calculator" }),
    el("p", { class: "tool-note", text: "Use the horizontal field of view from your camera's datasheet. Every number here is worked out in your browser." }),
    switcher,
    el("div", { class: "fields" },
      field("Lens", lens),
      field("Field of view (degrees)", fov),
      el("label", {}, distanceLabel, distance),
      field("Height of the camera (m)", height),
      field("Picture size", width),
      field("Frames a second", fps),
      field("Shutter", shutter),
      field("Ball speed (km/h)", speed)),
    results);

  function update() {
    const o = {
      fov: Number(fov.value), distance: Number(distance.value), height: Number(height.value),
      width: Number(width.value), fps: Number(fps.value), shutter: Number(shutter.value), speed: Number(speed.value),
    };
    if (![o.fov, o.distance, o.height, o.speed].every((value) => Number.isFinite(value) && value > 0)) return;
    results.replaceChildren(...(kind === "side" ? sideReport(o) : behindReport(o)));
  }

  function choose(next) {
    kind = next;
    switcher.querySelectorAll("button").forEach((button) => button.classList.toggle("on", button.dataset.kind === kind));
    const preset = DEFAULTS[kind];
    fov.value = preset.fov;
    distance.value = preset.distance;
    height.value = preset.height;
    lens.value = LENSES.some(([, value]) => value === preset.fov) ? String(preset.fov) : "custom";
    distanceLabel.textContent = kind === "side"
      ? "Distance from the stump line (m)"
      : "Distance behind the bowler's stumps (m)";
    update();
  }

  switcher.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (button) choose(button.dataset.kind);
  });
  lens.addEventListener("change", () => {
    if (lens.value !== "custom") fov.value = lens.value;
    update();
  });
  fov.addEventListener("input", () => {
    lens.value = LENSES.some(([, value]) => String(value) === fov.value) ? fov.value : "custom";
    update();
  });
  for (const input of [distance, height, width, fps, shutter, speed]) input.addEventListener("input", update);
  choose("side");
}

// ------------------------------------------------------------------ camera addresses

const BRANDS = {
  hikvision: { name: "Hikvision, Prama, HiLook", port: 554, path: (ch, main) => `/Streaming/Channels/${ch}0${main ? 1 : 2}` },
  dahua: { name: "Dahua, most CP Plus, Amcrest, Lorex", port: 554, path: (ch, main) => `/cam/realmonitor?channel=${ch}&subtype=${main ? 0 : 1}` },
  cpplus: { name: "CP Plus E-series and other models on port 5543", port: 5543, path: (ch) => `/live/channel${ch}` },
  uniview: { name: "Uniview (UNV)", port: 554, path: (ch, main) => `/unicast/c${ch}/s${main ? 0 : 1}/live` },
  reolink: { name: "Reolink", port: 554, path: (ch, main) => `/Preview_${String(ch).padStart(2, "0")}_${main ? "main" : "sub"}` },
  tapo: { name: "TP-Link Tapo and VIGI", port: 554, path: (ch, main) => `/stream${main ? 1 : 2}` },
  axis: { name: "Axis", port: 554, path: () => "/axis-media/media.amp" },
};

function rtspBuilder(root) {
  const brand = select("brand", Object.entries(BRANDS).map(([key, item]) => [item.name, key]), "hikvision");
  const role = select("role", [["Side-on camera", "SIDE"], ["Behind the bowler", "BEHIND"]], "SIDE");
  const host = el("input", { name: "host", value: "192.168.1.20", autocomplete: "off", spellcheck: "false" });
  const port = numberInput("port", 554, 1, 65535, 1);
  const user = el("input", { name: "user", value: "vansh", autocomplete: "off", spellcheck: "false" });
  const password = el("input", { name: "password", type: "password", value: "", autocomplete: "new-password" });
  const channel = numberInput("channel", 1, 1, 64, 1);
  const stream = select("stream", [["Main stream (use this)", "main"], ["Sub-stream", "sub"]], "main");
  const output = el("output", { "aria-live": "polite" });
  const copy = el("button", { type: "button", text: "Copy" });
  root.replaceChildren(
    el("h3", { text: "Camera address builder" }),
    el("p", { class: "tool-note", text: "Builds the line for vansh-edge.env. It stays in this browser; nothing is sent." }),
    el("div", { class: "fields" },
      field("Brand", brand), field("Which camera", role), field("Camera IP address", host), field("RTSP port", port),
      field("User name", user), field("Password", password), field("Channel (1 for a camera, the input on an NVR)", channel),
      field("Stream", stream)),
    el("div", { class: "url" }, output, copy));

  function update() {
    const item = BRANDS[brand.value];
    const login = user.value ? `${encodeURIComponent(user.value)}${password.value ? `:${encodeURIComponent(password.value)}` : ""}@` : "";
    const address = (host.value || "192.168.1.20").trim();
    const portPart = Number(port.value) && Number(port.value) !== 554 ? `:${Number(port.value)}` : ":554";
    const url = `rtsp://${login}${address}${portPart}${item.path(Math.max(1, Number(channel.value) || 1), stream.value === "main")}`;
    output.value = `VANSH_${role.value}_URL=${url}`;
    output.textContent = output.value;
  }

  brand.addEventListener("change", () => {
    port.value = BRANDS[brand.value].port;
    update();
  });
  for (const input of [role, host, port, user, password, channel, stream]) input.addEventListener("input", update);
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(output.textContent);
      copy.textContent = "Copied";
      window.setTimeout(() => { copy.textContent = "Copy"; }, 1500);
    } catch {
      copy.textContent = "Select and copy";
    }
  });
  update();
}

document.querySelectorAll('[data-tool="planner"]').forEach(planner);
document.querySelectorAll('[data-tool="rtsp"]').forEach(rtspBuilder);
