from ..exceptions import TypeParseError


def get_inner_spec(spec):
    brackets = 0
    offset = spec.find('(')
    if offset == -1:
        return ''
    i = offset
    for i, ch in enumerate(spec[offset:], offset):
        if ch == '(':
            brackets += 1

        elif ch == ')':
            brackets -= 1

        if brackets == 0:
            break

    return spec[offset + 1:i]


def parse_arguments(param_string):
    """
    Given a string of function arguments, parse them into a tuple.
    """
    params = []
    bracket_level = 0
    current_param = ''

    for char in param_string:
        if char == '(':
            bracket_level += 1
        elif char == ')':
            bracket_level -= 1
        elif char == ',' and bracket_level == 0:
            params.append(current_param.strip())
            current_param = ''
            continue

        current_param += char

    # Append the last parameter
    if current_param:
        params.append(current_param.strip())

    return tuple(params)


def _scan_quoted(text, start, column=None, context_spec=None):
    """Scan a quoted identifier starting at ``text[start]``.

    Both backtick (``` `name` ```) and double quote (``"name"``)
    delimiters are accepted. Inside the identifier, the quote can be
    escaped either with a backslash (``\\```) or by doubling it
    (`` ``` ``).

    Returns the index just past the closing quote. Raises
    :class:`TypeParseError` if the identifier is unterminated.
    """
    quote = text[start]
    i = start + 1
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == '\\' and i + 1 < length:
            i += 2
            continue
        if ch == quote:
            if i + 1 < length and text[i + 1] == quote:
                # Doubled quote: one literal quote character.
                i += 2
                continue
            return i + 1
        i += 1
    raise TypeParseError(
        "unterminated quoted identifier (missing closing %r)" % quote,
        column=column, spec=context_spec if context_spec is not None else text,
    )


def _split_top_level(spec, separator=',', column=None, context_spec=None,
                     allow_empty=False):
    """Split ``spec`` on top-level occurrences of ``separator``.

    Parentheses and quoted identifiers (`` `name` `` or ``"name"``) are
    respected, so separators inside nested types, string literals and
    quoted names are never treated as split points. Whitespace around
    each part is stripped.

    By default an empty part (a trailing or doubled separator) raises
    :class:`TypeParseError`, matching ClickHouse's own grammar. Pass
    ``allow_empty`` to keep empty parts out silently instead.

    ``context_spec`` is the outermost type string attached to errors,
    which may be wider than the ``spec`` fragment being split.
    """
    error_spec = context_spec if context_spec is not None else spec
    parts = []
    current = []
    depth = 0
    i = 0
    length = len(spec)

    def flush():
        part = ''.join(current).strip()
        if part:
            parts.append(part)
        elif not allow_empty:
            raise TypeParseError(
                "empty element around %r separator" % separator,
                column=column, spec=error_spec,
            )

    while i < length:
        ch = spec[i]

        if ch in ('`', '"'):
            end = _scan_quoted(spec, i, column, error_spec)
            current.append(spec[i:end])
            i = end
            continue
        elif ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth -= 1
            if depth < 0:
                raise TypeParseError(
                    "unexpected ')'", column=column, spec=error_spec
                )
            current.append(ch)
        elif ch == separator and depth == 0:
            flush()
            current = []
        else:
            current.append(ch)
        i += 1

    if depth != 0:
        raise TypeParseError(
            "unbalanced parentheses: %d unclosed '('" % depth,
            column=column, spec=error_spec,
        )

    flush()
    return parts


def _unquote_identifier(token):
    """Return the bare identifier for a quoted or unquoted token.

    `` `a b` `` and ``"a b"`` both become ``a b`` (with backslash and
    doubled-quote escape sequences resolved); an unquoted token is
    returned unchanged.
    """
    if not _is_quoted(token):
        return token
    quote = token[0]
    result = []
    i = 1
    end = len(token) - 1
    while i < end:
        ch = token[i]
        if ch == '\\' and i + 1 < end:
            result.append(token[i + 1])
            i += 2
            continue
        if ch == quote and i + 1 < end and token[i + 1] == quote:
            result.append(quote)
            i += 2
            continue
        result.append(ch)
        i += 1
    return ''.join(result)


def _is_quoted(token):
    return (
        len(token) >= 2
        and token[0] in ('`', '"')
        and token[-1] == token[0]
    )


def parse_type_spec(spec, column=None):
    """Parse a ClickHouse column type string.

    Returns a tuple ``(name, type_spec)`` for elements of a named
    ``Tuple`` and a plain type string for every other type. Named and
    anonymous tuple elements are recognized at every nesting level.

    :raises TypeParseError: on unbalanced parentheses, unterminated
        quotes or mixed/duplicate tuple element names. The reflected
        column name is attached to every raised error.
    """
    spec = spec.strip()
    if not spec:
        raise TypeParseError("empty type specification", column=column)

    open_pos = spec.find('(')
    if open_pos == -1:
        # Simple type without parameters, e.g. ``Int32`` or ``String``.
        return spec

    type_name = spec[:open_pos].strip()
    if not type_name:
        raise TypeParseError(
            "missing type name before '('", column=column, spec=spec
        )

    # Walk the whole spec so an unclosed '(' or stray ')' is reported
    # instead of being silently accepted.
    depth = 0
    close_pos = -1
    i = open_pos
    while i < len(spec):
        ch = spec[i]
        if ch in ('`', '"'):
            i = _scan_quoted(spec, i, column, spec)
            continue
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                raise TypeParseError(
                    "unexpected ')' in type %r" % type_name,
                    column=column, spec=spec,
                )
            if depth == 0:
                close_pos = i
                break
        i += 1

    if close_pos == -1 or depth != 0:
        raise TypeParseError(
            "missing closing ')' for type %r" % type_name,
            column=column, spec=spec,
        )
    # Anything after the closing parenthesis is malformed for a type.
    if spec[close_pos + 1:].strip():
        raise TypeParseError(
            "unexpected trailing data after type %r" % type_name,
            column=column, spec=spec,
        )

    inner = spec[open_pos + 1:close_pos]

    if type_name == 'Tuple':
        return _parse_tuple_elements(inner, column, spec)

    # Non-tuple parameterized types keep their raw inner spec; the
    # individual callers (Decimal, DateTime64, Enum, ...) parse it.
    return spec


def _parse_tuple_elements(inner, column, full_spec):
    raw_elements = _split_top_level(
        inner, column=column, context_spec=full_spec
    )
    if not raw_elements:
        # ``Tuple()`` is not a valid ClickHouse type.
        raise TypeParseError(
            "Tuple must contain at least one element",
            column=column, spec=full_spec,
        )

    elements = []
    named_flags = []
    for index, element in enumerate(raw_elements):
        name, type_spec = _split_named_element(element, full_spec, column)
        named_flags.append(name is not None)
        elements.append((index, name, type_spec))

    if not all(named_flags) and any(named_flags):
        mixed = [
            str(index + 1) if name is None else repr(name)
            for index, name, _ in elements
        ]
        raise TypeParseError(
            "Tuple elements must be either all named or all anonymous, "
            "got mixed elements at positions %s" % ', '.join(mixed),
            column=column, spec=full_spec,
        )

    if all(named_flags):
        seen = set()
        duplicates = []
        for _, name, _ in elements:
            if name in seen and name not in duplicates:
                duplicates.append(name)
            seen.add(name)
        if duplicates:
            raise TypeParseError(
                "duplicate Tuple element name(s): %s"
                % ', '.join(repr(name) for name in duplicates),
                column=column, spec=full_spec,
            )
        # Keep the element type specs as raw strings: the dialect's
        # type dispatch parses them, preserving element order and names.
        return [
            (name, type_spec) for _, name, type_spec in elements
        ]

    return [type_spec for _, _, type_spec in elements]


def _split_named_element(element, full_spec, column):
    """Split one tuple element into ``(name, type_spec)``.

    Returns ``(None, element)`` for anonymous elements. The name may be
    a quoted identifier (`` `first name` Type ``) or a bare identifier
    (``name Type``); the remaining part must itself be a type.
    """
    first_end = _first_token_end(element, column, full_spec)
    first_token = element[:first_end].strip()
    rest = element[first_end:].strip()

    if not rest:
        # Single token: anonymous element (or an invalid named one).
        return None, element

    if _is_quoted(first_token):
        name = _unquote_identifier(first_token)
        return name, rest

    # Bare identifiers only count as names when followed by another
    # type, i.e. they must look like plain identifiers themselves.
    if _is_simple_identifier(first_token):
        return first_token, rest

    # Something like ``Nullable(Int32)`` split off a parameter, meaning
    # this is an anonymous element with extra tokens, which is invalid.
    raise TypeParseError(
        "cannot parse Tuple element %r" % element,
        column=column, spec=full_spec,
    )


def _first_token_end(text, column=None, context_spec=None):
    """Length of the first token, honoring quotes and parentheses."""
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch in ('`', '"'):
            i = _scan_quoted(text, i, column, context_spec)
            continue
        elif ch == '(':
            depth = 1
            i += 1
            while i < length and depth:
                if text[i] == '(':
                    depth += 1
                elif text[i] == ')':
                    depth -= 1
                i += 1
            continue
        elif ch.isspace():
            break
        i += 1
    return i


def _is_simple_identifier(token):
    if not token:
        return False
    first = token[0]
    if not (first.isalpha() or first == '_'):
        return False
    return all(ch.isalnum() or ch == '_' for ch in token)
