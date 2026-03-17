#
# Pickle-based persistence layer.
#
# Saves/loads the entire MemoryStore to/from a single pickle file.
# Writes immediately after every data change via the engine hook.
# Keeps 10 timestamped backups.
#
import os, pickle, threading, signal, logging, datetime, time, glob
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

    if os.path.exists(pickle_path):
        _load_pickle(data_dir, pickle_path)
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
