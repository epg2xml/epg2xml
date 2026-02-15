import ast


ALLOWED_ID_STR_METHODS = {
    "capitalize",
    "casefold",
    "lower",
    "lstrip",
    "replace",
    "rstrip",
    "strip",
    "swapcase",
    "title",
    "upper",
    "zfill",
}


def _eval_id_expr(node: ast.AST, values: dict):
    if isinstance(node, ast.Name):
        value = values[node.id]
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        raise TypeError(f"Unsupported value type for '{node.id}'")

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (str, int, float, bool, type(None))):
            return node.value
        raise TypeError("Unsupported constant type")

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        target = _eval_id_expr(node.func.value, values)
        if not isinstance(target, str):
            raise TypeError("Method calls are allowed only on strings")
        if node.func.attr not in ALLOWED_ID_STR_METHODS:
            raise TypeError(f"Unsupported string method: {node.func.attr}")
        if node.keywords:
            raise TypeError("Keyword arguments are not allowed")
        args = [_eval_id_expr(arg, values) for arg in node.args]
        return getattr(target, node.func.attr)(*args)

    raise TypeError(f"Unsupported expression: {type(node).__name__}")


def render_id_format(id_format: str, values: dict) -> str:
    tree = ast.parse(f"f{id_format!r}", mode="eval")
    if not isinstance(tree.body, ast.JoinedStr):
        raise TypeError("ID_FORMAT must be an f-string compatible template")
    return _render_id_joinedstr(tree.body, values)


def _render_id_joinedstr(node: ast.JoinedStr, values: dict) -> str:
    chunks = []
    for item in node.values:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            chunks.append(item.value)
            continue
        if isinstance(item, ast.FormattedValue):
            value = _eval_id_expr(item.value, values)
            if item.conversion == 115:  # !s
                value = str(value)
            elif item.conversion == 114:  # !r
                value = repr(value)
            elif item.conversion == 97:  # !a
                value = ascii(value)
            elif item.conversion != -1:
                raise TypeError("Unsupported conversion")
            if item.format_spec is not None:
                if not isinstance(item.format_spec, ast.JoinedStr):
                    raise TypeError("Unsupported format spec")
                format_spec = _render_id_joinedstr(item.format_spec, values)
                chunks.append(format(value, format_spec))
            else:
                chunks.append(f"{value}")
            continue
        raise TypeError(f"Unsupported f-string node: {type(item).__name__}")
    return "".join(chunks)
