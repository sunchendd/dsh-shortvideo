/* ============================================================
   dsh-shortvideo 剪辑台 · 前端
   ------------------------------------------------------------
   原生 JS，无构建步骤：这个工具要在任何装了 Python 的机器上直接跑起来，
   引入打包器会让"一条命令启动"变成"先 npm install"。

   状态只有一个 S，所有渲染都是 S 的纯函数式投影；
   每次改动的入口都抛给 commit()，它负责：入栈撤销 → 标脏 → 防抖保存。
   ============================================================ */

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const fmtTC = (t) => {
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(2).padStart(5, "0")}`;
};

const THEMES = [
  ["porcelain", "瓷白"], ["rice-paper", "宣纸"], ["arctic", "极地"],
  ["midnight", "午夜"], ["graphite", "石墨"], ["jade", "青玉"],
  ["noir-gold", "黑金"], ["aubergine", "茄紫"],
];
const KIND_NAME = { big: "大字", compare: "对比", flow: "流程", cards: "卡片", stat: "数字" };

const S = {
  slug: null,
  proj: null,
  sel: 0,
  t: 0,
  playing: false,
  pxPerSec: 34,
  undo: [],
  redo: [],
  lastSaved: null,
  frameSeq: 0,
  frameTimer: null,
  saveTimer: null,
  raf: 0,
  panel: "assets",
};

/* ------------------------------------------------------------ 取数据 */
async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} ${r.status}`);
  return r.json();
}
async function jpost(url, body, method = "POST") {
  const r = await fetch(url, {
    method, headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

/* ------------------------------------------------------------ 启动 */
async function boot() {
  const slug = decodeURIComponent((location.hash || "").replace("#", "")) || null;
  let target = slug;
  if (!target) {
    try {
      const list = await jget("/api/projects");
      target = (list.projects || [])[0]?.slug;
    } catch (e) { /* 服务未就绪 */ }
  }
  if (!target) return showEmpty("没有可编辑的项目");
  await loadProject(target);
  bindKeys();
  bindUI();
  fitStage();
  new ResizeObserver(fitStage).observe(document.querySelector(".canvaswrap"));
  window.addEventListener("resize", fitStage);
}

async function loadProject(slug) {
  S.slug = slug;
  history.replaceState(null, "", "#" + slug);
  try {
    S.proj = await jget(`/api/project?slug=${encodeURIComponent(slug)}`);
  } catch (e) {
    return showEmpty("读不到这个项目：" + slug);
  }
  S.sel = 0;
  S.t = 0;
  S.undo = []; S.redo = [];
  S.lastSaved = JSON.stringify(snapshot());
  $("#titleInput").value = S.proj.title;
  $("#fTheme").innerHTML = THEMES
    .map(([k, n]) => `<option value="${k}" ${k === S.proj.theme ? "selected" : ""}>${n}</option>`)
    .join("");
  $("#fVoice").value = S.proj.voice || "yunxi";
  $("#tcTotal").textContent = fmtTC(S.proj.total);
  $("#fProjMeta").textContent =
    `${S.proj.shots.length} 镜 · ${S.proj.total.toFixed(1)}s · ${S.proj.size[0]}×${S.proj.size[1]} · ${S.proj.fps}fps`;
  $("#tlInfo").textContent = `${S.proj.shots.length} 个镜头`;
  renderTimeline();
  renderInspector();
  requestFrame(true);
  refreshQA();
  toast("已打开 " + slug);
}

function showEmpty(msg) {
  $("#stage").innerHTML = `<div class="empty">${msg}</div>`;
}

/* ------------------------------------------------------------ 时间线 */
function renderTimeline() {
  const p = S.proj; if (!p) return;
  const pps = S.pxPerSec;
  const PAD = 12;
  const width = Math.max(p.total * pps + PAD * 2 + 60, $("#tlBody").clientWidth);
  const R = $("#ruler"), T = $("#track"), SUB = $("#subtrack"), AUD = $("#audiotrack");
  [R, $("#lanes"), T, SUB, AUD].forEach((el) => { el.style.width = width + "px"; });
  R.style.position = "relative";

  // 刻度：按缩放选步长
  const step = pps > 70 ? 1 : pps > 30 ? 2 : pps > 16 ? 5 : 10;
  let html = "";
  for (let t = 0; t <= p.total + 0.01; t += step) {
    html += `<i style="left:${PAD + t * pps}px"></i><span style="left:${PAD + t * pps}px">${t}s</span>`;
  }
  R.innerHTML = html;

  // 视频轨
  T.innerHTML = p.shots.map((sh, i) => {
    const left = PAD + sh.start * pps + 1;
    const w = Math.max(28, sh.duration * pps - 2);
    const lines = (sh.headline || "").split(" / ").filter(Boolean);
    const cap = lines[0] || sh.narration.slice(0, 16);
    return `<div class="clip ${i === S.sel ? "sel" : ""}" data-i="${i}"
      style="left:${left}px;width:${w}px" title="${esc(sh.narration)}">
      <div class="thumb" data-thumb="${i}"></div>
      <span class="idx">${String(i + 1).padStart(2, "0")} ${KIND_NAME[sh.kind] || sh.kind}</span>
      <span class="dur">${sh.duration.toFixed(1)}s</span>
      <div class="cap">${esc(cap)}</div>
      <div class="handle" data-handle="${i}"></div>
    </div>`;
  }).join("");

  // 字幕轨：把每镜口播按句切分，按字数分配宽度（导出时会用真实配音时长对齐）
  SUB.innerHTML = p.shots.map((sh, i) => {
    const sents = splitSentences(sh.narration);
    if (!sents.length) return "";
    const chars = sents.reduce((a, b) => a + b.length, 0) || 1;
    let acc = sh.start;
    return sents.map((sen) => {
      const d = sh.duration * sen.length / chars;
      const el = `<div class="subclip" style="left:${PAD + acc * pps}px;width:${Math.max(16, d * pps - 2)}px"
        title="${esc(sen)}">${esc(sen)}</div>`;
      acc += d;
      return el;
    }).join("");
  }).join("");

  // 音频轨：波形是占位图形（真实波形要跑完 TTS 才有），长度严格等于镜头时长
  AUD.innerHTML = p.shots.map((sh, i) => {
    const left = PAD + sh.start * pps + 1, w = Math.max(28, sh.duration * pps - 2);
    const bars = Math.min(60, Math.max(6, Math.round(w / 5)));
    const inner = Array.from({ length: bars }, (_, k) => {
      const h = 6 + Math.round(10 * Math.abs(Math.sin((k + i * 3) * 0.7)));
      return `<i style="height:${h}px"></i>`;
    }).join("");
    return `<div class="audioclip" style="left:${left}px;width:${w}px">${inner}<span>${sh.duration.toFixed(1)}s</span></div>`;
  }).join("");

  updatePlayhead();
  paintThumbs();
}

/** 缩略图：每镜取起始后 0.4s 的一帧，懒加载，避免一次请求 30 张。 */
const _thumbCache = {};
function paintThumbs() {
  const nodes = [...document.querySelectorAll("[data-thumb]")];
  const paint = (node) => {
    const i = +node.dataset.thumb;
    const sh = S.proj.shots[i];
    if (!sh) return;
    const t = Math.round((sh.start + Math.min(0.4, sh.duration / 3)) * 30) / 30;
    const key = `${S.slug}@${t}`;
    const url = _thumbCache[key] || `/api/frame?slug=${encodeURIComponent(S.slug)}&t=${t}`;
    _thumbCache[key] = url;
    const img = new Image();
    img.onload = () => { node.style.backgroundImage = `url("${url}")`; };
    img.src = url;
  };
  if (nodes.length <= 12) return nodes.forEach(paint);
  const io = new IntersectionObserver((es) => {
    for (const e of es) if (e.isIntersecting) { io.unobserve(e.target); paint(e.target); }
  }, { root: $("#tlBody"), threshold: 0.05 });
  nodes.forEach((n) => io.observe(n));
}

function updatePlayhead() {
  const ph = $("#playhead");
  ph.style.left = 12 + S.t * S.pxPerSec + "px";
  $("#tcNow").textContent = fmtTC(S.t);
  const idx = shotAt(S.t);
  $("#shotPill").textContent = idx >= 0 ? `镜头 ${String(idx + 1).padStart(2, "0")}` : "—";
  $("#shotBadge").textContent = idx >= 0 ? `${String(idx + 1).padStart(2, "0")} / ${S.proj.shots.length}` : "";
  if (S.playing) {
    const body = $("#tlBody");
    const x = 12 + S.t * S.pxPerSec;
    if (x > body.scrollLeft + body.clientWidth - 120) body.scrollLeft = x - 120;
  }
}

function shotAt(t) {
  const p = S.proj; if (!p) return -1;
  for (let i = 0; i < p.shots.length; i++) {
    const s = p.shots[i];
    if (t >= s.start && t < s.start + s.duration) return i;
  }
  return p.shots.length - 1;
}

/* ------------------------------------------------------------ 画布尺寸
   9:16 的画布必须按容器高度等比缩放，否则会溢出到时间线上。
   这里用 ResizeObserver 跟随窗口变化，不写死像素。 */
function fitStage() {
  const wrap = document.querySelector(".canvaswrap");
  if (!wrap) return;
  const box = wrap.getBoundingClientRect();
  const padY = 76;          // 上下留白 + 传输控件
  const availH = box.height - padY;
  const availW = box.width - 120;
  const h = Math.min(availH, availW * 16 / 9);
  const w = h * 9 / 16;
  const st = $("#stage");
  st.style.height = Math.max(180, h) + "px";
  st.style.width = Math.max(101, w) + "px";
}

/* ------------------------------------------------------------ 画面 */
function requestFrame(force = false) {
  clearTimeout(S.frameTimer);
  const seq = ++S.frameSeq;
  const run = () => {
    const t = Math.round(S.t * 30) / 30;
    const url = `/api/frame?slug=${encodeURIComponent(S.slug)}&t=${t}`;
    const img = new Image();
    img.onload = () => {
      if (seq !== S.frameSeq) return;         // 有更新的请求了，丢掉旧图
      $("#frame").src = url;
      $("#skel").style.display = "none";
    };
    img.onerror = () => { if (seq === S.frameSeq) $("#skel").style.display = "block"; };
    img.src = url;
  };
  // 拖动时限制到 ~12fps：每帧服务端要 30~90ms，打满反而卡
  if (force) run(); else S.frameTimer = setTimeout(run, 60);
}

function seek(t, opts = {}) {
  S.t = clamp(t, 0, S.proj.total - 1 / 30);
  updatePlayhead();
  requestFrame(!!opts.force);
  if (opts.select) {
    const i = shotAt(S.t);
    if (i !== S.sel) { S.sel = i; renderInspector(); markSelection(); }
  }
}

function markSelection() {
  $$(".clip").forEach((c) => c.classList.toggle("sel", +c.dataset.i === S.sel));
}

/* ------------------------------------------------------------ 播放 */
function togglePlay() {
  if (S.playing) return pause();
  if (S.t >= S.proj.total - 0.05) S.t = 0;
  S.playing = true;
  $("#playIcon").innerHTML = `<path d="M7 4h4v16H7zM13 4h4v16h-4z"/>`;
  const t0 = performance.now() - S.t * 1000;
  const tick = () => {
    if (!S.playing) return;
    const t = (performance.now() - t0) / 1000;
    if (t >= S.proj.total) { seek(S.proj.total - 0.05, { force: true }); pause(); return; }
    S.t = t;
    updatePlayhead();
    requestFrame();
    S.raf = requestAnimationFrame(tick);
  };
  S.raf = requestAnimationFrame(tick);
}
function pause() {
  S.playing = false;
  cancelAnimationFrame(S.raf);
  $("#playIcon").innerHTML = `<path d="M7 4l13 8-13 8z"/>`;
  requestFrame(true);
}

/* ------------------------------------------------------------ 检查器 */
function renderInspector() {
  const sh = S.proj.shots[S.sel];
  if (!sh) return;
  $("#fKind").value = sh.kind || "big";
  $("#fDur").value = sh.duration.toFixed(1);
  $("#fStart").value = sh.start.toFixed(2);
  $("#fNarr").value = sh.narration;
  const subs = splitSentences(sh.narration);
  const chars = sh.narration.replace(/[\s，。！？、；：""'']/g, "").length;
  const rate = chars / Math.max(0.5, sh.duration);
  const bad = rate > 6.2;
  $("#fMeta").innerHTML =
    `${subs.length} 句 · ${chars} 字 · <span class="pill ${bad ? "bad" : "ok"}">` +
    `语速 ${rate.toFixed(1)} 字/秒</span>` +
    (bad ? `<div style="margin-top:6px;color:var(--bad)">偏快：删 1~2 字或把时长加到 ${(chars / 6).toFixed(1)}s</div>` : "");
}

function splitSentences(s) {
  return (s || "").split(/(?<=[。！？；?])/).map((x) => x.trim()).filter(Boolean);
}

/* ------------------------------------------------------------ 改动与保存 */
function snapshot() {
  return {
    title: S.proj.title, theme: S.proj.theme, voice: S.proj.voice,
    shots: S.proj.shots.map((s) => ({ id: s.id, narration: s.narration, duration: s.duration, kind: s.kind })),
  };
}

function commit(mutate, { save = true, undo = true } = {}) {
  if (undo) { S.undo.push(snapshot()); S.redo = []; if (S.undo.length > 60) S.undo.shift(); }
  mutate();
  renderTimeline();
  renderInspector();
  updateUndoBtns();
  if (save) scheduleSave();
}

function scheduleSave() {
  const st = $("#saveState");
  const now = JSON.stringify(snapshot());
  if (now === S.lastSaved) { st.textContent = "已保存"; st.className = "save-state saved"; return; }
  st.textContent = "未保存";
  st.className = "save-state saving";
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(doSave, 700);
}

async function doSave() {
  const snap = snapshot();
  const st = $("#saveState");
  st.textContent = "保存中…";
  try {
    const r = await jpost("/api/project", {
      slug: S.slug, title: snap.title, theme: snap.theme, voice: snap.voice,
      shots: snap.shots,
    }, "POST");
    if (r.ok) {
      S.lastSaved = JSON.stringify(snap);
      st.textContent = "已保存";
      st.className = "save-state saved";
      retime();
      requestFrame(true);
    } else {
      st.textContent = "保存失败";
    }
  } catch (e) {
    st.textContent = "保存失败";
  }
}

/** 时长改动后重算 start，并同步 timecode/总长。 */
function retime() {
  let c = 0;
  for (const s of S.proj.shots) { s.start = +c.toFixed(2); c += s.duration; }
  S.proj.total = +c.toFixed(2);
  $("#tcTotal").textContent = fmtTC(S.proj.total);
  $("#fProjMeta").textContent =
    `${S.proj.shots.length} 镜 · ${S.proj.total.toFixed(1)}s · ${S.proj.size[0]}×${S.proj.size[1]} · ${S.proj.fps}fps`;
}

function updateUndoBtns() {
  $("#btnUndo").disabled = !S.undo.length;
  $("#btnRedo").disabled = !S.redo.length;
}

function undo() {
  if (!S.undo.length) return;
  S.redo.push(snapshot());
  applySnapshot(S.undo.pop());
  toast("已撤销");
}
function redo() {
  if (!S.redo.length) return;
  S.undo.push(snapshot());
  applySnapshot(S.redo.pop());
  toast("已重做");
}
function applySnapshot(snap) {
  S.proj.title = snap.title;
  S.proj.theme = snap.theme;
  S.proj.voice = snap.voice;
  snap.shots.forEach((s, i) => Object.assign(S.proj.shots[i], s));
  retime();
  $("#titleInput").value = S.proj.title;
  $("#fTheme").value = S.proj.theme;
  $("#fVoice").value = S.proj.voice;
  renderTimeline(); renderInspector(); updateUndoBtns();
  scheduleSave();
  requestFrame(true);
}

/* ------------------------------------------------------------ 交互绑定 */
function bindUI() {
  // 播放/暂停与跳转
  $("#btnPlay").onclick = togglePlay;
  $("#btnPrev").onclick = () => { pause(); seek(S.t - 1 / 30, { force: true, select: true }); };
  $("#btnNext").onclick = () => { pause(); seek(S.t + 1 / 30, { force: true, select: true }); };
  $("#btnStart").onclick = () => { pause(); seek(0, { force: true, select: true }); };
  $("#btnEnd").onclick = () => { pause(); seek(S.proj.total - 0.1, { force: true, select: true }); };
  $("#btnUndo").onclick = undo;
  $("#btnRedo").onclick = redo;
  $("#btnLib").onclick = () => { location.href = "/"; };

  // 标题
  $("#titleInput").oninput = (e) => { S.proj.title = e.target.value; scheduleSave(); };
  $("#titleInput").onblur = (e) => { commit(() => { S.proj.title = e.target.value; }, { undo: false }); };

  // 检查器字段
  $("#fKind").onchange = (e) => commit(() => { S.proj.shots[S.sel].kind = e.target.value; });
  $("#fDur").onchange = (e) => {
    const v = clamp(parseFloat(e.target.value) || 4, 2, 30);
    commit(() => { S.proj.shots[S.sel].duration = +v.toFixed(2); retime(); });
    seek(S.proj.shots[S.sel].start + 0.2, { force: true });
  };
  let narrTimer;
  $("#fNarr").oninput = (e) => {
    const v = e.target.value;
    clearTimeout(narrTimer);
    narrTimer = setTimeout(() => {
      commit(() => { S.proj.shots[S.sel].narration = v; });
    }, 400);
  };
  $("#fTheme").onchange = (e) => {
    commit(() => { S.proj.theme = e.target.value; });
    requestFrame(true);
  };
  $("#fVoice").onchange = (e) => commit(() => { S.proj.voice = e.target.value; });

  // 缩放
  $("#zoom").oninput = (e) => { S.pxPerSec = +e.target.value; renderTimeline(); };

  bindTimeline();
  bindPanels();
  bindExport();
}

function bindTimeline() {
  const body = $("#tlBody"), track = $("#track"), ruler = $("#ruler");

  const tFromEvent = (e) => {
    const rect = track.getBoundingClientRect();
    return clamp((e.clientX - rect.left - 12) / S.pxPerSec, 0, S.proj.total - 0.01);
  };

  // 标尺/空白处拖动 = 拖动播放头（剪辑软件的基本肌肉记忆）
  const startScrub = (e) => {
    if (e.target.closest(".clip")) return;
    pause();
    seek(tFromEvent(e), { force: true, select: true });
    const move = (ev) => seek(tFromEvent(ev), { force: true, select: true });
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  ruler.addEventListener("pointerdown", startScrub);
  body.addEventListener("pointerdown", (e) => { if (e.target === body) startScrub(e); });

  // 点字幕/音频块也能跳到对应位置（剪辑软件里整条轨道都该响应点击）
  $("#subtrack").addEventListener("pointerdown", (e) => {
    const el = e.target.closest(".subclip");
    if (!el) return;
    pause();
    const x = e.clientX - $("#track").getBoundingClientRect().left - 12;
    seek(x / S.pxPerSec, { force: true, select: true });
  });
  $("#audiotrack").addEventListener("pointerdown", (e) => {
    const el = e.target.closest(".audioclip");
    if (!el) return;
    pause();
    const x = e.clientX - $("#track").getBoundingClientRect().left - 12;
    seek(x / S.pxPerSec, { force: true, select: true });
  });

  // 点选片段
  track.addEventListener("pointerdown", (e) => {
    const clip = e.target.closest(".clip");
    if (!clip) return;
    const i = +clip.dataset.i;
    if (e.target.dataset.handle !== undefined) return startTrim(e, i);
    S.sel = i;
    markSelection();
    renderInspector();
    seek(S.proj.shots[i].start + 0.15, { force: true });
  });

  // 拖右边缘改时长
  function startTrim(e, i) {
    e.preventDefault();
    pause();
    const sh = S.proj.shots[i];
    const x0 = e.clientX, d0 = sh.duration;
    // 快照要在动手之前拍：之前拍在 pointerup 里，撤销回去的是"改完的样子"，等于没撤。
    const pre = snapshot();
    S.sel = i; markSelection(); renderInspector();
    const move = (ev) => {
      const d = clamp(d0 + (ev.clientX - x0) / S.pxPerSec, 2, 30);
      sh.duration = +d.toFixed(2);
      retime();
      renderTimeline();
      $("#fDur").value = sh.duration.toFixed(1);
      renderInspector();
      requestFrame();
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      S.undo.push(pre);
      S.redo = [];
      scheduleSave();
      updateUndoBtns();
      toast(`镜头 ${i + 1} 时长 ${sh.duration.toFixed(1)}s`);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // 滚轮横向滚动（触控板/鼠标都能用）
  body.addEventListener("wheel", (e) => {
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
    if (e.ctrlKey) {   // 捏合缩放
      e.preventDefault();
      S.pxPerSec = clamp(S.pxPerSec * (e.deltaY < 0 ? 1.1 : 0.9), 10, 120);
      $("#zoom").value = S.pxPerSec;
      renderTimeline();
    }
  }, { passive: false });
}

/* ------------------------------------------------------------ 左侧面板 */
function bindPanels() {
  $$(".rail button[data-panel]").forEach((b) => {
    b.onclick = () => openPanel(b.dataset.panel, b);
  });
  $("#drawerClose").onclick = () => closePanel();
}

async function openPanel(name, btn) {
  if (S.panel === name && $("#drawer").classList.contains("on")) return closePanel();
  S.panel = name;
  $$(".rail button").forEach((b) => b.classList.toggle("on", b === btn));
  $("#drawer").classList.add("on");
  const T = $("#drawerTitle"), L = $("#drawerList");
  T.textContent = { assets: "素材", voice: "配音", theme: "主题", checks: "检查", keys: "快捷键" }[name] || name;

  if (name === "assets") {
    L.innerHTML = S.proj.shots.map((sh, i) => `
      <div class="asset" data-goto="${i}">
        <img src="/api/frame?slug=${encodeURIComponent(S.slug)}&t=${sh.start + 0.4}" alt="">
        <div><div class="t">${esc((sh.headline || sh.narration).slice(0, 22))}</div>
        <div class="s">${String(i + 1).padStart(2, "0")} · ${KIND_NAME[sh.kind] || sh.kind} · ${sh.duration.toFixed(1)}s</div></div>
      </div>`).join("");
    L.querySelectorAll("[data-goto]").forEach((el) => {
      el.onclick = () => {
        const i = +el.dataset.goto;
        S.sel = i; markSelection(); renderInspector();
        seek(S.proj.shots[i].start + 0.15, { force: true });
      };
    });
  } else if (name === "voice") {
    L.innerHTML = [
      ["yunxi", "云希 · 男声", "沉稳、适合硬核科普"],
      ["xiaoxiao", "晓晓 · 女声", "清晰、适合知识口播"],
      ["yunjian", "云健 · 男声", "厚重、适合叙事"],
    ].map(([k, n, d]) => `
      <div class="asset ${S.proj.voice === k ? "on" : ""}" data-voice="${k}">
        <div style="width:44px;height:44px;border-radius:22px;background:var(--bg3);display:grid;place-items:center">🔊</div>
        <div><div class="t">${n}</div><div class="s">${d}</div></div>
      </div>`).join("");
    L.querySelectorAll("[data-voice]").forEach((el) => {
      el.onclick = () => {
        commit(() => { S.proj.voice = el.dataset.voice; });
        $("#fVoice").value = S.proj.voice;
        openPanel("voice", $('.rail button[data-panel="voice"]'));
      };
    });
  } else if (name === "theme") {
    L.innerHTML = THEMES.map(([k, n]) => `
      <div class="asset" data-theme="${k}">
        <div style="width:44px;height:44px;border-radius:6px;display:grid;place-items:center;background:${themeSwatch(k)}">Aa</div>
        <div><div class="t">${n}</div><div class="s">${k}</div></div>
      </div>`).join("");
    L.querySelectorAll("[data-theme]").forEach((el) => {
      el.onclick = () => {
        commit(() => { S.proj.theme = el.dataset.theme; });
        $("#fTheme").value = S.proj.theme;
        requestFrame(true);
        toast("主题：" + el.querySelector(".t").textContent);
      };
    });
  } else if (name === "keys") {
    L.innerHTML = [
      ["空格", "播放 / 暂停"], ["← →", "逐帧"], ["⇧← ⇧→", "跳 1 秒"],
      ["Home / End", "首帧 / 末帧"], ["⌘Z", "撤销"], ["⇧⌘Z", "重做"],
      ["⌘S", "立即保存"], ["⌘E", "导出"], ["滚轮", "横向滚动时间线"],
      ["Ctrl+滚轮", "缩放时间线"], ["Esc", "回片库"],
    ].map(([k, d]) => `<div class="asset"><div style="width:44px;text-align:center;font:11px var(--mono);color:var(--ink1)">${k}</div>
      <div><div class="t">${d}</div></div></div>`).join("");
  } else {
    L.innerHTML = `<div class="meta" style="padding:8px">正在读取质检结果…</div>`;
    const qa = await jget(`/api/stage-status?slug=${encodeURIComponent(S.slug)}`).catch(() => null);
    renderChecks(L, qa);
  }
}

function renderChecks(L, qa) {
  const items = (qa && qa.items) || [];
  if (!items.length) {
    L.innerHTML = `<div class="meta" style="padding:10px;line-height:1.9">这个项目还没跑过质检。<br>
      点顶栏「质检」跑一次，会检查：清单完成度、图形事实链、文档完整度、发布可行性、
      长短版一致、响度一致、配音情绪、封面可读性、前 3 秒留人、画面体检。</div>`;
    return;
  }
  L.innerHTML = items.map((x) => `
    <div class="asset"><div style="width:22px;text-align:center;color:${x.ok ? "var(--ok)" : "var(--bad)"}">
      ${x.ok ? "✓" : "✕"}</div>
      <div><div class="t">${esc(x.name)}</div><div class="s">${esc(x.detail || "")}</div></div></div>`).join("");
}

const THEME_SWATCH = {
  porcelain: "#eaf1f5", "rice-paper": "#f2ece1", arctic: "#e8f0f7",
  midnight: "#0e1220", graphite: "#15171a", jade: "#0f1d19",
  "noir-gold": "#12100c", aubergine: "#1a1020",
};
const themeSwatch = (k) => `background:${THEME_SWATCH[k] || "#333"};color:${
  ["porcelain", "rice-paper", "arctic"].includes(k) ? "#1b1b1b" : "#fff"};font:11px var(--sans)`;

function closePanel() {
  $("#drawer").classList.remove("on");
  $$(".rail button").forEach((b) => b.classList.remove("on"));
  S.panel = null;
}

/* ------------------------------------------------------------ 质检 */
async function runQA() {
  $("#qaBox").innerHTML = `<span class="pill">质检中…</span>`;
  toast("质检开始（约 15 秒）");
  const r = await jpost("/api/stage", { slug: S.slug, stage: "qa" });
  pollJob(r.job, (line) => {
    $("#qaBox").innerHTML = `<span class="pill">质检中…</span><div class="log">${esc(line)}</div>`;
  }, async () => {
    await refreshQA();
    toast("质检完成");
  });
}

async function refreshQA() {
  const qa = await jget(`/api/stage-status?slug=${encodeURIComponent(S.slug)}`).catch(() => null);
  const items = (qa && qa.items) || [];
  if (!items.length) {
    $("#qaBox").innerHTML = `<span class="pill">未跑</span>`;
    return;
  }
  const bad = items.filter((x) => !x.ok).length;
  $("#qaBox").innerHTML = bad
    ? `<span class="pill bad">${items.length - bad}/${items.length} 通过</span>
       <div class="meta" style="margin-top:8px">${items.filter((x) => !x.ok).map((x) => "· " + esc(x.name)).join("<br>")}</div>`
    : `<span class="pill ok">全部通过</span>`;
}

/* ------------------------------------------------------------ 导出 */
function bindExport() {
  $("#btnExport").onclick = () => { $("#exportScrim").classList.add("on"); updateExMeta(); };
  $("#btnQA").onclick = runQA;
  $("#exportClose").onclick = $("#exCancel").onclick = () => $("#exportScrim").classList.remove("on");
  $("#exPreset").onchange = updateExMeta;
  $("#exGo").onclick = startExport;
}

function updateExMeta() {
  const p = $("#exPreset").value;
  const est = p === "xhs" ? S.proj.total * 1.1 : S.proj.total * 0.5;
  $("#exMeta").innerHTML = `预计 ${est.toFixed(0)}s · 音轨 edge-tts · 编码 h264 + aac<br>
    <span style="color:var(--ink3)">渲染是逐帧程序化绘制，不含任何素材版权风险</span>`;
}

let exTimer = null;
async function startExport() {
  const t0 = performance.now();
  $("#exGo").disabled = true;
  $("#exBar").style.width = "3%";
  $("#exLog").textContent = "启动渲染…";
  const r = await jpost("/api/stage", { slug: S.slug, stage: "render" });
  if (!r.job) { $("#exLog").textContent = "启动失败"; $("#exGo").disabled = false; return; }
  pollJob(r.job, (line) => {
    $("#exLog").textContent = line;
    // make.py 会输出「渲染 120/1104 30fps ETA 12s」的进度行，抓出来做真实进度
    const m = line.match(/渲染\s+(\d+)\/(\d+)/);
    if (m) {
      const pct = 20 + 70 * (+m[1] / +m[2]);
      $("#exBar").style.width = pct.toFixed(1) + "%";
    } else if (/\[tts\]/.test(line)) $("#exBar").style.width = "12%";
    else if (/\[render\]/.test(line)) $("#exBar").style.width = "20%";
    else if (/\[ok\]/.test(line)) $("#exBar").style.width = "96%";
  }, () => {
    $("#exBar").style.width = "100%";
    const secs = ((performance.now() - t0) / 1000).toFixed(0);
    $("#exLog").textContent += `\n\n导出完成，用时 ${secs}s。`;
    $("#exGo").disabled = false;
    toast("导出完成");
    _thumbCacheClear();
    renderTimeline();
  });
}

function _thumbCacheClear() {
  for (const k of Object.keys(_thumbCache)) delete _thumbCache[k];
}

async function pollJob(id, onLine, done) {
  let last = "";
  for (;;) {
    let j;
    try { j = await jget("/api/job?id=" + id); } catch (e) { await sleep(1500); continue; }
    const text = (j.log || []).join("\n");
    if (text !== last) {
      last = text;
      onLine(text.slice(-600));
    }
    if (j.status === "done") return done && done();
    if (j.status === "fail") { toast("失败，看日志"); return done && done(); }
    await sleep(700);
  }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------ 快捷键 */
function bindKeys() {
  window.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    const meta = e.metaKey || e.ctrlKey;
    if (meta && e.key.toLowerCase() === "s") { e.preventDefault(); doSave(); return; }
    if (meta && e.key.toLowerCase() === "e") { e.preventDefault(); $("#btnExport").click(); return; }
    if (meta && e.key.toLowerCase() === "z") {
      e.preventDefault();
      e.shiftKey ? redo() : undo();
      return;
    }
    if (typing) return;
    switch (e.key) {
      case " ": e.preventDefault(); togglePlay(); break;
      case "ArrowLeft": e.preventDefault(); pause(); seek(S.t - (e.shiftKey ? 1 : 1 / 30), { force: true, select: true }); break;
      case "ArrowRight": e.preventDefault(); pause(); seek(S.t + (e.shiftKey ? 1 : 1 / 30), { force: true, select: true }); break;
      case "Home": e.preventDefault(); pause(); seek(0, { force: true, select: true }); break;
      case "End": e.preventDefault(); pause(); seek(S.proj.total - 0.1, { force: true, select: true }); break;
      case "Escape": location.href = "/"; break;
    }
  });
}

/* ------------------------------------------------------------ 小工具 */
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 1600);
  setTimeout(() => el.remove(), 2000);
}

boot();
