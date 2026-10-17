/* ============ 公共前端工具：API 封装 / toast / 倒计时 / 格式化 ============ */
(function (w) {
  'use strict';

  const state = { user: null };

  /** 统一 fetch：自动 JSON、自动带 401 跳登录、自动抛后端 detail */
  async function api(url, opt) {
    opt = opt || {};
    const headers = Object.assign({ 'Content-Type': 'application/json' }, opt.headers || {});
    const res = await fetch(url, Object.assign({ credentials: 'same-origin' }, opt, { headers }));
    let data = null;
    const text = await res.text();
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text }; }
    if (res.status === 401) {
      if (!location.pathname.startsWith('/login')) {
        location.href = '/login?next=' + encodeURIComponent(location.pathname);
      }
      throw new Error('未登录');
    }
    if (!res.ok) throw new Error((data && (data.detail || data.message)) || ('请求失败 ' + res.status));
    return data;
  }

  let toastTimer = null;
  function toast(msg, type) {
    let el = document.getElementById('toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toast';
      el.className = 'toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.toggle('err', type === 'err');
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2800);
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  /** 倒计时按钮：cd(btn, 60) */
  function cd(btn, n, idleText) {
    const idle = idleText || btn.textContent;
    btn.disabled = true;
    const tick = () => {
      if (n <= 0) { btn.disabled = false; btn.textContent = idle; return; }
      btn.textContent = n + 's 后重发';
      n -= 1;
      setTimeout(tick, 1000);
    };
    tick();
  }

  function ts(sec) {
    if (!sec) return '-';
    const d = new Date(sec * 1000);
    const p = v => String(v).padStart(2, '0');
    return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function mb(n) {
    if (n < 0) return '不限';
    return n >= 1024 ? (n / 1024).toFixed(1) + ' GB' : n + ' MB';
  }

  /** 计算验证码：绑定 span + input + 换一题按钮 */
  function bindCaptcha(spanId, btnId) {
    const span = document.getElementById(spanId);
    let token = '';
    async function load() {
      try {
        const d = await api('/api/auth/captcha');
        token = d.token;
        if (span) span.textContent = d.q;
      } catch (e) { /* 忽略 */ }
    }
    load();
    const btn = btnId && document.getElementById(btnId);
    if (btn) btn.onclick = load;
    return { load, get token() { return token; } };
  }

  /* ---------- 原站共用工具（从 dashboard.html 内联脚本提取，保持语义一致） ---------- */

  /** 秒数转人话：<60 秒 / <60 分 / 时分 */
  function fmt(s) {
    s = Math.max(0, Math.floor(s || 0));
    if (s < 60) return s + ' 秒';
    const m = Math.floor(s / 60);
    if (m < 60) return m + ' 分 ' + (s % 60) + ' 秒';
    return Math.floor(m / 60) + ' 时 ' + (m % 60) + ' 分';
  }

  /** 大数字缩写：1.2万 / 3.4亿 */
  function fmtN(n) {
    n = parseInt(n || 0, 10);
    if (n >= 100000000) return (n / 100000000).toFixed(1) + '亿';
    if (n >= 10000) return (n / 10000).toFixed(1) + '万';
    return '' + n;
  }

  /** UID 打码：前 3 位 … 后 2 位（页面上不露完整 UID） */
  function maskMid(m) {
    m = (m == null ? '' : '' + m);
    if (m.length <= 5) return m;
    return m.slice(0, 3) + '…' + m.slice(-2);
  }

  /** 图标：<svg><use href="#ic-NAME">，icon 符号在 base.html 里定义 */
  function ico(name, cls) {
    return '<svg class="ic ' + (cls || '') + '"><use href="#ic-' + name + '"></use></svg>';
  }

  /** 账号展示名：优先隐私别名，其次昵称，最后 UID（仅前端展示，后端始终真实） */
  function accName(a) {
    return (a && (a.alias_name || a.uname)) || (a && a.mid ? ('UID ' + a.mid) : '') || '账号';
  }

  function accFace(a) {
    return (a && (a.alias_face || a.face)) || '';
  }

  function accMid(a) {
    return (a && (a.alias_mid || a.mid)) || '';
  }

  /** 头像 URL：/api/img/ 开头的直接用，其它走代理防 403 */
  function faceUrl(f) {
    if (!f) return '';
    if (f.indexOf('/api/accounts/') === 0 || f.indexOf('/api/img/') === 0) return f;
    return '/api/img/proxy?u=' + encodeURIComponent(f);
  }

  function q(sel) {
    return document.querySelector(sel);
  }

  /**
   * 列表/表格只显示前 N 条，按钮插到指定容器。
   *   bpClamp('list', 3)            按钮在列表末尾
   *   bpClamp('list', 3, 'listMore') 按钮插到别处（表格可用）
   */
  function bpClamp(boxId, n, btnBoxId) {
    const box = document.getElementById(boxId);
    if (!box) return;
    n = n || 3;
    const kids = Array.prototype.filter.call(box.children, c =>
      c.tagName !== 'SCRIPT' && c.tagName !== 'STYLE' && !c.classList.contains('clp-btn'));
    const prev = document.getElementById('clp-btn-' + boxId);
    if (prev) prev.remove();
    if (kids.length <= n) {
      kids.forEach(k => { k.style.display = ''; });
      return;
    }
    const hidden = kids.slice(n);
    hidden.forEach(k => { k.style.display = 'none'; });
    const btn = document.createElement('button');
    btn.id = 'clp-btn-' + boxId;
    btn.className = 'btn ghost sm clp-btn';
    btn.textContent = '展开全部 ' + kids.length + ' 条';
    let open = false;
    btn.onclick = () => {
      open = !open;
      hidden.forEach(k => { k.style.display = open ? '' : 'none'; });
      btn.textContent = open ? '收起' : ('展开全部 ' + kids.length + ' 条');
    };
    (btnBoxId ? document.getElementById(btnBoxId) : box).appendChild(btn);
  }

  /**
   * 超宽表格在手机端给「左右滑动查看全部」提示，真滑动后自动隐藏。
   * 动态渲染出来的表格也能被 MutationObserver 捕到。
   */
  function tblHintScan() {
    const els = document.querySelectorAll('.tbl-scroll');
    for (let i = 0; i < els.length; i++) {
      const el = els[i];
      if (el._hinted) continue;
      if (!el.querySelector('table')) continue;
      el._hinted = 1;
      const hint = document.createElement('div');
      hint.className = 'tbl-hint';
      hint.innerHTML = '<i>↔</i>左右滑动查看全部';
      if (el.parentNode) el.parentNode.insertBefore(hint, el.nextSibling);
      (function (el, hint) {
        function upd() {
          const empty = !!el.querySelector('tbody .empty, .empty');
          const sc = el.scrollWidth > el.clientWidth + 2;
          hint.classList.toggle('hide', empty || !sc || el.scrollLeft > 4);
        }
        el.addEventListener('scroll', upd, { passive: true });
        hint._upd = upd;
        upd();
      })(el, hint);
    }
  }

  function tblHintRefresh() {
    const hs = document.querySelectorAll('.tbl-hint');
    for (let i = 0; i < hs.length; i++) {
      if (hs[i]._upd) hs[i]._upd();
    }
  }

  function tblHintAll() {
    try { tblHintScan(); tblHintRefresh(); } catch (e) { /* 忽略 */ }
  }

  function installTblHint() {
    tblHintAll();
    setTimeout(tblHintAll, 600);
    setTimeout(tblHintAll, 2000);
    setTimeout(tblHintAll, 4500);
    window.addEventListener('resize', tblHintAll);
    window.addEventListener('orientationchange', tblHintAll);
    if (window.MutationObserver) {
      let t = 0;
      new MutationObserver(() => {
        clearTimeout(t);
        t = setTimeout(tblHintAll, 400);
      }).observe(document.body, { childList: true, subtree: true });
    }
  }


  /* 页面里大量使用裸函数名（esc/fmtN/accName…），这里统一挂到 window，
     实现只有一份，页面不要再定义。 */
  const ALIAS = { esc, fmtN, maskMid, accName, accFace, accMid, faceUrl,
                  fmt, ico, q, bpClamp };
  Object.keys(ALIAS).forEach(function (k) { w[k] = ALIAS[k]; });

  w.BP = {
    api, toast, esc, cd, ts, mb, state, bindCaptcha,
    fmt, fmtN, maskMid, ico, accName, accFace, accMid, faceUrl,
    q, bpClamp, tblHintAll, installTblHint,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installTblHint);
  } else {
    installTblHint();
  }
})(window);
