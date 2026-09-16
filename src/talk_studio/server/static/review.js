"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  decision: null, view: null, queue: [], duration: 0, excerpt: 0, selected: "original",
  ctx: null, buffers: {}, gains: {}, sources: [], playing: false, startedAt: 0, offset: 0, poll: null, split: 50,
};

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || response.statusText);
  }
  return response.json();
}

const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function fmt(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(el.timer);
  el.timer = setTimeout(() => { el.hidden = true; }, 1600);
}

// names

const TITLES = { audio: "Audio clean-up", master: "Mastering", grade: "Colour" };
const KINDS = { audio: "Listen", image: "Look", video: "Short" };

function title(name) {
  if (TITLES[name]) return TITLES[name];
  const slug = name.replace(/^clip-/, "").replaceAll("-", " ");
  return slug.charAt(0).toUpperCase() + slug.slice(1);
}

// the queue: only decisions with a round waiting for a verdict

async function loadProject(preferred) {
  const project = await api("/api/project");
  $("source").textContent = project.source;
  state.duration = project.duration;
  state.queue = project.decisions.filter((d) => d.open_round).map((d) => d.name);

  const nav = $("decisions");
  nav.replaceChildren();
  for (const name of state.queue) {
    const b = document.createElement("button");
    b.textContent = title(name);
    b.dataset.decision = name;
    b.onclick = () => openDecision(name);
    nav.append(b);
  }
  const left = state.queue.length;
  $("remaining").textContent = left ? `${left} to review` : "All done";

  if (!left) {
    stop();
    pauseVideos();
    state.view = null;
    $("decision").hidden = true;
    $("verdict").hidden = true;
    $("done").hidden = false;
    schedulePoll();
    return;
  }
  $("done").hidden = true;
  const target = state.queue.includes(preferred) ? preferred : state.queue[0];
  await openDecision(target);
}

async function openDecision(name) {
  stop();
  if (state.decision !== name) {
    pauseVideos();
    state.excerpt = 0;
    state.selected = "original";
  }
  state.decision = name;
  state.view = await api(`/api/decisions/${name}`);
  state.excerpt = Math.min(state.excerpt, Math.max(state.view.excerpts.length - 1, 0));
  if (!playable().includes(state.selected)) state.selected = "original";
  render();
  await loadBuffers();
  schedulePoll();
}

function measured(label) {
  return state.view.ready?.[label]?.[state.excerpt] === true;
}

const isImage = () => state.view?.kind === "image";
const isVideo = () => state.view?.kind === "video";

function sampleUrl(label, extension) {
  const v = state.view;
  // The round in the query: label A is a different candidate in every round.
  return `/api/decisions/${v.name}/samples/${label}/${state.excerpt}.${extension}?round=${v.open_round}`;
}

function available(label) {
  return isImage() || isVideo() ? measured(label) : Boolean(state.buffers[label]);
}

function playable() {
  const rendered = state.view.candidates
    .filter((c) => c.state === "rendered" && measured(c.label))
    .map((c) => c.label);
  return measured("original") ? ["original", ...rendered] : rendered;
}

function labelText(label) {
  return label === "original" ? state.view.reference : label;
}

// rendering

function render() {
  const v = state.view;
  if (!v) return;
  $("decision").hidden = false;
  $("verdict").hidden = !v.open_round;
  $("decision-kind").textContent = KINDS[v.kind] || "";
  $("decision-title").textContent = title(v.name);
  for (const b of $("decisions").children) b.classList.toggle("on", b.dataset.decision === v.name);

  const excerpt = v.excerpts[state.excerpt];
  const pending = v.candidates.filter((c) => c.state === "pending" || (c.state === "rendered" && !measured(c.label))).length;
  const count = v.candidates.length;
  $("decision-status").textContent = v.blocked
    ? v.blocked
    : pending ? `Rendering ${pending} of ${count} versions…`
    : isVideo() && excerpt ? `${count} versions · ${fmt(excerpt.start)}–${fmt(excerpt.end)} · ${Math.round(excerpt.end - excerpt.start)} s`
    : `${count} versions`;

  const excerpts = $("excerpts");
  excerpts.replaceChildren();
  if (v.excerpts.length > 1) {
    v.excerpts.forEach((e, i) => {
      const b = document.createElement("button");
      b.textContent = fmt(e.start);
      if (i === state.excerpt) b.className = "on";
      b.onclick = async () => { stop(); pauseVideos(); state.excerpt = i; state.offset = 0; render(); await loadBuffers(); schedulePoll(); };
      excerpts.append(b);
    });
  }

  const image = isImage();
  const video = isVideo();
  $("frame").hidden = !image;
  $("videos").hidden = !video;
  $("sample-here").hidden = image || video;
  $("player").hidden = image;
  $("candidates").hidden = video;

  const candidates = $("candidates");
  candidates.replaceChildren();
  const pill = (label, index) => {
    const b = document.createElement("button");
    b.textContent = labelText(label);
    b.title = `Key ${index}`;
    if (label === state.selected) b.className = "on";
    b.disabled = !available(label);
    b.onclick = () => select(label);
    candidates.append(b);
  };
  pill("original", 0);
  v.candidates.forEach((c, i) => pill(c.label, i + 1));

  if (image) drawFrame();
  if (video) drawVideos();

  const ready = video ? playable().length > 0 : Boolean(state.buffers.original);
  $("play").disabled = !ready && !state.playing;
  $("hint").innerHTML = video
    ? "Every version plays together; you hear the highlighted one. <kbd>1</kbd>–<kbd>9</kbd> to switch, <kbd>Space</kbd> to play, <kbd>Enter</kbd> to pick."
    : image
      ? "Drag across the image: reference on the left, the selected version on the right. <kbd>1</kbd>–<kbd>9</kbd> to switch, <kbd>Enter</kbd> to pick."
      : "Levels are matched, so louder never wins. <kbd>1</kbd>–<kbd>9</kbd> to switch, <kbd>0</kbd> reference, <kbd>Space</kbd> to play, <kbd>Enter</kbd> to pick.";

  const pick = $("pick");
  pick.disabled = state.selected === "original" || !available(state.selected);
  pick.textContent = state.selected === "original" ? "Select a version" : `Pick ${state.selected}`;
}

function drawFrame() {
  if (!measured("original")) return;
  const base = sampleUrl("original", "jpg");
  const over = sampleUrl(available(state.selected) ? state.selected : "original", "jpg");
  if ($("frame-base").getAttribute("src") !== base) $("frame-base").src = base;
  if ($("frame-over").getAttribute("src") !== over) $("frame-over").src = over;
  $("frame-over").style.clipPath = `inset(0 0 0 ${state.split}%)`;
  $("frame-divider").style.left = `${state.split}%`;
}

function videos() {
  return [...$("video-grid").querySelectorAll("video")];
}

function drawVideos() {
  const grid = $("video-grid");
  const key = `${state.view.name}/${state.view.open_round}/${state.excerpt}/${playable().join(",")}`;
  if (grid.dataset.key !== key) {
    pauseVideos();
    grid.dataset.key = key;
    grid.replaceChildren();
    // Candidates first; the wide reference last, where it reads as context.
    for (const label of [...playable().filter((l) => l !== "original"), ...playable().filter((l) => l === "original")]) {
      const figure = document.createElement("figure");
      figure.className = label === "original" ? "vcard wide" : "vcard";
      figure.dataset.label = label;
      const video = document.createElement("video");
      video.src = sampleUrl(label, "mp4");
      video.preload = "auto";
      video.playsInline = true;
      video.ontimeupdate = () => { if (!video.muted) tickVideo(video); };
      video.onended = () => { if (!video.muted) { pauseVideos(); } };
      const badge = document.createElement("span");
      badge.className = "badge";
      const index = state.view.candidates.findIndex((c) => c.label === label);
      badge.textContent = label === "original" ? `0 · ${state.view.reference}` : `${index + 1} · ${label}`;
      const sound = document.createElement("span");
      sound.className = "sound";
      sound.textContent = "♪ sound";
      figure.append(video, badge, sound);
      figure.onclick = () => select(label);
      grid.append(figure);
    }
  }
  for (const figure of grid.children) {
    const on = figure.dataset.label === state.selected;
    figure.classList.toggle("on", on);
    figure.querySelector("video").muted = !on;
  }
}

function videosPlaying() {
  return videos().some((v) => !v.paused);
}

function pauseVideos() {
  videos().forEach((v) => v.pause());
  if (isVideo()) $("play").textContent = "▶";
}

function leadVideo() {
  const all = videos();
  return all.find((v) => !v.muted) || all[0];
}

function toggleVideos() {
  const all = videos();
  if (!all.length) return;
  if (videosPlaying()) {
    pauseVideos();
    return;
  }
  const lead = leadVideo();
  const at = lead.currentTime >= lead.duration - 0.1 ? 0 : lead.currentTime;
  all.forEach((v) => { v.currentTime = at; v.play().catch(() => {}); });
  $("play").textContent = "❚❚";
}

function tickVideo(video) {
  const length = video.duration || 0;
  $("seek").value = length ? Math.round((video.currentTime / length) * 1000) : 0;
  $("clock").textContent = `${fmt(video.currentTime)} / ${fmt(length)}`;
}

async function loadBuffers() {
  const v = state.view;
  state.buffers = {};
  if (isVideo() || isImage()) {
    if (isImage()) for (const label of playable()) new Image().src = sampleUrl(label, "jpg");
    render();
    return;
  }
  if (!v.excerpts.length) { render(); return; }
  state.ctx = state.ctx || new AudioContext();
  await Promise.all(playable().map(async (label) => {
    try {
      const response = await fetch(sampleUrl(label, "wav"));
      if (!response.ok) return;
      state.buffers[label] = await state.ctx.decodeAudioData(await response.arrayBuffer());
    } catch (error) {
      // One undecodable sample must not leave every button disabled.
      console.error(`could not load sample ${label}`, error);
    }
  }));
  state.offset = 0;
  render();
  tick();
}

// audio playback: every sample plays in lock-step, only the selected one is audible

function matchedGain(label) {
  const levels = Object.values(state.view.loudness)
    .map((perExcerpt) => perExcerpt[state.excerpt])
    .filter((x) => typeof x === "number");
  const own = state.view.loudness[label]?.[state.excerpt];
  if (typeof own !== "number" || !levels.length) return 1;
  return Math.pow(10, (Math.min(...levels) - own) / 20);
}

function position() {
  const length = state.buffers.original?.duration || 0;
  if (!state.playing) return state.offset;
  return Math.min(state.ctx.currentTime - state.startedAt, length);
}

function play() {
  if (isImage() || isVideo() || !state.buffers.original) return;
  state.ctx.resume();
  const when = state.ctx.currentTime + 0.05;
  state.sources = [];
  state.gains = {};
  for (const [label, buffer] of Object.entries(state.buffers)) {
    const source = state.ctx.createBufferSource();
    source.buffer = buffer;
    const gain = state.ctx.createGain();
    gain.gain.value = label === state.selected ? matchedGain(label) : 0;
    source.connect(gain).connect(state.ctx.destination);
    source.start(when, state.offset);
    state.sources.push(source);
    state.gains[label] = gain;
  }
  state.sources[0].onended = () => {
    if (!state.playing) return;
    stop();
    state.offset = 0;
    tick();
  };
  state.startedAt = when - state.offset;
  state.playing = true;
  $("play").textContent = "❚❚";
  requestAnimationFrame(loop);
}

function stop() {
  if (!state.playing) return;
  state.offset = position();
  state.playing = false;
  for (const source of state.sources) {
    try { source.stop(); } catch (_) { /* already stopped */ }
  }
  state.sources = [];
  $("play").textContent = "▶";
}

function select(label) {
  if (!state.view || !available(label)) return;
  state.selected = label;
  if (state.playing) {
    const now = state.ctx.currentTime;
    for (const [l, gain] of Object.entries(state.gains)) {
      gain.gain.cancelScheduledValues(now);
      gain.gain.setTargetAtTime(l === label ? matchedGain(l) : 0, now, 0.01);
    }
  }
  render();
}

function tick() {
  const length = state.buffers.original?.duration || 0;
  const at = position();
  $("seek").value = length ? Math.round((at / length) * 1000) : 0;
  const excerpt = state.view?.excerpts[state.excerpt];
  $("clock").textContent = excerpt ? `${fmt(excerpt.start + at)} / ${fmt(excerpt.end)}` : "0:00";
}

function loop() {
  tick();
  if (state.playing) requestAnimationFrame(loop);
}

function schedulePoll() {
  clearTimeout(state.poll);
  const v = state.view;
  // With nothing on screen, keep checking for new rounds. Otherwise poll while
  // any sample of any excerpt is still rendering.
  const waiting = !v
    || v.candidates.some((c) => c.state === "pending")
    || Object.values(v.ready || {}).some((perExcerpt) => perExcerpt.some((x) => x !== true));
  if (!waiting) return;
  state.poll = setTimeout(async () => {
    if (state.playing || (isVideo() && videosPlaying())) return schedulePoll();
    try {
      if (!state.view) await loadProject();
      else await openDecision(state.decision);
    } catch (error) {
      // A transient fetch failure must not silently end polling for the session.
      schedulePoll();
    }
  }, 3000);
}

// verdicts move straight on to the next choice

async function verdict(kind) {
  const body = { verdict: kind, note: $("note").value };
  if (kind === "pick") body.label = state.selected;
  const current = state.decision;
  const index = state.queue.indexOf(current);
  try {
    await post(`/api/decisions/${current}/feedback`, body);
  } catch (error) {
    toast(error.message);
    return;
  }
  $("note").value = "";
  stop();
  pauseVideos();
  toast(kind === "pick" ? `Picked ${body.label} for ${title(current)}` : `Sent back ${title(current)}`);
  const rest = state.queue.filter((name) => name !== current);
  const next = rest[index] || rest[0];
  await loadProject(next);
}

$("play").onclick = () => {
  if (isVideo()) toggleVideos();
  else state.playing ? stop() : play();
};
$("seek").oninput = () => {
  if (isVideo()) {
    const all = videos();
    const lead = leadVideo();
    if (!lead || !lead.duration) return;
    const at = (Number($("seek").value) / 1000) * lead.duration;
    all.forEach((v) => { v.currentTime = at; });
    tickVideo(lead);
    return;
  }
  const length = state.buffers.original?.duration || 0;
  const wasPlaying = state.playing;
  stop();
  state.offset = (Number($("seek").value) / 1000) * length;
  tick();
  if (wasPlaying) play();
};
$("pick").onclick = () => verdict("pick");
$("reject").onclick = () => {
  if (!$("note").value.trim()) {
    $("note").focus();
    toast("Add a note so the next round can improve");
    return;
  }
  verdict("reject");
};
$("sample-here").onclick = async () => {
  const excerpt = state.view?.excerpts[state.excerpt];
  if (!excerpt) return;
  const at = excerpt.start + position();
  const start = Math.max(0, at - 6);
  const end = Math.min(state.duration, start + 12);
  try {
    await post(`/api/decisions/${state.decision}/excerpts`, { start, end });
    await openDecision(state.decision);
  } catch (error) {
    toast(error.message);
  }
};

document.addEventListener("keydown", (event) => {
  if (event.target.tagName === "INPUT" && event.target.type === "text") {
    if (event.key === "Escape") event.target.blur();
    return;
  }
  if (!state.view) return;
  if (event.code === "Space") {
    event.preventDefault();
    $("play").click();
  } else if (event.key === "Enter") {
    if (!$("pick").disabled) verdict("pick");
  } else if (event.key === "0") {
    select("original");
  } else if (/^[1-9]$/.test(event.key)) {
    const candidate = state.view.candidates[Number(event.key) - 1];
    if (candidate) select(candidate.label);
  }
});

function dragSplit(event) {
  const box = $("frame").getBoundingClientRect();
  state.split = Math.min(100, Math.max(0, ((event.clientX - box.left) / box.width) * 100));
  drawFrame();
}
$("frame").onpointerdown = (event) => { $("frame").setPointerCapture(event.pointerId); dragSplit(event); };
$("frame").onpointermove = (event) => { if (event.buttons) dragSplit(event); };
$("frame").ondblclick = () => { state.split = 50; drawFrame(); };

loadProject().catch((error) => { document.body.textContent = `talk-studio: ${error.message}`; });
