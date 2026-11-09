/* 本地主题切换 + 会话过期统一跳转。纯本地脚本，不请求任何外部地址。 */
(function () {
  'use strict';
  var KEY = 'hbtv-theme';
  var root = document.documentElement;
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) { /* 隐私模式 */ }
  if (saved === 'dark' || saved === 'light') {
    root.setAttribute('data-theme', saved);
  } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
    root.setAttribute('data-theme', 'dark');
  }

  function label() {
    var dark = root.getAttribute('data-theme') === 'dark';
    document.querySelectorAll('[data-theme-label]').forEach(function (el) {
      el.textContent = dark ? '浅色' : '深色';
    });
  }

  window.HBTV_TOGGLE_THEME = function () {
    var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem(KEY, next); } catch (e) { /* 忽略 */ }
    label();
  };

  document.addEventListener('DOMContentLoaded', function () {
    label();
    document.querySelectorAll('[data-theme-toggle]').forEach(function (el) {
      el.addEventListener('click', window.HBTV_TOGGLE_THEME);
    });
  });

  /* 会话过期时，任何管理接口返回 401 都回到登录页，避免页面静默卡在旧状态。
     登录 / 初始化接口自身的 401（密码错误）不触发跳转。 */
  var nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    return nativeFetch(input, init).then(function (r) {
      var u = typeof input === 'string' ? input : (input && input.url) || '';
      if (r.status === 401 && u.indexOf('/api/') === 0 && u.indexOf('/api/auth/') !== 0) {
        setTimeout(function () {
          window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname);
        }, 400);
      }
      return r;
    });
  };
})();
