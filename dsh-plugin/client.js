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
      starting: '正在打开工作台…',
      failed: '工作台没打开',
      retry: '重试',
    };

    const en = {
      panel: 'Shorts',
      starting: 'Opening the workbench…',
      failed: 'The workbench did not open',
      retry: 'Retry',
    };

    const CSS = `
      /* 只做一件事：把工作台铺满侧边栏页面。
         不提供"新窗口打开/复制链接"之类的出口——用户要的是在 Harness 里看，
         不是被丢到另一个地址上。 */
      .dshShorts {
        position: absolute; inset: 0; display: flex; min-height: 0;
        background: var(--dsw-alias-bg-base, #0b0d10);
      }
      .dshShortsFrame { flex: 1; min-width: 0; min-height: 0; border: 0; background: #0b0d10; }
      .dshShortsEmpty {
        flex: 1; display: grid; place-content: center; gap: 10px;
        text-align: center; padding: 30px;
      }
      .dshShortsEmptyTitle { font: 600 14px/1.4 inherit; color: var(--dsw-alias-text-l1, #e9edf3); }
      .dshShortsEmptyBody {
        font: 12px/1.8 inherit; color: var(--dsw-alias-text-l3, #8b93a1);
        max-width: 520px; white-space: pre-wrap;
      }
      .dshShortsBtn {
        justify-self: center; font: 500 11.5px/1 inherit; padding: 6px 12px; border-radius: 6px;
        cursor: pointer; color: var(--dsw-alias-text-l2, #c6ccd6);
        background: var(--dsw-alias-bg-l2, #14181e);
        border: 1px solid var(--dsw-alias-border-l2, #2a2f38);
      }
      .dshShortsBtn:hover { color: var(--dsw-alias-text-l1, #fff); border-color: var(--dsw-alias-border-l3, #3a4150); }
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
          const [nonce, setNonce] = useState(0);
          const mounted = useRef(true);

          const boot = useCallback(async () => {
            setPhase('starting');
            setDetail('');
            try {
              const state = await call('start');
              if (!mounted.current) return;
              if (state.running !== true) throw new Error(state.error ?? '服务没有启动');
              setNonce((value) => value + 1);
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

          return h('div', { className: 'dshShorts' },
            h('style', null, CSS),
            phase === 'running'
              ? h('iframe', {
                className: 'dshShortsFrame',
                key: nonce,
                src: `${MOUNT}/app/`,
                title: t('panel'),
                // allow-same-origin 是必须的（工作台要读写自己的 /api）；
                // 不放 allow-popups / allow-top-navigation —— 里面任何跳转都不能把
                // Harness 窗口带到别的地址去。
                sandbox: 'allow-scripts allow-same-origin allow-forms allow-modals allow-downloads',
              })
              : h('div', { className: 'dshShortsEmpty' },
                h('div', { className: 'dshShortsEmptyTitle' }, phase === 'failed' ? t('failed') : t('starting')),
                detail === '' ? null : h('div', { className: 'dshShortsEmptyBody' }, detail),
                h('button', { className: 'dshShortsBtn', onClick: boot }, t('retry'))));
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
