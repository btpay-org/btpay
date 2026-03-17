
class DictObj(dict):
    '''
        Act like a dictionary, but also an object. Keys are attributes, attributes
        are keys and so on.
    '''

    def __getattr__(self, name):
        if name in self:
            return self[name]
        else:
            raise AttributeError('No such attribute: %s\nKnown attrs: %s'
                                        % (name, ', '.join(self.keys())))

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        del self[name]

    def __repr__(self):
        ret = '[%s:' % self.__class__.__name__
        for k, v in self.items():
            ret += ' %s=%r' % (k, v)
        return ret + ']'

# EOF
