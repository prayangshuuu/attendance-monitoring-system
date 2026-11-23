/**
 * Attendance Monitoring System — Core Application JavaScript
 * CSP-safe client controller (no eval, no inline handlers in markup):
 * 1. Strict HTMX CSP configuration (no dynamic eval)
 * 2. Automated CSRF token injection for all state-changing requests
 * 3. Sidebar drawer (< 1024px) with accessible ARIA state
 * 4. Toast notifications with auto-dismiss and hx-trigger support
 * 5. Check-in countdown timers with `countdown:expired` event
 * 6. Demo account quick-fill on the login page
 * 7. Modal dialogs, dismissible alerts, history-back buttons
 * 8. Motion: count-up stat values and animated progress fills
 *
 * Interactive behaviour uses event delegation on `document`, so it keeps
 * working after hx-boost replaces the <body> content and never double-binds.
 */

(function () {
  'use strict';

  var DESKTOP_MIN_WIDTH = 1024;

  function prefersReducedMotion() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  // -------------------------------------------------------------------------
  // 1. HTMX CSP Configuration
  // -------------------------------------------------------------------------
  if (window.htmx) {
    window.htmx.config.allowEval = false;
  } else {
    document.addEventListener('DOMContentLoaded', function () {
      if (window.htmx) {
        window.htmx.config.allowEval = false;
      }
    });
  }

  // -------------------------------------------------------------------------
  // 2. CSRF Token Configuration for HTMX Requests
  // -------------------------------------------------------------------------
  document.body.addEventListener('htmx:configRequest', function (evt) {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) {
      evt.detail.headers['X-CSRF-Token'] = meta.content;
    }
  });

  // -------------------------------------------------------------------------
  // Scroll lock helper (class-based; no inline style needed)
  // -------------------------------------------------------------------------
  function syncScrollLock() {
    var locked = !!document.querySelector('#sidebar-drawer.is-open, .modal-overlay.is-open');
    document.body.classList.toggle('is-scroll-locked', locked);
  }

  // -------------------------------------------------------------------------
  // 3. Sidebar Drawer (< 1024px)
  // -------------------------------------------------------------------------
  function getDrawer() {
    return document.getElementById('sidebar-drawer');
  }

  function setDrawer(open) {
    var drawer = getDrawer();
    if (!drawer) return;
    var backdrop = document.getElementById('drawer-backdrop');
    drawer.classList.toggle('is-open', open);
    if (backdrop) backdrop.classList.toggle('is-open', open);
    document.querySelectorAll('[data-drawer-toggle], #mobile-menu-btn').forEach(function (btn) {
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    syncScrollLock();
    if (open) {
      var firstLink = drawer.querySelector('a.nav-link');
      if (firstLink) firstLink.focus({ preventScroll: true });
    }
  }

  var drawerBound = false;

  function initDrawer() {
    // Re-sync ARIA/scroll state for freshly swapped markup.
    var drawer = getDrawer();
    if (drawer && !drawer.classList.contains('is-open')) {
      var backdrop = document.getElementById('drawer-backdrop');
      if (backdrop) backdrop.classList.remove('is-open');
    }
    syncScrollLock();

    if (drawerBound) return;
    drawerBound = true;

    document.addEventListener('click', function (e) {
      var target = e.target;
      if (!(target instanceof Element)) return;

      if (target.closest('[data-drawer-toggle], #mobile-menu-btn')) {
        e.preventDefault();
        var d = getDrawer();
        setDrawer(!(d && d.classList.contains('is-open')));
        return;
      }

      if (target.closest('[data-drawer-close]') || target.closest('#drawer-backdrop')) {
        e.preventDefault();
        setDrawer(false);
        return;
      }

      if (target.closest('#sidebar-drawer a.nav-link') && window.innerWidth < DESKTOP_MIN_WIDTH) {
        setDrawer(false);
      }
    });

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      var d = getDrawer();
      if (d && d.classList.contains('is-open')) {
        setDrawer(false);
        var btn = document.getElementById('mobile-menu-btn');
        if (btn) btn.focus();
      }
    });

    window.addEventListener('resize', function () {
      var d = getDrawer();
      if (window.innerWidth >= DESKTOP_MIN_WIDTH && d && d.classList.contains('is-open')) {
        setDrawer(false);
      }
    });
  }

  // -------------------------------------------------------------------------
  // 4. Toast Notification Handler
  // -------------------------------------------------------------------------
  function showToast(message, type, duration) {
    var container = document.getElementById('toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      document.body.appendChild(container);
    }

    type = type || 'info';
    duration = duration !== undefined ? duration : 4000;

    var toast = document.createElement('div');
    toast.className = 'toast toast-' + encodeURIComponent(type);
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');

    var body = document.createElement('div');
    body.className = 'toast-body';
    body.textContent = message;
    toast.appendChild(body);

    var closeBtn = document.createElement('button');
    closeBtn.className = 'toast-close';
    closeBtn.setAttribute('type', 'button');
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', function () {
      dismissToast(toast);
    });
    toast.appendChild(closeBtn);

    container.appendChild(toast);

    if (duration > 0) {
      setTimeout(function () {
        dismissToast(toast);
      }, duration);
    }
  }

  function dismissToast(toast) {
    if (!toast || toast.classList.contains('toast-hiding')) return;
    toast.classList.add('toast-hiding');
    setTimeout(function () {
      if (toast.parentNode) {
        toast.parentNode.removeChild(toast);
      }
    }, 180);
  }

  window.showToast = showToast;

  document.body.addEventListener('toast', function (evt) {
    var detail = evt.detail;
    if (!detail) return;

    if (typeof detail === 'string') {
      showToast(detail, 'info');
    } else if (Array.isArray(detail)) {
      detail.forEach(function (item) {
        if (typeof item === 'string') {
          showToast(item, 'info');
        } else if (item && item.message) {
          showToast(item.message, item.type || 'info', item.duration);
        }
      });
    } else if (detail.message) {
      showToast(detail.message, detail.type || 'info', detail.duration);
    }
  });

  // -------------------------------------------------------------------------
  // 5. Check-in Code Countdown Timer Helper
  // -------------------------------------------------------------------------
  function initCountdownTimers() {
    var timerElements = document.querySelectorAll('[data-countdown-target], [data-expires-at]');

    timerElements.forEach(function (el) {
      if (el._countdownInterval) {
        clearInterval(el._countdownInterval);
      }

      var targetAttr = el.getAttribute('data-countdown-target') || el.getAttribute('data-expires-at');
      if (!targetAttr) return;

      var targetDate;
      if (/^\d+$/.test(targetAttr)) {
        var num = parseInt(targetAttr, 10);
        targetDate = num < 10000000000 ? num * 1000 : num;
      } else {
        targetDate = Date.parse(targetAttr);
      }

      if (isNaN(targetDate)) return;

      function updateTimer() {
        var diffMs = targetDate - Date.now();

        if (diffMs <= 0) {
          el.textContent = 'Expired';
          el.classList.add('is-expired');
          clearInterval(el._countdownInterval);
          el._countdownInterval = null;
          el.dispatchEvent(new CustomEvent('countdown:expired', { bubbles: true }));
          return;
        }

        var totalSeconds = Math.floor(diffMs / 1000);
        var minutes = Math.floor(totalSeconds / 60);
        var seconds = totalSeconds % 60;

        el.textContent = String(minutes).padStart(2, '0') + ':' + String(seconds).padStart(2, '0');
        el.classList.remove('is-expired');
      }

      updateTimer();
      el._countdownInterval = setInterval(updateTimer, 1000);
    });
  }

  // -------------------------------------------------------------------------
  // 6. Demo Login Quick-fill + 7. Alerts / history-back / modals (delegated)
  // -------------------------------------------------------------------------
  var delegatesBound = false;

  function openModal(modal) {
    modal.classList.add('is-open');
    syncScrollLock();
    var focusable = modal.querySelector('input, select, textarea, button:not([data-modal-close])');
    if (focusable) focusable.focus({ preventScroll: true });
  }

  function closeModal(modal) {
    modal.classList.remove('is-open');
    syncScrollLock();
  }

  function initDemoLogins() {
    initDelegates();
  }

  function initModals() {
    initDelegates();
  }

  function initDelegates() {
    if (delegatesBound) return;
    delegatesBound = true;

    document.addEventListener('click', function (e) {
      var target = e.target;
      if (!(target instanceof Element)) return;

      // Demo account chips
      var chip = target.closest('[data-demo-email]');
      if (chip) {
        e.preventDefault();
        var emailInput = document.getElementById('email');
        var passwordInput = document.getElementById('password');
        var email = chip.getAttribute('data-demo-email');
        var password = chip.getAttribute('data-demo-password');
        if (emailInput && email) {
          emailInput.value = email;
          emailInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
        if (passwordInput && password) {
          passwordInput.value = password;
          passwordInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
        showToast('Filled credentials for ' + (chip.textContent || email).trim(), 'info', 2500);
        return;
      }

      // Dismissible alerts
      var dismiss = target.closest('[data-alert-dismiss]');
      if (dismiss) {
        e.preventDefault();
        var alertEl = dismiss.closest('.alert');
        if (alertEl) alertEl.remove();
        return;
      }

      // Copy-to-clipboard buttons
      var copyBtn = target.closest('[data-copy-text]');
      if (copyBtn) {
        e.preventDefault();
        var text = copyBtn.getAttribute('data-copy-text') || '';
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            showToast('Copied to clipboard', 'success', 2000);
          }, function () {
            showToast('Could not copy to clipboard', 'danger', 2500);
          });
        }
        return;
      }

      // History back buttons (error pages)
      if (target.closest('[data-history-back]')) {
        e.preventDefault();
        if (window.history.length > 1) {
          window.history.back();
        } else {
          window.location.href = '/';
        }
        return;
      }

      // Modals
      var opener = target.closest('[data-modal-open]');
      if (opener) {
        e.preventDefault();
        var modal = document.getElementById(opener.getAttribute('data-modal-open'));
        if (modal) openModal(modal);
        return;
      }

      var closer = target.closest('[data-modal-close]');
      if (closer) {
        e.preventDefault();
        var owner = closer.closest('.modal-overlay');
        if (owner) closeModal(owner);
        return;
      }

      if (target.classList.contains('modal-overlay') && target.classList.contains('is-open')) {
        closeModal(target);
      }
    });

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      document.querySelectorAll('.modal-overlay.is-open').forEach(closeModal);
    });
  }

  // -------------------------------------------------------------------------
  // 8. Motion: count-up stat values & animated progress fills
  // -------------------------------------------------------------------------
  function initCountUps() {
    var reduce = prefersReducedMotion();

    document.querySelectorAll('.stat-value.count-up').forEach(function (el) {
      if (el.dataset.countUpDone === '1') return;
      el.dataset.countUpDone = '1';

      var original = el.textContent.trim();
      var match = original.match(/^([^0-9\-]*)(-?[\d,]*\.?\d+)(.*)$/);
      if (!match || reduce) return;

      var prefix = match[1];
      var numStr = match[2];
      var suffix = match[3];
      var hasComma = numStr.indexOf(',') !== -1;
      var target = parseFloat(numStr.replace(/,/g, ''));
      if (!isFinite(target)) return;
      var decimals = (numStr.split('.')[1] || '').length;

      var duration = 900;
      var start = null;

      function format(v) {
        var s = v.toFixed(decimals);
        if (hasComma) {
          var parts = s.split('.');
          parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
          s = parts.join('.');
        }
        return prefix + s + suffix;
      }

      function frame(ts) {
        if (start === null) start = ts;
        var t = Math.min(1, (ts - start) / duration);
        var eased = 1 - Math.pow(1 - t, 3);
        el.textContent = format(target * eased);
        if (t < 1) {
          window.requestAnimationFrame(frame);
        } else {
          el.textContent = original;
        }
      }

      el.textContent = format(0);
      window.requestAnimationFrame(frame);
    });
  }

  function initProgressFills() {
    var reduce = prefersReducedMotion();
    var bars = [];

    document.querySelectorAll('.progress-bar.animate-fill[data-value]').forEach(function (el) {
      if (el.dataset.fillDone === '1') return;
      el.dataset.fillDone = '1';
      var v = parseFloat(el.getAttribute('data-value'));
      if (!isFinite(v)) return;
      v = Math.max(0, Math.min(100, v));
      if (reduce) {
        el.style.width = v + '%';
        return;
      }
      el.style.width = '0%';
      bars.push([el, v]);
    });

    document.querySelectorAll('.progress-threshold-marker[data-threshold]').forEach(function (el) {
      var t = parseFloat(el.getAttribute('data-threshold'));
      if (isFinite(t)) {
        el.style.left = Math.max(0, Math.min(100, t)) + '%';
      }
    });

    if (!bars.length) return;
    // Two frames so the 0% width is painted before transitioning.
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        bars.forEach(function (pair) {
          pair[0].style.width = pair[1] + '%';
        });
      });
    });
  }

  // -------------------------------------------------------------------------
  // 8b. Check-in code input: upper-case, slot indicators and counter
  //     (delegated, CSP-safe replacement for inline oninput handlers)
  // -------------------------------------------------------------------------
  function updateCodeHelper(input) {
    var upper = input.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
    if (input.value !== upper) {
      var pos = input.selectionStart;
      input.value = upper;
      try { input.setSelectionRange(pos, pos); } catch (err) { /* non-text input */ }
    }
    var len = input.value.length;
    var max = parseInt(input.getAttribute('maxlength'), 10) || 6;

    var slots = document.getElementById(input.getAttribute('data-code-slots') || '');
    if (slots) {
      slots.querySelectorAll('.slot').forEach(function (slot, i) {
        slot.classList.toggle('is-filled', i < len);
      });
    }
    var counter = document.getElementById(input.getAttribute('data-code-counter') || '');
    if (counter) counter.textContent = len + ' / ' + max;
  }

  document.addEventListener('input', function (e) {
    var t = e.target;
    if (t && t.matches && t.matches('[data-code-input]')) updateCodeHelper(t);
  });

  function initCodeInputs() {
    document.querySelectorAll('[data-code-input]').forEach(updateCodeHelper);
  }

  // -------------------------------------------------------------------------
  // 8c. Swap 4xx responses (htmx 2 drops them by default). Validation errors
  //     (400/401/409/422) re-render forms and check-in results with messages.
  // -------------------------------------------------------------------------
  document.body.addEventListener('htmx:beforeSwap', function (evt) {
    var status = evt.detail.xhr ? evt.detail.xhr.status : 0;
    if (status >= 400 && status < 500 && status !== 403 && status !== 404) {
      evt.detail.shouldSwap = true;
      evt.detail.isError = false;
    }
  });

  // -------------------------------------------------------------------------
  // 9. Initialization & HTMX Hook Bindings
  // -------------------------------------------------------------------------
  function initAll() {
    initDrawer();
    initCountdownTimers();
    initDemoLogins();
    initModals();
    initCountUps();
    initProgressFills();
    initCodeInputs();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }

  document.body.addEventListener('htmx:afterSettle', function () {
    initDrawer();
    initCountdownTimers();
    initCountUps();
    initProgressFills();
    initCodeInputs();
  });
})();
