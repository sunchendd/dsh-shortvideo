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

const S = { all: [], filter: "all", q: "", templates: [], tpl: null };
const FILTER_NAME = { all: "全部", video: "已成片", draft: "未出片", qa: "未质检" };

async function boot() {
  S.templates = (await (await fetch("/api/templates")).json().catch(() => ({ templates: [] }))).templates || [];
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
  const empty = $("#libEmpty");
  empty.hidden = rows.length > 0;
  if (!rows.length) {
    grid.innerHTML = "";
    // 两种"空"必须分开说：一条都没有 ≠ 筛出来的没有。
    // 合成一个"还没有项目"会让用户以为自己的片子丢了。
    const none = S.all.length === 0;
    empty.innerHTML = none
      ? `<p class="et">还没有项目</p>
         <p class="ed">从一句话选题开始，五个镜头由流水线自动生成；也可以先挑个结构模板。</p>
         <button class="btn primary" id="btnNew2">建第一条片子</button>`
      : `<p class="et">没有匹配的项目</p>
         <p class="ed">当前筛选：${esc(S.q ? `关键词「${S.q}」` : FILTER_NAME[S.filter] || "全部")}
            · 共 ${S.all.length} 条却一条都没命中。</p>
         <button class="btn" id="btnClearFilter">清除筛选</button>`;
    const b1 = $("#btnNew2"), b2 = $("#btnClearFilter");
    if (b1) b1.onclick = () => $("#newScrim").classList.add("on");
    if (b2) b2.onclick = () => {
      S.q = ""; S.filter = "all";
      const inp = $("#search"); if (inp) inp.value = "";
      $$(".libnav nav a[data-filter]").forEach((x) =>
        x.classList.toggle("on", x.dataset.filter === "all"));
      render();
    };
    return;
  }
  grid.innerHTML = rows.map((p) => {
    // 状态收成一个点 + 一句话，不再叠两三个胶囊压在封面上：
    // 封面本身是"大字 + 字幕"的构图，上面的每一点文字都在跟它抢注意力。
    const state = p.has_video
      ? `<span class="dot ok"></span>已成片${p.size_mb ? ` · ${p.size_mb}MB` : ""}`
      : `<span class="dot warn"></span>未出片`;
    const qaBad = p.stages.qa && p.detail?.qa_ok === false
      ? `<span class="cardqa" title="质检未通过">质检 ✕</span>` : "";
    const thumb = `/api/frame?slug=${encodeURIComponent(p.slug)}&t=1.2`;
    return `<article class="card" data-slug="${esc(p.slug)}">
      <div class="cover" style="background-image:url('${thumb}')">
        <div class="hoverbar">
          <button class="main" data-open="${esc(p.slug)}">剪辑</button>
          <button data-export="${esc(p.slug)}">导出</button>
        </div>
      </div>
      <div class="meta">
        <b title="${esc(p.title)}">${esc(p.title)}</b>
        <div class="sub">
          <span class="dur">${p.duration}s</span>
          <span>${p.shots} 镜</span>
          <span class="kind">${p.kind === "short" ? "18s 版" : "长版"}</span>
          ${qaBad}
        </div>
        <div class="state">${state}</div>
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
  renderTemplates();
  if (!$("#nLines").children.length) fillLines(null);
  setTimeout(() => $("#nTitle").focus(), 60);
}

/** 模板卡：点了就换掉占位提示和视觉主题；示例文案可以选择一键填入。 */
function renderTemplates() {
  const box = $("#nTpls");
  box.innerHTML = S.templates.map((t) => `
    <button class="tpl ${S.tpl && S.tpl.id === t.id ? "on" : ""}" data-tpl="${t.id}">
      <b>${esc(t.name)}</b><span>${esc(t.desc)}</span>
      <em>点两次用示例填满 →</em>
    </button>`).join("");
  box.querySelectorAll("[data-tpl]").forEach((b) => {
    b.onclick = () => {
      const t = S.templates.find((x) => x.id === b.dataset.tpl);
      const again = S.tpl && S.tpl.id === t.id;
      S.tpl = t;
      $("#nTheme").value = t.theme || "porcelain";
      fillLines(t);
      if (again) {
        // 第二次点：把示例文案灌进去，用户可以照着改
        const ins = [...$("#nLines").querySelectorAll("input")];
        (t.example || []).forEach((v, i) => { if (ins[i]) ins[i].value = v; });
        if (!$("#nTitle").value.trim() && t.example && t.example[0]) {
          $("#nTitle").value = t.example[0].replace(/[。！？]$/, "");
        }
      }
      renderTemplates();
    };
  });
}

function fillLines(t) {
  const wrap = $("#nLines");
  wrap.innerHTML = "";
  const hints = (t && t.hints) || ["钩子：一句反直觉的话", "规则：为什么会这样", "原理：机制/数字", "高潮：最反常识的一点", "实战：怎么用"];
  hints.forEach((h, i) => addLine("", i, h));
}

function addLine(v = "", i = null, hint = null) {
  const wrap = $("#nLines");
  const n = wrap.children.length;
  const row = document.createElement("div");
  row.className = "ln";
  const ph = hint || (S.tpl && S.tpl.hints && S.tpl.hints[i ?? n]) || "再补一句";
  row.innerHTML = `<span>${(i ?? n) + 1}</span><input value="${esc(v)}" placeholder="${esc(ph)}">`;
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
      template: S.tpl ? S.tpl.id : null,
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
