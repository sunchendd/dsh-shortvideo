/* 片库首页：负责"挑一条开工"和"从零开一条"两件事。 */

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const THEMES = [
  ["porcelain", "瓷白"], ["rice-paper", "宣纸"], ["arctic", "极地"],
  ["midnight", "午夜"], ["graphite", "石墨"], ["jade", "青玉"],
  ["noir-gold", "黑金"], ["aubergine", "茄紫"],
];

const S = { all: [], filter: "all", q: "" };

async function boot() {
  const d = await (await fetch("/api/projects")).json();
  S.all = d.projects || [];
  $("#libSub").textContent =
    `${S.all.length} 条片子 · ${S.all.filter((p) => p.has_video).length} 条已成片 · 主题与配音都能在剪辑台里换`;
  $("#cAll").textContent = S.all.length;
  $("#cVideo").textContent = S.all.filter((p) => p.has_video).length;
  $("#cDraft").textContent = S.all.filter((p) => !p.has_video).length;
  $("#cQA").textContent = S.all.filter((p) => !p.stages.qa).length;
  render();
  bind();
}

function match(p) {
  if (S.q) {
    const q = S.q.toLowerCase();
    if (!(`${p.title} ${p.slug} ${p.publish_title}`.toLowerCase().includes(q))) return false;
  }
  if (S.filter === "video") return p.has_video;
  if (S.filter === "draft") return !p.has_video;
  if (S.filter === "qa") return !p.stages.qa;
  return true;
}

function render() {
  const rows = S.all.filter(match);
  const grid = $("#grid");
  $("#libEmpty").hidden = rows.length > 0;
  if (!rows.length) { grid.innerHTML = ""; return; }
  grid.innerHTML = rows.map((p) => {
    const badge = p.has_video
      ? `<span class="tag ok">已成片 ${p.size_mb || ""}MB</span>`
      : `<span class="tag warn">未出片</span>`;
    const qa = p.stages.qa ? `<span class="tag ${p.detail?.qa_ok === false ? "warn" : "ok"}">质检</span>` : "";
    const thumb = `/api/frame?slug=${encodeURIComponent(p.slug)}&t=1.2`;
    return `<article class="card" data-slug="${esc(p.slug)}">
      <div class="cover" style="background-image:url('${thumb}')">
        <div class="badges">${badge}${qa}<span class="tag mute">${p.kind === "short" ? "18s" : "长版"}</span></div>
        <div class="meta">
          <b>${esc(p.title)}</b>
          <span>${p.duration}s · ${p.shots} 镜 · ${esc(p.slug)}</span>
        </div>
        <div class="hoverbar">
          <button class="main" data-open="${esc(p.slug)}">剪辑</button>
          <button data-export="${esc(p.slug)}">导出</button>
        </div>
      </div>
    </article>`;
  }).join("");

  grid.querySelectorAll("[data-open]").forEach((b) => {
    b.onclick = (e) => { e.stopPropagation(); open(b.dataset.open); };
  });
  grid.querySelectorAll("[data-export]").forEach((b) => {
    b.onclick = (e) => {
      e.stopPropagation();
      toast("已在后台开始导出：" + b.dataset.export);
      fetch("/api/stage", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slug: b.dataset.export, stage: "render" }),
      });
    };
  });
  grid.querySelectorAll(".card").forEach((c) => {
    c.onclick = () => open(c.dataset.slug);
  });
}

const open = (slug) => { location.href = "/app/#" + encodeURIComponent(slug); };

function bind() {
  $$(".libnav nav a[data-filter]").forEach((a) => {
    a.onclick = () => {
      $$(".libnav nav a").forEach((x) => x.classList.remove("on"));
      a.classList.add("on");
      S.filter = a.dataset.filter;
      render();
    };
  });
  let t;
  $("#search").oninput = (e) => {
    clearTimeout(t);
    t = setTimeout(() => { S.q = e.target.value.trim(); render(); }, 120);
  };
  window.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement.tagName !== "INPUT") {
      e.preventDefault(); $("#search").focus();
    }
    if (e.key === "n" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); openNew(); }
  });
  $("#btnNew").onclick = openNew;
  $("#btnNew2").onclick = openNew;
  $("#newClose").onclick = $("#nCancel").onclick = () => $("#newScrim").classList.remove("on");
  $("#nAddLine").onclick = () => addLine("");
  $("#nCreate").onclick = create;
  $("#nTheme").innerHTML = THEMES.map(([k, n]) => `<option value="${k}">${n}</option>`).join("");
}

function openNew() {
  $("#newScrim").classList.add("on");
  if (!$("#nLines").children.length) {
    ["", "", "", "", ""].forEach((_, i) => addLine("", i));
  }
  setTimeout(() => $("#nTitle").focus(), 60);
}

function addLine(v = "", i = null) {
  const wrap = $("#nLines");
  const n = wrap.children.length;
  const row = document.createElement("div");
  row.className = "ln";
  row.innerHTML = `<span>${(i ?? n) + 1}</span><input value="${esc(v)}" placeholder="${
    ["钩子：一句反直觉的话", "规则：为什么会这样", "原理：机制/数字", "高潮：最反常识的一点", "实战：怎么用"][i ?? n] || "再补一句"}">`;
  wrap.appendChild(row);
  renumber();
}

function renumber() {
  [...$("#nLines").children].forEach((r, i) => { r.querySelector("span").textContent = i + 1; });
}

async function create() {
  const title = $("#nTitle").value.trim();
  const lines = [...$("#nLines").querySelectorAll("input")].map((i) => i.value.trim()).filter(Boolean);
  const warn = $("#nWarn");
  if (!title) { warn.textContent = "标题必填"; return; }
  if (lines.length < 3) { warn.textContent = "至少写 3 句（推荐 5 句：钩子/规则/原理/高潮/实战）"; return; }
  warn.textContent = "创建中…";
  const r = await (await fetch("/api/project/new", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title, lines, kicker: $("#nKicker").value.trim() || null,
      theme: $("#nTheme").value, voice: $("#nVoice").value,
    }),
  })).json();
  if (r.error) { warn.textContent = r.error; return; }
  warn.textContent = "已建项目，正在生成脚本…";
  const slug = r.slug;
  // 等脚本工序完成再跳，避免进编辑台看到空项目
  for (let i = 0; i < 60; i++) {
    await new Promise((res) => setTimeout(res, 800));
    const j = await (await fetch("/api/job?id=" + r.job)).json();
    if (j.status === "done" || j.status === "fail") break;
  }
  location.href = "/app/#" + encodeURIComponent(slug);
}

function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 2200);
}

boot();
