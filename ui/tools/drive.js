#!/usr/bin/env node
/**
 * 极简 CDP 驱动：给前端做真交互验证用。
 *
 * 为什么不用 playwright：这台机器没装（pip/npm 都没有，且没网）。
 * Chrome 自带 --remote-debugging-port，Node 22+ 自带 WebSocket，
 * 两个加起来就够了 —— 不要为一个测试去拉几十兆依赖。
 *
 * 用法：
 *   node ui/tools/drive.js --url URL --eval-file /tmp/ops.js [--shot out.png] [--w 1600 --h 1000]
 *
 * ops.js 的内容在页面里执行，可用：
 *   await sleep(ms)         等待
 *   await click(sel)        点击元素（会先滚动到可见）
 *   await drag(sel, dx, dy) 按住元素拖拽
 *   await type(sel, text)   设置输入框的值并触发 input 事件
 *   q(sel) / qa(sel)        querySelector / querySelectorAll
 * 返回值（JSON 可序列化）会打印到 stdout。
 */
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");

const args = {};
for (let i = 2; i < process.argv.length; i += 2) {
  args[process.argv[i].replace(/^--/, "")] = process.argv[i + 1];
}
const URL_ = args.url;
const PORT = +(args.port || 9333);
const W = +(args.w || 1600), H = +(args.h || 1000);
const CHROME = process.env.CHROME ||
  "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome";

const get = (path, method = "GET") => new Promise((res, rej) => {
  const req = http.request({ host: "127.0.0.1", port: PORT, path, method }, (r) => {
    let b = ""; r.on("data", (c) => (b += c)); r.on("end", () => res(b));
  });
  req.on("error", rej); req.end();
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const chrome = spawn(CHROME, [
    "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
    "--hide-scrollbars", `--remote-debugging-port=${PORT}`,
    `--window-size=${W},${H}`, "about:blank",
  ], { stdio: "ignore" });

  let ver = null;
  for (let i = 0; i < 60; i++) {
    try { ver = JSON.parse(await get("/json/version")); break; } catch { await sleep(200); }
  }
  if (!ver) { console.error("chrome 起不来"); chrome.kill(); process.exit(1); }

  const target = JSON.parse(await get(`/json/new?${encodeURIComponent(URL_)}`, "PUT"));
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  const waiters = new Map();
  let id = 0;
  const send = (method, params = {}) => new Promise((res, rej) => {
    const mid = ++id;
    waiters.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  ws.addEventListener("message", (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && waiters.has(msg.id)) {
      const w = waiters.get(msg.id); waiters.delete(msg.id);
      msg.error ? w.rej(new Error(JSON.stringify(msg.error))) : w.res(msg.result);
    }
  });
  await new Promise((r) => ws.addEventListener("open", r));

  await send("Page.enable");
  await send("Runtime.enable");
  // --window-size 对新建的 target 不一定生效，用设备指标覆盖才可靠
  await send("Emulation.setDeviceMetricsOverride", {
    width: W, height: H, deviceScaleFactor: 1, mobile: false,
  });
  await sleep(+(args.wait || 3000));

  const helpers = `
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const q = (s) => document.querySelector(s);
    const qa = (s) => [...document.querySelectorAll(s)];
    const click = async (s) => { const e = q(s); if (!e) throw new Error("no element " + s);
      e.scrollIntoView({block:"center"}); e.click(); await sleep(120); return true; };
    const type = async (s, v) => { const e = q(s); if (!e) throw new Error("no element " + s);
      e.value = v; e.dispatchEvent(new Event("input", {bubbles:true}));
      e.dispatchEvent(new Event("change", {bubbles:true})); await sleep(60); return true; };
    const drag = async (s, dx, dy=0) => {
      const e = q(s); if (!e) throw new Error("no element " + s);
      const r = e.getBoundingClientRect();
      const x = r.left + r.width/2, y = r.top + r.height/2;
      const up = (t, cx, cy) => e.dispatchEvent(new PointerEvent(t, {bubbles:true, clientX:cx, clientY:cy, buttons:1}));
      up("pointerdown", x, y);
      for (let i=1;i<=6;i++) { up("pointermove", x + dx*i/6, y + dy*i/6); window.dispatchEvent(new PointerEvent("pointermove",{bubbles:true,clientX:x+dx*i/6,clientY:y+dy*i/6,buttons:1})); await sleep(30); }
      window.dispatchEvent(new PointerEvent("pointerup",{bubbles:true,clientX:x+dx,clientY:y+dy}));
      await sleep(150); return true;
    };
    const key = async (k, opt={}) => {
      const e = new KeyboardEvent("keydown", {key:k, bubbles:true, ...opt});
      window.dispatchEvent(e); await sleep(80); return true;
    };
    window.__errs = window.__errs || [];
    window.addEventListener("error", (e) => window.__errs.push(String(e.message)));
  `;

  const ops = args["eval-file"] ? fs.readFileSync(args["eval-file"], "utf8") : (args.eval || "1");
  const expr = `(async () => { ${helpers}\n${ops} })()`;
  let out;
  try {
    const r = await send("Runtime.evaluate", {
      expression: expr, awaitPromise: true, returnByValue: true, timeout: 60000,
    });
    out = r.result && (r.result.value !== undefined ? r.result.value
      : r.result.description || JSON.stringify(r.result));
    if (r.exceptionDetails) out = { error: r.exceptionDetails.text, detail: r.exceptionDetails.exception && r.exceptionDetails.exception.description };
  } catch (e) {
    // 页面在 evaluate 期间跳转（比如新建项目后跳进剪辑台）会让连接断掉，
    // 这不是失败，是预期行为：重新连上新页面，把落点 URL 读回来。
    out = { navigated: true, error: String(e).slice(0, 120) };
    await sleep(1500);
    try {
      const list = JSON.parse(await get("/json/list"));
      const page = list.find((t) => t.type === "page" && t.url.includes(args.url.split("/")[2]));
      if (page) {
        out.url = page.url;
        const ws2 = new WebSocket(page.webSocketDebuggerUrl);
        const res = await new Promise((resolve) => {
          ws2.addEventListener("open", () => ws2.send(JSON.stringify({
            id: 1, method: "Runtime.evaluate",
            params: { expression: "JSON.stringify({href: location.href, title: document.title, clips: document.querySelectorAll('.clip').length, errs: window.__errs || []})", returnByValue: true },
          })));
          ws2.addEventListener("message", (ev) => resolve(JSON.parse(ev.data)));
        });
        out.landed = JSON.parse(res.result.value);
        ws2.close();
      }
    } catch (e2) { out.landError = String(e2).slice(0, 120); }
  }
  if (args.shot) {
    const png = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(args.shot, Buffer.from(png.data, "base64"));
  }
  console.log(typeof out === "string" ? out : JSON.stringify(out, null, 2));
  ws.close(); chrome.kill();
  process.exit(0);
}
main();
