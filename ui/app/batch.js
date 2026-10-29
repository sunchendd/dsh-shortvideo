/* 批量工序：一屏看全部项目 × 五道工序，点哪格跑哪道。 */

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const STAGES = [
  ["script", "脚本"], ["render", "成片"], ["assets", "封面"], ["qa", "质检"], ["deliver", "发布包"],
];
let DATA = { projects: [] };

async function load() {
  DATA = await (await fetch("/api/projects")).json();
  $("#btHead").innerHTML = `<span>项目</span>` +
    STAGES.map(([, n]) => `<span>${n}</span>`).join("");
  $("#btRows").innerHTML = DATA.projects.map((p) => `
    <div class="btrow" data-slug="${esc(p.slug)}">
      <div>
        <div class="t"><a href="/app/#${esc(p.slug)}" style="color:inherit;text-decoration:none">${esc(p.title)}</a></div>
        <div class="s">${esc(p.slug)} · ${p.duration}s · ${p.shots} 镜</div>
      </div>
      ${STAGES.map(([id]) => cell(p, id)).join("")}
    </div>`).join("") || `<div class="s" style="padding:20px">没有项目</div>`;

  $("#btRows").querySelectorAll(".cell").forEach((c) => {
    c.onclick = () => runStage(c.dataset.slug, c.dataset.stage, c);
  });
}

function cell(p, id) {
  const done = p.stages[id];
  return `<div class="cell ${done ? "done" : ""}" data-slug="${esc(p.slug)}" data-stage="${id}"
    title="${done ? "已完成，点击重跑" : "点击运行"}">
    <span class="dot"></span>${done ? "完成" : "待跑"}</div>`;
}

const running = new Set();
async function runStage(slug, stage, cellEl) {
  const key = slug + ":" + stage;
  if (running.has(key)) return;
  running.add(key);
  cellEl.className = "cell run";
  cellEl.innerHTML = `<span class="dot"></span>跑…`;
  log(`▶ ${slug} · ${stage}`);
  const r = await (await fetch("/api/stage", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug, stage }),
  })).json();
  if (!r.job) { log("启动失败"); running.delete(key); return; }
  let last = "";
  for (;;) {
    await new Promise((res) => setTimeout(res, 900));
    const j = await (await fetch("/api/job?id=" + r.job)).json();
    const text = (j.log || []).join("\n");
    if (text !== last) { last = text; log(text.slice(-900)); }
    if (j.status !== "running") {
      running.delete(key);
      log(j.status === "done" ? `✓ ${slug} · ${stage} 完成` : `✗ ${slug} · ${stage} 失败`);
      toast(j.status === "done" ? "完成" : "失败");
      load();
      return;
    }
  }
}

async function runAllPending() {
  const todo = [];
  for (const p of DATA.projects) for (const [id] of STAGES) if (!p.stages[id]) todo.push([p.slug, id]);
  if (!todo.length) return toast("没有待办");
  log(`批量：${todo.length} 个工序，按顺序跑（渲染是 CPU 密集的，不并行）`);
  for (const [slug, stage] of todo) {
    const el = document.querySelector(`.cell[data-slug="${CSS.escape(slug)}"][data-stage="${stage}"]`);
    if (el) await runStage(slug, stage, el);
  }
  toast("批量完成");
}

function log(text) {
  const b = $("#logBody");
  b.textContent = b.textContent === "选一格开始。" ? text : b.textContent + "\n" + text;
  b.scrollTop = b.scrollHeight;
}

function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast"; el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 2000);
}

$("#btnReload").onclick = load;
$("#btnAll").onclick = runAllPending;
$("#logClear").onclick = () => { $("#logBody").textContent = "已清空"; };
load();
