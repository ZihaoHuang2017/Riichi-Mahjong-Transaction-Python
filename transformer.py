import ast
import builtins
import dataclasses
import enum
import os
import sys
import types
import typing
import inspect
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
        "-v",
        dest="verbose",
        action="store_true",
        help="""
        VERBOSE: If set to True, then the program will try to expand the test case into 
        individual assertions; if False, then the whole list/dict/tuple will be asserted at once.
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
        original_print = builtins.print
        histories = ipython.history_manager.get_range(output=True)
        for session, line, (lin, lout) in histories:
            print_buffer = []
            ipython.builtin_trap.remove_builtin("print", original_print)
            ipython.builtin_trap.add_builtin(
                "print",
                return_hijacked_print(original_print, print_buffer, lin, ipython),
            )
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
                    normal_statements.extend(print_buffer)
                    # not the most ideal way if we have some weird crap going on (remote apis???)
                    continue
                obj_result = ipython.ev(revised_statement)
                output_lines.append(line)
                var_name = f"_{line}"
                normal_statements.append(f"{var_name} = {revised_statement}")
                if args.verbose:
                    normal_statements.extend(
                        generate_verbose_tests(obj_result, var_name, {}, ipython)
                    )
                else:
                    representation, assertions = generate_concise_tests(
                        obj_result, var_name, {}, True, ipython
                    )
                    normal_statements.extend(assertions)
                    pass

            except (SyntaxError, NameError) as e:
                # raise e
                continue
            # except Exception as e:
            #     import_statements.add("import pytest")
            #     normal_statements.append(f"with pytest.raises({type(e).__name__}):")
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


def generate_verbose_tests(
    obj: any, var_name: str, visited: dict[int, str], ipython
) -> list[str]:
    """Parses the object and generates verbose tests.

    We are only interested in the top level assertion as well as the objects that can't be parsed directly,
    in which case it is necessary to compare the individual fields.

    Args:
        obj (any): The object to be transformed into tests.
        var_name (str): The name referring to the object.
        visited (dict[int, str]): A dict associating the obj with the var_names. Used for cycle detection.

    Returns:
        list[str]: A list of assertions to be added.
    """
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
    visited[id(obj)] = var_name
    result = [get_type_assertion(obj, var_name, ipython)]
    if isinstance(obj, typing.Sequence):
        for idx, val in enumerate(obj):
            result.extend(
                generate_verbose_tests(val, f"{var_name}[{idx}]", visited, ipython)
            )
    elif type(obj) is dict:
        for key, value in obj.items():
            result.extend(
                generate_verbose_tests(value, f'{var_name}["{key}"]', visited, ipython)
            )
    else:
        attrs = dir(obj)
        for attr in attrs:
            if not attr.startswith("_"):
                value = getattr(obj, attr)
                if not callable(value):
                    result.extend(
                        generate_verbose_tests(
                            value, f"{var_name}.{attr}", visited, ipython
                        )
                    )
    return result


def generate_concise_tests(
    obj: any, var_name: str, visited: dict[int, str], propagation: bool, ipython
) -> tuple[str, list[str]]:
    """Parses the object and generates concise tests.

    We are only interested in the top level assertion as well as the objects that can't be parsed directly,
    in which case it is necessary to compare the individual fields.

    Args:
        obj (any): The object to be transformed into tests.
        var_name (str): The name referring to the object.
        visited (dict[int, str]): A dict associating the obj with the var_names. Used for cycle detection.
        propagation (bool): Whether the result should be propagated.

    Returns:
        tuple[str, list[str]]: The repr of the obj if it can be parsed easily, var_name if it can't, and a list of
        assertions to be added
    """
    # readable-repr, assertions
    if type(type(obj)) is enum.EnumMeta and is_legal_python_obj(
        type(obj).__name__, type(obj), ipython
    ):
        if propagation:
            return str(obj), [f"assert {var_name} == {str(obj)}"]
        return str(obj), []
    if is_legal_python_obj(repr(obj), obj, ipython):
        if propagation:
            return repr(obj), generate_verbose_tests(
                obj, var_name, visited, ipython
            )  # to be expanded
        return repr(obj), []
    if id(obj) in visited:
        return var_name, [f"assert {var_name} == {visited[id(obj)]}"]
    visited[id(obj)] = var_name
    if isinstance(obj, typing.Sequence):
        reprs, overall_assertions = [], []
        for idx, val in enumerate(obj):
            representation, assertions = generate_concise_tests(
                val, f"{var_name}[{idx}]", visited, False, ipython
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
            representation, assertions = generate_concise_tests(
                value, f'{var_name}["{field}"]', visited, False, ipython
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
            representation, assertions = generate_concise_tests(
                getattr(obj, field.name),
                f"{var_name}.{field.name}",
                visited,
                False,
                ipython,
            )
            reprs.append(f'"{field.name}": {representation}')
            overall_assertions.extend(assertions)
        repr_str = "{" + ", ".join(reprs) + "}"
        if propagation:
            overall_assertions.insert(0, f"assert {var_name} == {repr_str}")
        return repr_str, overall_assertions
    else:
        overall_assertions = [get_type_assertion(obj, var_name, ipython)]
        attrs = dir(obj)
        for attr in attrs:
            if not attr.startswith("_"):
                value = getattr(obj, attr)
                if not callable(value):
                    _, assertions = generate_concise_tests(
                        value, f"{var_name}.{attr}", visited, True, ipython
                    )
                    overall_assertions.extend(assertions)
        return var_name, overall_assertions


def get_type_assertion(obj, var_name, ipython) -> str:
    class_name = type(obj).__name__
    if is_legal_python_obj(class_name, type(obj), ipython):
        return f"assert type({var_name}) is {class_name}"
    else:
        return f'assert type({var_name}).__name__ == "{class_name}"'


def is_legal_python_obj(
    statement: str, obj: any, ipython: IPython.InteractiveShell
) -> bool:
    try:
        return obj == ipython.ev(statement)
    except (SyntaxError, NameError):
        return False


class DetermineReturnType(ast.NodeVisitor):
    def __init__(self):
        self.ret = None

    def visit_Return(self, node):
        self.ret = node.value


class ExpressionParser(ast.NodeVisitor):
    def __init__(self, caller_frame: types.FrameType, global_index_start):
        self.expression: str = ""
        self.caller_frame = caller_frame
        self.lineno = caller_frame.f_lineno - global_index_start + 1
        self.stack: dict[str, tuple] = dict()

    def visit_For(
        self, node
    ):  # handles for, foreach and enumerate, most scuffed method
        if (
            node.lineno <= self.lineno <= node.end_lineno
        ):  # The loop actually contains the desired print statement
            if isinstance(node.iter, ast.Call):
                func_name = node.iter.func.id
                if func_name == "range" and len(node.iter.args) == 1:
                    # for a in range b:
                    assert isinstance(node.target, ast.Name)
                    self.stack[node.target.id] = (
                        str(
                            eval(
                                node.target.id,
                                self.caller_frame.f_globals,
                                self.caller_frame.f_locals,
                            )
                        ),
                        "",
                    )
                elif func_name == "enumerate" and len(node.iter.args) == 1:
                    if (
                        isinstance(node.target, ast.Tuple)
                        and len(node.target.elts) == 2
                    ):
                        index, obj_name = node.target.elts
                        index_num = eval(
                            ast.unparse(index),
                            self.caller_frame.f_globals,
                            self.caller_frame.f_locals,
                        )
                        self.stack[obj_name.id] = (
                            f"{node.iter.args[0].id}",
                            f"[{index_num}]",
                        )
            elif isinstance(node.iter, ast.Name) and isinstance(node.target, ast.Name):
                obj = eval(
                    node.target.id,
                    self.caller_frame.f_globals,
                    self.caller_frame.f_locals,
                )
                container = eval(
                    node.iter.id,
                    self.caller_frame.f_globals,
                    self.caller_frame.f_locals,
                )

                self.stack[node.target.id] = (
                    f"{node.iter.id}",
                    f"[{container.index(obj)}]",
                )

        self.generic_visit(node)

    def visit_Call(self, node):
        if node.lineno == self.lineno and getattr(node.func, "id", "") == "print":
            name_replacer = ReplaceNames(self.stack)
            parsed_obj_name = name_replacer.visit(node.args[1])
            self.expression = ast.unparse(parsed_obj_name)


class ReplaceNames(ast.NodeTransformer):
    def __init__(self, names):
        self.names = names

    def visit_Name(self, node):
        temp_id = node.id
        bruh = []
        while temp_id in self.names:
            temp_id, suffix = self.names.get(temp_id)
            bruh.append(suffix)
        bruh.reverse()
        for suf in bruh:
            temp_id += suf
        node.id = temp_id
        return node


class RewriteToName(ast.NodeTransformer):
    def visit_Name(self, node):
        return ast.Constant(node.id)


def return_hijacked_print(original_print, buffer, lin, ipython):
    # TODO: add line number if it is not an assignment
    # TODO: deal with the case where the user enters more than 1 line for assignment
    # ASSUME: single point of return, yes ifs, the ret value starts with some part of the explore session,
    # The ultimate input matches in position with the first output
    def hijack_print(
        *values: object,
        sep: str | None = " ",
        end: str | None = "\n",
        file=None,
        flush=False,
    ):
        if len(values) != 2 or values[0] != "--explore":
            original_print(*values, sep=sep, end=end, file=file, flush=flush)
            return
        obj = values[1]
        original_print(obj, sep=sep, end=end, file=file, flush=flush)
        parsed_input = ast.parse(lin).body[0]
        if not isinstance(parsed_input, ast.Assign):
            return
        caller_frame = inspect.currentframe().f_back
        code_list, global_index_start = inspect.getsourcelines(caller_frame)

        parsed_ast = ast.parse(inspect.getsource(caller_frame))
        expression_parser = ExpressionParser(caller_frame, global_index_start)
        expression_parser.visit(parsed_ast)
        explore_expression = expression_parser.expression

        return_type_determiner = DetermineReturnType()
        return_type_determiner.visit(
            ast.parse(code_list[-1].strip())
        )  # Assuming that this is the correct deal
        name_rewriter = RewriteToName()
        ret = ipython.ev(ast.unparse(name_rewriter.visit(return_type_determiner.ret)))

        desired_ret = ""
        if isinstance(ret, str):
            desired_ret = ret
        else:
            for sub_ret in ret:
                if isinstance(sub_ret, str) and explore_expression.startswith(sub_ret):
                    desired_ret = sub_ret

        # No support for multiple assignment yet
        assignment_target_names = ipython.ev(
            ast.unparse(name_rewriter.visit(parsed_input).targets[0])
        )
        correct_assignment_name = match_return_with_assignment(
            assignment_target_names, ret, desired_ret
        )
        var_name = correct_assignment_name + explore_expression.lstrip(desired_ret)
        buffer.extend(
            generate_verbose_tests(obj, var_name, {}, ipython)
        )  # potential race cond?

    return hijack_print


def match_return_with_assignment(
    assign_to: str or tuple[any] or list[any],
    return_from: str or tuple[any] or list[any],
    desired_ret: str,
):
    if isinstance(return_from, str):
        assert return_from == desired_ret and isinstance(assign_to, str)
        return assign_to
    if isinstance(assign_to, str):
        return f"{assign_to}[{return_from.index(desired_ret)}]"
    return assign_to[return_from.index(desired_ret)]


# class Foo:
#     def __init__(self, value):
#         self.value = value
#         self.next = None
#
#
# def main():
#     a = Foo(3)
#     b = Foo(5)
#     c = Foo(7)
#     print(f"--explore a.value:{a.value}, b.value:{b.value}, c.value:{c.value}")
