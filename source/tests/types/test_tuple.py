from unittest import TestCase

from clickhouse_sqlalchemy import types
from clickhouse_sqlalchemy.drivers.base import ClickHouseDialect
from clickhouse_sqlalchemy.exceptions import TypeParseError


class TupleReflectionTestCase(TestCase):
    """Offline coverage of DESCRIBE-style type string reflection."""

    def setUp(self):
        self.dialect = ClickHouseDialect()

    def parse(self, spec, column='orders'):
        return self.dialect._get_column_type(column, spec)

    def roundtrip(self, spec, column='orders'):
        return self.parse(spec, column).compile()

    # --- legacy / anonymous tuples stay compatible ----------------------

    def test_simple_anonymous_tuple(self):
        coltype = self.parse('Tuple(String, Int32)')
        self.assertIsInstance(coltype, types.Tuple)
        self.assertEqual(
            coltype.nested_types, (types.String, types.Int32)
        )
        self.assertEqual(coltype.compile(), 'Tuple(String, Int32)')

    def test_anonymous_tuple_preserves_order(self):
        coltype = self.parse('Tuple(UInt8, UInt16, UInt32, UInt64)')
        self.assertEqual(
            coltype.nested_types,
            (types.UInt8, types.UInt16, types.UInt32, types.UInt64)
        )

    def test_anonymous_tuple_with_nested_composite_types(self):
        spec = (
            'Tuple(Array(String), Map(String, String), '
            'Nullable(Int32), Decimal(18, 4))'
        )
        coltype = self.parse(spec)
        self.assertIsInstance(coltype.nested_types[0], types.Array)
        self.assertIsInstance(coltype.nested_types[1], types.Map)
        self.assertIsInstance(coltype.nested_types[2], types.Nullable)
        self.assertIsInstance(coltype.nested_types[3], types.Decimal)
        self.assertEqual(
            coltype.nested_types[3].precision, 18
        )
        self.assertEqual(coltype.nested_types[3].scale, 4)
        self.assertEqual(coltype.compile(), spec)

    # --- named tuples ----------------------------------------------------

    def test_named_tuple_preserves_names_order_and_types(self):
        coltype = self.parse('Tuple(id Int64, name String)')
        self.assertEqual(
            coltype.nested_types,
            (
                ('id', types.Int64),
                ('name', types.String),
            )
        )
        self.assertEqual(coltype.compile(), 'Tuple(id Int64, name String)')

    def test_named_tuple_with_array_map_nullable_decimal(self):
        spec = (
            'Tuple('
            'order_id UInt64, '
            'items Array(Tuple(sku String, price Decimal(18, 4), '
            'tags Map(String, Nullable(String)))), '
            'meta Map(String, String), '
            'note Nullable(String)'
            ')'
        )
        coltype = self.parse(spec)
        names = [name for name, _ in coltype.nested_types]
        self.assertEqual(names, ['order_id', 'items', 'meta', 'note'])

        items = coltype.nested_types[1][1].item_type
        self.assertEqual(
            [name for name, _ in items.nested_types],
            ['sku', 'price', 'tags']
        )
        price = items.nested_types[1][1]
        self.assertEqual((price.precision, price.scale), (18, 4))
        tags = items.nested_types[2][1]
        self.assertIsInstance(tags, types.Map)
        self.assertIsInstance(tags.value_type, types.Nullable)
        self.assertEqual(coltype.compile(), spec)

    def test_nested_named_inside_anonymous(self):
        coltype = self.parse(
            'Tuple(Tuple(a Int32, b String), Array(UInt64))'
        )
        self.assertIsInstance(coltype.nested_types[0], types.Tuple)
        self.assertEqual(
            coltype.nested_types[0].nested_types,
            (('a', types.Int32), ('b', types.String))
        )
        self.assertIsInstance(coltype.nested_types[1], types.Array)

    def test_named_tuple_inside_array_and_map(self):
        in_array = self.parse(
            'Array(Tuple(a Int32, b Nullable(Decimal(38, 10))))'
        )
        self.assertEqual(
            [n for n, _ in in_array.item_type.nested_types], ['a', 'b']
        )

        in_map = self.parse(
            'Map(String, Tuple(k String, v Decimal(10, 2)))'
        )
        self.assertEqual(
            [n for n, _ in in_map.value_type.nested_types], ['k', 'v']
        )

    def test_named_tuple_enum_with_commas(self):
        # The commas inside the Enum option list must not split elements.
        spec = "Tuple(status Enum8('new' = 1, 'done' = 2), amount Int64)"
        coltype = self.parse(spec)
        self.assertEqual(
            [name for name, _ in coltype.nested_types], ['status', 'amount']
        )
        # The Enum branch defers instantiation behind a lambda (existing
        # reflection behavior); compiling resolves it correctly.
        self.assertIn("Enum8('new' = 1, 'done' = 2)", coltype.compile())
        self.assertEqual(coltype.nested_types[1][1], types.Int64)

    # --- quoted identifiers ----------------------------------------------

    def test_quoted_names_with_spaces_and_commas(self):
        coltype = self.parse(
            'Tuple(`first name` String, `a, b` Int32, normal UInt8)'
        )
        self.assertEqual(
            [name for name, _ in coltype.nested_types],
            ['first name', 'a, b', 'normal']
        )
        # DDL re-quotes through the dialect identifier preparer.
        compiled = coltype.compile()
        self.assertIn('"first name" String', compiled)
        self.assertIn('"a, b" Int32', compiled)
        self.assertIn('normal UInt8', compiled)

    def test_double_quoted_input_names(self):
        coltype = self.parse(
            'Tuple("first name" String, "a, b" Int32)'
        )
        self.assertEqual(
            [name for name, _ in coltype.nested_types],
            ['first name', 'a, b']
        )

    def test_quoted_escape_sequences(self):
        coltype = self.parse(
            'Tuple(`q``x` String, `back\\`slash` UInt8)'
        )
        self.assertEqual(
            [name for name, _ in coltype.nested_types],
            ['q`x', 'back`slash']
        )

    def test_quote_roundtrip_through_full_ddl(self):
        from sqlalchemy import Column, MetaData
        from sqlalchemy.schema import CreateTable
        from clickhouse_sqlalchemy import engines, Table

        metadata = MetaData()
        table = Table(
            'orders', metadata,
            Column('order', types.Tuple(
                ('first name', types.String),
                ('a, b', types.Int32),
            )),
            engines.Memory()
        )
        ddl = str(
            CreateTable(table).compile(dialect=self.dialect)
        ).replace('\n', ' ')
        self.assertIn('Tuple("first name" String, "a, b" Int32)', ddl)

    # --- error reporting --------------------------------------------------

    def _assert_parse_error(self, spec, column='bad_col'):
        with self.assertRaises(TypeParseError) as ctx:
            self.parse(spec, column)
        self.assertEqual(ctx.exception.column, column)
        self.assertEqual(ctx.exception.spec, spec)
        return str(ctx.exception)

    def test_mixed_named_and_anonymous_names_column(self):
        message = self._assert_parse_error('Tuple(a Int32, String)')
        self.assertIn('all named or all anonymous', message)
        self.assertIn("'a'", message)

        message = self._assert_parse_error('Tuple(String, b Int32)')
        self.assertIn('all named or all anonymous', message)
        self.assertIn("'b'", message)

    def test_duplicate_element_names_report_column(self):
        message = self._assert_parse_error(
            'Tuple(a Int32, a String, a UInt8)'
        )
        self.assertIn('duplicate Tuple element name', message)
        self.assertIn("'a'", message)

    def test_unclosed_parenthesis_reports_column(self):
        message = self._assert_parse_error('Tuple(a Int32, b String')
        self.assertIn("missing closing ')'", message)

    def test_stray_closing_parenthesis_reports_column(self):
        message = self._assert_parse_error('Tuple(a Int32))')
        self.assertIn('trailing data', message)

    def test_unterminated_quoted_identifier_reports_column(self):
        message = self._assert_parse_error(
            'Tuple(`unclosed String, Int32)'
        )
        self.assertIn('unterminated quoted identifier', message)

    def test_map_requires_two_parameters(self):
        message = self._assert_parse_error('Map(String)')
        self.assertIn('exactly two parameters', message)

    def test_trailing_comma_raises(self):
        # ClickHouse itself rejects a trailing comma, so do not silently
        # drop the empty element.
        message = self._assert_parse_error('Tuple(a Int32,)')
        self.assertIn('empty element', message)

    def test_map_value_type_with_nested_commas(self):
        coltype = self.parse(
            'Map(String, Map(String, Tuple(a Int32, b Decimal(5, 2))))'
        )
        self.assertIsInstance(coltype.value_type, types.Map)
        self.assertEqual(
            [n for n, _ in coltype.value_type.value_type.nested_types],
            ['a', 'b']
        )

    def test_no_silent_null_type_fallback(self):
        # Every failure must raise rather than degrade into NullType.
        for spec in (
            'Tuple(a Int32, String)',
            'Tuple(a Int32, a String)',
            'Tuple(a Int32',
        ):
            with self.assertRaises(TypeParseError):
                self.parse(spec)

    def test_column_info_carries_name_and_nullable_flag(self):
        info = self.dialect._get_column_info(
            'order',
            'Nullable(Tuple(a String, b Int32))',
            None, None, None
        )
        self.assertEqual(info['name'], 'order')
        self.assertTrue(info['nullable'])
        self.assertIsInstance(info['type'], types.Nullable)
        self.assertIsInstance(info['type'].nested_type, types.Tuple)


class TupleConstructionTestCase(TestCase):
    def test_anonymous_construction_still_supported(self):
        t = types.Tuple(types.String, types.Int32)
        self.assertFalse(t.is_named)
        self.assertEqual(t.compile(), 'Tuple(String, Int32)')

    def test_named_construction_still_supported(self):
        t = types.Tuple(
            ('name', types.String), ('value', types.Float32)
        )
        self.assertTrue(t.is_named)
        self.assertEqual(t.compile(), 'Tuple(name String, value Float32)')

    def test_mixed_construction_raises(self):
        with self.assertRaises(TypeParseError):
            types.Tuple(('name', types.String), types.Int32)

    def test_duplicate_names_construction_raises(self):
        with self.assertRaises(TypeParseError):
            types.Tuple(
                ('a', types.Int32), ('a', types.String)
            )

    def test_empty_tuple_raises(self):
        with self.assertRaises(ValueError):
            types.Tuple()

    def test_malformed_named_pair_raises(self):
        with self.assertRaises(TypeParseError):
            types.Tuple(('a',))
