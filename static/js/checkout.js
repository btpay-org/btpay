//
// BTPay — checkout.js
// Payment polling, countdown timer, copy-to-clipboard.
//

(function() {
    'use strict';

    // Variables set by the template:
    //   invoiceRef   — invoice reference number
    //   quoteDeadline — seconds until quote expires
    //   rateLockedAt  — unix timestamp when rate was locked

    var POLL_INTERVAL = 5000;   // 5 seconds
    var pollTimer = null;

    // ── Countdown Timer ───────────────────────────────────────

    function startCountdown() {
        if (!window.rateLockedAt || !window.quoteDeadline) return;

        var countdownEl = document.getElementById('countdown');
        var barEl = document.getElementById('countdown-bar');
        if (!countdownEl) return;

        var expiresAt = (window.rateLockedAt + window.quoteDeadline) * 1000;

        function update() {
            var now = Date.now();
            var remaining = Math.max(0, expiresAt - now);
            var secs = Math.floor(remaining / 1000);
            var mins = Math.floor(secs / 60);
            secs = secs % 60;

            countdownEl.textContent = pad(mins) + ':' + pad(secs);

            if (remaining <= 0) {
                countdownEl.textContent = 'EXPIRED';
                if (barEl) {
                    barEl.classList.remove('bg-yellow-50', 'dark:bg-yellow-900/20');
                    barEl.classList.add('bg-red-50', 'dark:bg-red-900/20');
                }
                // Reload to get new quote
                setTimeout(function() { location.reload(); }, 3000);
                return;
            }

            // Flash red when under 2 minutes
            if (remaining < 120000 && barEl) {
                barEl.classList.remove('bg-yellow-50', 'dark:bg-yellow-900/20');
                barEl.classList.add('bg-red-50', 'dark:bg-red-900/20');
            }

            setTimeout(update, 1000);
        }

        update();
    }

    function pad(n) {
        return n < 10 ? '0' + n : '' + n;
    }

    // ── Payment Status Polling ────────────────────────────────

    function startPolling() {
        if (!window.invoiceRef) return;

        function poll() {
            var url = '/checkout/' + encodeURIComponent(window.invoiceRef) + '/status.json';
            fetch(url)
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    handleStatusUpdate(data);
                })
                .catch(function() {
                    // Silently ignore polling errors
                });
        }

        pollTimer = setInterval(poll, POLL_INTERVAL);
        // Initial poll after 2 seconds
        setTimeout(poll, 2000);
    }

    function setAllStatus(html) {
        var els = document.querySelectorAll('.payment-status');
        for (var i = 0; i < els.length; i++) els[i].innerHTML = html;
    }

    function handleStatusUpdate(data) {
        var statusEls = document.querySelectorAll('.payment-status');
        if (!statusEls.length) return;

        var status = data.status;

        if (status === 'paid' || status === 'confirmed') {
            // Payment detected — redirect to status page
            clearInterval(pollTimer);
            setAllStatus(
                '<p class="text-sm font-medium text-green-600">' +
                (status === 'confirmed' ? 'Payment confirmed!' : 'Payment detected!') +
                '</p>' +
                '<p class="text-xs text-gray-500 mt-1">Redirecting...</p>');

            setTimeout(function() {
                window.location.href = '/checkout/' + encodeURIComponent(window.invoiceRef) + '/status';
            }, 2000);

        } else if (status === 'partial') {
            setAllStatus(
                '<p class="text-sm font-medium text-yellow-600">Partial payment received</p>' +
                '<p class="text-xs text-gray-500 mt-1">' +
                'Received: ' + (data.amount_paid || '0') +
                ' of ' + (data.total || '0') +
                '</p>');

        } else if (status === 'expired') {
            clearInterval(pollTimer);
            setAllStatus(
                '<p class="text-sm font-medium text-red-600">Invoice expired</p>' +
                '<p class="text-xs text-gray-500 mt-1">Please contact the merchant for a new invoice.</p>');

        } else if (status === 'cancelled') {
            clearInterval(pollTimer);
            setAllStatus(
                '<p class="text-sm font-medium text-gray-600">Invoice cancelled</p>');
        }
    }

    // ── Copy to Clipboard ─────────────────────────────────────

    window.copyAddress = function() {
        var el = document.getElementById('btc-address');
        if (!el) return;
        var text = el.textContent.trim();
        copyToClipboard(text, 'copy-msg');
    };

    window.copyAmount = function() {
        var el = document.getElementById('btc-amount');
        if (!el) return;
        var text = el.textContent.trim();
        copyToClipboard(text, 'copy-msg');
    };

    function copyToClipboard(text, msgId) {
        if (navigator.clipboard) {
            navigator.clipboard.writeText(text).then(function() {
                showCopyMsg(msgId);
            });
        } else {
            // Fallback for older browsers
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            try {
                document.execCommand('copy');
                showCopyMsg(msgId);
            } catch(e) {}
            document.body.removeChild(ta);
        }
    }

    function showCopyMsg(msgId) {
        var msg = document.getElementById(msgId);
        if (!msg) return;
        msg.classList.remove('hidden');
        setTimeout(function() {
            msg.classList.add('hidden');
        }, 2000);
    }

    // ── Init ──────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', function() {
        startCountdown();
        startPolling();
    });

})();
