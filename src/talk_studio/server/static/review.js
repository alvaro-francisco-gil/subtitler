"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  decision: null, view: null, duration: 0, excerpt: 0, selected: "original",
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

async function loadProject() {
  const project = await api("/api/project");
  $("source").textContent = project.source;
  state.duration = project.duration;
  const nav = $("decisions");
  nav.replaceChildren();
  for (const d of project.decisions) {
    const b = document.createElement("button");
    b.textContent = `${d.name} · ${d.status}${d.open_round ? " · to review" : ""}`;
    b.dataset.decision = d.name;
    b.onclick = () => openDecision(d.name);
    nav.append(b);
  }
  // Land on the round waiting for a verdict: a picked decision has nothing to play.
  const waiting = project.decisions.find((d) => d.open_round);
  const first = state.decision || (waiting || project.decisions[0])?.name;
  if (first) await openDecision(first);
}

async function openDecision(name) {
  stop();
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

function drawVideos() {
  const grid = $("video-grid");
  const key = `${state.view.name}/${state.view.open_round}/${state.excerpt}/${playable().join(",")}`;
  if (grid.dataset.key !== key) {
    grid.dataset.key = key;
    grid.replaceChildren();
    for (const label of playable()) {
      const figure = document.createElement("figure");
      figure.dataset.label = label;
      const video = document.createElement("video");
      video.src = sampleUrl(label, "mp4");
      video.preload = "auto";
      video.playsInline = true;
      const caption = document.createElement("figcaption");
      const index = state.view.candidates.findIndex((c) => c.label === label);
      caption.textContent = label === "original" ? `0 · ${state.view.reference}` : `${index + 1} · ${label}`;
      figure.append(video, caption);
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
  return [...$("video-grid").querySelectorAll("video")].some((v) => !v.paused);
}

function toggleVideos() {
  const videos = [...$("video-grid").querySelectorAll("video")];
  if (!videos.length) return;
  if (videosPlaying()) {
    videos.forEach((v) => v.pause());
    $("video-play").textContent = "Play all";
    return;
  }
  const lead = videos.find((v) => !v.muted) || videos[0];
  const at = lead.currentTime >= lead.duration - 0.1 ? 0 : lead.currentTime;
  videos.forEach((v) => { v.currentTime = at; v.play().catch(() => {}); });
  $("video-play").textContent = "Pause all";
}

function playable() {
  const rendered = state.view.candidates
    .filter((c) => c.state === "rendered" && measured(c.label))
    .map((c) => c.label);
  return measured("original") ? ["original", ...rendered] : rendered;
}

function button(text, label) {
  const b = document.createElement("button");
  b.textContent = text;
  if (label === state.selected) b.className = "on";
  b.onclick = () => select(label);
  return b;
}

function render() {
  const v = state.view;
  $("decision").hidden = false;
  $("decision-title").textContent = v.name;
  for (const b of $("decisions").children) b.classList.toggle("on", b.dataset.decision === v.name);
  $("decision-status").textContent = v.blocked
    ? `Waiting · ${v.blocked}`
    : v.open_round ? `Round ${v.open_round} · ${v.status}` : `No open round · ${v.status}`;

  const excerpts = $("excerpts");
  excerpts.replaceChildren();
  v.excerpts.forEach((e, i) => {
    const b = document.createElement("button");
    b.textContent = isImage() ? fmt(e.start) : e.label;
    if (i === state.excerpt) b.className = "on";
    b.onclick = async () => { stop(); state.excerpt = i; state.offset = 0; render(); await loadBuffers(); schedulePoll(); };
    excerpts.append(b);
  });

  const candidates = $("candidates");
  const originalButton = button(`0 · ${v.reference}`, "original");
  if (!measured("original")) {
    originalButton.disabled = true;
    originalButton.textContent += " · rendering…";
  }
  candidates.replaceChildren(originalButton);
  v.candidates.forEach((c, i) => {
    const b = button(`${i + 1} · ${c.label}`, c.label);
    if (c.state !== "rendered" || !measured(c.label)) {
      b.disabled = true;
      b.textContent += c.state === "failed" ? " · failed" : " · rendering…";
    }
    candidates.append(b);
    if (c.error) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `${c.label} log`;
      const pre = document.createElement("pre");
      pre.textContent = c.error;
      details.append(summary, pre);
      candidates.append(details);
    }
  });

  $("verdict").hidden = !v.open_round;
  const image = isImage();
  const video = isVideo();
  $("frame").hidden = !image;
  $("frame-hint").hidden = !image;
  $("videos").hidden = !video;
  $("player").hidden = image || video;
  $("audio-hint").hidden = image || video;
  if (image) drawFrame();
  if (video) drawVideos();
  const ready = Boolean(state.buffers.original);
  $("play").disabled = !ready && !state.playing;
  $("play").title = ready ? "" : v.open_round ? "Loading samples…" : "Nothing to play: this decision has no open round.";
  $("pick").disabled = state.selected === "original";

  const list = $("history-list");
  list.replaceChildren();
  for (const round of v.history) {
    const heading = document.createElement("h4");
    heading.textContent = `Round ${round.round}`;
    const verdicts = document.createElement("p");
    verdicts.textContent = round.verdicts
      .map((x) => `${x.verdict}${x.candidate ? ` ${x.candidate}` : ""}${x.note ? ` — “${x.note}”` : ""}`)
      .join("; ");
    const items = document.createElement("ul");
    for (const c of round.candidates) {
      const li = document.createElement("li");
      li.textContent = `${c.label} (${c.id}): ${c.tool} ${JSON.stringify(c.settings)} [${c.state}]`;
      items.append(li);
    }
    list.append(heading, verdicts, items);
  }
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

async function loadBuffers() {
  const v = state.view;
  state.buffers = {};
  if (isVideo()) {
    render();
    return;
  }
  if (isImage()) {
    // Warm the browser cache so switching candidates is instant.
    for (const label of playable()) new Image().src = sampleUrl(label, "jpg");
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
  if (isImage() || !state.buffers.original) return;
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
  $("play").textContent = "Pause";
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
  $("play").textContent = "Play";
}

function select(label) {
  if (!available(label)) return;
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
  // Every excerpt, not just the one on screen: a background re-render (e.g.
  // after "Sample here" adds a new excerpt) can be in flight for an excerpt
  // the user has since navigated away from, and polling must not stop while
  // that render is still pending.
  const waiting = v.candidates.some((c) => c.state === "pending")
    || Object.values(v.ready || {}).some((perExcerpt) => perExcerpt.some((x) => x !== true));
  if (!waiting) return;
  state.poll = setTimeout(async () => {
    if (state.playing || (isVideo() && videosPlaying())) return schedulePoll();
    try {
      await openDecision(state.decision);
    } catch (error) {
      // A transient fetch failure must not silently end polling for the
      // session — keep trying on the same schedule.
      schedulePoll();
    }
  }, 3000);
}

async function verdict(kind) {
  const body = { verdict: kind, note: $("note").value };
  if (kind === "pick") body.label = state.selected;
  try {
    const result = await post(`/api/decisions/${state.decision}/feedback`, body);
    $("note").value = "";
    $("reveal").textContent = "Revealed: " + result.revealed
      .map((c) => `${c.label} = ${c.tool} ${JSON.stringify(c.settings)}`)
      .join(" · ");
    state.selected = "original";
    await loadProject();
  } catch (error) {
    alert(error.message);
  }
}

$("play").onclick = () => (state.playing ? stop() : play());
$("seek").oninput = () => {
  const length = state.buffers.original?.duration || 0;
  const wasPlaying = state.playing;
  stop();
  state.offset = (Number($("seek").value) / 1000) * length;
  tick();
  if (wasPlaying) play();
};
$("pick").onclick = () => verdict("pick");
$("reject").onclick = () => {
  if (!$("note").value.trim() && !confirm("Reject without a note? The agent learns more from one.")) return;
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
    alert(error.message);
  }
};

document.addEventListener("keydown", (event) => {
  if (event.target.tagName === "TEXTAREA" || !state.view) return;
  if (event.code === "Space" && isVideo()) {
    event.preventDefault();
    toggleVideos();
  } else if (event.code === "Space" && !isImage()) {
    event.preventDefault();
    state.playing ? stop() : play();
  } else if (event.key === "0") {
    select("original");
  } else if (/^[1-9]$/.test(event.key)) {
    const candidate = state.view.candidates[Number(event.key) - 1];
    if (candidate) select(candidate.label);
  }
});

$("video-play").onclick = toggleVideos;

function dragSplit(event) {
  const box = $("frame").getBoundingClientRect();
  state.split = Math.min(100, Math.max(0, ((event.clientX - box.left) / box.width) * 100));
  drawFrame();
}
$("frame").onpointerdown = (event) => { $("frame").setPointerCapture(event.pointerId); dragSplit(event); };
$("frame").onpointermove = (event) => { if (event.buttons) dragSplit(event); };
$("frame").ondblclick = () => { state.split = 50; drawFrame(); };

loadProject().catch((error) => { document.body.textContent = `talk-studio: ${error.message}`; });
