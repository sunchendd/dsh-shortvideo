# dsh-plugin：在 DSH 侧边栏里打开短视频工作台

这个目录是给 DeepSeek Harness 用的插件源码，让工作台像「情报雷达」一样有一个**侧边栏入口**，
点开就在 Harness 窗口里用 —— 不需要记 IP 和端口。

## 它做了什么

| 半边 | 职责 |
|---|---|
| 宿主 `index.js` | 按需拉起编辑器进程（`ui/preview.py`）；把 `/shortvideo/*` 反代到它；提供 `/api/shortvideo` 动作路由和 `shortvideo` 工具 |
| 浏览器 `client.js` | 侧边栏一项「短视频」，页面是一条 38px 工具条 + 内嵌工作台（同源 iframe） |

**为什么是反代而不是直接嵌 `http://host:8099`**：Harness 常常是通过隧道或端口映射打开的，
浏览器里的 `127.0.0.1:8099` 指的不是同一台机器；同源反代之后，工作台跟着 Harness 走，
隧道、反向代理、HTTPS 都自动继承。

反代会把文本资源里以引号/括号开头的 `/api/`、`/files/`、`/app/`、`/batch` 重写成带前缀的形式；
`<video>` 的 Range 请求原样透传，mp4 可以拖动进度条。

## 装

```bash
dsh plugin --profile web add link:/path/to/this/repo/dsh-plugin
```

`dsh plugin add` 会把这个包同时写进 profile 的 `dependencies` 和 `dsh.profile.bundles`，
然后重启 Harness（或在 Harness 里重开一次页面）：

```bash
# 只重启 web 服务，不动会话
python3 /root/.dsh/dsh-web-restart.py
```

没有 `dsh` CLI 时，等价的手工做法是：

1. 在 `$DSH_HOME/profiles/web/package.json` 的 `dsh.profile.bundles` 里加 `"@local/dsh-shortvideo"`，
   并在 `dependencies` 里加 `"@local/dsh-shortvideo": "link:/path/to/dsh-plugin"`；
2. `ln -s /path/to/dsh-plugin $DSH_HOME/profiles/web/node_modules/@local/dsh-shortvideo`；
3. 重启 Harness。

## 配置

`cordis.patch.yml` 里三个字段：

```yaml
repo: /root/dsh-shortvideo   # 编辑器仓库（ui/preview.py 所在目录）
port: 8099                   # 编辑器监听端口，只在 127.0.0.1 上，外部一律走反代
# python: /path/to/python    # 不填则自动挑一个能 import PIL + imageio_ffmpeg 的解释器
```

服务不会随 Harness 启动：第一次有人打开 `/shortvideo/*` 时才拉起，进程 detached，
日志写在 `$DSH_HOME/shortvideo/server.log`。

## 验证

```bash
# 反代是否通（不需要 Harness 登录态）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3080/shortvideo/api/projects

# 动作路由
curl -s -X POST http://127.0.0.1:3080/api/shortvideo \
  -H 'content-type: application/json' -d '{"action":"status"}'
```

侧边栏里点「短视频」应该看到：顶部一条 38px 工具条（状态灯 / 重新载入 / 重启服务 /
新窗口打开 / 复制链接），下面就是完整的片库与剪辑台。

## 已知边界

- 反代只处理 `/api/`、`/files/`、`/app/`、`/batch` 四个前缀；如果以后编辑器新增了别的
  绝对路径入口，要同步加到 `index.js` 的 `REWRITE_PREFIXES`。
- 反代路由本身不额外做鉴权，安全性依赖 Harness 的监听地址（默认只绑回环）。
  要暴露到公网时，先在 Harness 前面加一层反向代理与鉴权。
- 插件只管"把工作台打开"；导出、质检等重活仍然由编辑器自己的流水线完成。
