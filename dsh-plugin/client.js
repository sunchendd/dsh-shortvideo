/**
 * Browser half of the shortvideo bundle: one sidebar entry and the workbench
 * page it opens.
 *
 * The page is a thin shell. The editor itself is the zero-build app served by
 * `ui/preview.py`, reached through the Host's `/shortvideo` proxy, so it stays
 * on the same origin as the Harness window — no extra port to remember, and it
 * keeps working when the Harness is opened through a tunnel or another host.
 *
 * The Host guarantees the editor process is running before proxying, so this
 * page only needs to know: is it up, and what should the frame point at.
 */
window.__ModuleLoader__.load({
  id: '@local/dsh-shortvideo',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const { useCallback, useEffect, useRef, useState } = React;

    /** Locale namespace owned by this plugin. */
    const NS = 'shortvideo';
    /** Shared by the sidebar entry and the page it opens. */
    const PANEL_ID = 'shortvideo';
    /** Mounted editor (same origin, no port). */
    const MOUNT = '/shortvideo';
    /** Host action route. */
    const ROUTE = '/api/shortvideo';

    const zh = {
      panel: '短视频',
      title: '短视频工作台',
      subtitle: '片库、剪辑台、成片导出都在这里，不用记端口。',
      running: '运行中',
      stopped: '未启动',
      starting: '启动中…',
      reload: '重新载入',
      openNew: '新窗口打开',
      restart: '重启服务',
      copyLink: '复制链接',
      copied: '已复制',
      failed: '打不开工作台',
      retry: '重试',
      logHint: '服务日志',
    };

    const en = {
      panel: 'Shorts',
      title: 'Short-video workbench',
      subtitle: 'Library, editor and export in one place — no port to remember.',
      running: 'Running',
      stopped: 'Stopped',
      starting: 'Starting…',
      reload: 'Reload',
      openNew: 'Open in new tab',
      restart: 'Restart',
      copyLink: 'Copy link',
      copied: 'Copied',
      failed: 'The workbench did not load',
      retry: 'Retry',
      logHint: 'Server log',
    };

    const CSS = `
      /* 撑满 main 槽：用 absolute 而不是 height:100%，因为槽本身高度是 auto，
         子元素 100% 会塌成内容高度，一屏里挤出滚动条。 */
      .dshShorts {
        position: absolute; inset: 0; display: flex; flex-direction: column;
        min-height: 0; background: var(--dsw-alias-bg-base, #0b0d10);
      }
      /* 一条窄工具条就够：下面编辑器自己有顶栏，两条粗栏会显得很笨重。 */
      .dshShortsHead {
        display: flex; align-items: center; gap: 8px; padding: 0 12px;
        height: 38px; flex: none; border-bottom: 1px solid var(--dsw-alias-border-l1, #22262d);
      }
      .dshShortsName { font: 600 12px/1 inherit; color: var(--dsw-alias-text-l1, #e9edf3); white-space: nowrap; }
      .dshShortsDot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
      .dshShortsStatus { display: flex; align-items: center; gap: 6px; font: 11px/1 inherit; color: var(--dsw-alias-text-l3, #8b93a1); }
      .dshShortsGrow { flex: 1; }
      .dshShortsBtn {
        font: 500 11px/1 inherit; padding: 5px 9px; border-radius: 6px; cursor: pointer; white-space: nowrap;
        color: var(--dsw-alias-text-l2, #c6ccd6);
        background: var(--dsw-alias-bg-l2, #14181e);
        border: 1px solid var(--dsw-alias-border-l2, #2a2f38);
      }
      .dshShortsBtn:hover { color: var(--dsw-alias-text-l1, #fff); border-color: var(--dsw-alias-border-l3, #3a4150); }
      .dshShortsBtn[disabled] { opacity: .5; cursor: default; }
      .dshShortsFrame { flex: 1; min-height: 0; border: 0; width: 100%; background: #0b0d10; }
      .dshShortsEmpty { flex: 1; display: grid; place-content: center; gap: 10px; text-align: center; padding: 30px; }
      .dshShortsEmptyTitle { font: 600 14px/1.4 inherit; color: var(--dsw-alias-text-l1, #e9edf3); }
      .dshShortsEmptyBody { font: 12px/1.8 inherit; color: var(--dsw-alias-text-l3, #8b93a1); max-width: 520px; white-space: pre-wrap; }
    `;

    function PanelIcon(props) {
      const size = props?.size ?? 20;
      return h('svg', {
        viewBox: '0 0 24 24', width: size, height: size, 'aria-hidden': true,
        style: { display: 'block', color: props?.active === true ? 'var(--dsw-alias-brand-primary)' : 'currentColor' },
      },
      h('rect', { x: 3.2, y: 5.2, width: 17.6, height: 13.6, rx: 2.6, fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, opacity: 0.7 }),
      h('path', { d: 'M10.4 9.6 L14.9 12 L10.4 14.4 Z', fill: 'currentColor', opacity: 0.9 }),
      h('path', { d: 'M3.2 17.2 H20.8', stroke: 'currentColor', strokeWidth: 1.5, opacity: 0.45 }));
    }

    return {
      inject: ['slots', 'locale'],
      apply(ctx) {
        ctx.effect(() => ctx.locale.register(NS, { zh, en }), 'shortvideo: dictionaries');
        const t = ctx.locale.bind(NS);

        async function call(action) {
          const response = await fetch(ROUTE, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ action }),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok || data?.error !== undefined) throw new Error(data?.error ?? `HTTP ${response.status}`);
          return data;
        }

        function ShortsPage() {
          /** '' | 'starting' | 'running' | 'failed' */
          const [phase, setPhase] = useState('');
          const [detail, setDetail] = useState('');
          const [url, setUrl] = useState('');
          const [nonce, setNonce] = useState(0);
          const [copied, setCopied] = useState(false);
          const mounted = useRef(true);

          const boot = useCallback(async () => {
            setPhase('starting');
            setDetail('');
            try {
              const state = await call('start');
              if (!mounted.current) return;
              if (state.running !== true) throw new Error(state.error ?? '服务没有启动');
              setUrl(`${MOUNT}/app/`);
              setPhase('running');
            } catch (error) {
              if (!mounted.current) return;
              setPhase('failed');
              setDetail(String(error?.message ?? error));
            }
          }, []);

          useEffect(() => {
            mounted.current = true;
            boot();
            return () => { mounted.current = false; };
          }, [boot]);

          const restart = useCallback(async () => {
            setPhase('starting');
            try {
              await call('restart');
              if (!mounted.current) return;
              setPhase('running');
              setNonce((value) => value + 1);
            } catch (error) {
              if (!mounted.current) return;
              setPhase('failed');
              setDetail(String(error?.message ?? error));
            }
          }, []);

          const statusColor = phase === 'running' ? '#3fb950' : phase === 'starting' ? '#d29922' : '#f85149';
          const statusText = phase === 'running' ? t('running') : phase === 'starting' ? t('starting') : t('stopped');

          const head = h('div', { className: 'dshShortsHead' },
            h('span', { className: 'dshShortsDot', style: { background: statusColor } }),
            h('span', { className: 'dshShortsName', title: t('subtitle') }, t('title')),
            h('span', { className: 'dshShortsGrow' }),
            h('span', { className: 'dshShortsStatus' }, statusText),
            h('button', {
              className: 'dshShortsBtn',
              disabled: phase === 'starting',
              onClick: () => setNonce((value) => value + 1),
            }, t('reload')),
            h('button', {
              className: 'dshShortsBtn',
              disabled: phase === 'starting',
              onClick: restart,
            }, t('restart')),
            h('button', {
              className: 'dshShortsBtn',
              disabled: phase !== 'running',
              onClick: () => { if (url !== '') window.open(url, '_blank', 'noopener'); },
            }, t('openNew')),
            h('button', {
              className: 'dshShortsBtn',
              disabled: phase !== 'running',
              onClick: async () => {
                try {
                  await navigator.clipboard.writeText(new URL(url, window.location.origin).toString());
                  setCopied(true);
                  setTimeout(() => { if (mounted.current) setCopied(false); }, 1600);
                } catch { /* clipboard denied: silently ignore */ }
              },
            }, copied ? t('copied') : t('copyLink')));

          const body = phase === 'running'
            ? h('iframe', {
              className: 'dshShortsFrame',
              key: nonce,
              src: url,
              title: t('title'),
              allow: 'clipboard-write',
            })
            : h('div', { className: 'dshShortsEmpty' },
              h('div', { className: 'dshShortsEmptyTitle' }, phase === 'failed' ? t('failed') : t('starting')),
              detail === '' ? null : h('div', { className: 'dshShortsEmptyBody' }, detail),
              h('div', null,
                h('button', { className: 'dshShortsBtn', onClick: boot }, t('retry'))));

          return h('div', { className: 'dshShorts' }, h('style', null, CSS), head, body);
        }

        ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
          name: 'sidebar.panellist',
          id: PANEL_ID,
          order: 21,
          label: () => t('panel'),
          locale: NS,
        }, PanelIcon));

        ctx.slots.inject('main', () => ctx.slots.register({
          name: 'main',
          key: PANEL_ID,
          locale: NS,
        }, ShortsPage));
      },
    };
  },
});
