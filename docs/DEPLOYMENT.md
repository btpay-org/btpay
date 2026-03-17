# Production Deployment

This guide covers deploying BTPay on a Linux server with Gunicorn, Nginx, and systemd.

## Prerequisites

- Linux server (Debian/Ubuntu or similar) or FreeBSD
- Python 3.10+
- Nginx
- A domain name with DNS pointed to your server
- TLS certificate (Let's Encrypt recommended)

## Installation

```bash
# Create system user
sudo useradd -r -s /bin/false -d /opt/btpay btpay

# Clone and set up
sudo mkdir -p /opt/btpay
sudo chown btpay:btpay /opt/btpay
cd /opt/btpay

sudo -u btpay git clone https://github.com/user/btpay.git .
sudo -u btpay python3 -m venv .venv
sudo -u btpay .venv/bin/pip install --require-hashes -r requirements.lock
sudo -u btpay .venv/bin/pip install -e . --no-deps
```

## Configuration

Create a production config file:

```bash
sudo -u btpay cat > /opt/btpay/config.py << 'EOF'
# Generate secrets with: python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = 'your-64-char-hex-string-here'
DEV_MODE = False

# Reference number encryption (NaCl SecretBox)
# Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"
REFNUM_KEY = 'your-64-char-hex-string'
# Generate with: python3 -c "import secrets; print(secrets.token_hex(24))"
REFNUM_NONCE = 'your-48-char-hex-string'

# JWT secrets (one per purpose)
JWT_SECRETS = {
    'admin': 'your-random-secret-1',
    'login': 'your-random-secret-2',
    'api':   'your-random-secret-3',
    'invite': 'your-random-secret-4',
}

# Optional: SMTP for email notifications
SMTP_CONFIG = {
    'host': 'smtp.example.com',
    'port': 587,
    'username': 'your-smtp-user',
    'password': 'your-smtp-password',
    'from_email': 'payments@yourdomain.com',
    'from_name': 'Your Business',
}

# Optional: Tor privacy
# SOCKS5_PROXY = 'socks5h://127.0.0.1:9050'
EOF

chmod 600 /opt/btpay/config.py
```

### Generate Secrets

```bash
# Generate all required secrets at once
python3 -c "
import secrets
print(f'SECRET_KEY = \"{secrets.token_hex(32)}\"')
print(f'REFNUM_KEY = \"{secrets.token_hex(32)}\"')
print(f'REFNUM_NONCE = \"{secrets.token_hex(24)}\"')
for purpose in ['admin', 'login', 'api', 'invite']:
    print(f'  \"{purpose}\": \"{secrets.token_hex(24)}\",')
"
```

## Create Admin User

```bash
cd /opt/btpay
sudo -u btpay .venv/bin/flask --app app user-create \
  --email admin@yourdomain.com \
  --first-name Admin \
  --last-name User
```

You'll be prompted for a password.

## Gunicorn

The included config at `deploy/gunicorn.conf.py` is production-ready:

```bash
# Test it
sudo -u btpay .venv/bin/gunicorn -c deploy/gunicorn.conf.py wsgi:app
```

Key settings:
- **1 worker** (required — in-memory data store)
- **4 threads** (configurable via `BTPAY_THREADS`)
- **gthread** worker class
- Data is saved to disk on shutdown

Override via environment:

| Env Var | Default | Description |
|---------|---------|-------------|
| `BTPAY_BIND` | `127.0.0.1:5000` | Bind address |
| `BTPAY_THREADS` | `4` | Worker threads |
| `BTPAY_LOG_LEVEL` | `info` | Log level |

## systemd

Install the service:

```bash
sudo cp /opt/btpay/deploy/btpay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable btpay
sudo systemctl start btpay
```

Check status:

```bash
sudo systemctl status btpay
sudo journalctl -u btpay -f
```

The service includes security hardening:
- `ProtectSystem=strict` — read-only filesystem except data dir
- `PrivateTmp=yes` — isolated temp directory
- `NoNewPrivileges=yes` — no privilege escalation
- `ProtectHome=yes` — no access to home directories

The service sets `BTPAY_NUM_PROXIES=1` so Flask correctly identifies client IPs behind the nginx reverse proxy. This is required for per-IP rate limiting and logging to work. If you use more than one proxy layer (e.g. Cloudflare + nginx), set this to the number of trusted proxies.

## Nginx

Edit `deploy/nginx.conf` and replace:
- `btpay.example.com` with your domain
- TLS certificate paths with your Let's Encrypt paths
- Uncomment the rate limiting configuration in your main `nginx.conf`

Install:

```bash
sudo cp /opt/btpay/deploy/nginx.conf /etc/nginx/sites-available/btpay
sudo ln -s /etc/nginx/sites-available/btpay /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Let's Encrypt

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d btpay.yourdomain.com
```

## Tor (Optional)

For maximum privacy, route all external connections through Tor:

1. Install Tor:
   ```bash
   sudo apt install tor
   sudo systemctl enable tor
   ```

2. Add to `config.py`:
   ```python
   SOCKS5_PROXY = 'socks5h://127.0.0.1:9050'
   ```

3. Optionally expose as a Tor hidden service by adding to `/etc/tor/torrc`:
   ```
   HiddenServiceDir /var/lib/tor/btpay/
   HiddenServicePort 80 127.0.0.1:5000
   ```

4. Get your `.onion` address:
   ```bash
   sudo cat /var/lib/tor/btpay/hostname
   ```

## libsecp256k1 (Optional)

For faster BIP32 key derivation, build the C library:

```bash
cd /tmp
git clone https://github.com/bitcoin-core/secp256k1.git
cd secp256k1
./autogen.sh
./configure --enable-module-recovery
make
sudo make install
sudo ldconfig
```

BTPay automatically detects and uses it if available. Without it, a pure Python fallback is used.

## Backups

### Automatic Backups

BTPay auto-saves data every 60 seconds and keeps the last 5 backups. Data is stored in `data/`.

### Manual Backup

```bash
sudo -u btpay .venv/bin/flask --app app db-backup
```

### External Backup

Back up the entire `data/` directory:

```bash
# Simple rsync backup
rsync -av /opt/btpay/data/ /backup/btpay/

# Or with a cron job
echo "0 */6 * * * btpay cd /opt/btpay && .venv/bin/flask --app app db-backup" | sudo tee /etc/cron.d/btpay-backup
```

### Restore

```bash
sudo systemctl stop btpay
sudo -u btpay cp /backup/btpay/*.json /opt/btpay/data/
sudo systemctl start btpay
```

## Monitoring

### Health Check

```bash
curl http://localhost:5000/health
```

Returns `{"status": "ok"}` when the server is running.

### Logs

```bash
# Follow live logs
sudo journalctl -u btpay -f

# Recent errors only
sudo journalctl -u btpay --since "1 hour ago" -p err
```

In production, logs are JSON-formatted for easy parsing by log aggregators.

### Data Stats

```bash
sudo -u btpay .venv/bin/flask --app app db-stats
```

## Updating

### Via Settings UI (recommended)

Go to **Settings > Software Update**. BTPay will check GitHub for available versions, create a backup of your code and data, apply the update, and restart automatically. You can also upload a release ZIP for air-gapped deployments.

### Via CLI

```bash
cd /opt/btpay
source .venv/bin/activate

# Check what's available
flask --app app check-updates

# Update to a specific version
flask --app app update --version v0.2.0

# Or update from a ZIP file (air-gapped / Tor-only)
flask --app app update --zip /path/to/btpay-v0.2.0.zip

# Restart to apply
sudo systemctl restart btpay
```

The `update` command automatically creates pre-update backups of both code and data. Use `--skip-backup` to skip (not recommended).

### Via git (manual)

```bash
cd /opt/btpay
sudo -u btpay git pull
sudo -u btpay .venv/bin/pip install --require-hashes -r requirements.lock
sudo -u btpay .venv/bin/pip install -e . --no-deps
sudo systemctl restart btpay
```

### Rollback

If an update causes issues, rollback to the previous version:

```bash
# Via CLI — lists available backups and restores
flask --app app update-rollback

# Then restart
sudo systemctl restart btpay
```

You can also rollback from **Settings > Software Update** in the Update History section.

**Note:** For the self-update feature to work under systemd, the service needs write access to the application directory. Change `ReadWritePaths=/opt/btpay/data` to `ReadWritePaths=/opt/btpay` in the service file if you plan to use UI/CLI updates.

## FreeBSD

BTPay works on FreeBSD. Key differences:

- Use `pkg install python3 py3-pip` instead of apt
- Use `rc.d` scripts instead of systemd (or install `sysutils/py-supervisor`)
- libsecp256k1: `pkg install libsecp256k1`
- Tor: `pkg install tor`

## Troubleshooting

**Server won't start:**
- Check logs: `journalctl -u btpay -n 50`
- Verify config.py syntax: `python3 -c "exec(open('config.py').read())"`
- Ensure data directory is writable: `ls -la /opt/btpay/data/`

**Data lost after restart:**
- BTPay saves data every 60s and on graceful shutdown (SIGTERM)
- If killed with SIGKILL, up to 60s of data may be lost
- Always use `systemctl stop` for clean shutdown

**Rate limited:**
- Login: 5 attempts per minute
- API: 100 requests per minute
- Wait for the window to expire, or restart the server to clear rate limit state

**Exchange rates not updating:**
- Check if external API access works: `curl https://api.coingecko.com/api/v3/ping`
- If behind a firewall, configure `SOCKS5_PROXY`
- Check logs for rate fetch errors
