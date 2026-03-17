#
# Electrum protocol SPV client
#
# TCP/SSL JSON-RPC client for Electrum server protocol v1.4.
# Synchronous I/O — meant to run in a background thread.
# Supports SOCKS5 proxy for Tor privacy.
#
import json
import logging
import socket
import ssl
import threading

log = logging.getLogger(__name__)


class ElectrumError(Exception):
    '''Error from Electrum server.'''
    pass


class ElectrumClient:
    '''
    TCP/SSL JSON-RPC client for the Electrum server protocol.

    Usage:
        client = ElectrumClient('electrum.blockstream.info', 50002, ssl=True)
        client.connect()
        ver = client.server_version()
        balance = client.scripthash_get_balance(script_hash)
        client.disconnect()
    '''

    def __init__(self, host, port=50002, use_ssl=True, proxy=None, timeout=30,
                 verify_ssl=True):
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.proxy = proxy
        self.timeout = timeout
        self.verify_ssl = verify_ssl

        self._sock = None
        self._rfile = None
        self._wfile = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._connected = False

    @property
    def is_connected(self):
        return self._connected

    def connect(self):
        '''Establish TCP/SSL connection to Electrum server.'''
        if self._connected:
            return

        try:
            sock = self._create_socket()
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))

            if self.use_ssl:
                ctx = ssl.create_default_context()
                if not self.verify_ssl:
                    # Only disable for explicitly trusted self-signed servers
                    log.warning('TLS verification disabled for %s:%d — '
                                'vulnerable to MITM attacks', self.host, self.port)
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                sock = ctx.wrap_socket(sock, server_hostname=self.host)

            self._sock = sock
            self._rfile = sock.makefile('r', buffering=1)
            self._wfile = sock.makefile('w', buffering=1)
            self._connected = True

            log.info('Connected to Electrum server %s:%d (ssl=%s)',
                     self.host, self.port, self.use_ssl)
        except Exception as e:
            self._cleanup()
            raise ElectrumError('Connection failed: %s' % str(e))

    def disconnect(self):
        '''Close connection.'''
        self._cleanup()
        log.info('Disconnected from Electrum server %s:%d', self.host, self.port)

    def _create_socket(self):
        '''Create socket, optionally via SOCKS5 proxy.'''
        if self.proxy:
            return self._create_socks_socket()
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def _create_socks_socket(self):
        '''Create SOCKS5 proxy socket.'''
        # Parse proxy URL: socks5h://host:port
        proxy = self.proxy
        if proxy.startswith('socks5h://') or proxy.startswith('socks5://'):
            proxy = proxy.split('://', 1)[1]

        parts = proxy.split(':')
        proxy_host = parts[0]
        proxy_port = int(parts[1]) if len(parts) > 1 else 1080

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect((proxy_host, proxy_port))

        # SOCKS5 handshake — no auth
        sock.sendall(b'\x05\x01\x00')
        resp = sock.recv(2)
        if resp != b'\x05\x00':
            raise ElectrumError('SOCKS5 handshake failed')

        # SOCKS5 connect request (domain name)
        host_bytes = self.host.encode('utf-8')
        port_bytes = self.port.to_bytes(2, 'big')
        req = b'\x05\x01\x00\x03' + bytes([len(host_bytes)]) + host_bytes + port_bytes
        sock.sendall(req)

        resp = sock.recv(10)
        if len(resp) < 2 or resp[1] != 0x00:
            raise ElectrumError('SOCKS5 connect failed (status=%d)' % (resp[1] if len(resp) > 1 else -1))

        return sock

    def _cleanup(self):
        '''Close all resources.'''
        self._connected = False
        for f in (self._rfile, self._wfile):
            if f:
                try:
                    f.close()
                except Exception:
                    pass
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        self._rfile = None
        self._wfile = None

    def _call(self, method, *params):
        '''
        Send JSON-RPC request and return result.
        Thread-safe via lock.
        '''
        if not self._connected:
            raise ElectrumError('Not connected')

        with self._lock:
            self._request_id += 1
            req_id = self._request_id

            request = {
                'jsonrpc': '2.0',
                'id': req_id,
                'method': method,
                'params': list(params),
            }

            try:
                line = json.dumps(request) + '\n'
                self._wfile.write(line)
                self._wfile.flush()

                # Read response line
                response_line = self._rfile.readline()
                if not response_line:
                    self._cleanup()
                    raise ElectrumError('Connection closed by server')

                response = json.loads(response_line)
            except (socket.error, OSError) as e:
                self._cleanup()
                raise ElectrumError('Communication error: %s' % str(e))

        if 'error' in response and response['error']:
            err = response['error']
            msg = err.get('message', str(err)) if isinstance(err, dict) else str(err)
            raise ElectrumError('Server error: %s' % msg)

        return response.get('result')

    # ---- Server methods ----

    def server_version(self, client_name='BTPay/1.0', protocol='1.4'):
        '''
        server.version — handshake, negotiate protocol version.
        Returns [server_software, protocol_version].
        '''
        return self._call('server.version', client_name, protocol)

    def server_banner(self):
        '''server.banner — get server banner text.'''
        return self._call('server.banner')

    def server_ping(self):
        '''server.ping — keep-alive ping.'''
        return self._call('server.ping')

    # ---- Blockchain methods ----

    def headers_subscribe(self):
        '''
        blockchain.headers.subscribe — subscribe to new block headers.
        Returns current header: {hex, height}.
        '''
        return self._call('blockchain.headers.subscribe')

    def scripthash_subscribe(self, script_hash):
        '''
        blockchain.scripthash.subscribe — subscribe to address changes.
        Returns status hash or None if no history.
        '''
        return self._call('blockchain.scripthash.subscribe', script_hash)

    def scripthash_get_balance(self, script_hash):
        '''
        blockchain.scripthash.get_balance — get address balance.
        Returns {confirmed: int, unconfirmed: int} in satoshis.
        '''
        return self._call('blockchain.scripthash.get_balance', script_hash)

    def scripthash_get_history(self, script_hash):
        '''
        blockchain.scripthash.get_history — get address transaction history.
        Returns [{tx_hash, height}, ...].
        height=0 means unconfirmed, -1 means unconfirmed with unconfirmed parents.
        '''
        return self._call('blockchain.scripthash.get_history', script_hash)

    def scripthash_get_mempool(self, script_hash):
        '''
        blockchain.scripthash.get_mempool — get unconfirmed transactions.
        Returns [{tx_hash, height, fee}, ...].
        '''
        return self._call('blockchain.scripthash.get_mempool', script_hash)

    def scripthash_listunspent(self, script_hash):
        '''
        blockchain.scripthash.listunspent — get unspent outputs.
        Returns [{tx_hash, tx_pos, value, height}, ...].
        '''
        return self._call('blockchain.scripthash.listunspent', script_hash)

    # ---- Transaction methods ----

    def transaction_get(self, tx_hash, verbose=False):
        '''
        blockchain.transaction.get — get raw transaction.
        If verbose=True, returns decoded transaction dict.
        '''
        return self._call('blockchain.transaction.get', tx_hash, verbose)

    def transaction_get_merkle(self, tx_hash, height):
        '''
        blockchain.transaction.get_merkle — get merkle proof.
        Returns {merkle: [...], block_height, pos}.
        '''
        return self._call('blockchain.transaction.get_merkle', tx_hash, height)

    # ---- Fee estimation ----

    def estimate_fee(self, target_blocks=6):
        '''
        blockchain.estimatefee — fee rate estimate in BTC/kB.
        Returns Decimal fee rate or -1 if not available.
        '''
        return self._call('blockchain.estimatefee', target_blocks)

# EOF
