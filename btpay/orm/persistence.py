#
# Pickle-based persistence layer.
#
# Saves/loads the entire MemoryStore to/from a single pickle file.
# Writes immediately after every data change via the engine hook.
# Keeps 10 timestamped backups.
#
import os, pickle, json, threading, signal, logging, datetime, time, glob
from decimal import Decimal
from btpay.orm.engine import MemoryStore

log = logging.getLogger(__name__)

# Module-level data dir, set by init_persistence()
_data_dir = None
_write_lock = threading.Lock()


def init_persistence(data_dir):
    '''Initialize persistence: set the data dir and hook into engine writes.'''
    global _data_dir
    _data_dir = data_dir
    os.makedirs(data_dir, exist_ok=True)

    # Hook into the engine so every mutation triggers a write
    store = MemoryStore()
    store.set_after_write_hook(_after_write_hook)


def _after_write_hook():
    '''Called by MemoryStore after every insert/update/delete.'''
    if _data_dir:
        save_to_disk(_data_dir)


def save_to_disk(data_dir):
    '''Save all model data to a single pickle file in data_dir.'''
    store = MemoryStore()
    os.makedirs(data_dir, exist_ok=True)

    models = store.registered_models()
    snapshot = {
        'schema_version': 2,
        'saved_at': datetime.datetime.utcnow().isoformat(),
        'models': {},
    }

    for model_name in models:
        table_data = store.get_table_data(model_name)
        seq = store.get_sequence(model_name)
        snapshot['models'][model_name] = {
            'sequence': seq,
            'rows': table_data,
        }

    fpath = os.path.join(data_dir, 'btpay.db')
    tmp_path = fpath + '.tmp'

    with _write_lock:
        try:
            with open(tmp_path, 'wb') as f:
                pickle.dump(snapshot, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp_path, fpath)
        except Exception:
            log.exception("Failed to save data")
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


def load_from_disk(data_dir):
    '''Load data from pickle (or migrate from legacy JSON).'''
    if not os.path.isdir(data_dir):
        log.info("No data directory at %s, starting fresh" % data_dir)
        return

    pickle_path = os.path.join(data_dir, 'btpay.db')
    meta_path = os.path.join(data_dir, '_meta.json')

    if os.path.exists(pickle_path):
        _load_pickle(data_dir, pickle_path)
    elif os.path.exists(meta_path):
        log.info("Found legacy JSON data, migrating to pickle...")
        _migrate_from_json(data_dir)
    else:
        log.info("No data in %s, starting fresh" % data_dir)


def _load_pickle(data_dir, pickle_path):
    '''Load from pickle file.'''
    from btpay.orm.model import get_model_registry
    registry = get_model_registry()
    store = MemoryStore()

    try:
        with open(pickle_path, 'rb') as f:
            snapshot = pickle.load(f)
    except Exception:
        log.exception("Failed to load %s" % pickle_path)
        return

    for model_name, payload in snapshot.get('models', {}).items():
        if model_name not in registry:
            log.warning("Model '%s' in data but not registered, skipping" % model_name)
            continue

        model_cls = registry[model_name]
        rows = payload.get('rows', {})
        store.load_table_data(model_name, rows, model_cls)

        seq = payload.get('sequence', 1)
        store.set_sequence(model_name, seq)

        log.info("Loaded %d %s records" % (len(rows), model_name))

    log.info("Data loaded from %s" % pickle_path)


def _migrate_from_json(data_dir):
    '''Migrate legacy JSON files to pickle format.'''
    # Use the legacy JSON loader
    from btpay.orm.model import get_model_registry
    registry = get_model_registry()
    store = MemoryStore()

    meta_path = os.path.join(data_dir, '_meta.json')
    with open(meta_path, 'r') as f:
        meta = json.load(f)

    for model_name in meta.get('models', []):
        if model_name not in registry:
            log.warning("Model '%s' in data but not registered, skipping" % model_name)
            continue

        fpath = os.path.join(data_dir, '%s.json' % model_name)
        if not os.path.exists(fpath):
            continue

        try:
            with open(fpath, 'r') as f:
                payload = json.load(f, object_hook=_legacy_json_decoder)

            model_cls = registry[model_name]
            store.load_table_data(model_name, payload.get('rows', {}), model_cls)

            seq = payload.get('sequence', 1)
            store.set_sequence(model_name, seq)

            count = len(payload.get('rows', {}))
            log.info("Migrated %d %s records from JSON" % (count, model_name))
        except Exception:
            log.exception("Failed to migrate %s" % model_name)

    # Write the new pickle file
    save_to_disk(data_dir)

    # Move legacy JSON files to a backup subdirectory
    legacy_dir = os.path.join(data_dir, 'legacy_json')
    os.makedirs(legacy_dir, exist_ok=True)
    for fname in os.listdir(data_dir):
        if fname.endswith('.json') and os.path.isfile(os.path.join(data_dir, fname)):
            src = os.path.join(data_dir, fname)
            dst = os.path.join(legacy_dir, fname)
            os.replace(src, dst)

    log.info("Migration complete. Legacy JSON moved to %s" % legacy_dir)


def _legacy_json_decoder(obj):
    '''Decode legacy JSON with custom type markers.'''
    if '__decimal__' in obj:
        return Decimal(obj['__decimal__'])
    if '__datetime__' in obj:
        return datetime.datetime.fromisoformat(obj['__datetime__'])
    if '__set__' in obj:
        return set(obj['__set__'])
    return obj


def backup_rotation(data_dir, keep=10):
    '''Create a timestamped pickle backup and rotate old ones.'''
    backup_dir = os.path.join(data_dir, 'backups')
    os.makedirs(backup_dir, exist_ok=True)

    src = os.path.join(data_dir, 'btpay.db')
    if not os.path.exists(src):
        return

    timestamp = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(backup_dir, 'backup_%s.db' % timestamp)
    tmp_path = dest + '.tmp'

    try:
        with open(src, 'rb') as fin:
            data = fin.read()
        with open(tmp_path, 'wb') as fout:
            fout.write(data)
        os.replace(tmp_path, dest)
    except Exception:
        log.exception("Failed to create backup")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return

    # Remove old backups beyond keep limit
    backups = sorted(glob.glob(os.path.join(backup_dir, 'backup_*.db')))
    # Exclude the one we just created
    backups = [b for b in backups if b != dest]

    while len(backups) >= keep:
        old = backups.pop(0)
        try:
            os.unlink(old)
        except OSError:
            pass

    log.info("Backup created at %s" % dest)


class AutoSaver:
    '''Background thread that periodically creates backups and runs cleanup.'''

    def __init__(self, data_dir, interval=60, backup_interval=3600, backup_keep=10):
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
