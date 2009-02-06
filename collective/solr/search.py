from logging import getLogger
from zope.interface import implements
from zope.component import queryUtility
from re import compile

from collective.solr.interfaces import ISolrConnectionConfig
from collective.solr.interfaces import ISolrConnectionManager
from collective.solr.interfaces import ISearch
from collective.solr.parser import SolrResponse
from collective.solr.exceptions import SolrInactiveException

logger = getLogger('collective.solr.search')


# Solr/lucene reserved characters/terms: + - && || ! ( ) { } [ ] ^ " ~ * ? : \
# Four groups for tokenizer:
# 1) Whitespace (\s+)
# 2) Any non reserved characters ([^(){}\[\]+\-!^\"~*?:\\\&\|\s]+)
# 3) Any grouping characters ([(){}\[\]\"])
# 4) Any special operators ([+\-!^~*?:\\\]|\&\&|\|\|))
query_tokenizer = compile("(?:(\s+)|([^(){}\[\]+\-!^\"~*?:\\\&\|\s]+)|([(){}\[\]\"])|([+\-!^~*?:\\\]|\&\&|\|\|))")

class Whitespace(object):
    def __nonzero__(self):
        return False
    def __str__(self):
        return ' '

class Group(list):
    def __init__(self, start=None, end=None):
        self.start = start
        self.end = end
    def __str__(self):
        res = [x for x in self if x]
        lenres = len(res)
        if lenres == 0:
            return ''
        elif lenres == 1:
            return str(res[0])
        # Otherwise, also print whitespace
        return '%s%s%s' % (self.start, ''.join([str(x) for x in self]), self.end)

class Quote(Group):
    def __str__(self):
        if len(self) == 0:
            return '\\%s' % self.start
        elif len(self) == 1:
            return '\\%s%s' % (self.start, ''.join(self))
        return '%s%s%s' % (self.start, ''.join([str(x) for x in self]), self.end)

class Range(Group):
    def __str__(self):
        first=last='*'
        if len(self) == 0:
            return ''
        if 'TO' in self:
            # split on 'TO'
            split = self.index('TO')
            if split > 0:
                first = ''.join([str(x) for x in self[:split]])
            if split < (len(self)-1):
                last = ''.join([str(x) for x in self[split+1:]])
        else:
            first=''.join([str(x) for x in self])
        return '%s%s TO %s%s' % (self.start, first, last, self.end)

class Stack(list):
    def __init__(self):
        self.append([])
    def add(self, item):
        self.current.append(item)
        self.append(item)        
    @property
    def current(self):
        return self[-1]
    def __str__(self):
        return ''.join([str(x) for x in self[0]])

def quote2(term):
    if isinstance(term, unicode):
        term = term.encode('utf-8')
    stack = Stack()
    tokens = query_tokenizer.findall(term)
    # Counter enables lookahead
    i = 0
    stop = len(tokens)
    while i<stop:
        whitespace, text, grouping, special = tokens[i]

        if whitespace:
            # Add whitespace if quoted text
            if isinstance(stack.current, Quote):
                stack.current.append(Whitespace())
            elif isinstance(stack.current, Range):
                pass
            elif isinstance(stack.current, list):
                # We have whitespace with no grouping, insert group
                new = Group('(',')')
                new.extend(stack.current)
                new.append(Whitespace())
                stack.current[:] = []
                stack.add(new)

        elif grouping:
            # [] (inclusive range), {} (exclusive range), always with TO inside,
            # () group
            # "" for quotes
            if grouping == '"':
                if isinstance(stack.current, Quote):
                    # Handle empty double quote
                    if not stack.current:
                        stack.current.append('\\"')
                    stack.pop()
                else:
                    new = Quote(start='"', end='"')
                    stack.add(new)
            elif grouping in '[{':
                new = Range(start=grouping, end={'[':']','{':'}'}[grouping])
                stack.add(new)
            elif grouping == '(':
                new = Group(start='(',end=')')
                stack.add(new)
            elif grouping in ']})':
                stack.pop()

        elif text:
            stack.current.append(text)

        elif special:
            if isinstance(stack.current, Quote):
                stack.current.append('\\%s'%special)
            elif special == '\\':
                # Inspect next to see if it's quoted special
                if (i+1)<stop:
                    _, _, _, s2 = tokens[i+1]
                    if s2:
                        stack.current.append('%s%s' % (special, s2))
                        # Jump ahead
                        i+=1
                    else:
                        # Quote it
                        stack.current.append('\\%s' % special)
                else:
                    # Quote it
                    stack.current.append('\\\\')
            elif special in '+-':
                if (i+1)<stop:
                    _, t2, g2, _ = tokens[i+1]
                    # We allow + and - in front of phrase and text
                    if t2 or g2 == '"':
                        stack.current.append(special)
                    else:
                        # Quote it
                        stack.current.append('\\%s' % special)
            elif special in '~^':
                # Fuzzy or proximity is always after a term or phrase, and sometimes before int or float
                # like roam~0.8 or "jakarta apache"~10
                if i>0:
                    _, t0, g0, _ = tokens[i-1]
                    if t0 or g0 == '"':
                        # Look ahead to check for integer or float

                        if (i+1)<stop:
                            _, t2, _, _ = tokens[i+1]
                            try: # float(t2) might fail
                                if t2 and float(t2):
                                    stack.current.append('%s%s' % (special, t2))
                                    # Jump ahead
                                    i+=1
                                else:
                                    stack.current.append(special)
                            except ValueError:
                                stack.current.append(special)
                        else:# (i+1)<stop
                            stack.current.append(special)
                    else:# t0 or g0 == '"'
                        stack.current.append('\\%s'%special)
                else:# i>0
                    stack.current.append('\\%s'%special)
            elif special in '?*':
                # ? and * can not be the first characters of a search
                if stack.current:
                    stack.current.append(special)
            elif isinstance(stack.current, Group):
                stack.current.append(special)
            elif isinstance(stack.current, list):
                stack.current.append('\\%s'%special)
        i += 1
    return str(stack)


word = compile('^\w+$')
white = compile('^\s+$')
special = compile('([-+&|!(){}[\]^"~*?\\:])')

def quote(term):
    """ quote a given term according to the solr/lucene query syntax;
        see http://lucene.apache.org/java/docs/queryparsersyntax.html """
    if isinstance(term, unicode):
        term = term.encode('utf-8')
    if term.startswith('"') and term.endswith('"'):
        term = term[1:-1]
        if white.match(term):
            term = '"%s"' % term
    elif not word.match(term):
        term = '"%s"' % special.sub(r'\\\1', term)
    return term


class Search(object):
    """ a search utility for solr """
    implements(ISearch)

    def __init__(self):
        self.manager = None

    def getManager(self):
        if self.manager is None:
            self.manager = queryUtility(ISolrConnectionManager)
        return self.manager

    def search(self, query, **parameters):
        """ perform a search with the given querystring and parameters """
        manager = self.getManager()
        manager.setSearchTimeout()
        connection = manager.getConnection()
        if connection is None:
            raise SolrInactiveException
        if not parameters.has_key('rows'):
            config = queryUtility(ISolrConnectionConfig)
            parameters['rows'] = config.max_results or ''
        logger.debug('searching for %r (%r)', query, parameters)
        response = connection.search(q=query, **parameters)
        return getattr(SolrResponse(response), 'response', [])

    __call__ = search

    def buildQuery(self, default=None, **args):
        """ helper to build a querystring for simple use-cases """
        logger.debug('building query for "%r", %r', default, args)
        schema = self.getManager().getSchema() or {}
        defaultSearchField = getattr(schema, 'defaultSearchField', None)
        args[None] = default
        query = []
        for name, value in args.items():
            field = schema.get(name or defaultSearchField, None)
            if field is None or not field.indexed:
                logger.debug('dropping unknown search attribute "%s" (%r)', name, value)
                continue
            if isinstance(value, bool):
                quoted = False
                value = str(value).lower()
            elif not value:     # solr doesn't like empty fields (+foo:"")
                continue
            elif isinstance(value, (tuple, list)):
                quoted = False
                value = '(%s)' % ' '.join(map(quote, value))
            elif isinstance(value, basestring):
                quoted = value.startswith('"') and value.endswith('"')
                value = quote(value)
                if not value:       # don't search for an empty string, even if quoted
                    continue
            else:
                logger.info('skipping unsupported value "%r" (%s)', value, name)
                continue
            if name is None:
                if not quoted:      # don't prefix when value was quoted...
                    value = '+%s' % value
                query.append(value)
            else:
                query.append('+%s:%s' % (name, value))
        query = ' '.join(query)
        logger.debug('built query "%s"', query)
        return query

