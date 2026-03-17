//
// BTPay — app.js
// Theme toggle, HTMX config, flash dismiss, general UI.
//

(function() {
    'use strict';

    // ── Theme Toggle ──────────────────────────────────────────

    var THEME_KEY = 'btpay_theme';

    function getTheme() {
        var saved = localStorage.getItem(THEME_KEY);
        if (saved) return saved;
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }

    function applyTheme(theme) {
        if (theme === 'dark') {
            document.documentElement.classList.add('dark');
        } else {
            document.documentElement.classList.remove('dark');
        }
        localStorage.setItem(THEME_KEY, theme);

        // Update toggle button icons
        var sunIcon = document.getElementById('sun-icon');
        var moonIcon = document.getElementById('moon-icon');
        if (sunIcon && moonIcon) {
            sunIcon.classList.toggle('hidden', theme !== 'dark');
            moonIcon.classList.toggle('hidden', theme === 'dark');
        }
    }

    function toggleTheme() {
        var current = getTheme();
        applyTheme(current === 'dark' ? 'light' : 'dark');
    }

    // Apply theme immediately (before DOMContentLoaded to prevent flash)
    applyTheme(getTheme());

    // ── Flash Messages ────────────────────────────────────────

    function dismissFlash(btn) {
        var flash = btn.closest('[data-flash]');
        if (flash) {
            flash.style.transition = 'opacity 200ms';
            flash.style.opacity = '0';
            setTimeout(function() { flash.remove(); }, 200);
        }
    }

    // Auto-dismiss success/info flashes after 5 seconds
    function autoHideFlashes() {
        var flashes = document.querySelectorAll('[data-flash="success"], [data-flash="info"]');
        for (var i = 0; i < flashes.length; i++) {
            (function(el) {
                setTimeout(function() {
                    if (el.parentNode) {
                        el.style.transition = 'opacity 200ms';
                        el.style.opacity = '0';
                        setTimeout(function() { el.remove(); }, 200);
                    }
                }, 5000);
            })(flashes[i]);
        }
    }

    // ── HTMX Config ───────────────────────────────────────────

    function configureHtmx() {
        if (typeof htmx === 'undefined') return;

        // Include CSRF token in HTMX requests
        document.body.addEventListener('htmx:configRequest', function(evt) {
            var csrfMeta = document.querySelector('meta[name="csrf-token"]');
            if (csrfMeta) {
                evt.detail.headers['X-CSRF-Token'] = csrfMeta.content;
            }
        });

        // Show loading indicator during HTMX requests
        document.body.addEventListener('htmx:beforeRequest', function() {
            var indicator = document.getElementById('htmx-indicator');
            if (indicator) indicator.classList.remove('hidden');
        });

        document.body.addEventListener('htmx:afterRequest', function() {
            var indicator = document.getElementById('htmx-indicator');
            if (indicator) indicator.classList.add('hidden');
        });
    }

    // ── Confirm Dialogs ───────────────────────────────────────

    function setupConfirmDialogs() {
        document.addEventListener('click', function(e) {
            var btn = e.target.closest('[data-confirm]');
            if (btn) {
                var msg = btn.getAttribute('data-confirm');
                if (!confirm(msg)) {
                    e.preventDefault();
                    e.stopPropagation();
                }
            }
        });
    }

    // ── Mobile Sidebar Toggle ─────────────────────────────────

    function toggleSidebar() {
        var sidebar = document.getElementById('sidebar');
        var overlay = document.getElementById('sidebar-overlay');
        if (sidebar) {
            sidebar.classList.toggle('-translate-x-full');
            if (overlay) overlay.classList.toggle('hidden');
        }
    }

    // ── Init ──────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', function() {
        // Theme toggle button
        var themeBtn = document.getElementById('theme-toggle');
        if (themeBtn) {
            themeBtn.addEventListener('click', toggleTheme);
        }

        // Flash dismiss buttons
        document.addEventListener('click', function(e) {
            var btn = e.target.closest('[data-dismiss-flash]');
            if (btn) dismissFlash(btn);
        });

        autoHideFlashes();
        configureHtmx();
        setupConfirmDialogs();

        // Mobile sidebar
        var menuBtn = document.getElementById('mobile-menu-btn');
        if (menuBtn) {
            menuBtn.addEventListener('click', toggleSidebar);
        }
        var overlay = document.getElementById('sidebar-overlay');
        if (overlay) {
            overlay.addEventListener('click', toggleSidebar);
        }
    });

    // Expose for inline use
    window.BTPay = window.BTPay || {};
    window.BTPay.toggleTheme = toggleTheme;
    window.BTPay.toggleSidebar = toggleSidebar;

})();
