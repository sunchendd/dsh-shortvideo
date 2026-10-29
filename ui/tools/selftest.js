#!/usr/bin/env node
/**
 * 界面自检：把"只有真点过才会发现"的那类 bug 变成可重复的检查。
 *
 * 这一路踩到的坑几乎都不是语法错误，而是"改了没生效"：
 *   - 选择器写错，CSS 静默失效
 *   - 插错文件，UI 变了磁盘没变
 *   - 撤销只按下标合并，删镜头后恢复不回来
 * 这些跑一遍页面就能发现，所以做成脚本。
 *
 * 用法： node ui/tools/selftest.js [--port 8123] [--keep]
 * 退出码：0 全过，1 有失败项（CI/门禁可以直接用）
 *
 * 它在**一次性项目**上跑（examples/zz-selftest.content.json），
 * 跑完删除，不碰真实数据。
 */
const { execFileSync, spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const ROOT = path.resolve(__dirname, "..", "..");
const args = process.argv.slice(2);
const portArg = args.indexOf("--port");
const PORT = portArg >= 0 ? args[portArg + 1] : "8123";
const KEEP = args.includes("--keep");

const SLUG = "zz-selftest";
const CONTENT = path.join(ROOT, "examples", `${SLUG}.content.json`);
const SOURCE = path.join(ROOT, "examples", "01-monty-hall.content.json");

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "✅" : "❌"} ${name}${detail ? "  " + detail : ""}`);
};

function cleanup() {
  for (const p of [CONTENT, path.join(ROOT, "examples", `oneimage-${SLUG}.json`)]) {
    try { fs.unlinkSync(p); } catch {}
  }
  try { fs.rmSync(path.join(ROOT, "examples", "out", SLUG), { recursive: true, force: true }); } catch {}
}

async function waitServer(port, tries = 80) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/projects`);
      if (r.ok) return true;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

(async () => {
  if (!fs.existsSync(SOURCE)) {
    console.error(`找不到样本项目：${SOURCE}`);
    process.exit(1);
  }
  cleanup();
  // 一次性项目：内容照抄样本，slug 不同
  fs.copyFileSync(SOURCE, CONTENT);

  let server = null;
  const already = await waitServer(PORT, 2);
  if (!already) {
    // 必须用带 Pillow 的解释器：系统 python3 装了 Pillow 才行，否则帧接口全 500、
    // 页面里 S.proj 为 null，自检会得到一堆 undefined（第一次就是这么踩的）
    const PY = process.env.SV_PY
      || (fs.existsSync("/root/shortvideo-lab/.venv/bin/python")
            ? "/root/shortvideo-lab/.venv/bin/python"
            : path.join(ROOT, ".venv", "bin", "python"));
    server = spawn(fs.existsSync(PY) ? PY : "python3",
                   [path.join(ROOT, "ui", "preview.py"), "--port", PORT], {
      cwd: ROOT, stdio: "ignore", detached: true,
    });
    server.unref();
    if (!(await waitServer(PORT))) {
      console.error("服务起不来");
      cleanup();
      process.exit(1);
    }
  }

  const ops = path.join(os.tmpdir(), "sv-selftest-ops.js");
  fs.writeFileSync(ops, `
    const sleep2 = (ms) => new Promise(r => setTimeout(r, ms));
    await sleep2(2600);
    const out = {};
    out.errors = (window.__errs || []).length;
    out.clips = qa(".clip").length;
    out.title = S.proj && S.proj.title;
    out.coverFrame = !!q("#frame") && q("#frame").src.length > 0;

    // 点镜头
    q(".clip[data-i='1']").dispatchEvent(new PointerEvent("pointerdown", {bubbles:true, clientX:0, clientY:0}));
    await sleep2(400);
    out.selected = S.sel;
    out.badge = (q("#shotBadge") || {}).textContent;

    // 改画面文字 → 自动保存
    const isBig = S.proj.shots[1].kind === "big";
    const dkPath = isBig ? "lines.0" : Object.keys(S.proj.shots[1].deck || {})
        .filter(k => k !== "kind")[0];
    const el = q("#fDeck [data-dk='" + dkPath + "']") || qa("#fDeck [data-dk]")[0];
    el.value = "自检改动XYZ";
    el.dispatchEvent(new Event("input", {bubbles:true}));
    await sleep2(1500);
    out.saveState = (q("#saveState") || {}).textContent;
    out.deckAfterEdit = JSON.stringify(S.proj.shots[1].deck).includes("自检改动XYZ");

    // 字幕断句预览 + "改动未导出"标记
    out.subLines = qa("#fSubs .subline").length;
    out.staleTag = (q("#liveTag") || {}).textContent || "";

    // 删除 → 撤销 → 重做
    const n0 = S.proj.shots.length;
    await click("#btnDel"); await sleep2(900);
    const n1 = S.proj.shots.length;
    await key("z", {metaKey:true}); await sleep2(1200);
    const n2 = S.proj.shots.length;
    await key("Z", {metaKey:true, shiftKey:true}); await sleep2(1200);
    const n3 = S.proj.shots.length;
    out.shots = { n0, n1, n2, n3 };
    await key("z", {metaKey:true}); await sleep2(1500);   // 复原到 n0

    // ⌘K 与 Esc
    await key("k", {metaKey:true}); await sleep2(400);
    out.paletteOpen = !q("#palette").hidden;
    const urlBefore = location.href;
    await key("Escape"); await sleep2(300);
    // 按一次 Esc：命令面板关掉、且人还留在编辑页
    out.escStayed = location.href === urlBefore && q("#palette").hidden === true;

    // 五个面板能打开
    const panels = {};
    for (const p of ["assets", "voice", "theme", "checks"]) {
      await click(".rail button[data-panel='" + p + "']");
      await sleep2(700);
      panels[p] = q("#drawer").classList.contains("on") && qa("#drawerList .asset, #drawerList .meta").length > 0;
      await click("#drawerClose"); await sleep2(200);
    }
    out.panels = panels;
    out.finalErrors = (window.__errs || []).length;
    return out;
  `);

  let r = null;
  try {
    const raw = execFileSync("node", [
      path.join(ROOT, "ui", "tools", "drive.js"),
      "--url", `http://127.0.0.1:${PORT}/app/#${SLUG}`,
      "--eval-file", ops,
    ], { cwd: ROOT, encoding: "utf8", timeout: 180000 });
    r = JSON.parse(raw);
  } catch (e) {
    console.error("浏览器自检执行失败：", (e.stdout || e.message || "").toString().slice(0, 400));
  }

  if (r && r.error) {
    console.error("浏览器返回了错误：", JSON.stringify(r).slice(0, 300));
    r = null;
  }
  if (!r) {
    check("浏览器自检可执行", false, "drive.js 没有返回结果");
  } else {
    check("页面无 JS 报错", r.errors === 0, `错误数 ${r.errors}`);
    check("时间线渲染出镜头", r.clips >= 3, `${r.clips} 个 clip`);
    check("画布有画面", r.coverFrame === true);
    check("点镜头能选中", r.selected === 1, `sel=${r.selected}`);
    check("改画面文字会存", r.deckAfterEdit === true && /保存/.test(r.saveState || ""), r.saveState);
    check("字幕断句预览有行", r.subLines > 0, `${r.subLines} 行`);
    check("改过之后标记为未导出", /改动未导出/.test(r.staleTag || ""), r.staleTag);
    check("删除镜头生效", r.shots && r.shots.n1 === r.shots.n0 - 1, JSON.stringify(r.shots));
    check("撤销能恢复被删的镜头", r.shots && r.shots.n2 === r.shots.n0, JSON.stringify(r.shots));
    check("重做能再次删除", r.shots && r.shots.n3 === r.shots.n0 - 1, JSON.stringify(r.shots));
    check("⌘K 命令面板能打开", r.paletteOpen === true);
    check("Esc 不会离开编辑页", r.escStayed === true);
    const panelsOk = r.panels && Object.values(r.panels).every(Boolean);
    check("四个抽屉都能打开", panelsOk, JSON.stringify(r.panels));
  }

  if (!KEEP) cleanup();
  if (server) { try { process.kill(-server.pid); } catch {} }

  const bad = results.filter((x) => !x.ok);
  console.log(`\n${results.length - bad.length}/${results.length} 通过`);
  process.exit(bad.length ? 1 : 0);
})();
