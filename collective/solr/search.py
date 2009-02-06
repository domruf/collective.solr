from logging import getLogger
from zope.interface import implements
from zope.component import queryUtility
from re import compile

from collective.solr.interfaces import ISolrConnectionConfig
from collective.solr.interfaces import ISolrConnectionManager
from collective.solr.interfaces import ISearch
from collective.solr.parser import SolrResponse
from collective.solr.exceptions import SolrInactiveException
from collective.solr.queryparser import quote

logger = getLogger('collective.solr.search')


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
                value = str(value).lower()
            elif not value:     # solr doesn't like empty fields (+foo:"")
                continue
            elif isinstance(value, (tuple, list)):
                value = '(%s)' % ' '.join(map(quote, value))
            elif isinstance(value, basestring):
                value = quote(value)
                if not value:       # don't search for an empty string, even if quoted
                    continue
            else:
                logger.info('skipping unsupported value "%r" (%s)', value, name)
                continue
            if name is None:
                if value and value[0] not in '+-':
                    value = '+%s' % value
                query.append(value)
            else:
                query.append('+%s:%s' % (name, value))
        query = ' '.join(query)
        logger.debug('built query "%s"', query)
        return query

