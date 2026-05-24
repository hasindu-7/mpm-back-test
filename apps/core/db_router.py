import threading
import sys

_thread_locals = threading.local()

def set_tenant_db(db_name):
    _thread_locals.tenant_db = db_name

def get_tenant_db():
    return getattr(_thread_locals, 'tenant_db', 'default')

class TenantRouter:
    """
    A router to control all database operations on models in the
    tenant-specific applications.
    """

    def db_for_read(self, model, **hints):
        if model._meta.app_label == 'core':
            return 'default'
        return get_tenant_db()

    def db_for_write(self, model, **hints):
        if model._meta.app_label == 'core':
            return 'default'
        return get_tenant_db()

    def allow_relation(self, obj1, obj2, **hints):
        if obj1._meta.app_label == 'core' or obj2._meta.app_label == 'core':
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if 'test' in sys.argv:
            return True
        if app_label == 'core':
            return db == 'default'
        return db != 'default'

