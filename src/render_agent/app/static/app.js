// Render Agent desktop app. No build step: plain JS talking to the local API.
"use strict";

const TOKEN = document.querySelector('meta[name="session-token"]').content;
const view = document.getElementById("view");
let pollTimer = null;
let state = { selected: null, flagOpen: null, draftCorrections: [] };

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
const STEPS = ["Start", "Approve the plan", "Make the renders", "Review", "Done"];
function steps(now, attention = false) {
  return `<ol class="steps" aria-label="Job steps">${STEPS.map((s, i) =>
    `<li class="${i < now ? "done" : i === now ? "now" + (attention ? " attention" : "") : ""}" ${i === now ? 'aria-current="step"' : ""}><span></span>${esc(s)}</li>`).join("")}</ol>`;
}

function head(title, lede, meta = [], step = null, attention = false) {
  const m = meta.map((x) => `<span>${x}</span>`).join('<span class="sep"></span>');
  return `<header class="head">${m ? `<div class="eyebrow">${m}</div>` : ""}<h1>${esc(title)}</h1>${lede ? `<p class="lede">${esc(lede)}</p>` : ""}${step !== null ? steps(step, attention) : ""}</header>`;
}

function pill(status, text) {
  const kind = { AWAITING_APPROVAL: "wait", AWAITING_REVIEW: "wait", FAILED: "bad", PAUSED: "bad", SUCCEEDED: "good", RUNNING: "run", RENDERING: "run", NEW: "run" }[status] || "";
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
  if (state.selected && !home.inputs.some((i) => i.name === state.selected && i.startable)) state.selected = null;
  if (!state.selected) { const first = home.inputs.find((i) => i.startable); state.selected = first ? first.name : null; }
  const active = home.jobs.filter((j) => j.status !== "SUCCEEDED");
  const needYou = home.jobs.filter((j) => ["AWAITING_APPROVAL", "AWAITING_REVIEW", "FAILED", "PAUSED"].includes(j.status)).length;

  const warns = home.warnings.map((w) => `<div class="notice ${w.kind === "onedrive" ? "" : "bad"}"><p>${esc(w.text)}</p>${w.kind === "onedrive" ? '<div><button class="link" id="dismiss-od">Got it, don\'t show this again</button></div>' : w.kind === "key" ? '<div><a href="#/settings">Add the key</a></div>' : ""}</div>`).join("");

  const items = home.inputs.map((i) => `<div class="item" data-input="${esc(i.name)}" role="radio" tabindex="0" aria-checked="${i.name === state.selected}" ${i.startable ? "" : 'aria-disabled="true"'}>
      <span class="radio"></span>
      <span><span class="name">${esc(i.name)}</span><br><span class="muted">${i.kind === "folder" ? "Folder of drawings" : "PDF"}${i.kind === "pdf+txt" ? " with instructions" : ""} · ${i.size_mb} MB</span></span>
      ${i.startable ? '<span class="pill good">Ready</span>' : `<span class="pill bad">${esc(i.reason)}</span>`}</div>`).join("");

  const zone = `<section class="dropzone" aria-label="Drawing sets waiting in Input">
    <div class="dz-head"><strong>${home.inputs.length ? "Choose a drawing set" : "Add a drawing set"}</strong><button class="link" id="refresh">Refresh</button></div>
    ${home.inputs.length ? `<div class="items" role="radiogroup">${items}</div>` : `<div class="dz-empty">${PDF_ICON}<p><strong>Drag a PDF onto this window</strong></p><p class="muted">or put it in <span class="path">${esc(home.input_folder)}</span></p></div>`}
  </section>`;

  const jobs = home.jobs.length
    ? home.jobs.map((j) => `<div class="row selectable" data-job="${esc(j.id)}" tabindex="0">
        <span><span class="name">${esc(j.input_name)}</span><br><span class="sub">${j.error ? esc(j.error.what_happened) : `Updated ${esc(j.updated.slice(0, 16).replace("T", " "))}`}</span></span>
        <span class="end">${pill(j.status, j.status_text)}${j.can_retry ? `<button data-retry="${esc(j.id)}">Retry</button>` : ""}</span></div>`).join("")
    : '<div class="empty">Jobs you start appear here.</div>';

  const providers = home.providers.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
  const llms = home.llm_choices.map((c) => `<option value="${esc(c)}" ${c === prefs.llm_choice ? "selected" : ""}>${esc(c)}</option>`).join("");
  const meta = [`${home.inputs.length} waiting in Input`, `${active.length} in progress`, needYou ? `${needYou} need you` : "nothing needs you"];

  view.innerHTML = head("Start a job", "Choose a drawing set and press Start. The original moves to History, so it is never started twice.", meta)
    + warns + `<div id="msg">${keepMessage || ""}</div>` + zone
    + `<details class="options"><summary>Start options</summary><div class="panel"><div class="grid2">
        <div><label class="check"><input type="checkbox" id="o-dry"> Practice run: placeholder images, no cost</label>
             <label class="check"><input type="checkbox" id="o-night"> Add a night view of the front</label>
             <label class="check"><input type="checkbox" id="o-street" checked> Include the streetscape (2 or more dwellings)</label></div>
        <div><label for="o-llm">Drawing reader</label><select id="o-llm"><option value="">Standard</option>${llms}</select>
             <label for="o-provider">Image service</label><select id="o-provider"><option value="">Standard</option>${providers}</select></div>
        <div><label for="o-dw">Only these dwellings</label><input type="text" id="o-dw" placeholder="All, or e.g. 1, 3"></div>
        <div><label for="o-views">Only these views</label><input type="text" id="o-views" placeholder="All, or e.g. Front day, Kitchen"></div>
        <div><label for="o-max">Most images per dwelling</label><input type="number" id="o-max" min="1" max="16" placeholder="16"></div>
      </div></div></details>
    <div class="actions"><button class="primary" id="start" ${state.selected && home.can_start ? "" : "disabled"}>${state.selected ? `Start “${esc(state.selected)}”` : "Start"}</button>
      ${!home.can_start ? '<span class="muted">Fix the message above to start.</span>' : !state.selected ? '<span class="muted">Add a drawing set first.</span>' : ""}</div>
    <h2>Your jobs</h2><div class="list">${jobs}</div>`;

  view.querySelectorAll("[data-input]").forEach((el) => {
    const pick = () => { if (el.getAttribute("aria-disabled")) return; state.selected = el.dataset.input; homeScreen(document.getElementById("msg").innerHTML); };
    el.onclick = pick; el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } };
  });
  view.querySelectorAll("[data-job]").forEach((el) => {
    el.onclick = (e) => { if (!e.target.dataset.retry) location.hash = `#/job/${enc(el.dataset.job)}`; };
    el.onkeydown = (e) => { if (e.key === "Enter") location.hash = `#/job/${enc(el.dataset.job)}`; };
  });
  view.querySelectorAll("[data-retry]").forEach((b) => (b.onclick = async () => { await api(`/jobs/${enc(b.dataset.retry)}/retry`, {}); toast("Retrying: the job carries on from where it stopped."); homeScreen(); }));
  document.getElementById("refresh").onclick = () => homeScreen();
  const od = document.getElementById("dismiss-od");
  if (od) od.onclick = async () => { await api("/home/dismiss-onedrive", {}); homeScreen(); };
  document.getElementById("start").onclick = () => startSelected(false);
  poll(async () => { if (!document.querySelector("details.options[open]") && document.activeElement?.tagName !== "INPUT") homeScreen(document.getElementById("msg")?.innerHTML); }, 5000);
}

function startOptions() {
  const list = (v) => v.split(",").map((s) => s.trim()).filter(Boolean);
  const max = parseInt(document.getElementById("o-max").value, 10);
  return {
    night: document.getElementById("o-night").checked || null,
    streetscape: document.getElementById("o-street").checked ? null : false,
    dry_run: document.getElementById("o-dry").checked || null,
    llm: document.getElementById("o-llm").value || null,
    provider: document.getElementById("o-provider").value || null,
    dwellings: list(document.getElementById("o-dw").value),
    views: list(document.getElementById("o-views").value),
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
  if (job.queued === null && job.screen === "approval") return approvalScreen(job, meta, warn);
  if (job.queued === null && job.screen === "review") return reviewScreen(job, meta, warn);
  if (job.queued === null && job.screen === "results") return resultsScreen(job, meta, warn);
  return progressScreen(job, meta, warn);
}

function stepOf(job) {
  const at = { "Waiting for your approval": 1, "Preparing each render": 2, "Making the renders": 2, "Checking renders against the drawings": 2, "Getting renders ready for review": 3 };
  return at[job.stage_text] ?? 0;
}

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
  const afterApproval = stepOf(job) >= 1;
  const counting = job.stage_text === "Making the renders" || job.stage_text === "Checking renders against the drawings";
  const what = job.queued > 0 ? `Waiting its turn (${job.queued} ahead)`
    : job.stage_text === "Waiting for your approval" ? "Starting the renders…" : job.stage_text || "Working on it…";
  const detail = counting ? `${p.done} of ${p.total} renders` : afterApproval ? "Preparing each render from the drawings" : `Part ${Math.min(p.done + 1, 5)} of 5`;
  view.innerHTML = head(afterApproval ? "Making your renders" : "Reading the drawings", "You can close this window. The job carries on when you open Render Agent again.", meta, afterApproval ? 2 : 0)
    + warn + `<section class="panel progress"><p class="stage">${esc(what)}</p><div class="bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pct}%"></span></div>
    <p class="muted">${esc(detail)}</p></section>
    <div class="actions"><a href="#/home">Back to Home</a></div>`;
  poll(async () => { const j = await api(`/jobs/${enc(job.id)}`); if (j.status !== job.status || j.queued !== job.queued || j.stage_text !== job.stage_text || (j.progress && j.progress.done !== p.done)) route(); });
}

// ---------- Approval ----------
async function approvalScreen(job, meta, warn) {
  const a = await api(`/jobs/${enc(job.id)}/approval`);
  const need = a.materials.filter((m) => m.needs_choice).length;
  const dwellings = Object.entries(a.dwellings).map(([d, views]) => `<h2>${esc(d)} <span class="muted" style="font-size:15px">· ${views.filter((v) => v.ready).length} views</span></h2><div class="views">${views.map((v) => `<div class="view ${v.ready ? "" : "off"}"><strong>${esc(v.view)}</strong>${v.check ? " *" : ""}<small>${esc(v.where)}</small>${v.ready ? "" : `<small>Not made: ${esc(v.why_not)}</small>`}</div>`).join("")}</div>`).join("");
  const materials = a.materials.map((m) => {
    const opts = m.options.map((o) => `<option value="${esc(o)}">`).join("");
    const drawn = m.from_drawings && m.from_drawings !== "NOT FOUND";
    return `<div class="material ${m.needs_choice ? "need" : ""}"><div><strong>${esc(m.label)}</strong><br><span class="muted">${drawn ? "From the drawings: " + esc(m.from_drawings) : "Not in the drawings"}</span></div>
      <div><input type="text" list="opt-${esc(m.key)}" data-mat="${esc(m.key)}" data-was="${esc(m.value)}" value="${esc(m.value)}" placeholder="${drawn ? "As drawn" : "Choose or type"}" aria-label="${esc(m.label)}" ${m.needs_choice ? "required" : ""}><datalist id="opt-${esc(m.key)}">${opts}</datalist></div></div>`;
  }).join("");
  const unsure = a.uncertainties.length ? `<h2>The drawings disagree here</h2><div class="notice"><ul>${a.uncertainties.map((u) => `<li>${esc(u)}</li>`).join("")}</ul><p class="muted">Both values are kept. Correct the fact below if you know which is right.</p></div>` : "";
  const ignored = a.ignored_directives.length ? `<div class="notice"><strong>These options were not used</strong>${a.ignored_directives.map((d) => `<p class="mono">${esc(d)}</p>`).join("")}</div>` : "";
  const dwPick = a.dwellings_available.length > 1 ? `<label for="dw" style="margin:0">Dwellings</label><input type="text" id="dw" value="all" placeholder="all, or e.g. 1, 3" style="max-width:180px">` : "";
  const corr = state.draftCorrections.map((c, i) => `<div class="grid2"><div><label>Fact</label><input type="text" data-cf="${i}" value="${esc(c.fact)}" placeholder="e.g. Roof pitch"></div><div><label>Correct value</label><input type="text" data-cv="${i}" value="${esc(c.value)}" placeholder="e.g. 25 degrees"></div></div>`).join("");

  view.innerHTML = head("Waiting for your approval", "Check the views and materials. Nothing is rendered, and nothing is paid for, until you approve.", meta, 1)
    + warn + `<div id="msg"></div>`
    + `<section class="panel summary"><span class="count">${a.planned_images}</span><span><strong>images will be made</strong><br><span class="muted">${a.practice_run ? "Practice run: placeholder images, no cost." : "Each one is checked against the drawings."}${need ? ` ${need} material${need > 1 ? "s need" : " needs"} your choice first.` : ""}</span></span></section>`
    + ignored + dwellings + `<p class="muted">* will be marked for a closer look: the drawings were unclear.</p>`
    + `<h2>Materials and colours</h2>` + (materials ? `<section class="panel materials">${materials}</section>` : '<p class="muted">No material choices for this set.</p>')
    + unsure + `<h2>Correct a fact</h2><p class="muted" style="margin-top:-6px">Corrections apply to this job only.</p><div id="corr">${corr}</div><button class="link" id="add-corr">+ Add a correction</button>`
    + `<div class="sticky-actions"><div class="actions"><button class="primary" id="approve">Approve and start rendering</button>${dwPick}</div></div>`;

  const sync = () => state.draftCorrections.forEach((c, i) => { c.fact = view.querySelector(`[data-cf="${i}"]`).value; c.value = view.querySelector(`[data-cv="${i}"]`).value; });
  document.getElementById("add-corr").onclick = () => { sync(); state.draftCorrections.push({ fact: "", value: "" }); approvalScreen(job, meta, warn); };
  document.getElementById("approve").onclick = async () => {
    sync();
    const choices = {};
    view.querySelectorAll("[data-mat]").forEach((i) => { if (i.value.trim() && i.value !== i.dataset.was) choices[i.dataset.mat] = i.value.trim(); });
    const corrections = state.draftCorrections.filter((c) => c.fact.trim() && c.value.trim());
    const btn = document.getElementById("approve"); btn.disabled = true; btn.textContent = "Approving…";
    try {
      const r = await api(`/jobs/${enc(job.id)}/approval`, { choices, corrections, dwellings: document.getElementById("dw")?.value || "all" });
      state.draftCorrections = [];
      (r.warnings || []).forEach(toast);
      route();
    } catch (e) { btn.disabled = false; btn.textContent = "Approve and start rendering"; document.getElementById("msg").innerHTML = problem(e.data); window.scrollTo({ top: 0, behavior: "smooth" }); }
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
  const label = flagged ? `Continue: redo ${flagged} flagged` : pending ? "Continue" : "Finish";
  view.innerHTML = head("Ready for your review", "Approve the renders you are happy with. Flag the others with what to change: only flagged renders are made again.", meta, 3)
    + warn + `<div id="msg"></div><div class="cards">${cards}</div>
    <div class="sticky-actions"><div class="actions"><button class="primary" id="continue">${label}</button>
      ${pending ? `<button class="link" id="approve-rest">Approve all ${pending} waiting</button>` : ""}
      <span class="tally"><span class="pill good">${approved} approved</span><span class="pill bad">${flagged} flagged</span><span class="pill">${pending} waiting</span></span></div>
      <p class="muted" style="margin:10px 0 0;font-size:14px">* not fully checked against the drawings: look closely.</p></div>`;

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
      <figure><img src="${file(c.image)}" alt="${esc(c.title)} render"><figcaption>Render (DRAFT until you approve it)</figcaption></figure>
      ${c.reference ? `<figure><img src="${file(c.reference)}" alt="Drawing for ${esc(c.title)}"><figcaption>The drawing it was made from</figcaption></figure>` : ""}
    </div>`;
  v.showModal();
  document.getElementById("viewer-close").onclick = () => v.close();
}

// ---------- Results ----------
function resultsScreen(job, meta, warn) {
  view.innerHTML = head("Finished", "Every render is approved. The final images are in the Output folder, filed by dwelling.", meta, 4)
    + warn + `<section class="panel progress"><div class="done-mark" aria-hidden="true">✓</div>
      <p style="margin:0 0 6px"><strong>Final images</strong> <span class="path">${esc(job.output_folder || "")}\\Final</span></p>
      <p class="muted" style="margin:0">Every version with the DRAFT mark is in the Draft folder next to it, with report.html.</p></section>
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
