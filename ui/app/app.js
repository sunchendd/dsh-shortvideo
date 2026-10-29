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
  bindPalette();
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
    S.rev = S.proj.rev || 0;
  } catch (e) {
    return showEmpty("读不到这个项目：" + slug);
  }
  S.sel = 0;
  S.t = 0;
  S.undo = []; S.redo = [];
  S.edited = false;
  S.lastSaved = JSON.stringify(snapshot());
  $("#titleInput").value = S.proj.title;
  $("#fTheme").innerHTML = THEMES
    .map(([k, n]) => `<option value="${k}" ${k === S.proj.theme ? "selected" : ""}>${n}</option>`)
    .join("");
  $("#fVoice").value = S.proj.voice || "yunxi";
  renderTransition();
  $("#tcTotal").textContent = fmtTC(S.proj.total);
  $("#fProjMeta").textContent =
    `${S.proj.shots.length} 镜 · ${S.proj.total.toFixed(1)}s · ${S.proj.size[0]}×${S.proj.size[1]} · ${S.proj.fps}fps`;
  $("#tlInfo").textContent = `${S.proj.shots.length} 个镜头`;
  renderTimeline();
  renderInspector();
  requestFrame(true);
  refreshQA();
  const f = filmEl();
  f.removeAttribute("src");
  f.load();
  S.filmOk = await ensureFilm();
  if (S.filmOk) {
    $("#stage").classList.add("live");   // 只加标记，画面仍显示静帧，播放时才切视频
    $("#stage").classList.remove("live");
  }
  clearEmpty();
  toast("已打开 " + slug);
}

/* 空态只加一层提示，不要把画布结构整个替换掉。
   之前直接 innerHTML = ...，之后 #frame / #skel 就没了，
   同一个页面换到另一个项目时全部报 null（实测过一次）。 */
function showEmpty(msg) {
  const stage = $("#stage");
  if (!stage) return;
  let box = stage.querySelector(".empty");
  if (!box) {
    box = document.createElement("div");
    box.className = "empty";
    stage.appendChild(box);
  }
  box.textContent = msg;
  box.style.display = "block";
}

function clearEmpty() {
  const box = $("#stage") && $("#stage").querySelector(".empty");
  if (box) box.style.display = "none";
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
    // 带版本号：编辑后浏览器不能再拿旧图（服务端 LRU 与浏览器缓存各有一套）
    const url = `/api/frame?slug=${encodeURIComponent(S.slug)}&t=${t}&v=${S.frameRev || 0}`;
    const img = new Image();
    // 骨架屏只在"真的慢"时出现：单帧 30~90ms，无脑显示会闪一下反而像卡顿。
    clearTimeout(S.skelTimer);
    S.skelTimer = setTimeout(() => {
      if (seq === S.frameSeq) $("#skel").style.display = "block";
    }, 120);
    img.onload = () => {
      clearTimeout(S.skelTimer);
      if (seq !== S.frameSeq) return;         // 有更新的请求了，丢掉旧图
      $("#frame").src = url;
      $("#skel").style.display = "none";
    };
    img.onerror = () => {
      clearTimeout(S.skelTimer);
      if (seq === S.frameSeq) $("#skel").style.display = "block";
    };
    img.src = url;
  };
  // 拖动时限制到 ~12fps：每帧服务端要 30~90ms，打满反而卡
  if (force) run(); else S.frameTimer = setTimeout(run, 60);
}

function seek(t, opts = {}) {
  if (S.playing) pause();
  useFrames();
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
/* 播放有两档：
   - 已经导出过成片 → 直接播那个 mp4，**有声音**，播放头跟视频 currentTime 走；
   - 没导出（或成片比当前版本旧）→ 退回逐帧动画，无声，但画面是当前编辑结果。
   剪辑台里"按空格没声音"是很伤的，所以优先走第一档，并在画面上标出来。 */
function filmEl() { return $("#film"); }

function filmReady() {
  const f = filmEl();
  return f && f.src && f.readyState >= 2 && !f.error;
}

/** 成片标签：改过但没导出时必须显眼，否则用户会拿旧片子当新片子发。 */
function updateFilmTag() {
  const tag = $("#liveTag");
  if (!tag) return;
  tag.textContent = S.edited ? "成片音画 · 改动未导出" : "成片音画";
  tag.style.color = S.edited ? "var(--warn)" : "var(--ok)";
  tag.title = S.edited ? "当前播放的还是上次导出的成片，改动要重新导出才进片" : "";
}

function useFilm() {
  updateFilmTag();
  const f = filmEl();
  if (!f.src) f.src = `/files/${encodeURIComponent(S.slug)}/${encodeURIComponent(S.slug)}.mp4`;
  $("#stage").classList.add("live");
  $("#frame").style.display = "none";
  f.style.display = "block";
}

function useFrames() {
  $("#stage").classList.remove("live");
  const f = filmEl();
  if (!f.paused) f.pause();
  f.style.display = "none";
  $("#frame").style.display = "block";
}

async function ensureFilm() {
  const f = filmEl();
  if (f.src) return filmReady();
  f.src = `/files/${encodeURIComponent(S.slug)}/${encodeURIComponent(S.slug)}.mp4`;
  return await new Promise((res) => {
    f.onloadeddata = () => res(true);
    f.onerror = () => res(false);
    setTimeout(() => res(filmReady()), 1500);
  });
}

function togglePlay() {
  if (S.playing) return pause();
  if (S.t >= S.proj.total - 0.05) S.t = 0;
  S.playing = true;
  $("#playIcon").innerHTML = `<path d="M7 4h4v16H7zM13 4h4v16h-4z"/>`;
  // 有成片就播成片（带声音），播放头由视频驱动
  if (S.filmOk) {
    useFilm();
    const f = filmEl();
    f.currentTime = S.t;
    f.play().catch((err) => {
      S._playErr = (err && err.name) || String(err);
      // 浏览器可能因为"没有用户手势"拒绝带声音播放：此时退回逐帧动画继续播，
      // 而不是把自己 pause 掉（原来那版会让空格看起来没反应）。
      S.filmOk = false;
      useFrames();
      startFrameLoop();
    });
    $("#playIcon").innerHTML = `<path d="M7 4h4v16H7zM13 4h4v16h-4z"/>`;
    const sync = () => {
      if (!S.playing) return;
      S.t = Math.min(f.currentTime, S.proj.total);
      updatePlayhead();
      if (f.ended) { pause(); return; }
      S.raf = requestAnimationFrame(sync);
    };
    S.raf = requestAnimationFrame(sync);
    return;
  }
  startFrameLoop();
}

function startFrameLoop() {
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
  const f = filmEl();
  if (f && !f.paused) f.pause();
  $("#playIcon").innerHTML = `<path d="M7 4l13 8-13 8z"/>`;
  if ($("#stage").classList.contains("live")) {
    // 停在成片画面上的那一帧；如果要看编辑后的画面，用 ←→ 逐帧或点时间线
    updatePlayhead();
  } else {
    requestFrame(true);
  }
}

/* ------------------------------------------------------------ 检查器 */
function renderInspector() {
  const sh = S.proj.shots[S.sel];
  if (!sh) return;
  $("#fKind").value = sh.kind || "big";
  $("#fDur").value = sh.duration.toFixed(1);
  $("#fStart").value = sh.start.toFixed(2);
  $("#fNarr").value = sh.narration;
  renderDeckFields(sh);
  const subs = splitSentences(sh.narration);
  const chars = sh.narration.replace(/[\s，。！？、；：""'']/g, "").length;
  const rate = chars / Math.max(0.5, sh.duration);
  const bad = rate > 6.2;
  renderSubs(sh);
  const clauseN = splitClauses(sh.narration).length;
  // "句"和"字幕段"不是一个数：按句号算 2 句，按逗号切却有 4 段字幕。
  // 只显示 2 句会让用户以为导出是两屏字幕。
  $("#fMeta").innerHTML =
    `${subs.length} 句 / ${clauseN} 段字幕 · ${chars} 字 · <span class="pill ${bad ? "bad" : "ok"}">` +
    `语速 ${rate.toFixed(1)} 字/秒</span>` +
    (bad ? `<div style="margin-top:6px;color:var(--bad)">偏快：删 1~2 字或把时长加到 ${(chars / 6).toFixed(1)}s</div>` : "") +
    numMismatchHint(sh);
}

/* 口播里的数字 vs 画面里的数字。
   "改了一半"是这个工具最容易出的事故：画面改成 4.4%，字幕还念 3.3%，
   成片发出去就是错的。流水线有事实链门禁，但那是导出后；编辑器里应该当场提醒。 */
function numMismatchHint(sh) {
  const nums = (t) => (String(t).match(/\d+(?:\.\d+)?/g) || []);
  const deckText = JSON.stringify(sh.deck || {});
  const inDeck = nums(deckText);
  const inNarr = nums(sh.narration || "");
  const missing = [...new Set(inNarr)].filter((n) => !inDeck.includes(n));
  if (!missing.length) return "";
  return `<div style="margin-top:6px;color:var(--warn)">
    口播里有 ${missing.slice(0, 3).join("、")}，画面上没有 —— 检查是不是只改了一边</div>`;
}

/* 画面文字：每种镜头类型吃不同字段（big 吃 lines / compare 吃左右两组 /
   stat 吃数值+单位 / cards 与 flow 吃条目）。这里按类型动态出表单，
   改哪个字段就只回传哪个，后端做深合并。 */
function renderDeckFields(sh) {
  const d = sh.deck || {};
  const box = $("#fDeck");
  const inp = (key, label, val, ph = "") => `
    <div class="dk"><label>${label}</label>
      <input data-dk="${key}" value="${esc(val ?? "")}" placeholder="${esc(ph)}"></div>`;
  const area = (key, label, val, rows = 2) => `
    <div class="dk"><label>${label}</label>
      <textarea data-dk="${key}" rows="${rows}">${esc(val ?? "")}</textarea></div>`;
  let html = "";
  if (sh.kind === "big") {
    const lines = d.lines || [];
    html += inp("lines.0", "第一行", lines[0] || "", "主角大字");
    html += inp("lines.1", "第二行", lines[1] || "", "补充说明");
    html += inp("sub", "副标", d.sub || "", "一句话解释");
    html += inp("ghost", "背景水印字", d.ghost || "", "可选，如 1.3");
  } else if (sh.kind === "compare") {
    const L = d.left || {}, R = d.right || {};
    html += `<div class="grp"><div class="ghead">左边</div>
      ${inp("left.label", "标签", L.label)}${inp("left.value", "主数值", L.value)}${inp("left.note", "注释", L.note)}</div>`;
    html += `<div class="grp"><div class="ghead">右边</div>
      ${inp("right.label", "标签", R.label)}${inp("right.value", "主数值", R.value)}${inp("right.note", "注释", R.note)}</div>`;
    html += inp("note", "底部结论", d.note);
  } else if (sh.kind === "stat") {
    html += inp("value", "数字", d.value, "2.7");
    html += inp("unit", "单位", d.unit, "倍");
    html += inp("label", "这数字是什么", d.label);
    html += inp("note", "补充一句", d.note);
  } else {
    const items = d.items || [];
    const n = sh.kind === "cards" ? 4 : 3;
    for (let i = 0; i < n; i++) {
      const it = items[i] || {};
      html += `<div class="grp"><div class="ghead">第 ${i + 1} 条</div>
        ${inp(`items.${i}.t`, "标题", it.t)}
        ${inp(`items.${i}.d`, "说明", it.d)}</div>`;
    }
    html += inp("note", "底部结论", d.note);
  }
  box.innerHTML = html;
  box.querySelectorAll("[data-dk]").forEach((el) => {
    let t;
    el.oninput = () => {
      clearTimeout(t);
      const val = el.value;
      t = setTimeout(() => { patchDeck(sh, el.dataset.dk, val); }, 350);
    };
  });
}

/** 把 "left.label" / "items.0.t" 这种路径写回 deck 并保存。 */
function patchDeck(sh, path, raw) {
  const parts = path.split(".");
  const deck = JSON.parse(JSON.stringify(sh.deck || {}));
  let cur = deck;
  for (let i = 0; i < parts.length - 1; i++) {
    const k = parts[i];
    if (cur[k] == null) cur[k] = /^\d+$/.test(parts[i + 1]) ? [] : {};
    cur = cur[k];
  }
  const last = parts[parts.length - 1];
  if (raw === "" && /^\d+$/.test(last)) return;
  cur[last] = raw;
  // lines 为空行时不留空串，保持数据干净
  if (Array.isArray(deck.lines)) deck.lines = deck.lines.filter((x) => x && x.trim());
  if (Array.isArray(deck.items)) deck.items = deck.items.filter((x) => x && (x.t || x.d));
  beginEdit();
  commit(() => { sh.deck = deck; sh.headline = (deck.lines || []).join(" / "); }, { undo: false });
  requestFrame();
}

/** 复制当前镜头：插在它后面，id 加后缀。 */
function duplicateShot() {
  const i = S.sel, src = S.proj.shots[i];
  if (!src) return;
  const id = `${src.id}-c${Date.now().toString(36).slice(-3)}`;
  beginEdit();
  const clone = {
    id, narration: src.narration, duration: src.duration, kind: src.kind,
    deck: JSON.parse(JSON.stringify(src.deck || {})), start: 0, emphasis: [],
    headline: src.headline,
  };
  commit(() => {
    S.proj.shots.splice(i + 1, 0, clone);
    retime();
  }, { undo: false });
  S.sel = i + 1;
  renderInspector();
  renderTimeline();
  requestFrame(true);
  scheduleSave();
  toast("已复制镜头");
}

/** 删除当前镜头。只剩一个时不允许——没有镜头的片子渲不出来。 */
function deleteShot() {
  if (S.proj.shots.length <= 1) return toast("至少留一个镜头");
  const i = S.sel;
  beginEdit();
  commit(() => {
    S.proj.shots.splice(i, 1);
    retime();
  }, { undo: false });
  S.sel = Math.max(0, i - 1);
  renderInspector();
  renderTimeline();
  requestFrame(true);
  scheduleSave();
  toast("已删除镜头");
}

/** 与流水线 make.py 的 CLAUSE 完全一致：字幕就是按这个切的。 */
function splitClauses(s) {
  const m = String(s || "").match(/[^，。；：？！,;?!]+[，。；：?!]?/g) || [];
  return m.map((x) => x.trim().replace(/[，。；：？！,;?!]+$/, "").trim()).filter(Boolean);
}

/** 字幕断句预览：用户看不见导出后的分行，等到成片才发现"这句顶成两行"。 */
function renderSubs(sh) {
  const box = $("#fSubs");
  if (!box) return;
  const lines = splitClauses(sh.narration);
  if (!lines.length) {
    box.innerHTML = `<span class="meta">还没有口播文案</span>`;
    return;
  }
  box.innerHTML = lines.map((l, i) => {
    const n = l.length;
    const cls = n > 34 ? "bad" : n > 17 ? "warn" : "";
    return `<div class="subline ${cls}" title="${n} 字">
      <span class="sn">${i + 1}</span><span class="st">${esc(l)}</span>
      <span class="sc">${n}</span></div>`;
  }).join("");
}

function splitSentences(s) {
  return (s || "").split(/(?<=[。！？；?])/).map((x) => x.trim()).filter(Boolean);
}

/* ------------------------------------------------------------ 改动与保存 */
function snapshot() {
  return {
    title: S.proj.title, theme: S.proj.theme, voice: S.proj.voice,
    transition: S.proj.transition || { type: "", dur: 0.24 },
    shots: S.proj.shots.map((s) => ({
      id: s.id, narration: s.narration, duration: s.duration, kind: s.kind, deck: s.deck,
    })),
    // order：服务端据此重排/新增/删除镜头（只发逐镜更新是改不了镜头数量的）
    order: S.proj.shots.map((s) => ({
      id: s.id, narration: s.narration, duration: s.duration, mood: s.mood, deck: s.deck,
    })),
  };
}

/* 换镜头类型时给一套"能立刻渲出来"的默认内容。
   否则从"对比"切到"卡片"会得到空卡片 —— 用户看到的是黑屏，还以为工具坏了。
   素材优先用原有 lines，其次从口播里按句切。 */
function defaultDeck(kind, sh) {
  const old = sh.deck || {};
  const lines = (old.lines || []).slice();
  if (!lines.length) {
    lines.push(...String(sh.narration || "").split(/[，。！？；]/).map((x) => x.trim()).filter(Boolean));
  }
  const txt = lines.join(" ") || sh.narration || "";
  const num = (txt.match(/\d+(?:\.\d+)?%?/) || [""])[0];
  const L = (i, d) => lines[i] || d || "";
  if (kind === "big") return { kind, lines: lines.slice(0, 2), style: old.style || "slam" };
  if (kind === "compare") {
    return {
      kind,
      left: { label: L(0, "左边"), value: (old.left && old.left.value) || num || "—", note: "" },
      right: { label: L(1, "右边"), value: (old.right && old.right.value) || "—", note: "" },
      note: L(2, ""),
    };
  }
  if (kind === "stat") {
    const unit = (txt.match(/\d+(?:\.\d+)?\s*([^\s\d，。]{1,3})/) || ["", ""])[1];
    return { kind, value: num.replace(/[^\d.]/g, "") || "—", unit: old.unit || unit || "",
             label: L(0, ""), note: L(1, "") };
  }
  // 卡片/流程的标题要短：整句口播塞进去会撑爆版面（实测一行只放得下 ~14 字）
  const chunks = String(sh.narration || lines.join("，"))
    .split(/[，。！？；：]/).map((x) => x.trim()).filter(Boolean);
  const items = [];
  for (const c of chunks) {
    if (items.length >= 3) break;
    if (c.length < 2) continue;
    items.push({ t: c.length > 14 ? c.slice(0, 13) + "…" : c, d: "" });
  }
  while (items.length && items.length < 3) items.push({ t: "补一句关键信息", d: "" });
  return { kind, items, note: "" };
}

/* 连续输入（打字、拖滑块）应该只产生一个撤销点。
   机制：开一个 900ms 的窗口，窗口内第一次改动压栈，后续改动共用这个点。 */
let _editWindow = 0;
function beginEdit() {
  const now = Date.now();
  if (now - _editWindow > 900) {
    S.undo.push(snapshot());
    if (S.undo.length > 60) S.undo.shift();
    S.redo = [];
    updateUndoBtns();
  }
  _editWindow = now;
}

function commit(mutate, { save = true, undo = true } = {}) {
  S.edited = true;
  updateFilmTag();
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
  S.frameRev = (S.frameRev || 0) + 1;
  const snap = snapshot();
  const st = $("#saveState");
  st.textContent = "保存中…";
  try {
    const r = await jpost("/api/project", {
      slug: S.slug, title: snap.title, theme: snap.theme, voice: snap.voice,
      transition: snap.transition,
      shots: snap.shots,
      // order 必须真的发出去：服务端靠它重排/增删镜头。
      // （第一次只加进了 snapshot()，那是撤销用的本地快照，服务端根本没收到。）
      order: snap.order,
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
  // 必须整表替换：按下标 Object.assign 只在"镜头数量不变"时才对，
  // 删除/复制镜头之后长度变了，撤销就永远少一个（实测删 1 个 → 撤销仍是 4 个）。
  S.proj.shots = snap.shots.map((s) => ({
    ...s,
    deck: s.deck ? JSON.parse(JSON.stringify(s.deck)) : {},
    emphasis: s.emphasis || [],
    start: 0,
  }));
  S.sel = Math.min(S.sel, S.proj.shots.length - 1);
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
  $("#titleInput").oninput = (e) => { beginEdit(); S.proj.title = e.target.value; scheduleSave(); };
  $("#titleInput").onblur = (e) => { commit(() => { S.proj.title = e.target.value; }, { undo: false }); };

  // 检查器字段
  $("#fKind").onchange = (e) => {
    const v = e.target.value;
    commit(() => {
      const sh = S.proj.shots[S.sel];
      // 只改 kind 不改 deck 的话，场景还是按旧 kind 画（deck.kind 才是渲染真源）
      sh.kind = v;
      sh.deck = defaultDeck(v, sh);
      sh.headline = (sh.deck.lines || []).join(" / ");
    });
    renderInspector();
  };
  $("#fDur").onchange = (e) => {
    const v = clamp(parseFloat(e.target.value) || 4, 2, 30);
    commit(() => { S.proj.shots[S.sel].duration = +v.toFixed(2); retime(); });
    seek(S.proj.shots[S.sel].start + 0.2, { force: true });
  };
  let narrTimer;
  $("#fNarr").oninput = (e) => {
    const v = e.target.value;
    clearTimeout(narrTimer);
    beginEdit();
    narrTimer = setTimeout(() => {
      commit(() => { S.proj.shots[S.sel].narration = v; }, { undo: false });
    }, 400);
  };
  $("#fTheme").onchange = (e) => {
    commit(() => { S.proj.theme = e.target.value; });
    requestFrame(true);
  };
  $("#fTrans").onchange = (e) => {
    const type = e.target.value;
    const dur = parseFloat($("#fTransDur").value) || 0.24;
    commit(() => { S.proj.transition = { type, dur }; });
    $("#fTransDur").disabled = !type;
    toast(type ? "转场已设置（导出时生效）" : "已关闭转场");
  };
  $("#fTransDur").onchange = (e) => {
    const dur = clamp(parseFloat(e.target.value) || 0.24, 0.05, 1);
    e.target.value = dur.toFixed(2);
    commit(() => { S.proj.transition = { type: (S.proj.transition || {}).type || "", dur }; });
  };

  $("#fVoice").onchange = (e) => commit(() => { S.proj.voice = e.target.value; });

  // 缩放
  $("#zoom").oninput = (e) => { S.pxPerSec = +e.target.value; renderTimeline(); };

  bindTimeline();
  bindPanels();
  bindExport();
  bindTour();
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
  if (name === "deliver") { return openDeliver(false); }
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
    // 选音色必须能先听：看文字描述选出来的音色，八成不是想要的那个
    L.innerHTML = [
      ["yunxi", "云希 · 男声", "沉稳、适合硬核科普"],
      ["xiaoxiao", "晓晓 · 女声", "清晰、适合知识口播"],
      ["yunjian", "云健 · 男声", "厚重、适合叙事"],
    ].map(([k, n, d]) => `
      <div class="asset voice ${S.proj.voice === k ? "on" : ""}" data-voice="${k}">
        <button class="playbtn" data-play="${k}" title="试听">▶</button>
        <div><div class="t">${n}${S.proj.voice === k ? " ✓" : ""}</div>
          <div class="s">${d}</div></div>
      </div>`).join("") +
      `<div class="meta" style="padding:8px 10px">点 ▶ 试听；点卡片切换音色（导出时生效）。</div>`;

    L.querySelectorAll("[data-play]").forEach((b) => {
      b.onclick = (e) => {
        e.stopPropagation();
        playVoiceSample(b.dataset.play, b);
      };
    });
    L.querySelectorAll("[data-voice]").forEach((el) => {
      el.onclick = () => {
        commit(() => { S.proj.voice = el.dataset.voice; });
        $("#fVoice").value = S.proj.voice;
        openPanel("voice", $('.rail button[data-panel="voice"]'));
        toast("音色已切换：" + el.querySelector(".t").textContent.replace(" ✓", ""));
      };
    });
  } else if (name === "theme") {
    // 用**真实帧**做色板预览：纯色块看不出这套配色在真画面上好不好看
    L.innerHTML = THEMES.map(([k, n]) => `
      <div class="asset theme ${S.proj.theme === k ? "on" : ""}" data-theme="${k}">
        <img src="/api/theme-preview?slug=${encodeURIComponent(S.slug)}&theme=${k}&v=${S.rev || 0}"
             loading="lazy" alt="">
        <div><div class="t">${n}${S.proj.theme === k ? " ✓" : ""}</div>
          <div class="s">${k}${["porcelain", "rice-paper", "arctic"].includes(k) ? " · 浅色" : " · 深色"}</div></div>
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
      ["Ctrl+滚轮", "缩放时间线"], ["Esc", "逐层关闭（面板/弹窗/抽屉）"],
    ].map(([k, d]) => `<div class="asset"><div style="width:44px;text-align:center;font:11px var(--mono);color:var(--ink1)">${k}</div>
      <div><div class="t">${d}</div></div></div>`).join("");
    L.insertAdjacentHTML("beforeend", `<button class="btn" id="btnTour" style="margin:12px 10px">再看一次引导</button>`);
    const bt = $("#btnTour");
    if (bt) bt.onclick = () => { closePanel(); showTour(); };
  } else {
    L.innerHTML = `<div class="meta" style="padding:8px">正在读取质检结果…</div>`;
    const qa = await jget(`/api/stage-status?slug=${encodeURIComponent(S.slug)}`).catch(() => null);
    renderChecks(L, qa);
  }
}

/* 本地即时体检：不依赖跑 QA，改一个字就能重算。
   跑一次 verify.sh 要十几秒，而"数字对不上/语速过快/标题太长"这类问题
   当场就能看出来——不该等到导出之后。 */
function localChecks() {
  const out = [];
  const shotOf = (i) => `镜头 ${String(i + 1).padStart(2, "0")}`;
  S.proj.shots.forEach((sh, i) => {
    const nums = (t) => (String(t).match(/\d+(?:\.\d+)?/g) || []);
    const inDeck = nums(JSON.stringify(sh.deck || {}));
    const miss = [...new Set(nums(sh.narration))].filter((n) => !inDeck.includes(n));
    if (miss.length) {
      out.push({ ok: false, name: `${shotOf(i)} 口播与画面数字不一致`, shot: i,
                 detail: `口播有 ${miss.slice(0, 3).join("、")}，画面上没有` });
    }
    const chars = sh.narration.replace(/[\s，。！？、；：""'']/g, "").length;
    const rate = chars / Math.max(0.5, sh.duration);
    if (rate > 6.2) {
      out.push({ ok: false, name: `${shotOf(i)} 语速偏快`, shot: i,
                 detail: `${rate.toFixed(1)} 字/秒，建议删字或加到 ${(chars / 6).toFixed(1)}s` });
    }
    if (!String(sh.narration || "").trim()) {
      out.push({ ok: false, name: `${shotOf(i)} 没有口播`, shot: i, detail: "这一镜会是静音" });
    }
  });
  const n = (S.proj.title || "").trim().length;
  if (n > 20) out.push({ ok: false, name: "标题超过 20 字", detail: `现在 ${n} 字，信息流会截断` });
  const hook = S.proj.shots[0];
  // 8s 是个宽松阈值：7 秒左右的钩子配动态画面完全能留人，
  // 卡到 5s 会天天误报，用户就不看这一栏了。
  if (hook && hook.duration > 8) {
    out.push({ ok: false, name: "首镜偏长", shot: 0,
               detail: `${hook.duration.toFixed(1)}s，前 3 秒要留住人，建议 ≤8s` });
  }
  return out;
}

function renderChecks(L, qa) {
  const items = (qa && qa.items) || [];
  const local = localChecks();
  const localHtml = `
    <div class="cksec">本地即时体检</div>
    ${local.length
      ? local.map((x) => `<div class="asset ckbad" ${x.shot != null ? `data-shot="${x.shot}"` : ""}>
          <div style="width:22px;text-align:center;color:var(--bad)">✕</div>
          <div><div class="t">${esc(x.name)}</div><div class="s">${esc(x.detail || "")}</div></div></div>`).join("")
      : `<div class="asset"><div style="width:22px;text-align:center;color:var(--ok)">✓</div>
         <div><div class="t">没有发现问题</div><div class="s">数字、语速、标题、首镜长度都正常</div></div></div>`}
    <div class="cksec">流水线质检${items.length ? `（${items.filter((x) => x.ok).length}/${items.length} 通过）` : ""}</div>`;
  const bindLocal = () => {
    L.querySelectorAll("[data-shot]").forEach((el) => {
      el.onclick = () => {
        const i = +el.dataset.shot;
        S.sel = i; markSelection(); renderInspector();
        seek(S.proj.shots[i].start + 0.15, { force: true });
      };
    });
  };
  if (!items.length) {
    L.innerHTML = localHtml + `<div class="meta" style="padding:10px;line-height:1.9">还没跑过流水线质检。<br>
      点顶栏「质检」跑一次，会检查：清单完成度、图形事实链、文档完整度、发布可行性、
      长短版一致、响度一致、配音情绪、封面可读性、前 3 秒留人、画面体检。</div>`;
    bindLocal();
    return;
  }
  L.innerHTML = localHtml + items.map((x) => `
    <div class="asset"><div style="width:22px;text-align:center;color:${x.ok ? "var(--ok)" : "var(--bad)"}">
      ${x.ok ? "✓" : "✕"}</div>
      <div><div class="t">${esc(x.name)}</div>
        <div class="s" title="${esc(x.detail || "")}">${esc((x.detail || "").slice(0, 90))}${
          (x.detail || "").length > 90 ? "…" : ""}</div></div></div>`).join("");
  bindLocal();
}

const THEME_SWATCH = {
  porcelain: "#eaf1f5", "rice-paper": "#f2ece1", arctic: "#e8f0f7",
  midnight: "#0e1220", graphite: "#15171a", jade: "#0f1d19",
  "noir-gold": "#12100c", aubergine: "#1a1020",
};
const themeSwatch = (k) => `background:${THEME_SWATCH[k] || "#333"};color:${
  ["porcelain", "rice-paper", "arctic"].includes(k) ? "#1b1b1b" : "#fff"};font:11px var(--sans)`;

/* 试听：优先命中服务端缓存的样本；没有样本就如实说，不放静音糊弄。 */
let _voiceAudio = null;
async function playVoiceSample(key, btn) {
  if (_voiceAudio) { _voiceAudio.pause(); _voiceAudio = null; }
  const old = btn.textContent;
  btn.textContent = "…";
  try {
    const r = await fetch(`/api/voice-sample?voice=${encodeURIComponent(key)}`);
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      toast(j.error ? "试听不可用：" + j.error.slice(0, 40) : "试听不可用");
      btn.textContent = old;
      return;
    }
    const blob = await r.blob();
    _voiceAudio = new Audio(URL.createObjectURL(blob));
    _voiceAudio.onended = () => { btn.textContent = "▶"; };
    btn.textContent = "■";
    await _voiceAudio.play();
  } catch (e) {
    toast("试听失败");
    btn.textContent = old;
  }
}

function closePanel() {
  $("#drawer").classList.remove("on");
  $$(".rail button").forEach((b) => b.classList.remove("on"));
  S.panel = null;
}

/* ------------------------------------------------------------ 交付
   导出完不是"结束了"，而是要拿到能直接发的东西：视频 + 封面 + 标题 + 话题。
   这一屏就是交付清单，缺什么如实标出来。 */
async function openDeliver(auto = false) {
  const d = await jget(`/api/deliver?slug=${encodeURIComponent(S.slug)}`).catch(() => null);
  if (!d) return toast("读不到交付信息");
  if (!d.video) {
    // 一条 toast 一闪而过，用户还是不知道该干嘛。直接把交付面板打开，
    // 显示"还缺哪一步"，并给一个直达导出的按钮。
    $("#dlTitle").textContent = "还没有成片";
    $("#dlVideo").removeAttribute("src");
    if ($("#dlVideo").load) $("#dlVideo").load();
    const left = $("#dlLeft");
    if (left) left.classList.add("off");
    $("#deliverScrim").classList.add("novideo");
    $("#dlStat").textContent = "先导出一次，这里会出现成片、封面、标题与话题";
    $("#dlPubTitle").value = d.publish_title || "";
    $("#dlTitleCount").textContent = "";
    $("#dlTopics").innerHTML = `<span class="meta">导出后自动带上话题</span>`;
    $("#dlCovers").innerHTML = `<div class="meta" style="padding:6px 0">
      还没有封面 —— 导出时会一起生成 5 张（主封面 / 电影感 / 编辑风 / 蓝图 / 竖版）</div>`;
    $("#dlChosen").textContent = "";
    $("#dlQA").innerHTML = `<span class="pill">未质检</span>`;
    if ($("#dlDown")) $("#dlDown").style.display = "none";
    if ($("#dlDownSummary")) $("#dlDownSummary").style.display = "none";
    if ($("#dlRerun")) {
      $("#dlRerun").textContent = "去导出";
      $("#dlRerun").onclick = () => {
        $("#deliverScrim").classList.remove("on");
        $("#exportScrim").classList.add("on");
      };
    }
    $("#deliverScrim").classList.add("on");
    return;
  }
  if ($("#dlDown")) $("#dlDown").style.display = "";
  const left2 = $("#dlLeft");
  if (left2) left2.classList.remove("off");
  $("#deliverScrim").classList.remove("novideo");
  if ($("#dlRerun")) $("#dlRerun").textContent = "重新导出";
  $("#dlTitle").textContent = auto ? "导出完成" : "成片交付";
  $("#dlVideo").src = d.video + "?v=" + Date.now();
  $("#dlStat").textContent =
    `${d.duration}s · ${d.size_mb}MB · ${(d.size || "").replace("x", "×")} · ${d.fps || 30}fps · h264+aac` +
    (d.preset === "draft" ? " · 快速预览画质" : "");
  $("#dlPubTitle").value = d.publish_title || "";
  const count = () => {
    const n = $("#dlPubTitle").value.trim().length;
    $("#dlTitleCount").innerHTML = `${n} 字 ` +
      (n > 20 ? `<span style="color:var(--bad)">超过 20 字，信息流会被截断</span>` : "");
  };
  count();
  $("#dlPubTitle").oninput = count;
  $("#dlTopics").innerHTML = (d.topics && d.topics.length ? d.topics : d.tags || [])
    .map((t) => `<span class="chip">${esc(t)}</span>`).join("") || `<span class="meta">没有话题，建议加 2~3 个</span>`;
  // 上次选过的封面要记住：选了 A 下次打开还是 A（存在 out/<slug>/publish.json）
  const picked = d.picked_cover || (d.covers[0] && d.covers[0].url) || "";
  $("#dlCovers").innerHTML = d.covers.map((c) => `
    <label class="${c.url === picked ? "on" : ""}" data-cover="${esc(c.url)}">
      <input type="radio" name="cover" ${c.url === picked ? "checked" : ""}>
      <img src="${c.url}" alt=""><span>${esc(c.label)}</span>
    </label>`).join("");
  const markChosen = () => {
    const on = $("#dlCovers label.on");
    $("#dlChosen").textContent = on ? "已选：" + on.querySelector("span").textContent : "";
  };
  markChosen();
  $("#dlCovers").querySelectorAll("label").forEach((l) => {
    l.onclick = async () => {
      $("#dlCovers").querySelectorAll("label").forEach((x) => x.classList.remove("on"));
      l.classList.add("on");
      markChosen();
      await jpost("/api/publish", { slug: S.slug, cover: l.dataset.cover }).catch(() => null);
    };
  });
  $("#dlDown").href = d.video;
  const sum = $("#dlDownSummary");
  if (d.summary) { sum.href = d.summary; sum.style.display = ""; } else { sum.style.display = "none"; }
  const qa = d.qa || { items: [] };
  const bad = (qa.items || []).filter((x) => !x.ok);
  $("#dlQA").innerHTML = bad.length
    ? `<span class="pill bad">${(qa.items.length - bad.length)}/${qa.items.length} 通过</span><div style="margin-top:6px">${
        bad.map((x) => "· " + esc(x.name)).join("<br>")}</div>`
    : (qa.items && qa.items.length ? `<span class="pill ok">质检全过</span>` : `<span class="pill">未跑质检</span>`);
  $("#dlRerun").onclick = () => {
    $("#deliverScrim").classList.remove("on");
    $("#exportScrim").classList.add("on");
  };
  $("#deliverScrim").classList.add("on");
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
  const dup = $("#btnDup"), del = $("#btnDel");
  if (dup) dup.onclick = () => duplicateShot();
  if (del) del.onclick = () => deleteShot();
  $("#btnExport").onclick = () => { $("#exportScrim").classList.add("on"); updateExMeta(); };
  // 窄屏（<1180px）检查器变抽屉，点顶栏"检查"开合
  const inspBtn = $("#btnInsp");
  if (inspBtn) inspBtn.onclick = () => document.body.classList.toggle("insp-open");

  $("#btnQA").onclick = runQA;
  $("#exportClose").onclick = $("#exCancel").onclick = () => $("#exportScrim").classList.remove("on");
  $("#dlClose").onclick = () => $("#deliverScrim").classList.remove("on");
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
  const r = await jpost("/api/stage", {
    slug: S.slug, stage: "render", opts: { preset: $("#exPreset").value } });
  if (!r.job) { $("#exLog").textContent = "启动失败"; $("#exGo").disabled = false; return; }
  pollJob(r.job, (line) => {
    $("#exLog").textContent = line;
    // make.py 会输出「渲染 120/1104 30fps ETA 12s」的进度行，抓出来做真实进度
    const all = line.match(/渲染\s+(\d+)\/(\d+)/g) || [];
    const m = all.length ? all[all.length - 1].match(/渲染\s+(\d+)\/(\d+)/) : null;
    if (m) {
      const pct = 20 + 70 * (+m[1] / +m[2]);
      $("#exBar").style.width = pct.toFixed(1) + "%";
      const eta = line.match(/ETA\s+(\d+)\s*s/);
      if (eta) {
        $("#exEta").textContent = `还剩 ~${eta[1]} 秒`;
      } else {
        // 没有 ETA 就按已用时间与进度估一个（比"转圈"诚实）
        const done = +m[1], all2 = +m[2];
        const used = (performance.now() - t0) / 1000;
        const left = done > 8 ? (used / done) * (all2 - done) : 0;
        $("#exEta").textContent = left > 0 ? `还剩 ~${Math.round(left)} 秒` : "估算中…";
      }
    } else if (/\[tts\]/.test(line)) { $("#exBar").style.width = "12%"; $("#exEta").textContent = "配音中…"; }
    else if (/\[render\]/.test(line)) $("#exBar").style.width = "20%";
    else if (/\[ok\]/.test(line)) $("#exBar").style.width = "96%";
  }, (ok) => {
    const secs = ((performance.now() - t0) / 1000).toFixed(0);
    $("#exGo").disabled = false;
    if (!ok) {
      $("#exBar").style.width = "100%";
      $("#exBar").style.background = "var(--bad)";
      $("#exLog").textContent += `\n\n导出失败（${secs}s），看上面的日志。`;
      toast("导出失败");
      return;
    }
    $("#exBar").style.background = "";
    $("#exBar").style.width = "100%";
    $("#exLog").textContent += `\n\n导出完成，用时 ${secs}s。`;
    toast("导出完成");
    // 成片换了：清掉"改动未导出"标记，并让 <video> 重新拉一次，
    // 否则按空格播的还是浏览器缓存里的旧文件。
    S.edited = false;
    updateFilmTag();
    try {
      const f = filmEl();
      f.src = `/files/${encodeURIComponent(S.slug)}/${encodeURIComponent(S.slug)}.mp4?v=${Date.now()}`;
      f.load();
    } catch {}
    _thumbCacheClear();
    renderTimeline();
    setTimeout(() => openDeliver(true), 400);
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
    // done(ok)：把成功/失败都交给回调，别让调用方自己猜
    if (j.status === "done") return done && done(true);
    if (j.status === "fail") { toast("失败，看日志"); return done && done(false); }
    await sleep(700);
  }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------ 命令面板
   剪辑软件里"换一条片子"和"跑一个动作"都该是一次按键的事，
   而不是回到首页再点三层。⌘K 一个入口把两件事合在一起。 */
const PAL = { items: [], idx: 0, open: false };

async function openPalette() {
  PAL.open = true;
  $("#palette").hidden = false;
  $("#palInput").value = "";
  const list = await jget("/api/projects").catch(() => ({ projects: [] }));
  const acts = [
    { label: "导出成片", hint: "⌘E", run: () => $("#btnExport").click() },
    { label: "跑质检", hint: "", run: () => runQA() },
    { label: "查看交付面板", hint: "", run: () => openDeliver(false) },
    { label: "新建项目", hint: "", run: () => { location.href = "/"; } },
    { label: "回到片库", hint: "Esc", run: () => { location.href = "/"; } },
    { label: "立即保存", hint: "⌘S", run: () => doSave() },
    { label: "切换主题：瓷白", run: () => setTheme("porcelain") },
    { label: "切换主题：午夜", run: () => setTheme("midnight") },
    { label: "切换主题：宣纸", run: () => setTheme("rice-paper") },
  ];
  const projs = (list.projects || []).slice(0, 40).map((p) => ({
    label: p.title, kind: p.slug, hint: p.duration + "s",
    run: () => { location.href = "/app/#" + encodeURIComponent(p.slug); },
  }));
  PAL.items = [...acts.map((a) => ({ ...a, kind: "命令" })),
               ...projs.map((p) => ({ ...p, kind: "项目" }))];
  PAL.idx = 0;
  renderPalette("");
  $("#palInput").focus();
}

function setTheme(t) {
  commit(() => { S.proj.theme = t; });
  $("#fTheme").value = t;
  requestFrame(true);
}

function renderPalette(q) {
  const kw = q.trim().toLowerCase();
  const rows = PAL.items.filter((it) =>
    !kw || (it.label + " " + (it.kind || "")).toLowerCase().includes(kw));
  PAL.shown = rows;
  PAL.idx = Math.min(PAL.idx, Math.max(0, rows.length - 1));
  $("#palList").innerHTML = rows.map((it, i) => `
    <div class="palitem ${i === PAL.idx ? "on" : ""}" data-i="${i}">
      <span class="kind">${esc(it.kind || "")}</span>
      <span>${esc(it.label)}</span>
      <span class="k">${esc(it.hint || "")}</span>
    </div>`).join("") || `<div class="palitem">没有匹配项</div>`;
  $("#palList").querySelectorAll("[data-i]").forEach((el) => {
    el.onclick = () => runPalette(+el.dataset.i);
  });
}

function runPalette(i) {
  const it = (PAL.shown || [])[i];
  closePalette();
  if (it) it.run();
}

function closePalette() {
  PAL.open = false;
  $("#palette").hidden = true;
}

function bindPalette() {
  $("#palInput").oninput = (e) => { PAL.idx = 0; renderPalette(e.target.value); };
  $("#palette").onclick = (e) => { if (e.target.id === "palette") closePalette(); };
  $("#palInput").onkeydown = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); PAL.idx = Math.min(PAL.idx + 1, (PAL.shown || []).length - 1); renderPalette($("#palInput").value); }
    else if (e.key === "ArrowUp") { e.preventDefault(); PAL.idx = Math.max(0, PAL.idx - 1); renderPalette($("#palInput").value); }
    else if (e.key === "Enter") { e.preventDefault(); runPalette(PAL.idx); }
    else if (e.key === "Escape") { closePalette(); }
  };
}

/* ------------------------------------------------------------ 快捷键 */
function bindKeys() {
  window.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    const meta = e.metaKey || e.ctrlKey;
    if (meta && e.key.toLowerCase() === "k") {
      e.preventDefault(); PAL.open ? closePalette() : openPalette(); return;
    }
    // Esc 必须在 PAL.open 的拦截之前处理：命令面板打开时，其余按键要让位，
    // 但"关闭它"这件事本身不能被吞掉（之前就是被 `if (PAL.open) return` 挡住的）。
    if (e.key === "Escape") { e.preventDefault(); escClose(); return; }
    if (PAL.open) return;
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
    }
  });
}

/** Esc 逐层关闭：命令面板 → 弹窗 → 抽屉 → 检查器抽屉。
    原来 Esc 是直接 location.href="/"，用户关个弹窗就被踢回片库、上下文全丢。 */
function escClose() {
  if (!$("#palette").hidden) return closePalette();
  const modal = ["#exportScrim", "#deliverScrim", "#newScrim"]
    .map((s) => $(s)).find((el) => el && el.classList.contains("on"));
  if (modal) return modal.classList.remove("on");
  if ($("#drawer").classList.contains("on")) return closePanel();
  if (document.body.classList.contains("insp-open")) {
    return document.body.classList.remove("insp-open");
  }
  stopPlayIfAny();
}

function stopPlayIfAny() { if (S.playing) pause(); }

/** 转场控件：项目级设置，渲染在「项目」区。 */
function renderTransition() {
  const tr = S.proj.transition || { type: "", dur: 0.24 };
  const sel = $("#fTrans"), dur = $("#fTransDur");
  if (!sel || !dur) return;
  sel.value = tr.type || "";
  dur.value = (tr.dur || 0.24).toFixed(2);
  dur.disabled = !tr.type;
}

/* ------------------------------------------------------------ 首用引导 */
const TOUR_KEY = "sv.tour.v1";

function bindTour() {
  const scrim = $("#tourScrim");
  if (!scrim) return;
  const close = () => {
    if ($("#tourNever") && $("#tourNever").checked) localStorage.setItem(TOUR_KEY, "1");
    scrim.classList.remove("on");
    scrim.hidden = true;
  };
  $("#tourOk").onclick = close;
  $("#tourClose").onclick = close;
  scrim.onclick = (e) => { if (e.target === scrim) close(); };
  if (!localStorage.getItem(TOUR_KEY)) { scrim.hidden = false; scrim.classList.add("on"); }
}

function showTour() {
  const scrim = $("#tourScrim");
  if (scrim) {
    scrim.hidden = false;
    scrim.classList.add("on");
    localStorage.removeItem(TOUR_KEY);
  }
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
