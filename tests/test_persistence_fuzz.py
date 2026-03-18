#
# Fuzz and crash-resilience tests for ORM persistence.
#
# Simulates real-world failure modes for self-hosted installs:
# - Power loss / kill -9 during save (partial writes)
# - Corrupt or truncated JSON files on disk
# - Random garbage in data files
# - Stale .tmp files left from interrupted saves
# - Missing or empty _meta.json
# - Random field values through save/load (type fuzzing)
# - Concurrent save from multiple threads
#
import json
import os
import random
import string
import threading
import time
from decimal import Decimal
from unittest.mock import patch

import pytest

from btpay.auth.models import User, Organization, Membership
from btpay.invoicing.models import Invoice, InvoiceLine
from btpay.orm.engine import MemoryStore
from btpay.orm.persistence import save_to_disk, load_from_disk, AutoSaver


# ---- Helpers ----

def _seed_data(app):
    '''Create a baseline dataset for crash tests.'''
    with app.app_context():
        org = Organization(name='CrashTest Inc', slug='crashtest')
        org.save()

        for i in range(5):
            u = User(email='user%d@crash.test' % i, first_name='User%d' % i)
            u.set_password('crashpass%d' % i)
            u.save()
            Membership(user_id=u.id, org_id=org.id, role='owner' if i == 0 else 'viewer').save()

        for i in range(10):
            inv = Invoice(org_id=org.id, user_id=1,
                          customer_name='Customer %d' % i,
                          currency='USD', status='draft',
                          total=Decimal('%d.%02d' % (i * 100, i)))
            inv.save()

        return org


def _verify_baseline(org_id):
    '''Verify the baseline dataset is intact.'''
    assert Organization.get(org_id) is not None
    assert Organization.get(org_id).name == 'CrashTest Inc'
    assert User.query.count() == 5
    assert Membership.query.count() == 5
    assert Invoice.query.count() == 10
    for i in range(5):
        u = User.get_by(email='user%d@crash.test' % i)
        assert u is not None, 'Missing user%d' % i
        assert u.check_password('crashpass%d' % i), 'Bad password for user%d' % i


# ---- Partial write / crash simulation ----

class TestCrashResilience:
    '''Simulate crashes during save and verify data recovery.'''

    def test_stale_tmp_files_dont_break_load(self, app):
        '''Leftover .tmp files from interrupted saves are harmless.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            org = _seed_data(app)
            save_to_disk(data_dir)

            # Simulate interrupted save: leave .tmp files around
            for fname in os.listdir(data_dir):
                if fname.endswith('.json'):
                    tmp_path = os.path.join(data_dir, fname + '.tmp')
                    with open(tmp_path, 'w') as f:
                        f.write('{"partial": "garbage data that was being written')

            # Load should ignore .tmp files and read the good .json files
            MemoryStore().clear()
            load_from_disk(data_dir)
            _verify_baseline(org.id)

    def test_crash_during_model_save_preserves_other_models(self, app):
        '''If one model file fails to write, others are still saved.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            org = _seed_data(app)

            # Patch os.replace to fail for User.json only (simulates disk full for one file)
            original_replace = os.replace
            def failing_replace(src, dst):
                if 'User.json' in dst:
                    raise OSError('Simulated disk full')
                return original_replace(src, dst)

            with patch('btpay.orm.persistence.os.replace', side_effect=failing_replace):
                save_to_disk(data_dir)

            # User.json should not exist (failed), but others should
            assert not os.path.exists(os.path.join(data_dir, 'User.json'))
            assert os.path.exists(os.path.join(data_dir, 'Organization.json'))
            assert os.path.exists(os.path.join(data_dir, 'Invoice.json'))

    def test_truncated_json_file_doesnt_crash_load(self, app):
        '''A file cut off mid-write (power loss) doesn't crash the app.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            org = _seed_data(app)
            save_to_disk(data_dir)

            # Truncate User.json mid-content
            user_file = os.path.join(data_dir, 'User.json')
            with open(user_file, 'r') as f:
                content = f.read()
            with open(user_file, 'w') as f:
                f.write(content[:len(content) // 2])  # cut in half

            # Load should handle gracefully — User fails, others load
            MemoryStore().clear()
            load_from_disk(data_dir)
            assert User.query.count() == 0  # truncated file can't parse
            assert Organization.query.count() == 1  # other files intact
            assert Invoice.query.count() == 10

    def test_empty_json_file_doesnt_crash_load(self, app):
        '''A 0-byte file (power loss right at open) doesn't crash.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            org = _seed_data(app)
            save_to_disk(data_dir)

            # Zero out User.json
            with open(os.path.join(data_dir, 'User.json'), 'w') as f:
                pass  # empty file

            MemoryStore().clear()
            load_from_disk(data_dir)
            assert User.query.count() == 0
            assert Organization.query.count() == 1

    def test_meta_json_missing_after_crash(self, app):
        '''If _meta.json is lost, load starts fresh (no partial state).'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            _seed_data(app)
            save_to_disk(data_dir)

            # Delete _meta.json (simulates crash during meta write)
            os.unlink(os.path.join(data_dir, '_meta.json'))

            MemoryStore().clear()
            load_from_disk(data_dir)
            # Without meta, nothing loads — clean slate
            assert User.query.count() == 0
            assert Organization.query.count() == 0

    def test_meta_json_truncated(self, app):
        '''Truncated _meta.json doesn't crash the app.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            _seed_data(app)
            save_to_disk(data_dir)

            meta_path = os.path.join(data_dir, '_meta.json')
            with open(meta_path, 'r') as f:
                content = f.read()
            with open(meta_path, 'w') as f:
                f.write(content[:10])  # truncate

            MemoryStore().clear()
            # Should not raise — handles corrupt meta gracefully
            try:
                load_from_disk(data_dir)
            except Exception as e:
                pytest.fail('load_from_disk crashed on truncated _meta.json: %s' % e)


# ---- Random data fuzzing ----

class TestDataFuzzing:
    '''Fuzz field values through save/load to find serialization bugs.'''

    def _random_string(self, length=None):
        if length is None:
            length = random.randint(0, 500)
        chars = string.printable + 'àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ'
        return ''.join(random.choice(chars) for _ in range(length))

    def test_random_org_names_survive_persistence(self, app):
        '''Organization names with random unicode/special chars persist.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            random.seed(42)  # reproducible
            orgs = []
            for i in range(50):
                name = self._random_string(random.randint(1, 200))
                org = Organization(name=name, slug='org-%d' % i)
                org.save()
                orgs.append((org.id, name))

            save_to_disk(data_dir)
            MemoryStore().clear()
            load_from_disk(data_dir)

            for org_id, expected_name in orgs:
                loaded = Organization.get(org_id)
                assert loaded is not None, 'Lost org %d' % org_id
                assert loaded.name == expected_name, \
                    'Name mismatch for org %d: %r != %r' % (org_id, loaded.name, expected_name)

    def test_random_email_addresses_survive_persistence(self, app):
        '''User emails with various formats persist correctly.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            emails = [
                'simple@test.com',
                'very.long.email.address.with.many.dots@subdomain.example.co.uk',
                'user+tag@gmail.com',
                'münster@umlaut.de',
                '"quoted spaces"@test.com',
                'a@b.c',
                'UPPERCASE@TEST.COM',
            ]

            for email in emails:
                u = User(email=email)
                u.set_password('password123')
                u.save()

            save_to_disk(data_dir)
            MemoryStore().clear()
            load_from_disk(data_dir)

            for email in emails:
                assert User.get_by(email=email) is not None, \
                    'Lost user with email: %r' % email

    def test_extreme_decimal_values(self, app):
        '''Edge-case decimal values survive persistence.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            org = Organization(name='DecFuzz', slug='decfuzz')
            org.save()

            values = [
                Decimal('0'),
                Decimal('0.0'),
                Decimal('-1.00'),
                Decimal('0.00000001'),
                Decimal('99999999999999.99999999'),
                Decimal('1E+10'),
                Decimal('1E-10'),
            ]

            for i, val in enumerate(values):
                inv = Invoice(org_id=org.id, user_id=1,
                              customer_name='DecFuzz %d' % i,
                              currency='USD', status='draft',
                              total=val)
                inv.save()

            save_to_disk(data_dir)
            MemoryStore().clear()
            load_from_disk(data_dir)

            for i, val in enumerate(values):
                inv = Invoice.query.filter(customer_name='DecFuzz %d' % i).first()
                assert inv is not None, 'Lost invoice for value %s' % val
                assert inv.total == val, \
                    'Decimal mismatch: expected %s got %s' % (val, inv.total)

    def test_random_garbage_in_json_file(self, app):
        '''Random binary garbage in a JSON file doesn't crash load.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            org = _seed_data(app)
            save_to_disk(data_dir)

            random.seed(42)
            # Write random bytes to User.json
            garbage = bytes(random.randint(0, 255) for _ in range(1000))
            with open(os.path.join(data_dir, 'User.json'), 'wb') as f:
                f.write(garbage)

            MemoryStore().clear()
            load_from_disk(data_dir)
            assert User.query.count() == 0  # garbage, can't load
            assert Organization.query.count() == 1  # untouched


# ---- Concurrent write stress ----

class TestConcurrentWrites:
    '''Multiple threads writing simultaneously (e.g., AutoSaver + manual save).'''

    def test_concurrent_saves_dont_corrupt(self, app):
        '''Two threads saving at the same time don't produce corrupt files.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            org = _seed_data(app)
            errors = []

            def save_thread(thread_id):
                try:
                    for _ in range(30):
                        save_to_disk(data_dir)
                        time.sleep(random.uniform(0.001, 0.01))
                except Exception as e:
                    errors.append((thread_id, e))

            threads = [threading.Thread(target=save_thread, args=(i,)) for i in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)

            assert not errors, 'Concurrent save errors: %s' % errors

            # Verify data is still loadable after the stress
            MemoryStore().clear()
            load_from_disk(data_dir)
            _verify_baseline(org.id)

    def test_save_during_data_mutation(self, app):
        '''Saving while another thread modifies data doesn't crash.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            org = Organization(name='MutateTest', slug='mutate')
            org.save()
            for i in range(20):
                User(email='mut%d@test.com' % i, first_name='Mut%d' % i).save()

            errors = []
            stop = threading.Event()

            def mutate_loop():
                try:
                    counter = 0
                    while not stop.is_set():
                        u = User(email='new%d@test.com' % counter)
                        u.save()
                        counter += 1
                        time.sleep(0.005)
                except Exception as e:
                    errors.append(('mutate', e))

            def save_loop():
                try:
                    for _ in range(20):
                        save_to_disk(data_dir)
                        time.sleep(0.01)
                except Exception as e:
                    errors.append(('save', e))

            t_mut = threading.Thread(target=mutate_loop)
            t_save = threading.Thread(target=save_loop)
            t_mut.start()
            t_save.start()
            t_save.join(timeout=10)
            stop.set()
            t_mut.join(timeout=5)

            assert not errors, 'Concurrent mutation errors: %s' % errors

            # Final save and reload should work
            save_to_disk(data_dir)
            user_count = User.query.count()
            MemoryStore().clear()
            load_from_disk(data_dir)
            # Should have at least the original 20, maybe more from mutations
            assert User.query.count() >= 20


# ---- Recovery scenarios ----

class TestRecovery:
    '''Test that the app can recover from various bad states on disk.'''

    def test_extra_unknown_json_files_ignored(self, app):
        '''Unknown .json files in data dir don't interfere.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            org = _seed_data(app)
            save_to_disk(data_dir)

            # Drop extra files
            with open(os.path.join(data_dir, 'SomeNewModel.json'), 'w') as f:
                json.dump({'sequence': 1, 'rows': {'1': {'name': 'test'}}}, f)
            with open(os.path.join(data_dir, 'random_notes.json'), 'w') as f:
                json.dump({'random': 'data'}, f)

            MemoryStore().clear()
            load_from_disk(data_dir)
            _verify_baseline(org.id)

    def test_model_file_with_wrong_structure(self, app):
        '''A JSON file that parses but has wrong structure doesn't crash.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            org = _seed_data(app)
            save_to_disk(data_dir)

            # Replace User.json with valid JSON but wrong structure
            with open(os.path.join(data_dir, 'User.json'), 'w') as f:
                json.dump({'wrong_key': 'wrong_value'}, f)

            MemoryStore().clear()
            load_from_disk(data_dir)
            assert User.query.count() == 0  # couldn't parse rows
            assert Organization.query.count() == 1  # others intact

    def test_permissions_error_on_save_doesnt_crash(self, app):
        '''Read-only data dir doesn't crash save_to_disk.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            _seed_data(app)

            # Make a model file read-only
            user_file = os.path.join(data_dir, 'User.json')
            save_to_disk(data_dir)  # first save to create files

            os.chmod(user_file, 0o444)
            try:
                # Should not raise — logs error and continues
                save_to_disk(data_dir)
            finally:
                os.chmod(user_file, 0o644)  # restore for cleanup

    def test_sequence_preserved_across_save_load(self, app):
        '''Auto-increment sequences survive persistence.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            # Create users to advance the sequence
            for i in range(10):
                User(email='seq%d@test.com' % i).save()
            last_id = User.query.all()[-1].id

            save_to_disk(data_dir)
            MemoryStore().clear()
            load_from_disk(data_dir)

            # New user should get id > last_id
            new_user = User(email='after_reload@test.com')
            new_user.save()
            assert new_user.id > last_id, \
                'Sequence reset: new id %d <= last id %d' % (new_user.id, last_id)


# EOF
