#
# JSON file persistence layer.
#
# Saves/loads the entire MemoryStore to/from JSON files.
# Auto-save every N seconds + graceful shutdown save.
#
import os, json, threading, signal, logging, datetime, time
from decimal import Decimal
from btpay.orm.engine import MemoryStore

log = logging.getLogger(__name__)


class BTPayEncoder(json.JSONEncoder):
    '''Custom JSON encoder for Decimal, datetime, set.'''
    def default(self, obj):
        if isinstance(obj, Decimal):
            return {'__decimal__': str(obj)}
        if isinstance(obj, datetime.datetime):
            return {'__datetime__': obj.isoformat()}
        if isinstance(obj, set):
            return {'__set__': sorted(list(obj))}
        return super().default(obj)


def btpay_decoder(obj):
    '''Custom JSON decoder hook.'''
    if '__decimal__' in obj:
        return Decimal(obj['__decimal__'])
    if '__datetime__' in obj:
        return datetime.datetime.fromisoformat(obj['__datetime__'])
    if '__set__' in obj:
        return set(obj['__set__'])
    return obj


def save_to_disk(data_dir):
    '''Save all model data to JSON files in data_dir.'''
    store = MemoryStore()
    os.makedirs(data_dir, exist_ok=True)

    models = store.registered_models()
    meta = {
        'schema_version': 1,
        'saved_at': datetime.datetime.utcnow().isoformat(),
        'models': models,
    }

    for model_name in models:
        table_data = store.get_table_data(model_name)
        seq = store.get_sequence(model_name)

        payload = {
            'sequence': seq,
            'rows': table_data,
        }

        fpath = os.path.join(data_dir, '%s.json' % model_name)
        tmp_path = fpath + '.tmp'

        try:
            with open(tmp_path, 'w') as f:
                json.dump(payload, f, cls=BTPayEncoder, indent=2)
            os.replace(tmp_path, fpath)
        except Exception:
            log.exception("Failed to save %s" % model_name)
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # Save meta
    meta_path = os.path.join(data_dir, '_meta.json')
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)

    log.info("Saved %d models to %s" % (len(models), data_dir))


def load_from_disk(data_dir):
    '''Load all JSON files from data_dir into MemoryStore.'''
    if not os.path.isdir(data_dir):
        log.info("No data directory at %s, starting fresh" % data_dir)
        return

    from btpay.orm.model import get_model_registry
    registry = get_model_registry()

    meta_path = os.path.join(data_dir, '_meta.json')
    if not os.path.exists(meta_path):
        log.info("No _meta.json in %s, starting fresh" % data_dir)
        return

    with open(meta_path, 'r') as f:
        meta = json.load(f)

    store = MemoryStore()

    for model_name in meta.get('models', []):
        if model_name not in registry:
            log.warning("Model '%s' found in data but not registered, skipping" % model_name)
            continue

        fpath = os.path.join(data_dir, '%s.json' % model_name)
        if not os.path.exists(fpath):
            continue

        try:
            with open(fpath, 'r') as f:
                payload = json.load(f, object_hook=btpay_decoder)

            model_cls = registry[model_name]
            store.load_table_data(model_name, payload.get('rows', {}), model_cls)

            seq = payload.get('sequence', 1)
            store.set_sequence(model_name, seq)

            count = len(payload.get('rows', {}))
            log.info("Loaded %d %s records" % (count, model_name))
        except Exception:
            log.exception("Failed to load %s" % model_name)

    log.info("Data loaded from %s" % data_dir)


def backup_rotation(data_dir, keep=5):
    '''Create a single-file timestamped backup and rotate old ones.'''
    backup_dir = os.path.join(data_dir, 'backups')
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')

    # Collect all model data into a single snapshot
    snapshot = {}
    for fname in os.listdir(data_dir):
        if fname.endswith('.json'):
            fpath = os.path.join(data_dir, fname)
            with open(fpath, 'r') as f:
                snapshot[fname] = json.load(f)

    # Write atomically: tmp file + rename
    dest = os.path.join(backup_dir, 'backup_%s.json' % timestamp)
    tmp_path = dest + '.tmp'
    try:
        with open(tmp_path, 'w') as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp_path, dest)
    except Exception:
        log.exception("Failed to create backup")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return

    # Remove old backup files
    backups = sorted([
        fname for fname in os.listdir(backup_dir)
        if fname.startswith('backup_') and fname.endswith('.json')
        and os.path.isfile(os.path.join(backup_dir, fname))
        and fname != ('backup_%s.json' % timestamp)
    ])

    while len(backups) >= keep:
        old = backups.pop(0)
        old_path = os.path.join(backup_dir, old)
        os.unlink(old_path)

    log.info("Backup created at %s" % dest)


class AutoSaver:
    '''Background thread that periodically saves data to disk.'''

    def __init__(self, data_dir, interval=60, backup_interval=3600, backup_keep=5):
        self.data_dir = data_dir
        self.interval = interval
        self.backup_interval = backup_interval
        self.backup_keep = backup_keep
        self._thread = None
        self._stop_event = threading.Event()
        self._last_backup = time.time()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name='autosave')
        self._thread.start()

        # Register shutdown handler
        for sig in (signal.SIGTERM, signal.SIGINT):
            prev = signal.getsignal(sig)
            def handler(signum, frame, prev=prev):
                self.shutdown_save()
                if callable(prev) and prev not in (signal.SIG_DFL, signal.SIG_IGN):
                    prev(signum, frame)
            try:
                signal.signal(sig, handler)
            except (OSError, ValueError):
                pass        # can't set signal handler from non-main thread

        log.info("AutoSaver started (interval=%ds)" % self.interval)

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def shutdown_save(self):
        '''Save on shutdown.'''
        try:
            save_to_disk(self.data_dir)
            log.info("Shutdown save complete")
        except Exception:
            log.exception("Shutdown save failed")

    def _run(self):
        while not self._stop_event.is_set():
            self._stop_event.wait(self.interval)
            if self._stop_event.is_set():
                break
            try:
                save_to_disk(self.data_dir)

                # Clean up expired sessions periodically
                try:
                    from btpay.auth.sessions import cleanup_expired_sessions
                    cleaned = cleanup_expired_sessions()
                    if cleaned:
                        log.info("Cleaned up %d expired sessions", cleaned)
                except Exception:
                    log.exception("Session cleanup failed")

                # Check if backup is due
                now = time.time()
                if now - self._last_backup >= self.backup_interval:
                    backup_rotation(self.data_dir, keep=self.backup_keep)
                    self._last_backup = now
            except Exception:
                log.exception("AutoSave failed")

# EOF
