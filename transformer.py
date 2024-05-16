import ast
import dataclasses
import enum
import os
import sys
import typing
from io import open

import IPython
from IPython.core.error import StdinNotImplementedError
from IPython.core.magic import register_line_magic
from IPython.core.magic_arguments import magic_arguments, argument, parse_argstring
from IPython.utils import io

INDENT_SIZE = 4


class RewriteUnderscores(ast.NodeTransformer):
    def __init__(self, one_underscore, two_underscores, three_underscores):
        self.one_underscore = one_underscore
        self.two_underscores = two_underscores
        self.three_underscores = three_underscores

    def visit_Name(self, node):
        if node.id == "_":
            return ast.Name(id=f"_{self.one_underscore}", ctx=ast.Load())
        elif node.id == "__":
            return ast.Name(id=f"_{self.two_underscores}", ctx=ast.Load())
        elif node.id == "___":
            return ast.Name(id=f"_{self.three_underscores}", ctx=ast.Load())
        else:
            return node


def revise_line_input(lin, output_lines):
    # Undefined Behaviour if the user tries to invoke _ with len < 3. Why would you want to do that?
    one_underscore, two_underscores, three_underscores = (
        output_lines[-1],
        output_lines[-2],
        output_lines[-3],
    )
    node = ast.parse(lin)
    revised_node = RewriteUnderscores(
        one_underscore, two_underscores, three_underscores
    ).visit(node)
    revised_statement = ast.unparse(revised_node)
    return revised_statement


def assert_recursive_depth(
    obj: any, ipython: IPython.InteractiveShell, visited: list
) -> bool:
    if is_legal_python_obj(repr(obj), obj, ipython):
        return True
    if type(type(obj)) is enum.EnumMeta:
        return True
    if obj in visited:
        return False
    visited.append(obj)
    if type(obj) in [list, tuple, set]:
        for item in obj:
            if not assert_recursive_depth(item, ipython, visited):
                return False
        return True
    if type(obj) is dict:
        for k, v in obj.items():
            if not assert_recursive_depth(v, ipython, visited):
                return False
        return True
    attrs = dir(obj)
    for attr in attrs:
        if not attr.startswith("_") and not callable(attr):
            if not assert_recursive_depth(getattr(obj, attr), ipython, visited):
                return False
    return True


def load_ipython_extension(ipython: IPython.InteractiveShell):
    @register_line_magic
    @magic_arguments()
    @argument(
        "-f",
        dest="filename",
        help="""
        FILENAME: instead of printing the output to the screen, redirect
        it to the given file.  The file is always overwritten, though *when
        it can*, IPython asks for confirmation first. In particular, running
        the command 'history -f FILENAME' from the IPython Notebook
        interface will replace FILENAME even if it already exists *without*
        confirmation.
        """,
    )
    @argument(
        "-r",
        dest="recursive_depth",
        default=100,
        help="""
        RECURSIVE_DEPTH: the maximum number of levels allowed before the program
        assumes that it contains an infinite loop. Default value is 100.
        """,
    )
    @argument(
        "-l",
        dest="long",
        action="store_true",
        help="""
        LONG: If set to True, then the program will try to expand the test case into 
        individual assertions; if False, then a dict representation will be used.
        """,
    )
    def transform_tests(parameter_s=""):
        args = parse_argstring(transform_tests, parameter_s)
        outfname = args.filename
        if not outfname:
            outfile = sys.stdout  # default
            # We don't want to close stdout at the end!
            close_at_end = False
        else:
            outfname = os.path.expanduser(outfname)
            if os.path.exists(outfname):
                try:
                    ans = io.ask_yes_no("File %r exists. Overwrite?" % outfname)
                except StdinNotImplementedError:
                    ans = True
                if not ans:
                    print("Aborting.")
                    return
                print("Overwriting file.")
            outfile = open(outfname, "w", encoding="utf-8")
            close_at_end = True

        import_statements = set()
        normal_statements = []
        output_lines = [0, 0, 0]
        histories = ipython.history_manager.get_range(output=True)
        for session, line, (lin, lout) in histories:
            try:
                if lin.startswith("%") or lin.endswith("?"):  # magic methods
                    continue
                if lin.startswith("from ") or lin.startswith("import "):
                    import_statements.add(lin)
                    continue
                revised_statement = revise_line_input(lin, output_lines)
                if lout is None:
                    ipython.ex(revised_statement)
                    normal_statements.append(revised_statement)
                    # not the most ideal way if we have some weird crap going on (remote apis???)
                    continue
                obj_result = ipython.ev(revised_statement)
                output_lines.append(line)
                var_name = f"_{line}"
                normal_statements.append(f"{var_name} = {revised_statement}")
                if args.long:
                    normal_statements.extend(
                        parse_statement_long(obj_result, var_name, {})
                    )
                else:
                    representation, assertions = parse_statement_short(
                        obj_result, var_name, {}, True
                    )
                    normal_statements.extend(assertions)
                    pass
            except (SyntaxError, NameError) as e:
                continue
            except Exception as e:
                import_statements.add("import pytest")
                normal_statements.append(f"with pytest.raises({e.__class__.__name__}):")
                normal_statements.append(" " * INDENT_SIZE + lin)
                continue
        print(*normal_statements, sep="\n")
        for statement in import_statements:
            lines = statement.split("\n")
            for line in lines:
                print(line, file=outfile)
        print("\n", file=outfile)
        print("def test_func():", file=outfile)
        for statement in normal_statements:
            lines = statement.split("\n")
            for line in lines:
                print(" " * INDENT_SIZE + line, file=outfile)
        if close_at_end:
            outfile.close()

    def parse_statement_long(
        obj: any, var_name: str, visited: dict[int, str]
    ) -> list[str]:
        if obj is True:
            return [f"assert {var_name}"]
        if obj is False:
            return [f"assert not {var_name}"]
        if obj is None:
            return [f"assert {var_name} is None"]
        if type(type(obj)) is enum.EnumMeta and is_legal_python_obj(
            type(obj).__name__, type(obj), ipython
        ):
            return [f"assert {var_name} == {str(obj)}"]
        if type(obj) is type:
            class_name = obj.__name__
            if is_legal_python_obj(class_name, obj, ipython):
                return [f"assert {var_name} is {class_name}"]
            else:
                return [f'assert {var_name}.__name__ == "{class_name}"']
        if is_legal_python_obj(repr(obj), obj, ipython):
            return [f"assert {var_name} == {repr(obj)}"]
        if id(obj) in visited:
            return [f"assert {var_name} == {visited[id(obj)]}"]
        result = []
        visited[id(obj)] = var_name
        class_name = obj.__class__.__name__
        if is_legal_python_obj(class_name, obj.__class__, ipython):
            result.append(f"assert type({var_name}) is {class_name}")
        else:
            result.append(f'assert type({var_name}).__name__ == "{class_name}"')
        if isinstance(obj, typing.Sequence):
            for idx, val in enumerate(obj):
                result.extend(parse_statement_long(val, f"{var_name}[{idx}]", visited))
        elif type(obj) is dict:
            for key, value in obj.items():
                result.extend(
                    parse_statement_long(value, f'{var_name}["{key}"]', visited)
                )
        else:
            attrs = dir(obj)
            for attr in attrs:
                if not attr.startswith("_"):
                    value = getattr(obj, attr)
                    if not callable(value):
                        result.extend(
                            parse_statement_long(value, f"{var_name}.{attr}", visited)
                        )
        return result

    def parse_statement_short(
        obj: any, var_name: str, visited: dict[int, str], propagation: bool
    ) -> tuple[str, list[str]]:
        # readable-repr, assertions
        if type(type(obj)) is enum.EnumMeta and is_legal_python_obj(
            type(obj).__name__, type(obj), ipython
        ):
            if propagation:
                return str(obj), [f"assert {var_name} == {str(obj)}"]
            return str(obj), []
        if is_legal_python_obj(repr(obj), obj, ipython):
            if propagation:
                return repr(obj), parse_statement_long(
                    obj, var_name, visited
                )  # to be expanded
            return repr(obj), []
        if id(obj) in visited:
            return var_name, [f"assert {var_name} == {visited[id(obj)]}"]
        visited[id(obj)] = var_name
        if isinstance(obj, typing.Sequence):
            reprs, overall_assertions = [], []
            for idx, val in enumerate(obj):
                representation, assertions = parse_statement_short(
                    val, f"{var_name}[{idx}]", visited, False
                )
                reprs.append(representation)
                overall_assertions.extend(assertions)
            if type(obj) is tuple:
                repr_str = f'({", ".join(reprs)})'
            else:
                repr_str = f'[{", ".join(reprs)}]'
            if propagation:
                overall_assertions.insert(0, f"assert {var_name} == {repr_str}")
            return repr_str, overall_assertions
        elif type(obj) is dict:
            reprs, overall_assertions = [], []
            for field, value in obj.items():
                representation, assertions = parse_statement_short(
                    value, f'{var_name}["{field}"]', visited, False
                )
                reprs.append(f'"{field}": {representation}')
                overall_assertions.extend(assertions)
            repr_str = "{" + ", ".join(reprs) + "}"
            if propagation:
                overall_assertions.insert(0, f"assert {var_name} == {repr_str}")
            return repr_str, overall_assertions
        elif dataclasses.is_dataclass(obj):
            reprs, overall_assertions = [], []
            for field in dataclasses.fields(obj):
                representation, assertions = parse_statement_short(
                    getattr(obj, field.name), f"{var_name}.{field.name}", visited, False
                )
                reprs.append(f'"{field.name}": {representation}')
                overall_assertions.extend(assertions)
            repr_str = "{" + ", ".join(reprs) + "}"
            if propagation:
                overall_assertions.insert(0, f"assert {var_name} == {repr_str}")
            return repr_str, overall_assertions
        else:
            overall_assertions = []
            class_name = obj.__class__.__name__
            if is_legal_python_obj(class_name, obj.__class__, ipython):
                overall_assertions.append(f"assert type({var_name}) is {class_name}")
            else:
                overall_assertions.append(
                    f'assert type({var_name}).__name__ == "{class_name}"'
                )
            attrs = dir(obj)
            for attr in attrs:
                if not attr.startswith("_"):
                    value = getattr(obj, attr)
                    if not callable(value):
                        _, assertions = parse_statement_short(
                            value, f"{var_name}.{attr}", visited, True
                        )
                        overall_assertions.extend(assertions)
            return var_name, overall_assertions


def is_legal_python_obj(
    statement: str, obj: any, ipython: IPython.InteractiveShell
) -> bool:
    try:
        return obj == ipython.ev(statement)
    except (SyntaxError, NameError):
        return False
