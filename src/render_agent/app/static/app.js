// Render Agent desktop app. No build step: plain JS talking to the local API.
"use strict";

const TOKEN = document.querySelector('meta[name="session-token"]').content;
const view = document.getElementById("view");
let pollTimer = null;
let state = { selected: null, flagOpen: null, draftCorrections: [], viewLabels: {}, meanings: {} };

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;

async function api(path, body, method) {
  const opts = { method: method || (body === undefined ? "GET" : "POST"), headers: {} };
  if (opts.method !== "GET") opts.headers["X-Session-Token"] = TOKEN;
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  let res;
  try { res = await fetch("/api" + path, opts); }
  catch { const err = new Error("Render Agent is not responding."); err.data = { what_happened: "Render Agent is not responding.", what_to_do: "Close the window and open Render Agent again. Your jobs are safe." }; throw err; }
  const data = await res.json().catch(() => ({ what_happened: "The app did not answer.", what_to_do: "Close and reopen Render Agent." }));
  if (!res.ok) { const err = new Error(data.what_happened); err.data = data; err.status = res.status; throw err; }
  return data;
}

function toast(text) {
  const t = document.getElementById("toast");
  t.textContent = text; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 4500);
}

function problem(p, extra = "") {
  if (!p) return "";
  const list = (p.problems || []).map((x) => `<li>${esc(x)}</li>`).join("");
  return `<div class="notice bad" role="alert"><strong>${esc(p.what_happened)}</strong><p>${esc(p.what_to_do || "")}</p>${list ? `<ul>${list}</ul>` : ""}${extra}</div>`;
}

// The job's one fixed sequence. `now` is the index of the current step; `attention` marks a stop.
const STEPS = ["Read the drawings", "Choose views", "Approve the plan", "Make the renders", "Review", "Done"];
// `job` (optional) adds "part 3 of 5" under the step that is running.
function steps(now, attention = false, job = null) {
  const part = job && job.parts > 1 && job.part && job.step - 1 === now ? `<small class="part">part ${job.part} of ${job.parts}</small>` : "";
  return `<ol class="steps" aria-label="Job steps">${STEPS.map((s, i) =>
    `<li class="${i < now ? "done" : i === now ? "now" + (attention ? " attention" : "") : ""}" ${i === now ? 'aria-current="step"' : ""}><span></span>${esc(s)}${i === now ? part : ""}</li>`).join("")}</ol>`;
}

function head(title, lede, meta = [], step = null, attention = false, job = null) {
  const m = meta.map((x) => `<span>${x}</span>`).join('<span class="sep"></span>');
  return `<header class="head">${m ? `<div class="eyebrow">${m}</div>` : ""}<h1>${esc(title)}</h1>${lede ? `<p class="lede">${esc(lede)}</p>` : ""}${step !== null ? steps(step, attention, job) : ""}</header>`;
}

// "Step 1 of 5: Start · part 3 of 5: Collecting the facts"
function stepLine(job) {
  const part = job.parts > 1 && job.part ? ` · part ${job.part} of ${job.parts}: ${job.part_name}` : "";
  return `Step ${job.step} of ${job.steps}: ${job.step_name}${part}`;
}

function pill(status, text) {
  const kind = { AWAITING_VIEWS: "wait", AWAITING_APPROVAL: "wait", AWAITING_REVIEW: "wait", FAILED: "bad", PAUSED: "bad", SUCCEEDED: "good", RUNNING: "run", RENDERING: "run", NEW: "run" }[status] || "";
  return `<span class="pill ${kind}">${esc(text)}</span>`;
}

function poll(fn, ms = 2000) { clearInterval(pollTimer); pollTimer = setInterval(fn, ms); }

function setNav(name) {
  document.querySelectorAll("[data-nav]").forEach((a) => { if (a.dataset.nav === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
}

// ---------- routing ----------
async function route() {
  clearInterval(pollTimer);
  const [, page, id] = location.hash.split("/");
  try {
    if (page === "job" && id) { setNav("home"); await jobScreen(decodeURIComponent(id)); }
    else if (page === "settings") { setNav("settings"); await settingsScreen(); }
    else if (page === "help") { setNav("help"); await helpScreen(); }
    else { setNav("home"); await homeScreen(); }
  } catch (e) {
    view.innerHTML = problem(e.data || { what_happened: e.message, what_to_do: "Go back to Home and try again." });
  }
}
window.addEventListener("hashchange", () => { state.flagOpen = null; route(); });

// ---------- Home ----------
const PDF_ICON = '<svg viewBox="0 0 48 48" aria-hidden="true"><path d="M12 4h17l9 9v31H12z"/><path d="M29 4v9h9M24 21v14m-6-6 6 6 6-6"/></svg>';

async function homeScreen(keepMessage) {
  const [home, prefs] = await Promise.all([api("/home"), api("/settings")]);
  state.viewLabels = Object.fromEntries(home.views.map((v) => [v.id, v.label]));
  if (state.selected && !home.inputs.some((i) => i.name === state.selected && i.startable)) state.selected = null;
  if (!state.selected) { const first = home.inputs.find((i) => i.startable); state.selected = first ? first.name : null; }
  const active = home.jobs.filter((j) => j.status !== "SUCCEEDED");
  const needYou = home.jobs.filter((j) => ["AWAITING_VIEWS", "AWAITING_APPROVAL", "AWAITING_REVIEW", "FAILED", "PAUSED"].includes(j.status)).length;

  const warns = home.warnings.map((w) => `<div class="notice ${w.kind === "onedrive" ? "" : "bad"}"><p>${esc(w.text)}</p>${w.kind === "onedrive" ? '<div><button class="link" id="dismiss-od">Got it, don\'t show this again</button></div>' : w.kind === "key" ? '<div><a href="#/settings">Add the key</a></div>' : ""}</div>`).join("");

  const items = home.inputs.map((i) => `<div class="item" data-input="${esc(i.name)}" role="radio" tabindex="0" aria-checked="${i.name === state.selected}" ${i.startable ? "" : 'aria-disabled="true"'}>
      <span class="radio"></span>
      <span><span class="name">${esc(i.name)}</span><br><span class="muted">${i.kind === "folder" ? "Folder of drawings" : "PDF"}${i.kind === "pdf+txt" ? " with instructions" : ""} · ${i.size_mb} MB</span></span>
      ${i.startable ? '<span class="pill good">Ready</span>' : `<span class="pill bad">${esc(i.reason)}</span>`}</div>`).join("");

  const zone = `<section class="dropzone" aria-label="Drawing sets waiting in Input">
    <div class="dz-head"><strong>${home.inputs.length ? "Choose a drawing set" : "Add a drawing set"}</strong><button class="link" id="refresh">Refresh</button></div>
    ${home.inputs.length ? `<div class="items" role="radiogroup">${items}</div>` : `<div class="dz-empty">${PDF_ICON}<p><strong>Drag a PDF onto this window</strong></p><p class="muted">or put it in <span class="path">${esc(home.input_folder)}</span></p></div>`}
  </section>`;

  const row = (j) => `<div class="row selectable" data-job="${esc(j.id)}" tabindex="0">
        <span><span class="name">${esc(j.input_name)}</span><br><span class="sub">${j.error ? esc(j.error.what_happened) : j.status === "SUCCEEDED" ? `Finished ${esc(j.updated.slice(0, 16).replace("T", " "))}` : `${esc(stepLine(j))} · updated ${esc(j.updated.slice(11, 16))}`}</span></span>
        <span class="end">${pill(j.status, j.status_text)}${j.can_retry ? `<button data-retry="${esc(j.id)}">Retry</button>` : ""}${j.status === "SUCCEEDED" && j.output_folder ? `<button data-open="${esc(j.id)}">Open folder</button>` : ""}</span></div>`;
  const done = home.jobs.filter((j) => j.status === "SUCCEEDED");
  const jobs = active.length ? active.map(row).join("") : '<div class="empty">Jobs you start appear here.</div>';
  const completed = done.length ? `<details class="completed"><summary><h2>Completed <span class="muted" style="font-size:15px">· ${done.length}</span></h2></summary><p class="muted" style="margin-top:0">Their images are in Output\\Completed.</p><div class="list">${done.map(row).join("")}</div></details>` : "";

  const providers = home.providers.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
  const llms = home.llm_choices.map((c) => `<option value="${esc(c)}" ${c === prefs.llm_choice ? "selected" : ""}>${esc(c)}</option>`).join("");
  const meta = [`${home.inputs.length} waiting in Input`, `${active.length} in progress`, needYou ? `${needYou} need you` : "nothing needs you"];

  view.innerHTML = head("Start a job", "Choose a drawing set and press Start. The original moves to History, so it is never started twice.", meta)
    + warns + `<div id="msg">${keepMessage || ""}</div>` + zone
    + `<details class="options"><summary>Start options</summary><div class="panel"><div class="grid2">
        <div><label class="check"><input type="checkbox" id="o-dry"> Practice run: placeholder images, no cost</label>
             <label class="check"><input type="checkbox" id="o-street" checked> Include the streetscape (2 or more dwellings)</label></div>
        <div><label for="o-llm">Drawing reader</label><select id="o-llm"><option value="">Standard</option>${llms}</select>
             <label for="o-provider">Image service</label><select id="o-provider"><option value="">Standard</option>${providers}</select></div>
        <div><label for="o-dw">Only these dwellings</label><input type="text" id="o-dw" placeholder="All, or e.g. 1, 3"></div>
        <div><label for="o-max">Most images per dwelling</label><input type="number" id="o-max" min="1" max="16" placeholder="16"></div>
      </div>
      </div></details>
    <div class="actions"><button class="primary" id="start" ${state.selected && home.can_start ? "" : "disabled"}>${state.selected ? `Start “${esc(state.selected)}”` : "Start"}</button>
      ${!home.can_start ? '<span class="muted">Fix the message above to start.</span>' : !state.selected ? '<span class="muted">Add a drawing set first.</span>' : ""}</div>
    <h2>Your jobs</h2><div class="list">${jobs}</div>${completed}`;

  view.querySelectorAll("[data-input]").forEach((el) => {
    const pick = () => { if (el.getAttribute("aria-disabled")) return; state.selected = el.dataset.input; homeScreen(document.getElementById("msg").innerHTML); };
    el.onclick = pick; el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } };
  });
  view.querySelectorAll("[data-job]").forEach((el) => {
    el.onclick = (e) => { if (!e.target.dataset.retry) location.hash = `#/job/${enc(el.dataset.job)}`; };
    el.onkeydown = (e) => { if (e.key === "Enter") location.hash = `#/job/${enc(el.dataset.job)}`; };
  });
  view.querySelectorAll("[data-retry]").forEach((b) => (b.onclick = async () => { await api(`/jobs/${enc(b.dataset.retry)}/retry`, {}); toast("Retrying: the job carries on from where it stopped."); homeScreen(); }));
  view.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => api(`/jobs/${enc(b.dataset.open)}/open-folder`, { which: "output" }).catch((e) => toast(e.data.what_happened))));
  document.getElementById("refresh").onclick = () => homeScreen();
  const od = document.getElementById("dismiss-od");
  if (od) od.onclick = async () => { await api("/home/dismiss-onedrive", {}); homeScreen(); };
  document.getElementById("start").onclick = () => startSelected(false);
  // the "started before" message survives the 5-second refresh; its button must keep working
  const anyway = document.getElementById("anyway");
  if (anyway) anyway.onclick = () => startSelected(true);
  poll(async () => { if (!document.querySelector("details.options[open]") && document.activeElement?.tagName !== "INPUT") homeScreen(document.getElementById("msg")?.innerHTML); }, 5000);
}

function startOptions() {
  const list = (v) => v.split(",").map((s) => s.trim()).filter(Boolean);
  const max = parseInt(document.getElementById("o-max").value, 10);
  return {
    streetscape: document.getElementById("o-street").checked ? null : false,
    dry_run: document.getElementById("o-dry").checked || null,
    llm: document.getElementById("o-llm").value || null,
    provider: document.getElementById("o-provider").value || null,
    dwellings: list(document.getElementById("o-dw").value),
    max_images: Number.isFinite(max) ? max : null,
  };
}

async function startSelected(anyway) {
  const btn = document.getElementById("start");
  btn.disabled = true; btn.textContent = "Starting…";
  try {
    const r = await api("/inputs/start", { name: state.selected, options: startOptions(), start_anyway: anyway });
    state.selected = null;
    toast(r.history_folder ? "Started. The original is now in History." : "Started.");
    location.hash = `#/job/${enc(r.job_id)}`;
  } catch (e) {
    const d = e.data || { what_happened: e.message };
    const extra = e.status === 409 ? `<div class="actions" style="margin-top:8px"><a href="#/job/${enc(d.duplicate_of)}">Open the existing job</a><button class="link" id="anyway">Start anyway</button></div>` : "";
    await homeScreen(problem(d, extra));
    const a = document.getElementById("anyway");
    if (a) a.onclick = () => startSelected(true);
  }
}

// ---------- one job ----------
async function jobScreen(id) {
  const job = await api(`/jobs/${enc(id)}`);
  const meta = [esc(job.input_name), pill(job.status, job.status_text), `<span class="mono">${esc(job.id)}</span>`];
  const warn = (job.warnings || []).map((w) => `<div class="notice"><p>${esc(w)}</p></div>`).join("") + (job.publish_problem ? problem({ what_happened: job.publish_problem, what_to_do: "" }) : "");
  if (job.queued === null && (job.status === "FAILED" || job.status === "PAUSED")) return stoppedScreen(job, meta, warn);
  if (job.queued === null && job.screen === "views") return viewsScreen(job, meta, warn);
  if (job.queued === null && job.screen === "approval") return approvalScreen(job, meta, warn);
  if (job.queued === null && job.screen === "review") return reviewScreen(job, meta, warn);
  if (job.queued === null && job.screen === "results") return resultsScreen(job, meta, warn);
  return progressScreen(job, meta, warn);
}

function stepOf(job) { return Math.max((job.step || 1) - 1, 0); }

function stoppedScreen(job, meta, warn) {
  const paused = job.status === "PAUSED";
  view.innerHTML = head(paused ? "Paused, needs your attention" : "Something went wrong", paused ? "Nothing is lost. Fix the problem below, then press Retry: the job carries on where it stopped." : "Read what happened below. Retry carries on from where it stopped.", meta, stepOf(job), true)
    + warn + problem(job.error || { what_happened: "The job stopped.", what_to_do: "Press Retry." })
    + `<div class="actions"><button class="primary" id="retry">Retry</button><a href="#/home">Back to Home</a></div>`;
  document.getElementById("retry").onclick = async () => { await api(`/jobs/${enc(job.id)}/retry`, {}); route(); };
}

function progressScreen(job, meta, warn) {
  const p = job.progress || { done: 0, total: 1 };
  const pct = Math.round((100 * p.done) / Math.max(p.total, 1));
  const afterApproval = stepOf(job) >= 3;
  const planning = stepOf(job) === 2;
  const counting = job.stage_text === "Making the renders" || job.stage_text === "Checking renders against the drawings";
  const what = job.queued > 0 ? `Waiting its turn (${job.queued} ahead)`
    : job.stage_text === "Waiting for your approval" ? "Starting the renders…" : stepLine(job);
  const detail = counting ? `${p.done} of ${p.total} renders` : afterApproval ? "Preparing each render from the drawings" : planning ? "Planning the views you chose" : "Reading the drawings";
  view.innerHTML = head(afterApproval ? "Making your renders" : planning ? "Planning your views" : "Reading the drawings", "You can close this window. The job carries on when you open Render Agent again.", meta, stepOf(job), false, job)
    + warn + `<section class="panel progress"><p class="stage">${esc(what)}</p><div class="bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pct}%"></span></div>
    <p class="muted">${esc(detail)}</p></section>
    <div class="actions"><a href="#/home">Back to Home</a></div>`;
  poll(async () => { const j = await api(`/jobs/${enc(job.id)}`); if (j.status !== job.status || j.queued !== job.queued || j.stage_text !== job.stage_text || j.part !== job.part || (j.progress && j.progress.done !== p.done)) route(); });
}

// ---------- Choose views ----------
async function viewsScreen(job, meta, warn) {
  const r = await api(`/jobs/${enc(job.id)}/views`);
  const rows = r.views.map((v) => {
    const locked = v.required || !v.possible;
    const note = v.required ? "always made" : !v.possible ? v.why_not : v.note;
    return `<label class="check ${locked ? "locked" : ""}"><input type="checkbox" data-pick="${esc(v.id)}" data-interior="${v.interior ? 1 : 0}" ${v.ticked ? "checked" : ""} ${locked ? "disabled" : ""}> ${esc(v.label)}${note ? ` <span class="muted">· ${esc(note)}</span>` : ""}</label>`;
  }).join("");
  // an instruction .txt that came with the drawings: its @views pre-ticks the boxes; its words can be read in
  const f = r.from_file || {};
  const fileNote = f.present && (f.views.length || f.text) ? `<div class="notice"><strong>From the instruction file</strong>
      ${f.views.length ? `<p style="margin:4px 0 0">Views asked for: <span class="mono">${esc(f.views.join(", "))}</span>. The ticks below are set from it; check them.</p>` : ""}
      ${f.text ? `<p style="margin:4px 0 0;white-space:pre-line">${esc(f.text)}</p><div style="margin-top:8px"><button id="use-file">Read views from this text</button> <span class="muted">puts it in the words box below for you to confirm</span></div>` : ""}</div>` : "";
  view.innerHTML = head("Choose the views", `The drawings are read (${r.dwellings.length} dwelling${r.dwellings.length > 1 ? "s" : ""}). Choose what to render; the agent then plans those views and shows you the plan to approve.`, meta, 1, false, job)
    + warn + `<div id="msg"></div>${fileNote}
    <section class="panel"><div class="actions" style="margin:0 0 8px"><button class="link" id="pick-all">All views</button><button class="link" id="pick-ext">External only</button><button class="link" id="pick-int">Interior only</button><button class="link" id="pick-none">Clear</button></div>
      <div class="viewlist">${rows}</div>
      <label for="words" style="margin-top:16px">Or say it in your own words</label><input type="text" id="words" value="${esc(r.words)}" placeholder="e.g. only external front and back elevation, front at night">
      <div id="understood"></div></section>
    <h2>What the agent will do</h2><section class="panel" id="summary"></section>
    <div class="sticky-actions"><div class="actions"><button class="primary" id="plan">Plan these views</button><a href="#/home">Back to Home</a></div></div>`;
  const boxes = [...view.querySelectorAll("[data-pick]")];
  const label = Object.fromEntries(r.views.map((v) => [v.id, v.label]));
  const picked = () => boxes.filter((c) => c.checked).map((c) => c.dataset.pick);
  const plan = document.getElementById("plan");
  let wordsPending = false;
  const summary = () => {
    const ids = picked();
    document.getElementById("summary").innerHTML = ids.length
      ? `<p style="margin:0 0 6px">For ${r.dwellings.map(esc).join(", ")}, the agent will plan:</p><p style="margin:0"><strong>${ids.map((id) => esc(label[id])).join(" · ")}</strong></p>
         <p class="muted" style="margin:8px 0 0">Interior views are planned only for rooms the floor plan shows. Next you see the plan, with each render and its materials, and approve it before anything is paid for.</p>`
      : '<p class="muted" style="margin:0">Tick at least one view.</p>';
    plan.disabled = !ids.length || wordsPending;
    plan.textContent = wordsPending ? "Confirm your words first" : `Plan these ${ids.length} view${ids.length === 1 ? "" : "s"}`;
  };
  const setTo = (keep) => { boxes.forEach((c) => { if (!c.disabled) c.checked = keep(c); }); summary(); };
  boxes.forEach((c) => (c.onchange = summary));
  document.getElementById("pick-all").onclick = () => setTo(() => true);
  document.getElementById("pick-ext").onclick = () => setTo((c) => c.dataset.interior === "0");
  document.getElementById("pick-int").onclick = () => setTo((c) => c.dataset.interior === "1");
  document.getElementById("pick-none").onclick = () => setTo(() => false);
  const words = document.getElementById("words");
  let typing;
  const readWords = async () => {
    const box = document.getElementById("understood");
    wordsPending = false;
    if (words.value.trim()) {
      const res = await api("/views/interpret", { text: words.value });
      const can = (id) => { const v = r.views.find((x) => x.id === id); return v && (v.possible || v.required); };
      const usable = res.views.filter((v) => can(v.id));
      const cannot = res.views.filter((v) => !can(v.id));
      wordsPending = usable.length > 0;
      box.innerHTML = `<div class="notice ${usable.length ? "" : "bad"}"><p style="margin:0">${usable.length ? `<strong>I understood:</strong> ${usable.map((v) => esc(v.label)).join(" · ")}` : "<strong>No view I can make matched these words.</strong>"}</p>
        ${cannot.length ? `<p class="muted" style="margin:4px 0 0">Cannot be made from these drawings: ${cannot.map((v) => esc(v.label)).join(", ")}</p>` : ""}
        ${res.unplaced.length ? `<p class="muted" style="margin:4px 0 0">Not understood: ${res.unplaced.map(esc).join(", ")}</p>` : ""}
        ${usable.length ? '<div style="margin-top:8px"><button id="words-ok">Yes, use these views</button> <span class="muted">sets the ticks above; you can still change them</span></div>' : ""}</div>`;
      const ok = document.getElementById("words-ok");
      if (ok) ok.onclick = () => {
        const ids = new Set(usable.map((v) => v.id));
        wordsPending = false;
        ok.replaceWith(Object.assign(document.createElement("span"), { className: "muted", textContent: "✓ Ticks set from your words" }));
        setTo((c) => ids.has(c.dataset.pick));
      };
    } else box.innerHTML = "";
    summary();
  };
  words.oninput = () => { clearTimeout(typing); typing = setTimeout(readWords, 400); };
  const useFile = document.getElementById("use-file");
  if (useFile) useFile.onclick = () => { words.value = f.text; readWords(); words.scrollIntoView({ behavior: "smooth", block: "center" }); };
  if (words.value.trim()) await readWords(); else summary();
  plan.onclick = async () => {
    plan.disabled = true; plan.textContent = "Planning…";
    try { await api(`/jobs/${enc(job.id)}/views`, { views: picked(), words: words.value.trim() }); route(); }
    catch (e) { summary(); document.getElementById("msg").innerHTML = problem(e.data); }
  };
}

// ---------- Approval ----------
async function approvalScreen(job, meta, warn) {
  const a = await api(`/jobs/${enc(job.id)}/approval`);
  if (!Object.keys(state.viewLabels).length) { const h = await api("/home"); state.viewLabels = Object.fromEntries(h.views.map((v) => [v.id, v.label])); }
  const need = a.materials.filter((m) => m.needs_choice).length;
  const dwellings = Object.entries(a.dwellings).map(([d, views]) => `<h2>${esc(d)} <span class="muted" style="font-size:15px">· ${views.filter((v) => v.ready).length} views</span></h2><div class="views">${views.map((v) => `<div class="view ${v.ready ? "" : "off"}"><strong>${esc(v.view)}</strong>${v.check ? " *" : ""}<small>${esc(v.where)}</small>${v.ready ? "" : `<small>Not made: ${esc(v.why_not)}</small>`}</div>`).join("")}</div>`).join("");
  const made = Object.entries(a.dwellings).map(([d, views]) => [d, views.filter((v) => v.ready)]);
  const madeList = made.map(([d, views]) => `<p style="margin:6px 0"><strong>${esc(d)}:</strong> ${views.length ? views.map((v) => esc(v.view) + (v.check ? " *" : "")).join(" · ") : '<span class="muted">nothing</span>'}</p>`).join("");
  const blocked = Object.values(a.dwellings).flat().filter((v) => !v.ready);
  // a blocked view: say what the drawings mean (re-plan) or have it rendered anyway (marked for a closer look)
  const blockedList = blocked.length ? `<h3 style="margin:16px 0 4px">Blocked by the drawings</h3><p class="muted" style="margin:0 0 8px">The drawings disagree or are unclear for these views, so they are not made yet. Say what it actually means and apply it, or tick Render anyway.</p>
    ${blocked.map((v) => `<div class="unsure"><p style="margin:0 0 4px"><strong>${esc(v.view)}</strong> <span class="muted">${esc(v.where)}</span></p>
      <ul style="margin:0 0 6px">${v.blockers.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>
      <label for="blk-${esc(v.render_id)}">What it actually means</label><textarea id="blk-${esc(v.render_id)}" data-blk="${esc(v.render_id)}" rows="2" maxlength="500" placeholder="e.g. The elevation is right: three upper front windows; the first-floor plan is out of date">${esc(state.meanings["blk:" + v.render_id] || "")}</textarea>
      ${v.can_render_anyway ? `<label class="check"><input type="checkbox" data-anyway="${esc(v.render_id)}"> Render anyway (marked for a closer look at review)</label>` : '<p class="muted" style="margin:6px 0 0">Cannot be rendered anyway: the drawing it needs is missing from the set.</p>'}</div>`).join("")}` : "";
  const materials = a.materials.map((m) => {
    const opts = m.options.map((o) => `<option value="${esc(o)}">`).join("");
    const drawn = m.from_drawings && m.from_drawings !== "NOT FOUND";
    return `<div class="material ${m.needs_choice ? "need" : ""}"><div><strong>${esc(m.label)}</strong><br><span class="muted">${drawn ? "From the drawings: " + esc(m.from_drawings) : "Not in the drawings"}</span></div>
      <div><input type="text" list="opt-${esc(m.key)}" data-mat="${esc(m.key)}" data-was="${esc(m.value)}" value="${esc(m.value)}" placeholder="${drawn ? "As drawn" : "Choose or type"}" aria-label="${esc(m.label)}" ${m.needs_choice ? "required" : ""}><datalist id="opt-${esc(m.key)}">${opts}</datalist></div></div>`;
  }).join("");
  const unsure = a.uncertainties.length ? `<h2>Unclear or disagreeing in the drawings</h2><p class="muted" style="margin-top:-6px">Both values are kept. If you know what it means, say so: the plan is made again with your answer before any render.</p>
    <section class="panel">${a.uncertainties.map((u, i) => `<div class="unsure"><p style="margin:0 0 6px">${esc(u)}</p><label for="mean-${i}">What it actually means</label><textarea id="mean-${i}" data-mean="${i}" rows="2" maxlength="500" placeholder="e.g. The window on the garage wall is 1800 wide; the 1200 on sheet 3 is old">${esc(state.meanings[u] || "")}</textarea></div>`).join("")}</section>` : "";
  const ignored = a.ignored_directives.length ? `<div class="notice"><strong>These options were not used</strong>${a.ignored_directives.map((d) => `<p class="mono">${esc(d)}</p>`).join("")}</div>` : "";
  const changed = a.changes ? `<div class="notice good" role="status"><strong>Revised plan</strong>${a.changes.length ? `<ul>${a.changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : "<p>The plan came out the same.</p>"}${a.corrections.length ? `<p class="muted" style="margin:6px 0 0">Corrections for this job: ${a.corrections.map((c) => esc(`${c.fact.slice(0, 60)} → ${c.value}`)).join("; ")}</p>` : ""}</div>` : "";
  const dwPick = a.dwellings_available.length > 1 ? `<label for="dw" style="margin:0">Dwellings</label><input type="text" id="dw" value="all" placeholder="all, or e.g. 1, 3" style="max-width:180px">` : "";
  const corr = state.draftCorrections.map((c, i) => `<div class="grid2"><div><label>Fact</label><input type="text" data-cf="${i}" value="${esc(c.fact)}" placeholder="e.g. Roof pitch"></div><div><label>Correct value</label><input type="text" data-cv="${i}" value="${esc(c.value)}" placeholder="e.g. 25 degrees"></div></div>`).join("");

  view.innerHTML = head("Waiting for your approval", "Check the views and materials. Nothing is rendered, and nothing is paid for, until you approve.", meta, 2)
    + warn + `<div id="msg"></div>` + changed
    + `<section class="panel summary"><span class="count">${a.planned_images}</span><span><strong>images will be made</strong><br><span class="muted">${a.practice_run ? "Practice run: placeholder images, no cost." : "Each one is checked against the drawings. Renders are drafts: approving one delivers that same image, without the DRAFT mark, at Full HD."}${need ? ` ${need} material${need > 1 ? "s need" : " needs"} your choice first.` : ""}</span></span></section>`
    + ignored
    + `<h2>These renders will be made</h2><section class="panel">${madeList}${blockedList}
        <div class="actions" style="margin-top:12px"><button id="change-views">Change the views</button><span class="muted">Back to Choose views; the plan is made again after your new choice.</span></div></section>`
    + dwellings + `<p class="muted">* will be marked for a closer look: the drawings were unclear.</p>`
    + `<h2>Materials and colours</h2>` + (materials ? `<section class="panel materials">${materials}</section>` : '<p class="muted">No material choices for this set.</p>')
    + unsure + `<h2>Correct a fact</h2><p class="muted" style="margin-top:-6px">Corrections apply to this job only.</p><div id="corr">${corr}</div><button class="link" id="add-corr">+ Add a correction</button>`
    + `<div class="sticky-actions"><div class="actions"><button id="replan" disabled>Apply and show the revised plan</button><button class="primary" id="approve">Approve these ${a.planned_images} renders</button>${dwPick}</div>
       <p class="muted" style="margin:8px 0 0">Answered something above? Apply it first: the drawings are read again with your answers (a small reading cost, no images) and you see the revised plan.</p></div>`;

  document.getElementById("change-views").onclick = async (e) => {
    e.target.disabled = true;
    try { await api(`/jobs/${enc(job.id)}/approval/choose-views`, {}); route(); }
    catch (err) { e.target.disabled = false; document.getElementById("msg").innerHTML = problem(err.data); }
  };

  // --- meanings and corrections: re-plan with the LLM, then show what changed ---
  const sync = () => {
    state.draftCorrections.forEach((c, i) => { c.fact = view.querySelector(`[data-cf="${i}"]`).value; c.value = view.querySelector(`[data-cv="${i}"]`).value; });
    view.querySelectorAll("[data-mean]").forEach((t) => (state.meanings[a.uncertainties[+t.dataset.mean]] = t.value));
    view.querySelectorAll("[data-blk]").forEach((t) => (state.meanings["blk:" + t.dataset.blk] = t.value));
  };
  const anyway = () => [...view.querySelectorAll("[data-anyway]:checked")].map((c) => c.dataset.anyway);
  const approveBtn = document.getElementById("approve");
  const count = () => { const n = a.planned_images + anyway().length; approveBtn.textContent = `Approve these ${n} render${n === 1 ? "" : "s"}`; approveBtn.disabled = n === 0; };
  view.querySelectorAll("[data-anyway]").forEach((c) => (c.onchange = count));
  count();
  const pending = () => [
    ...a.uncertainties.filter((u) => (state.meanings[u] || "").trim()).map((u) => ({ fact: u.slice(0, 200), value: state.meanings[u].trim(), note: "" })),
    ...blocked.filter((v) => (state.meanings["blk:" + v.render_id] || "").trim()).map((v) => ({ fact: `${v.view} (${v.render_id}): ${v.blockers.join("; ")}`.slice(0, 200), value: state.meanings["blk:" + v.render_id].trim(), note: "" })),
    ...state.draftCorrections.filter((c) => c.fact.trim() && c.value.trim()),
  ];
  const replanBtn = document.getElementById("replan");
  const watch = () => { sync(); replanBtn.disabled = !pending().length; };
  view.querySelectorAll("[data-mean], [data-blk], [data-cf], [data-cv]").forEach((t) => (t.oninput = watch));
  watch();
  replanBtn.onclick = async () => {
    sync();
    replanBtn.disabled = true; replanBtn.textContent = "Applying…";
    try {
      const r = await api(`/jobs/${enc(job.id)}/approval/replan`, { corrections: pending() });
      state.draftCorrections = []; state.meanings = {};
      (r.warnings || []).forEach(toast);
      route();
    } catch (e) { replanBtn.disabled = false; replanBtn.textContent = "Apply and show the revised plan"; document.getElementById("msg").innerHTML = problem(e.data); window.scrollTo({ top: 0, behavior: "smooth" }); }
  };
  document.getElementById("add-corr").onclick = () => { sync(); state.draftCorrections.push({ fact: "", value: "" }); approvalScreen(job, meta, warn); };
  document.getElementById("approve").onclick = async () => {
    sync();
    if (pending().length && !confirm("You have answers that are not applied yet. Approve without re-planning? They will still be passed to every render.")) return;
    const choices = {};
    view.querySelectorAll("[data-mat]").forEach((i) => { if (i.value.trim() && i.value !== i.dataset.was) choices[i.dataset.mat] = i.value.trim(); });
    const corrections = pending();
    const btn = document.getElementById("approve"); const label = btn.textContent; btn.disabled = true; btn.textContent = "Approving…";
    try {
      const r = await api(`/jobs/${enc(job.id)}/approval`, { choices, corrections, render_anyway: anyway(), dwellings: document.getElementById("dw")?.value || "all" });
      state.draftCorrections = []; state.meanings = {};
      (r.warnings || []).forEach(toast);
      route();
    } catch (e) { btn.disabled = false; btn.textContent = label; count(); document.getElementById("msg").innerHTML = problem(e.data); window.scrollTo({ top: 0, behavior: "smooth" }); }
  };
}

// ---------- Review ----------
async function reviewScreen(job, meta, warn) {
  const r = await api(`/jobs/${enc(job.id)}/review`);
  const file = (rel) => `/api/jobs/${enc(job.id)}/file/${rel.split("/").map(enc).join("/")}`;
  const cats = Object.entries(r.categories).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
  const count = (s) => r.cards.filter((c) => c.status === s).length;
  const flagged = count("flagged"), pending = count("pending"), approved = count("approved");
  const cards = r.cards.map((c) => `<article class="card ${c.status}">
      <div class="img">${c.image ? `<button class="zoom" data-zoom="${esc(c.render_id)}" aria-label="Look closely at ${esc(c.title)}"><img src="${file(c.image)}" alt="${esc(c.title)} render" loading="lazy"></button>` : `<span class="muted">${esc(c.status_text)}</span>`}
        ${c.reference ? `<figure class="ref" title="The drawing it was made from"><img src="${file(c.reference)}" alt="Drawing for ${esc(c.title)}"></figure>` : ""}</div>
      <div class="body"><h3>${esc(c.title)}${c.uncertain && c.image ? " *" : ""}</h3><span class="muted where" title="${esc(c.where)}">${esc(c.dwelling)}${c.where ? " · " + esc(c.where) : ""}</span>
        <div class="meta">${c.practice ? '<span class="pill wait" title="Made by a practice run: a stand-in picture, not a render">Practice image</span>' : ""}<span class="pill ${c.check.startsWith("Did not") ? "bad" : c.check.startsWith("Matches") ? "good" : ""}">${esc(c.check)}${c.confidence != null ? ` · ${Math.round(c.confidence * 100)}% match` : ""}</span>${c.status !== "pending" ? `<span class="pill ${c.status === "approved" ? "good" : c.status === "flagged" ? "bad" : ""}">${esc(c.status_text)}</span>` : ""}</div>
        ${c.note ? `<p>${esc(c.note)}</p>` : ""}
        ${c.status === "pending" && state.flagOpen !== c.render_id ? `<div class="buttons"><button class="approve" data-ok="${esc(c.render_id)}">Approve</button><button class="flag" data-flag="${esc(c.render_id)}">Flag</button></div>` : ""}
        ${c.status === "flagged" && state.flagOpen !== c.render_id ? `<div class="buttons"><button data-flag="${esc(c.render_id)}">Edit feedback</button></div>` : ""}
        ${state.flagOpen === c.render_id ? `<div class="flagform"><label for="cat">What is wrong?</label><select id="cat">${cats}</select>
          <label for="note">What should change?</label><textarea id="note" rows="3" placeholder="e.g. Remove the window on the garage wall">${c.status === "flagged" ? esc(c.note) : ""}</textarea>
          <label class="check"><input type="checkbox" id="tojob"> Also use this for the rest of this job</label>
          <label class="check"><input type="checkbox" id="rule"> Suggest this as a general rule</label>
          <div class="buttons"><button class="flag" id="send-flag">${c.status === "flagged" ? "Save feedback" : "Flag this render"}</button><button id="cancel-flag">Cancel</button></div></div>` : ""}
      </div></article>`).join("");
  const label = flagged ? `Continue: redo ${flagged} flagged` : pending ? "Save and come back later" : "Finish";
  view.innerHTML = head("Ready for your review", "Approve the renders you are happy with. Flag the others with what to change: only flagged renders are made again.", meta, 4)
    + warn + `<div id="msg"></div><div class="cards">${cards}</div>
    <div class="sticky-actions"><div class="actions"><button class="primary" id="continue">${label}</button>
      ${pending ? `<button class="link" id="approve-rest">Approve all ${pending} waiting</button>` : ""}
      <span class="tally"><span class="pill good">${approved} approved</span><span class="pill bad">${flagged} flagged</span><span class="pill">${pending} waiting</span></span></div>
      <p class="muted" style="margin:10px 0 0;font-size:14px">Only flagged renders are made again; renders you leave waiting stay as they are. * not fully checked against the drawings: look closely.</p></div>`;

  view.querySelectorAll("[data-zoom]").forEach((b) => (b.onclick = () => showViewer(r.cards.find((c) => c.render_id === b.dataset.zoom), file)));

  const decide = async (rid, body) => {
    try { const res = await api(`/jobs/${enc(job.id)}/review/${enc(rid)}`, body); if (res.warning) toast(res.warning); }
    catch (e) { document.getElementById("msg").innerHTML = problem(e.data); }
  };
  view.querySelectorAll("[data-ok]").forEach((b) => (b.onclick = async () => { await decide(b.dataset.ok, { action: "approve" }); reviewScreen(job, meta, warn); }));
  view.querySelectorAll("[data-flag]").forEach((b) => (b.onclick = () => { state.flagOpen = b.dataset.flag; reviewScreen(job, meta, warn).then(() => {
    const cat = document.getElementById("cat"), was = r.cards.find((c) => c.render_id === b.dataset.flag)?.category;
    if (cat && was) cat.value = was;
    document.getElementById("note")?.focus();
  }); }));
  const send = document.getElementById("send-flag");
  if (send) send.onclick = async () => {
    const note = document.getElementById("note").value.trim();
    if (!note) { document.getElementById("note").focus(); toast("Say what should change."); return; }
    await decide(state.flagOpen, { action: "flag", category: document.getElementById("cat").value, note, apply_to_job: document.getElementById("tojob").checked, suggest_rule: document.getElementById("rule").checked });
    state.flagOpen = null; reviewScreen(job, meta, warn);
  };
  const cancel = document.getElementById("cancel-flag");
  if (cancel) cancel.onclick = () => { state.flagOpen = null; reviewScreen(job, meta, warn); };
  const rest = document.getElementById("approve-rest");
  if (rest) rest.onclick = async () => { for (const c of r.cards.filter((x) => x.status === "pending")) await decide(c.render_id, { action: "approve" }); reviewScreen(job, meta, warn); };
  document.getElementById("continue").onclick = async () => { await api(`/jobs/${enc(job.id)}/review/continue`, {}); route(); };
}

// Look closely: the render large, next to the drawing it was made from.
function showViewer(c, file) {
  const v = document.getElementById("viewer");
  v.innerHTML = `<div class="viewer-head"><div><strong>${esc(c.title)}</strong> <span class="muted">${esc(c.dwelling)} · version ${esc(c.version)}</span>
      ${c.practice ? '<span class="pill wait">Practice image</span>' : ""}</div><button id="viewer-close" autofocus>Close</button></div>
    <div class="viewer-body ${c.reference ? "" : "single"}">
      <figure><img src="${file(c.image)}" alt="${esc(c.title)} render"><figcaption>${c.status === "approved" ? "Approved: delivered without the DRAFT mark at Full HD" : "Render (DRAFT until you approve it)"}</figcaption></figure>
      ${c.reference ? `<figure><img src="${file(c.reference)}" alt="Drawing for ${esc(c.title)}"><figcaption>The drawing it was made from</figcaption></figure>` : ""}
    </div>`;
  v.showModal();
  document.getElementById("viewer-close").onclick = () => v.close();
}

// ---------- Results ----------
function resultsScreen(job, meta, warn) {
  const f = { shared: "Common", draft: "Draft", draft_final: "draft-final", final: "FINAL APPROVED", ...(job.folders || {}) };
  view.innerHTML = head("Finished", "Every render is approved. The final images are in the Output folder, filed by dwelling.", meta, 5)
    + warn + `<section class="panel progress"><div class="done-mark" aria-hidden="true">✓</div>
      <p style="margin:0 0 6px"><strong>Approved images</strong> are in each dwelling's <span class="path">${esc(f.final)}</span> folder (views shared by several dwellings also in <span class="path">${esc(f.shared)}</span>), under <span class="path">${esc(job.output_folder || "")}</span></p>
      <p class="muted" style="margin:0">Every version with the DRAFT mark is in the <span class="path">${esc(f.draft)}</span> folder beside it; the approved one is also in <span class="path">${esc(f.draft)}\\${esc(f.draft_final)}</span>. report.html lists every render with tokens and estimated cost.</p></section>
    <div class="actions"><button class="primary" id="open-out">Open Output folder</button><button id="open-hist" ${job.history_folder ? "" : "disabled"}>Open History folder</button><a href="#/home">Back to Home</a></div>`;
  const open = (which) => api(`/jobs/${enc(job.id)}/open-folder`, { which }).catch((e) => toast(e.data.what_happened));
  document.getElementById("open-out").onclick = () => open("output");
  document.getElementById("open-hist").onclick = () => open("history");
}

// ---------- Settings ----------
async function settingsScreen(results = {}) {
  const s = await api("/settings");
  const rows = s.providers.map((p) => {
    const r = results[p.name];
    return `<section class="panel" style="padding:18px 22px;margin-bottom:14px">
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:center"><span><strong>${esc(p.label)}</strong><br><span class="muted">Used for ${esc(p.used_for)}</span></span>
      <span class="pill ${p.has_key ? "good" : "bad"}">${p.has_key ? "Key saved" : "No key yet"}</span></div>
      <label for="k-${esc(p.name)}">${p.has_key ? "Replace the key" : "Paste the key"}</label><input type="password" id="k-${esc(p.name)}" data-key="${esc(p.name)}" autocomplete="off" placeholder="sk-…">
      <div class="actions" style="margin-top:12px"><button data-test="${esc(p.name)}">Test connection</button><span id="t-${esc(p.name)}" class="muted">${r ? esc(r.message) : ""}</span></div></section>`;
  }).join("");
  const llms = s.llm_choices.map((c) => `<option value="${esc(c)}" ${c === s.llm_choice ? "selected" : ""}>${esc(c)}</option>`).join("");
  const saved = s.providers.filter((p) => p.has_key).length;
  view.innerHTML = head("Keys and services", "Keys are kept in Windows Credential Manager on this laptop. Testing a key never costs anything.", [`${saved} of ${s.providers.length} keys saved`, `Images: ${esc(s.image_providers.join(", ") || "none yet")}`])
    + rows + `<section class="panel" style="padding:18px 22px"><label for="llm" style="margin-top:0">Drawing reader for new jobs</label><select id="llm"><option value="">Standard</option>${llms}</select></section>
    <div class="actions"><button class="primary" id="save">Save keys</button></div>`;
  view.querySelectorAll("[data-test]").forEach((b) => (b.onclick = async () => {
    const out = document.getElementById(`t-${b.dataset.test}`); out.textContent = "Testing…";
    const r = await api("/settings/test", { provider: b.dataset.test, key: document.getElementById(`k-${b.dataset.test}`).value });
    out.textContent = r.message; out.style.color = r.ok ? "var(--approve)" : "var(--redline)";
  }));
  document.getElementById("llm").onchange = async (e) => { await api("/settings/llm", { llm_choice: e.target.value }); toast("Drawing reader saved."); };
  document.getElementById("save").onclick = async () => {
    const keys = {}; view.querySelectorAll("[data-key]").forEach((i) => { if (i.value.trim()) keys[i.dataset.key] = i.value.trim(); });
    if (!Object.keys(keys).length) { toast("Paste a key first."); return; }
    const r = await api("/settings", { keys });
    settingsScreen(r.results);
  };
}

// ---------- Help ----------
async function helpScreen() {
  const checks = await api("/help/doctor");
  const problems = checks.filter((c) => c.level !== "OK").length;
  const list = checks.map((c) => `<div class="row"><span>${esc(c.text)}</span><span class="pill ${c.level === "OK" ? "good" : "bad"}">${esc(c.level)}</span></div>`).join("");
  view.innerHTML = head("Help", "If something is not working, copy the diagnostics and send them to whoever looks after Render Agent.", [problems ? `${problems} check${problems > 1 ? "s" : ""} to look at` : "All checks fine"])
    + `<h2 style="margin-top:0">Health checks</h2><div class="list">${list}</div>
    <div class="actions"><button id="copy">Copy diagnostics</button></div>
    <h2>Send your feedback</h2><p class="muted">Makes a zip of your flags and corrections with small thumbnails. No drawings and no keys. It is saved in the RenderAgent folder on your Desktop.</p>
    <div class="actions"><button class="primary" id="export">Export feedback</button></div>`;
  document.getElementById("copy").onclick = async () => {
    const d = await api("/help/diagnostics");
    try { await navigator.clipboard.writeText(d.text); toast("Diagnostics copied."); }
    catch { const t = document.createElement("textarea"); t.value = d.text; document.body.append(t); t.select(); document.execCommand("copy"); t.remove(); toast("Diagnostics copied."); }
  };
  document.getElementById("export").onclick = async () => {
    try { const r = await api("/help/export-feedback", {}); toast(`Feedback exported: ${r.path}`); } catch (e) { toast(e.data.what_happened); }
  };
}

// ---------- drag a PDF onto the window ----------
const veil = document.getElementById("drop-veil");
let depth = 0;
window.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; veil.hidden = false; });
window.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; veil.hidden = true; } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", async (e) => {
  e.preventDefault(); depth = 0; veil.hidden = true;
  for (const f of e.dataTransfer.files) {
    const fd = new FormData(); fd.append("file", f);
    try { const item = await api("/inputs/upload", fd); state.selected = item.name; toast(`“${item.name}” is in Input. Press Start when ready.`); }
    catch (err) { toast(err.data.what_happened); }
  }
  location.hash = "#/home"; route();
});

route();
