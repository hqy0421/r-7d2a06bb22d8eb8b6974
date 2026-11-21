

class DatabaseException(Exception):
    def __init__(self, orig):
        self.orig = orig
        super(DatabaseException, self).__init__(orig)

    def __str__(self):
        return 'Orig exception: {}'.format(self.orig)


class TypeParseError(ValueError):
    """Raised when a ClickHouse column type string cannot be parsed.

    The offending column name and the original type spec are always
    attached so that callers can point at the exact reflected column
    instead of silently falling back to an unknown type.
    """

    def __init__(self, message, column=None, spec=None):
        self.raw_message = message
        self.column = column
        self.spec = spec
        super(TypeParseError, self).__init__(
            self._format(message, column, spec)
        )

    @staticmethod
    def _format(message, column, spec):
        full_message = message
        if column is not None:
            full_message = '%s (column %r' % (message, column)
            if spec is not None:
                full_message += ", type '%s'" % spec
            full_message += ')'
        elif spec is not None:
            full_message = "%s (type '%s')" % (message, spec)
        return full_message

    def with_context(self, column=None, spec=None):
        """Return a copy carrying the reflected column name and spec.

        Only fields that are currently missing are filled in, so an
        error that already carries column/spec context is not wrapped a
        second time.
        """
        return TypeParseError(
            self.raw_message,
            column=self.column if column is None else column,
            spec=self.spec if spec is None else spec,
        )
