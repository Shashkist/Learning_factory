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

function sparkline(days) {
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
    title.textContent = n + " closed";
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

load();
setInterval(load, 5 * 60 * 1000);
setInterval(showAge, 30 * 1000);
