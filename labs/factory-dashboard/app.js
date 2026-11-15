"use strict";
// Reads snapshot.json (same directory) and renders it. All snapshot text goes in
// through textContent, never innerHTML: issue titles are untrusted.

var STATION_RULES = [
  ["Exam", /\bexam\b|acceptance/i],
  ["Build", /\bbuild|implement|coding|writing code/i],
  // Ship before Check: "CI green, squash-merging" and "smoke check" are Ship steps.
  ["Ship", /merge|deploy|smoke|ship|release/i],
  ["Check", /\bci\b|review|check|verify/i],
];

function stationFor(step) {
  var text = step || "";
  for (var i = 0; i < STATION_RULES.length; i++) {
    if (STATION_RULES[i][1].test(text)) return STATION_RULES[i][0];
  }
  return "Plan";
}

function el(tag, text, cls) {
  var node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}

function link(href, text) {
  var a = el("a", text);
  if (/^https:\/\//.test(href || "")) a.href = href;
  a.rel = "noopener";
  return a;
}

function fill(id, rows, build, emptyText) {
  var ul = document.getElementById(id);
  ul.textContent = "";
  if (!rows || !rows.length) {
    ul.appendChild(el("li", emptyText || "Nothing here.", "empty"));
    return;
  }
  rows.forEach(function (row) {
    var li = el("li");
    build(li, row);
    ul.appendChild(li);
  });
}

function ago(iso) {
  var t = Date.parse(iso);
  if (isNaN(t)) return "at an unknown time";
  var min = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (min < 1) return "just now";
  if (min < 90) return min + " min ago";
  return Math.round(min / 60) + " h ago";
}

function sparkline(days, label) {
  var ns = "http://www.w3.org/2000/svg";
  var svg = document.createElementNS(ns, "svg");
  var max = Math.max.apply(null, days.concat([1]));
  var w = 100 / days.length;
  svg.setAttribute("viewBox", "0 0 100 40");
  svg.setAttribute("preserveAspectRatio", "none");
  days.forEach(function (n, i) {
    var h = n ? Math.max(2, (n / max) * 36) : 1;
    var r = document.createElementNS(ns, "rect");
    r.setAttribute("x", i * w + 0.5);
    r.setAttribute("y", 40 - h);
    r.setAttribute("width", w - 1);
    r.setAttribute("height", h);
    var title = document.createElementNS(ns, "title");
    title.textContent = label ? label(n, i) : n + " closed";
    r.appendChild(title);
    svg.appendChild(r);
  });
  return svg;
}

function runLine(li, name, run) {
  li.appendChild(el("b", name + ": "));
  if (!run) {
    li.appendChild(el("span", "n/a", "empty"));
    return;
  }
  var done = run.status === "completed";
  var word = done ? run.conclusion || "completed" : run.status;
  var cls = done ? (run.conclusion === "success" ? "ok" : "bad") : "warn";
  var a = link(run.url, word);
  a.className = cls;
  li.appendChild(a);
}

var lastGenerated = null;

function showAge() {
  if (lastGenerated) {
    document.getElementById("updated").textContent = "updated " + ago(lastGenerated);
  }
}

function render(s) {
  lastGenerated = s.generated_at;
  showAge();

  // pipeline
  var stations = document.querySelectorAll("#stations li");
  var claimLine = document.getElementById("claim");
  stations.forEach(function (li) { li.classList.remove("active"); });
  if (s.claim) {
    var st = stationFor(s.claim.step);
    stations.forEach(function (li) {
      if (li.dataset.station === st) li.classList.add("active");
    });
    claimLine.textContent = "";
    claimLine.appendChild(el("b", "#" + s.claim.issue));
    claimLine.appendChild(document.createTextNode(
      " " + (s.claim.step || "") + " (" + (s.claim.session || "?") + ", " +
      (s.claim.branch || "no branch") + ", heartbeat " + ago(s.claim.heartbeat) + ")"));
  } else {
    claimLine.textContent = "No issue is being worked on.";
  }

  // queue
  var by = s.issues.by_status || {};
  document.getElementById("counts").textContent =
    s.issues.open + " open, " + s.issues.closed + " closed. Ready " + (by.ready || 0) +
    ", blocked " + (by.blocked || 0) + ", needs owner " + (by["needs-owner"] || 0) + ".";
  fill("queue", s.queue, function (li, q) {
    li.appendChild(el("span", q.priority, "tag"));
    li.appendChild(link(q.url, "#" + q.number + " " + q.title));
  }, "No ready issues.");

  fill("open-prs", s.prs.open, function (li, p) {
    li.appendChild(link(p.url, "#" + p.number + " " + p.title));
  }, "No open pull requests.");
  fill("merged", s.prs.merged.slice(0, 8), function (li, p) {
    li.appendChild(link(p.url, "#" + p.number + " " + p.title));
    li.appendChild(el("div", ago(p.merged_at), "muted"));
  }, "Nothing merged yet.");
  fill("owner", s.owner_open, function (li, o) {
    li.appendChild(el("span", o.id, "tag"));
    li.appendChild(document.createTextNode(o.title));
  }, "Nothing is waiting for the owner.");
  fill("decisions", (s.decisions || []).slice(-6).reverse(), function (li, d) {
    li.appendChild(el("span", d.id, "tag"));
    li.appendChild(document.createTextNode(d.title));
  }, "No decisions recorded.");

  // health
  var runs = s.runs || {};
  var health = document.getElementById("health");
  health.textContent = "";
  ["CI", "Deploy", "Smoke"].forEach(function (name) {
    var li = el("li");
    runLine(li, name, runs[name]);
    health.appendChild(li);
  });
  ["Server memory", "Server disk"].forEach(function (name) {
    var key = name === "Server memory" ? "memory" : "disk";
    var li = el("li");
    li.appendChild(el("b", name + ": "));
    var v = s.server && s.server[key];
    li.appendChild(el("span", v === undefined || v === null ? "n/a" : v,
      v === undefined || v === null ? "empty" : ""));
    health.appendChild(li);
  });

  // throughput
  var t = s.throughput;
  var spark = document.getElementById("spark");
  spark.textContent = "";
  spark.appendChild(sparkline(t.closed_per_day));
  document.getElementById("throughput").textContent =
    "Closed per day, last 14 days. Median lead time " +
    Math.round(t.median_lead_time_hours * 10) / 10 + " h. Open incidents " +
    t.open_incidents + ".";
}

document.getElementById("theme-toggle").addEventListener("click", function () {
  var root = document.documentElement;
  var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  try { localStorage.setItem("theme", next); } catch (e) { /* private mode */ }
});

function load() {
  fetch("snapshot.json", { cache: "no-store" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(render)
    .catch(function (e) {
      if (!lastGenerated) {
        document.getElementById("updated").textContent =
          "snapshot unavailable (" + e.message + ")";
      }
    });
}

// ---- Live section: live.json, polled every 3 s. Every value is untrusted text. ----

var LIVE_STATIONS = ["plan", "exam", "build", "check", "ship"];
var LIVE_ICONS = { start: "⏳", done: "✅", fail: "❌", wait: "⏸", skip: "⏭", stopped: "⏹" };
var LIVE_OFFLINE_MS = 2 * 60 * 1000;
var LIVE_LOG_MAX = 60;
var LIVE_ROLE_ICONS = { manager: "🧭", "exam-writer": "📝", implementer: "🔨", reviewer: "🔍",
  researcher: "🔬", scribe: "✍️", agent: "🤖" };
var LIVE_ENDED = { done: 1, failed: 1, stopped: 1, ended: 1 };
var lastLive = null;
var lastRaw = null;
var livePending = false;

function S(v) { return v === undefined || v === null ? "" : String(v); }
function arr(v) { return Array.isArray(v) ? v : []; }

function lel(tag, text, cls, attrs) {
  var n = el(tag, text, cls);
  Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
  return n;
}

function elapsed(iso, until) {
  var t = Date.parse(iso);
  if (isNaN(t)) return "";
  var end = until ? Date.parse(until) : Date.now();
  if (isNaN(end)) end = Date.now();
  var min = Math.max(0, Math.round((end - t) / 60000));
  return min < 90 ? min + " min" : Math.round(min / 60) + " h";
}

function clock(iso) {
  var d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  function p(n) { return (n < 10 ? "0" : "") + n; }
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

function joined(parts) { return parts.filter(Boolean).join(" · "); }

function runnerText(r) {
  var s = S(r.state);
  if (s === "running") {
    var pass = r.pass_no === null || r.pass_no === undefined ? "" :
      "pass " + r.pass_no + (r.budget === null || r.budget === undefined ? "" : " of " + r.budget);
    return joined(["Running", pass, r.pass_started ? elapsed(r.pass_started) : "", S(r.note)]);
  }
  var names = { idle: "Idle", paused: "Paused", budget: "Daily budget used" };
  return joined([names[s] || s, S(r.note)]);
}

function agentCard(a, titles, workers) {
  var subs = arr(a.substeps);
  var card = lel("div", null, "live-agent", { "data-agent-id": S(a.id), "data-state": S(a.state) });
  var head = el("div", null, "live-head");
  head.appendChild(el("b", (LIVE_ROLE_ICONS[a.role] || "") + " " + S(a.role)));
  var issue = a.issue === null || a.issue === undefined ? "" : " #" + a.issue + " " + S(titles[a.issue]) + " ";
  if (issue) head.appendChild(el("span", issue));
  card.appendChild(head);
  var done = subs.filter(function (s) { return s.status === "done"; }).length;
  card.appendChild(el("div", joined([elapsed(a.started, LIVE_ENDED[a.state] ? a.last : null), subs.length ? done + "/" + subs.length : ""]), "muted"));
  var cur = null;
  subs.forEach(function (s) { if (s.status === "start" || s.status === "wait") cur = s; });
  cur = cur || subs[subs.length - 1];
  if (cur) {
    var now = el("div", "now: ", "live-now");
    now.appendChild(lel("b", S(cur.substep), "", { "data-current-substep": "" }));
    card.appendChild(now);
  }
  var ul = el("ul", null, "live-steps");
  subs.forEach(function (s) {
    ul.appendChild(lel("li", (LIVE_ICONS[s.status] || "") + " " + S(s.substep), "",
      { "data-substep": S(s.substep), "data-status": S(s.status) }));
  });
  card.appendChild(ul);
  workers.forEach(function (w) { card.appendChild(workerCard(w)); });
  return card;
}

function workerCard(w) {
  var card = lel("div", null, "live-worker", { "data-worker-id": S(w.id), "data-state": S(w.state) });
  card.appendChild(el("span", null, "live-dot"));
  card.appendChild(el("b", " " + (LIVE_ROLE_ICONS[w.role] || "") + " " + S(w.role)));
  card.appendChild(el("span", " " + joined([S(w.state), S(w.tool)]), "muted"));
  var tools = w.tools && typeof w.tools === "object" ? w.tools : {};
  var keys = Object.keys(tools);
  if (keys.length) {
    card.appendChild(el("div", keys.map(function (k) { return k + " " + tools[k]; }).join(" · "), "muted"));
    var box = el("div", null, "live-spark");
    box.appendChild(sparkline(keys.map(function (k) { return Number(tools[k]) || 0; }),
      function (n, i) { return keys[i] + " " + n; }));
    card.appendChild(box);
  }
  return card;
}

function renderLive(d) {
  var titles = Object.create(null);
  var box = el("div", null, "live-box");
  box.appendChild(el("div", runnerText(d.runner || {}), "live-runner"));

  var line = el("div", null, "live-line");
  var at = {};
  LIVE_STATIONS.forEach(function (name) {
    var st = lel("div", null, "live-station", { "data-live-station": name });
    st.appendChild(el("b", name.charAt(0).toUpperCase() + name.slice(1)));
    at[name] = st;
    line.appendChild(st);
  });
  function station(name) { return at[S(name).toLowerCase()] || at.plan; }
  arr(d.issues).forEach(function (i) {
    titles[i.issue] = S(i.title);
    station(i.station).appendChild(el("div", "#" + S(i.issue) + " " + S(i.title), "live-issue"));
  });
  // Workers carry no issue: one running agent with the same role owns the worker, else it goes in the row.
  var agents = arr(d.agents);
  var mine = Object.create(null);
  var loose = [];
  arr(d.workers).forEach(function (w) {
    var same = agents.filter(function (a) { return a.state === "running" && a.role === w.role; });
    if (same.length === 1) (mine[same[0].id] = mine[same[0].id] || []).push(w);
    else loose.push(w);
  });
  agents.forEach(function (a) { station(a.station).appendChild(agentCard(a, titles, mine[a.id] || [])); });
  box.appendChild(line);
  if (loose.length) {
    var row = el("div", null, "live-workers");
    loose.forEach(function (w) { row.appendChild(workerCard(w)); });
    box.appendChild(row);
  }

  var log = el("div", null, "live-log");
  arr(d.log).slice(-LIVE_LOG_MAX).reverse().forEach(function (e) {
    log.appendChild(lel("div", joined([clock(e.at), S(e.agent), S(e.station), S(e.substep), S(e.status),
      S(e.detail)]), "live-row", { "data-log-row": "" }));
  });
  box.appendChild(log);

  var body = document.getElementById("live-body");
  body.textContent = "";
  body.appendChild(box);
}

function showOffline() {
  var t = lastLive ? Date.parse(lastLive.generated_at) : NaN;
  // A fresh node every poll: the exam harness's ser() keeps attrs by reference, so a toggled node
  // would show its last state in every step.
  var banner = el("p", "Laptop offline or asleep: showing the last state received.", "live-offline");
  banner.id = "live-offline";
  banner.hidden = isNaN(t) || Date.now() - t <= LIVE_OFFLINE_MS;
  var old = document.getElementById("live-offline");
  if (old) old.remove();
  var body = document.getElementById("live-body");
  body.parentNode.insertBefore(banner, body);
}

function pollLive() {
  if (livePending) return;
  livePending = true;
  var ctl = typeof AbortController === "function" ? new AbortController() : null;
  var timer = ctl ? setTimeout(function () { ctl.abort(); }, 10000) : null;
  fetch("live.json?t=" + Date.now(), ctl ? { cache: "no-store", signal: ctl.signal } : { cache: "no-store" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.text();
    })
    .then(function (raw) {
      if (raw === lastRaw) return;
      var d = JSON.parse(raw);
      if (!d || typeof d !== "object" || d.schema !== 1) throw new Error("not live.json");
      if (lastLive && Date.parse(d.generated_at) < Date.parse(lastLive.generated_at)) return;
      renderLive(d);
      lastLive = d;
      lastRaw = raw;
    })
    .catch(function () { /* empty, half-written or unreachable: keep the last good state */ })
    .then(function () {
      if (timer !== null) clearTimeout(timer);
      livePending = false;
      showOffline();
    });
}

load();
pollLive();
setInterval(pollLive, 3000);
setInterval(load, 5 * 60 * 1000);
setInterval(showAge, 30 * 1000);
