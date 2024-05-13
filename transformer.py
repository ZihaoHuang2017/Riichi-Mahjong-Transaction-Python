import ast
import enum
import os
import sys
import types
from io import open

import IPython
import jsons
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


def assert_recursive_depth(obj: any, ipython: IPython.InteractiveShell, max_depth: int, visited: list) -> bool:
    if not max_depth:
        return False
    try:
        ipython.ex(repr(obj))
        return True
    except Exception:
        pass
    if type(type(obj)) is enum.EnumMeta:
        return True
    if obj in visited:
        return False
    visited.append(obj)
    if type(obj) in [list, tuple, set]:
        for item in obj:
            if not assert_recursive_depth(item, ipython, max_depth - 1, visited):
                return False
        return True
    if type(obj) is dict:
        for k, v in obj.items():
            if not assert_recursive_depth(v, ipython, max_depth - 1, visited):
                return False
        return True
    attrs = dir(obj)
    for attr in attrs:
        if not attr.startswith("_") and not callable(attr):
            if not assert_recursive_depth(getattr(obj, attr), ipython, max_depth - 1, visited):
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
                parse_statement(
                    import_statements, normal_statements, output_lines, line, lin, lout, args.recursive_depth
                )
            except (SyntaxError, NameError):
                continue
            # except Exception as e:
            #     import_statements.add("import pytest")
            #     normal_statements.append(f"with pytest.raises({e.__class__.__name__}):")
            #     normal_statements.append(" " * INDENT_SIZE + lin)
            #     continue

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

    def parse_statement(
        import_statements, normal_statements, output_lines, line, lin, lout, recursive_depth
    ):
        if lin.startswith("%"):  # magic methods
            return
        if lin.startswith("from ") or lin.startswith("import "):
            import_statements.add(lin)
            return
        revised_statement = revise_line_input(lin, output_lines)
        if not lout:
            ipython.ex(revised_statement)
            normal_statements.append(revised_statement)
            # not the most ideal way if we have some weird crap going on (remote apis???)
            return
        obj_result = ipython.ev(revised_statement)
        output_lines.append(line)
        normal_statements.append(f"_{line} = {revised_statement}")
        if obj_result is True:
            normal_statements.append(f"assert _{line}")
        elif obj_result is False:
            normal_statements.append(f"assert not _{line}")
        elif type(type(obj_result)) is enum.EnumMeta:
            normal_statements.append(f"assert _{line} == {str(obj_result)}")
        elif type(obj_result) is type:
            class_name = obj_result.__name__
            if is_legal_python_obj(class_name):
                normal_statements.append(f"assert _{line} is {class_name}")
            else:
                normal_statements.append(f"assert _{line}.__name__ == \"{class_name}\"")

        else:
            obj_repr = repr(obj_result)
            if is_legal_python_obj(obj_repr):
                normal_statements.append(f"assert _{line} == {repr(obj_result)}")
                return
            class_name = obj_result.__class__.__name__
            if is_legal_python_obj(class_name):
                normal_statements.append(
                    f"assert type(_{line}) is {class_name}"
                )
            else:
                normal_statements.append(f"assert type(_{line}).__name__ == \"{class_name}\"")
            if not assert_recursive_depth(obj_result, ipython, recursive_depth, []):
                print(f"Infinite loop detected in {obj_result}, can only assert the type")
                return
            try:
                serialised_obj = jsons.dump(obj_result)
                import_statements.add("import jsons")
                normal_statements.append(
                    f"assert jsons.dump(_{line}) == {serialised_obj}"
                )
            except Exception:  # 万策尽
                pass

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

    def is_legal_python_obj(statement: str) -> bool:
        try:
            ipython.ev(statement)
            return True
        except Exception:
            return False
