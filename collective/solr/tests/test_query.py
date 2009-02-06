# -*- coding: utf-8 -*-

from unittest import TestCase, defaultTestLoader, main
from DateTime import DateTime
from zope.component import provideUtility

from collective.solr.interfaces import ISolrConnectionConfig
from collective.solr.manager import SolrConnectionConfig
from collective.solr.manager import SolrConnectionManager
from collective.solr.tests.utils import getData, fakehttp
from collective.solr.search import quote, Search
from collective.solr.search import quote2


class QuoteTests(TestCase):

    def testQuoting2(self):
        # http://lucene.apache.org/java/2_3_2/queryparsersyntax.html
        self.assertEqual(quote2(''), '')
        self.assertEqual(quote2(' '), '')
        self.assertEqual(quote2('foo'), 'foo')
        self.assertEqual(quote2('foo '), 'foo')
        self.assertEqual(quote2('"foo'), '\\"foo')
        self.assertEqual(quote2('foo"'), 'foo\\"')
        self.assertEqual(quote2('foo bar'), '(foo bar)')
        self.assertEqual(quote2('"foo bar"'), '"foo bar"')
        self.assertEqual(quote2('foo bar what?'), '(foo bar what?)')
        self.assertEqual(quote2('[]'), '')
        self.assertEqual(quote2('()'), '')
        self.assertEqual(quote2('{}'), '')
        self.assertEqual(quote2('...""'), '...\\"\\"')
        self.assertEqual(quote2('\\'), '\\\\') # Search for \ has to be quoted
        self.assertEqual(quote2('\?'), '\?')
        self.assertEqual(quote2('john@foo.com'), 'john@foo.com')

        # Fields
        self.assertEqual(quote2('"jakarta apache" jakarta'), '("jakarta apache" jakarta)')

        # Wildcard searches
        self.assertEqual(quote2('te?t'), 'te?t')
        self.assertEqual(quote2('test*'), 'test*')
        self.assertEqual(quote2('te*t'), 'te*t')
        self.assertEqual(quote2('?test'), 'test')
        self.assertEqual(quote2('*test'), 'test')

        # Fuzzy searches
        self.assertEqual(quote2('roam~'), 'roam~')
        self.assertEqual(quote2('roam~0.8'), 'roam~0.8')

        # Proximity searches
        self.assertEqual(quote2('"jakarta apache"~10'), '"jakarta apache"~10')

        # Range searches
        self.assertEqual(quote2('[* TO NOW]'), '[* TO NOW]')
        self.assertEqual(quote2('[1972-05-11T00:00:00.000Z TO *]'), '[1972-05-11T00:00:00.000Z TO *]')
        self.assertEqual(quote2('[1972-05-11T00:00:00.000Z TO 2011-05-10T01:30:00.000Z]'),
                                '[1972-05-11T00:00:00.000Z TO 2011-05-10T01:30:00.000Z]')
        self.assertEqual(quote2('[20020101 TO 20030101]'), '[20020101 TO 20030101]')
        self.assertEqual(quote2('{Aida TO Carmen}'), '{Aida TO Carmen}')
        self.assertEqual(quote2('{Aida TO}'), '{Aida TO *}')
        self.assertEqual(quote2('{TO Carmen}'), '{* TO Carmen}')

        # Boosting a term
        self.assertEqual(quote2('jakarta^4 apache'), '(jakarta^4 apache)')
        self.assertEqual(quote2('jakarta^0.2 apache'), '(jakarta^0.2 apache)')
        self.assertEqual(quote2('"jakarta apache"^4 "Apache Lucene"'), '("jakarta apache"^4 "Apache Lucene")')

        # Operators and grouping
        self.assertEqual(quote2('+return +"pink panther"'), '(+return +"pink panther")')
        self.assertEqual(quote2('+jakarta lucene'), '(+jakarta lucene)')
        self.assertEqual(quote2('"jakarta apache" -"Apache Lucene"'), '("jakarta apache" -"Apache Lucene")')
        self.assertEqual(quote2('"jakarta apache" NOT "Apache Lucene"'), '("jakarta apache" NOT "Apache Lucene")')
        self.assertEqual(quote2('"jakarta apache" OR jakarta'), '("jakarta apache" OR jakarta)')
        self.assertEqual(quote2('"jakarta apache" AND "Apache Lucene"'), '("jakarta apache" AND "Apache Lucene")')
        self.assertEqual(quote2('(jakarta OR apache) AND website'), '((jakarta OR apache) AND website)')
        self.assertEqual(quote2('(a AND (b OR c))'), '(a AND (b OR c))')
        self.assertEqual(quote2('((a AND b) OR c)'), '((a AND b) OR c)')

        # Escaping special characters
        self.assertEqual(quote2('-+&&||!^~:'), '\\-\\+\\&&\\||\\!\\^\\~\\:')
        # Only quote * and ? if quoted
        self.assertEqual(quote2('"*?"'), '"\\*\\?"')

        # Unicode
        self.assertEqual(quote2(u'john@foo.com'), 'john@foo.com')


    def testQuoting(self):
        self.assertEqual(quote('foo'), 'foo')
        self.assertEqual(quote('foo '), '"foo "')
        self.assertEqual(quote('"foo'), '"\\"foo"')
        self.assertEqual(quote('foo"'), '"foo\\""')
        self.assertEqual(quote('foo bar'), '"foo bar"')
        self.assertEqual(quote('foo bar what?'), '"foo bar what\?"')
        self.assertEqual(quote('[]'), '"\[\]"')
        self.assertEqual(quote('()'), '"\(\)"')
        self.assertEqual(quote('{}'), '"\{\}"')
        self.assertEqual(quote('...""'), '"...\\"\\""')
        self.assertEqual(quote('\\'), '"\\"')
        self.assertEqual(quote('-+&|!^~*?:'), '"\\-\\+\\&\\|\\!\\^\\~\\*\\?\\:"')
        self.assertEqual(quote('john@foo.com'), '"john@foo.com"')
        self.assertEqual(quote(' '), '" "')
        self.assertEqual(quote(''), '""')

    def testQuoted(self):
        self.assertEqual(quote('"'), '')
        self.assertEqual(quote('""'), '')
        self.assertEqual(quote('"foo"'), 'foo')
        self.assertEqual(quote('"foo*"'), 'foo*')
        self.assertEqual(quote('"+foo"'), '+foo')
        self.assertEqual(quote('"foo bar"'), 'foo bar')
        self.assertEqual(quote('"foo bar?"'), 'foo bar?')
        self.assertEqual(quote('"-foo +bar"'), '-foo +bar')
        self.assertEqual(quote('" "'), '" "')
        self.assertEqual(quote('""'), '')

    def testUnicode(self):
        self.assertEqual(quote('foø'), '"fo\xc3\xb8"')
        self.assertEqual(quote('"foø'), '"\\"fo\xc3\xb8"')
        self.assertEqual(quote('whät?'), '"wh\xc3\xa4t\?"')
        self.assertEqual(quote('[ø]'), '"\[\xc3\xb8\]"')
        self.assertEqual(quote('"foø*"'), 'fo\xc3\xb8*')
        self.assertEqual(quote('"foø bar?"'), 'fo\xc3\xb8 bar?')
        self.assertEqual(quote(u'john@foo.com'), '"john@foo.com"')


class QueryTests(TestCase):

    def setUp(self):
        provideUtility(SolrConnectionConfig(), ISolrConnectionConfig)
        self.mngr = SolrConnectionManager()
        self.mngr.setHost(active=True)
        conn = self.mngr.getConnection()
        fakehttp(conn, getData('schema.xml'))       # fake schema response
        self.mngr.getSchema()                       # read and cache the schema
        self.search = Search()
        self.search.manager = self.mngr

    def tearDown(self):
        self.mngr.closeConnection()
        self.mngr.setHost(active=False)

    def testSimpleQueries(self):
        bq = self.search.buildQuery
        self.assertEqual(bq('foo'), '+foo')
        self.assertEqual(bq('foo*'), '+"foo\\*"')
        self.assertEqual(bq('foo!'), '+"foo\\!"')
        self.assertEqual(bq('(foo)'), '+"\\(foo\\)"')
        self.assertEqual(bq('(foo...'), '+"\\(foo..."')
        self.assertEqual(bq('foo bar'), '+"foo bar"')
        self.assertEqual(bq('john@foo.com'), '+"john@foo.com"')
        self.assertEqual(bq(name='foo'), '+name:foo')
        self.assertEqual(bq(name='foo*'), '+name:"foo\\*"')
        self.assertEqual(bq(name='foo bar'), '+name:"foo bar"')
        self.assertEqual(bq(name='john@foo.com'), '+name:"john@foo.com"')
        self.assertEqual(bq(name=' '), '+name:" "')
        self.assertEqual(bq(name=''), '')

    def testMultiValueQueries(self):
        bq = self.search.buildQuery
        self.assertEqual(bq(('foo', 'bar')), '+(foo bar)')
        self.assertEqual(bq(('foo', 'bar*')), '+(foo "bar\\*")')
        self.assertEqual(bq(('foo bar', 'hmm')), '+("foo bar" hmm)')
        self.assertEqual(bq(name=['foo', 'bar']), '+name:(foo bar)')
        self.assertEqual(bq(name=['foo', 'bar*']), '+name:(foo "bar\\*")')
        self.assertEqual(bq(name=['foo bar', 'hmm']), '+name:("foo bar" hmm)')

    def testMultiArgumentQueries(self):
        bq = self.search.buildQuery
        self.assertEqual(bq('foo', name='bar'), '+foo +name:bar')
        self.assertEqual(bq('foo', name=('bar', 'hmm')), '+foo +name:(bar hmm)')
        self.assertEqual(bq(name='foo', cat='bar'), '+name:foo +cat:bar')
        self.assertEqual(bq(name='foo', cat=['bar', 'hmm']), '+name:foo +cat:(bar hmm)')
        self.assertEqual(bq('foo', name=' '), '+foo +name:" "')
        self.assertEqual(bq('foo', name=''), '+foo')

    def testInvalidArguments(self):
        bq = self.search.buildQuery
        self.assertEqual(bq(title='foo'), '')
        self.assertEqual(bq(title='foo', name='bar'), '+name:bar')
        self.assertEqual(bq('bar', title='foo'), '+bar')

    def testUnicodeArguments(self):
        bq = self.search.buildQuery
        self.assertEqual(bq(u'foo'), '+foo')
        self.assertEqual(bq(u'foø'), '+"fo\xc3\xb8"')
        self.assertEqual(bq(u'john@foo.com'), '+"john@foo.com"')
        self.assertEqual(bq(name=['foo', u'bar']), '+name:(foo bar)')
        self.assertEqual(bq(name=['foo', u'bär']), '+name:(foo "b\xc3\xa4r")')
        self.assertEqual(bq(name='foo', cat=(u'bar', 'hmm')), '+name:foo +cat:(bar hmm)')
        self.assertEqual(bq(name='foo', cat=(u'bär', 'hmm')), '+name:foo +cat:("b\xc3\xa4r" hmm)')
        self.assertEqual(bq(name=u'john@foo.com', cat='spammer'), '+name:"john@foo.com" +cat:spammer')

    def testQuotedQueries(self):
        bq = self.search.buildQuery
        self.assertEqual(bq('"foo"'), 'foo')
        self.assertEqual(bq('"foo*"'), 'foo*')
        self.assertEqual(bq('"+foo"'), '+foo')
        self.assertEqual(bq('"foo bar"'), 'foo bar')
        self.assertEqual(bq('"foo bar?"'), 'foo bar?')
        self.assertEqual(bq('"-foo +bar"'), '-foo +bar')
        self.assertEqual(bq(name='"foo"'), '+name:foo')
        self.assertEqual(bq(name='"foo bar'), '+name:"\\"foo bar"')
        self.assertEqual(bq(name='"foo bar*'), '+name:"\\"foo bar\\*"')
        self.assertEqual(bq(name='"-foo"', timestamp='"[* TO NOW]"'),
            '+timestamp:[* TO NOW] +name:-foo')
        self.assertEqual(bq(name='"john@foo.com"'), '+name:john@foo.com')
        self.assertEqual(bq(name='" "'), '+name:" "')
        self.assertEqual(bq(name='""'), '')

    def testComplexQueries(self):
        bq = self.search.buildQuery
        self.assertEqual(bq('foo', name='"herb*"', cat=(u'bär', '"-hmm"')),
            '+foo +name:herb* +cat:("b\xc3\xa4r" -hmm)')

    def testBooleanQueries(self):
        bq = self.search.buildQuery
        self.assertEqual(bq(inStock=True), '+inStock:true')
        self.assertEqual(bq(inStock=False), '+inStock:false')


class InactiveQueryTests(TestCase):

    def testUnavailableSchema(self):
        provideUtility(SolrConnectionConfig(), ISolrConnectionConfig)
        search = Search()
        search.manager = SolrConnectionManager()
        self.assertEqual(search.buildQuery('foo'), '')
        self.assertEqual(search.buildQuery(name='foo'), '')


class SearchTests(TestCase):

    def setUp(self):
        provideUtility(SolrConnectionConfig(), ISolrConnectionConfig)
        self.mngr = SolrConnectionManager()
        self.mngr.setHost(active=True)
        self.conn = self.mngr.getConnection()
        self.search = Search()
        self.search.manager = self.mngr

    def tearDown(self):
        self.mngr.closeConnection()
        self.mngr.setHost(active=False)

    def testSimpleSearch(self):
        schema = getData('schema.xml')
        search = getData('search_response.txt')
        request = getData('search_request.txt')
        output = fakehttp(self.conn, schema, search)    # fake responses
        query = self.search.buildQuery('"id:[* TO *]"')
        results = self.search(query, rows=10, wt='xml', indent='on')
        normalize = lambda x: sorted(x.split('&'))      # sort request params
        self.assertEqual(normalize(output.get(skip=1)), normalize(request))
        self.assertEqual(results.numFound, '1')
        self.assertEqual(len(results), 1)
        match = results[0]
        self.assertEqual(match.id, '500')
        self.assertEqual(match.name, 'python test doc')
        self.assertEqual(match.popularity, 0)
        self.assertEqual(match.sku, '500')
        self.assertEqual(match.timestamp, DateTime('2008-02-29 16:11:46.998 GMT'))


def test_suite():
    return defaultTestLoader.loadTestsFromName(__name__)

if __name__ == '__main__':
    main(defaultTest='test_suite')

