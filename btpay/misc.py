#
# Small utilities
#
import sys

DEV_MODE = ('darwin' in sys.platform)
IS_PRODUCTION = not DEV_MODE

def singleton(cls):
    '''Decorator: make a class into a singleton.'''
    instances = {}
    def get_instance(*args, **kwargs):
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]
    get_instance._cls = cls
    return get_instance

def get_subclasses(cls):
    '''Recursively find all subclasses of a class.'''
    result = []
    for sc in cls.__subclasses__():
        result.append(sc)
        result.extend(get_subclasses(sc))
    return result

def u8(x):
    '''Ensure something is a UTF-8 string.'''
    if isinstance(x, bytes):
        return x.decode('utf-8')
    return str(x)

# EOF
