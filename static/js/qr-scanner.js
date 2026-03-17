//
// BTPay - QR Scanner with COLDCARD BBQr support
// Scans xpubs, descriptors, and COLDCARD JSON wallet exports.
// Supports both single QR codes and BBQr multi-part animated sequences.
//

(function() {
    'use strict';

    // ── BBQr Protocol Constants ─────────────────────────────────

    var BBQR_PREFIX = 'B$';
    var BASE32_ALPHA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';

    // ── BBQr Decoder ────────────────────────────────────────────

    function base32Decode(str) {
        var bits = '';
        for (var i = 0; i < str.length; i++) {
            var val = BASE32_ALPHA.indexOf(str[i]);
            if (val < 0) continue;
            bits += ('00000' + val.toString(2)).slice(-5);
        }
        var bytes = new Uint8Array(Math.floor(bits.length / 8));
        for (var j = 0; j < bytes.length; j++) {
            bytes[j] = parseInt(bits.substr(j * 8, 8), 2);
        }
        return bytes;
    }

    function hexDecode(str) {
        var bytes = new Uint8Array(str.length / 2);
        for (var i = 0; i < bytes.length; i++) {
            bytes[i] = parseInt(str.substr(i * 2, 2), 16);
        }
        return bytes;
    }

    function zlibInflate(data) {
        // Use DecompressionStream API (available in modern browsers)
        // Fallback: raw inflate via pako if available
        // For BBQr: wbits=10, no zlib header -- this is raw deflate
        if (typeof DecompressionStream !== 'undefined') {
            return new Promise(function(resolve, reject) {
                // DecompressionStream expects proper deflate/gzip, but BBQr uses
                // raw deflate with wbits=10. We need to add a zlib header.
                // zlib header for wbits=10: CMF=0x48 (CM=8, CINFO=4), FLG=0x01
                var header = new Uint8Array([0x48, 0x01]);
                var withHeader = new Uint8Array(header.length + data.length);
                withHeader.set(header);
                withHeader.set(data, header.length);

                var ds = new DecompressionStream('deflate');
                var writer = ds.writable.getWriter();
                var reader = ds.readable.getReader();
                var chunks = [];

                reader.read().then(function pump(result) {
                    if (result.done) {
                        var total = 0;
                        chunks.forEach(function(c) { total += c.length; });
                        var out = new Uint8Array(total);
                        var off = 0;
                        chunks.forEach(function(c) { out.set(c, off); off += c.length; });
                        resolve(out);
                        return;
                    }
                    chunks.push(result.value);
                    return reader.read().then(pump);
                }).catch(reject);

                writer.write(withHeader).then(function() {
                    writer.close();
                }).catch(reject);
            });
        }
        return Promise.reject(new Error('Decompression not supported'));
    }

    function parseBBQrHeader(data) {
        // Header: B$ + encoding(1) + filetype(1) + total(2, base36) + index(2, base36)
        if (data.length < 8 || data.substr(0, 2) !== BBQR_PREFIX) return null;
        return {
            encoding: data[2],    // H=hex, 2=base32, Z=zlib+base32
            fileType: data[3],    // J=JSON, U=UTF8, P=PSBT, T=tx, etc.
            total: parseInt(data.substr(4, 2), 36),
            index: parseInt(data.substr(6, 2), 36),
            payload: data.substr(8),
        };
    }

    // BBQr session: accumulates parts until complete
    function BBQrSession() {
        this.parts = {};
        this.total = 0;
        this.encoding = '';
        this.fileType = '';
    }

    BBQrSession.prototype.addPart = function(raw) {
        var hdr = parseBBQrHeader(raw);
        if (!hdr) return null;

        if (this.total === 0) {
            this.total = hdr.total;
            this.encoding = hdr.encoding;
            this.fileType = hdr.fileType;
        }

        // Validate consistency
        if (hdr.total !== this.total || hdr.encoding !== this.encoding) return null;

        this.parts[hdr.index] = hdr.payload;

        return {
            received: Object.keys(this.parts).length,
            total: this.total,
            complete: Object.keys(this.parts).length >= this.total,
        };
    };

    BBQrSession.prototype.assemble = function() {
        // Concatenate parts in order
        var combined = '';
        for (var i = 0; i < this.total; i++) {
            if (!this.parts[i]) return Promise.reject(new Error('Missing part ' + i));
            combined += this.parts[i];
        }

        var bytes;
        if (this.encoding === 'H') {
            bytes = hexDecode(combined);
        } else if (this.encoding === '2') {
            bytes = base32Decode(combined);
        } else if (this.encoding === 'Z') {
            var raw = base32Decode(combined);
            return zlibInflate(raw).then(function(decompressed) {
                return new TextDecoder().decode(decompressed);
            });
        } else {
            return Promise.reject(new Error('Unknown encoding: ' + this.encoding));
        }

        return Promise.resolve(new TextDecoder().decode(bytes));
    };

    // ── QR Content Parser ───────────────────────────────────────

    function parseQRContent(text) {
        // Returns { type: 'xpub'|'descriptor'|'json'|'address'|'unknown', data: ... }
        text = text.trim();

        // COLDCARD Generic JSON wallet export
        if (text[0] === '{') {
            try {
                var json = JSON.parse(text);
                if (json.xfp || json.bip84 || json.bip49 || json.bip44) {
                    return { type: 'json', data: json };
                }
            } catch(e) {}
        }

        // Output descriptor: wpkh(...), sh(wpkh(...)), pkh(...), tr(...), wsh(...)
        if (/^(wpkh|sh|pkh|tr|wsh)\(/.test(text)) {
            return { type: 'descriptor', data: text };
        }

        // Extended public key: xpub, ypub, zpub, tpub, upub, vpub
        if (/^[xyztuv]pub[A-Za-z0-9]{100,}$/.test(text)) {
            return { type: 'xpub', data: text };
        }

        // Bitcoin address
        if (/^(bc1|tb1|[13mn2])[A-Za-z0-9]{25,}$/.test(text)) {
            return { type: 'address', data: text };
        }

        // Could be a raw xpub without exact prefix match
        if (text.length > 100 && /^[A-Za-z0-9]+$/.test(text)) {
            return { type: 'xpub', data: text };
        }

        return { type: 'unknown', data: text };
    }

    function extractFromColdcardJSON(json) {
        // Prefer bip84 (native segwit), then bip49, then bip44
        if (json.bip84 && json.bip84.xpub) {
            return {
                xpub: json.bip84.xpub,
                derivation: json.bip84.deriv || "m/84'/0'/0'",
                type: json.bip84.name || 'p2wpkh',
                fingerprint: json.xfp || '',
                network: json.chain === 'XTN' ? 'testnet' : 'mainnet',
            };
        }
        if (json.bip49 && json.bip49.xpub) {
            return {
                xpub: json.bip49.xpub,
                derivation: json.bip49.deriv || "m/49'/0'/0'",
                type: json.bip49.name || 'p2sh-p2wpkh',
                fingerprint: json.xfp || '',
                network: json.chain === 'XTN' ? 'testnet' : 'mainnet',
            };
        }
        if (json.bip44 && json.bip44.xpub) {
            return {
                xpub: json.bip44.xpub,
                derivation: json.bip44.deriv || "m/44'/0'/0'",
                type: json.bip44.name || 'p2pkh',
                fingerprint: json.xfp || '',
                network: json.chain === 'XTN' ? 'testnet' : 'mainnet',
            };
        }
        // Fallback to root xpub
        if (json.xpub) {
            return {
                xpub: json.xpub,
                derivation: '',
                type: 'unknown',
                fingerprint: json.xfp || '',
                network: json.chain === 'XTN' ? 'testnet' : 'mainnet',
            };
        }
        return null;
    }

    // ── Scanner Modal UI ────────────────────────────────────────

    function createScannerModal() {
        var modal = document.getElementById('qr-scanner-modal');
        if (modal) return modal;

        modal = document.createElement('div');
        modal.id = 'qr-scanner-modal';
        modal.className = 'fixed inset-0 z-50 hidden';
        modal.innerHTML = [
            '<div class="absolute inset-0 bg-black/70" onclick="BTPay.closeScanner()"></div>',
            '<div class="absolute inset-4 sm:inset-auto sm:top-1/2 sm:left-1/2 sm:-translate-x-1/2 sm:-translate-y-1/2 sm:w-full sm:max-w-md bg-white dark:bg-gray-800 rounded-xl shadow-2xl flex flex-col overflow-hidden">',
            '  <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">',
            '    <h3 class="text-sm font-semibold">Scan QR Code</h3>',
            '    <button onclick="BTPay.closeScanner()" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">',
            '      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>',
            '    </button>',
            '  </div>',
            '  <div class="relative flex-1 min-h-0">',
            '    <div id="qr-reader" class="w-full" style="min-height:300px"></div>',
            '  </div>',
            '  <div id="qr-scanner-status" class="px-4 py-3 border-t border-gray-200 dark:border-gray-700 text-sm text-gray-500 dark:text-gray-400 text-center">',
            '    Point your camera at an xpub, descriptor, or COLDCARD QR code',
            '  </div>',
            '  <div id="qr-scanner-progress" class="hidden px-4 pb-3">',
            '    <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">',
            '      <div id="qr-progress-bar" class="bg-brand h-2 rounded-full transition-all" style="width:0%"></div>',
            '    </div>',
            '    <p id="qr-progress-text" class="text-xs text-gray-500 text-center mt-1"></p>',
            '  </div>',
            '</div>',
        ].join('\n');

        document.body.appendChild(modal);
        return modal;
    }

    // ── Scanner State ───────────────────────────────────────────

    var _scanner = null;
    var _bbqrSession = null;
    var _targetField = null;   // 'xpub' or 'descriptor'
    var _scannerCallback = null;
    var _html5QrLoaded = false;

    function loadHtml5Qr() {
        if (_html5QrLoaded) return Promise.resolve();
        return new Promise(function(resolve, reject) {
            var script = document.createElement('script');
            script.src = 'https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js';
            script.onload = function() { _html5QrLoaded = true; resolve(); };
            script.onerror = function() { reject(new Error('Failed to load QR scanner library')); };
            document.head.appendChild(script);
        });
    }

    function openScanner(targetField) {
        _targetField = targetField || 'xpub';
        _bbqrSession = null;

        loadHtml5Qr().then(function() {
            var modal = createScannerModal();
            modal.classList.remove('hidden');

            // Reset progress
            var progress = document.getElementById('qr-scanner-progress');
            if (progress) progress.classList.add('hidden');
            var status = document.getElementById('qr-scanner-status');
            if (status) status.textContent = 'Point your camera at an xpub, descriptor, or COLDCARD QR code';

            // Start scanning
            var reader = document.getElementById('qr-reader');
            reader.innerHTML = '';

            _scanner = new Html5Qrcode('qr-reader');
            _scanner.start(
                { facingMode: 'environment' },
                { fps: 10, qrbox: { width: 250, height: 250 }, aspectRatio: 1.0 },
                onScanSuccess,
                function() {} // ignore scan failures (no QR found in frame)
            ).catch(function(err) {
                if (status) status.textContent = 'Camera error: ' + err;
            });
        }).catch(function(err) {
            alert('Could not load QR scanner: ' + err.message);
        });
    }

    function closeScanner() {
        var modal = document.getElementById('qr-scanner-modal');
        if (modal) modal.classList.add('hidden');

        if (_scanner) {
            _scanner.stop().catch(function() {});
            _scanner.clear();
            _scanner = null;
        }
        _bbqrSession = null;
    }

    // ── Scan Handlers ───────────────────────────────────────────

    var _lastScanned = '';
    var _lastScanTime = 0;

    function onScanSuccess(decodedText) {
        // Debounce: ignore same QR within 500ms
        var now = Date.now();
        if (decodedText === _lastScanned && now - _lastScanTime < 500) return;
        _lastScanned = decodedText;
        _lastScanTime = now;

        // Check if BBQr
        if (decodedText.substr(0, 2) === BBQR_PREFIX) {
            handleBBQrPart(decodedText);
            return;
        }

        // Single QR -- parse and apply
        var parsed = parseQRContent(decodedText);
        applyResult(parsed);
    }

    function handleBBQrPart(raw) {
        if (!_bbqrSession) {
            _bbqrSession = new BBQrSession();
        }

        var result = _bbqrSession.addPart(raw);
        if (!result) return;

        // Update progress
        var progress = document.getElementById('qr-scanner-progress');
        var bar = document.getElementById('qr-progress-bar');
        var text = document.getElementById('qr-progress-text');
        var status = document.getElementById('qr-scanner-status');

        if (progress) progress.classList.remove('hidden');
        if (bar) bar.style.width = Math.round(result.received / result.total * 100) + '%';
        if (text) text.textContent = result.received + ' / ' + result.total + ' parts scanned';
        if (status) status.textContent = 'Scanning BBQr animated sequence...';

        if (result.complete) {
            if (status) status.textContent = 'Decoding...';
            _bbqrSession.assemble().then(function(data) {
                var parsed = parseQRContent(data);
                applyResult(parsed);
            }).catch(function(err) {
                if (status) status.textContent = 'Decode error: ' + err.message;
            });
        }
    }

    function applyResult(parsed) {
        var status = document.getElementById('qr-scanner-status');

        if (parsed.type === 'json') {
            // COLDCARD JSON wallet export
            var extracted = extractFromColdcardJSON(parsed.data);
            if (!extracted) {
                if (status) status.textContent = 'Could not find xpub in wallet export';
                return;
            }
            // Set xpub field
            setFieldValue('xpub', extracted.xpub);
            setWalletType('xpub');
            // Set network if detected
            if (extracted.network) {
                setNetwork(extracted.network);
            }
            if (status) {
                var info = extracted.type;
                if (extracted.fingerprint) info += ' [' + extracted.fingerprint + ']';
                status.textContent = 'Imported ' + info;
            }
            setTimeout(closeScanner, 1200);
            return;
        }

        if (parsed.type === 'descriptor') {
            setFieldValue('descriptor', parsed.data);
            setWalletType('descriptor');
            if (status) status.textContent = 'Descriptor imported';
            setTimeout(closeScanner, 1200);
            return;
        }

        if (parsed.type === 'xpub') {
            setFieldValue('xpub', parsed.data);
            setWalletType('xpub');
            if (status) status.textContent = 'Extended public key imported';
            setTimeout(closeScanner, 1200);
            return;
        }

        if (parsed.type === 'address') {
            setFieldValue('addresses', parsed.data);
            setWalletType('address_list');
            if (status) status.textContent = 'Address imported';
            setTimeout(closeScanner, 1200);
            return;
        }

        if (status) status.textContent = 'Unrecognized QR content';
    }

    // ── Form Field Helpers ──────────────────────────────────────

    function setFieldValue(fieldName, value) {
        // Try input[name=fieldName] or textarea[name=fieldName]
        var el = document.querySelector('input[name="' + fieldName + '"], textarea[name="' + fieldName + '"]');
        if (el) {
            el.value = value;
            // Trigger change event
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }

    function setWalletType(type) {
        var select = document.getElementById('wallet-type');
        if (select) {
            select.value = type;
            // Trigger the toggleWalletFields function
            if (typeof toggleWalletFields === 'function') {
                toggleWalletFields();
            }
            select.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }

    function setNetwork(network) {
        var select = document.querySelector('select[name="network"]');
        if (select) {
            select.value = network;
        }
    }

    // ── Public API ──────────────────────────────────────────────

    window.BTPay = window.BTPay || {};
    window.BTPay.openScanner = openScanner;
    window.BTPay.closeScanner = closeScanner;

})();
