from unittest import TestCase

from clickhouse_sqlalchemy.drivers.util import (
    get_inner_spec, parse_arguments, parse_type_spec, _split_top_level,
    _unquote_identifier,
)
from clickhouse_sqlalchemy.exceptions import TypeParseError


class GetInnerSpecTestCase(TestCase):
    def test_get_inner_spec(self):
        self.assertEqual(
            get_inner_spec("DateTime('Europe/Paris')"), "'Europe/Paris'"
        )
        self.assertEqual(get_inner_spec('Decimal(18, 2)'), "18, 2")
        self.assertEqual(get_inner_spec('DateTime64(3)'), "3")


class ParseArgumentsTestCase(TestCase):
    def test_parse_arguments(self):
        self.assertEqual(
            parse_arguments('uniq, UInt64'), ('uniq', 'UInt64')
        )
        self.assertEqual(
            parse_arguments('anyIf, String, UInt8'),
            ('anyIf', 'String', 'UInt8')
        )
        self.assertEqual(
            parse_arguments('quantiles(0.5, 0.9), UInt64'),
            ('quantiles(0.5, 0.9)', 'UInt64')
        )
        self.assertEqual(
            parse_arguments('sum, Int64, Int64'), ('sum', 'Int64', 'Int64')
        )
        self.assertEqual(
            parse_arguments('sum, Nullable(Int64), Int64'),
            ('sum', 'Nullable(Int64)', 'Int64')
        )
        self.assertEqual(
            parse_arguments('Float32, Decimal(18, 2)'),
            ('Float32', 'Decimal(18, 2)')
        )
        self.assertEqual(
            parse_arguments('sum, Float32, Decimal(18, 2)'),
            ('sum', 'Float32', 'Decimal(18, 2)')
        )
        self.assertEqual(
            parse_arguments('quantiles(0.5, 0.9), UInt64'),
            ('quantiles(0.5, 0.9)', 'UInt64')
        )
        self.assertEqual(
            parse_arguments("sumIf(total, status = 'accepted'), Float32"),
            ("sumIf(total, status = 'accepted')", "Float32")
        )


class SplitTopLevelTestCase(TestCase):
    def test_splits_on_top_level_commas(self):
        self.assertEqual(
            _split_top_level('String, Int32, UInt64'),
            ['String', 'Int32', 'UInt64']
        )

    def test_respects_nested_parentheses(self):
        self.assertEqual(
            _split_top_level(
                'Array(Tuple(a String, b Int32)), Decimal(18, 4), String'
            ),
            ['Array(Tuple(a String, b Int32))', 'Decimal(18, 4)', 'String']
        )

    def test_respects_quoted_identifiers(self):
        self.assertEqual(
            _split_top_level('`a, b` String, `c d` Int32'),
            ['`a, b` String', '`c d` Int32']
        )
        self.assertEqual(
            _split_top_level('"a, b" String, "c d" Int32'),
            ['"a, b" String', '"c d" Int32']
        )

    def test_unbalanced_parentheses_raise(self):
        with self.assertRaises(TypeParseError):
            _split_top_level('Tuple(a Int32, b String')

    def test_empty_elements_raise_by_default(self):
        # Trailing and doubled separators are invalid ClickHouse syntax.
        with self.assertRaises(TypeParseError):
            _split_top_level('a Int32,')
        with self.assertRaises(TypeParseError):
            _split_top_level('a Int32,, b String')

    def test_allow_empty_keeps_blank_parts_out(self):
        self.assertEqual(
            _split_top_level('a Int32,', allow_empty=True), ['a Int32']
        )

    def test_unterminated_quote_raises(self):
        with self.assertRaises(TypeParseError):
            _split_top_level('`unclosed String, Int32')


class UnquoteIdentifierTestCase(TestCase):
    def test_unquoted_unchanged(self):
        self.assertEqual(_unquote_identifier('simple'), 'simple')

    def test_backtick_and_double_quote(self):
        self.assertEqual(_unquote_identifier('`first name`'), 'first name')
        self.assertEqual(_unquote_identifier('"first name"'), 'first name')

    def test_doubled_quote_escape(self):
        self.assertEqual(_unquote_identifier('`q``x`'), 'q`x')
        self.assertEqual(_unquote_identifier('"q""x"'), 'q"x')

    def test_backslash_escape(self):
        self.assertEqual(_unquote_identifier(r'`back\`slash`'), 'back`slash')
        self.assertEqual(_unquote_identifier(r'"back\"slash"'), 'back"slash')


class ParseTypeSpecTestCase(TestCase):
    def test_simple_type(self):
        self.assertEqual(parse_type_spec('Int32'), 'Int32')

    def test_anonymous_tuple(self):
        self.assertEqual(
            parse_type_spec('Tuple(String, Int32)'),
            ['String', 'Int32']
        )

    def test_nested_anonymous_tuple(self):
        self.assertEqual(
            parse_type_spec('Tuple(Tuple(String, Int32), UInt8)'),
            ['Tuple(String, Int32)', 'UInt8']
        )

    def test_named_tuple_returns_name_spec_pairs(self):
        self.assertEqual(
            parse_type_spec('Tuple(id Int64, name String)'),
            [('id', 'Int64'), ('name', 'String')]
        )

    def test_named_tuple_keeps_complex_element_specs_raw(self):
        result = parse_type_spec(
            'Tuple(items Array(Tuple(sku String, price Decimal(18, 4))), '
            'note Nullable(String))'
        )
        self.assertEqual(result[0][0], 'items')
        self.assertEqual(
            result[0][1],
            'Array(Tuple(sku String, price Decimal(18, 4)))'
        )
        self.assertEqual(result[1], ('note', 'Nullable(String)'))

    def test_quoted_named_elements(self):
        self.assertEqual(
            parse_type_spec('Tuple(`first name` String, `a, b` Int32)'),
            [('first name', 'String'), ('a, b', 'Int32')]
        )

    def test_mixed_named_anonymous_raises_with_column(self):
        with self.assertRaises(TypeParseError) as ctx:
            parse_type_spec('Tuple(a Int32, String)', column='orders')
        self.assertEqual(ctx.exception.column, 'orders')
        self.assertIn('all named or all anonymous', str(ctx.exception))

    def test_duplicate_names_raise_with_column(self):
        with self.assertRaises(TypeParseError) as ctx:
            parse_type_spec('Tuple(a Int32, a String)', column='orders')
        self.assertEqual(ctx.exception.column, 'orders')
        self.assertIn('duplicate', str(ctx.exception))

    def test_unclosed_parenthesis_raises(self):
        with self.assertRaises(TypeParseError):
            parse_type_spec('Tuple(a Int32, b String', column='orders')

    def test_trailing_data_raises(self):
        with self.assertRaises(TypeParseError):
            parse_type_spec('Tuple(a Int32))', column='orders')

    def test_empty_tuple_raises(self):
        with self.assertRaises(TypeParseError):
            parse_type_spec('Tuple()', column='orders')

    def test_non_tuple_parameterized_type_returned_verbatim(self):
        self.assertEqual(
            parse_type_spec('Decimal(18, 4)'), 'Decimal(18, 4)'
        )
        self.assertEqual(
            parse_type_spec("DateTime64(3, 'UTC')"),
            "DateTime64(3, 'UTC')"
        )
