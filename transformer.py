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
import astpretty
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
                return_hijacked_print(
                    original_print, print_buffer, lin, ipython, args.verbose
                ),
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
                normal_statements.extend(
                    generate_tests(obj_result, var_name, ipython, args.verbose)
                )

            except (SyntaxError, NameError) as e:
                raise e
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


def generate_tests(obj: any, var_name: str, ipython, verbose: bool) -> list[str]:
    if verbose:
        result = generate_verbose_tests(obj, var_name, dict(), ipython)
    else:
        representation, assertions = generate_concise_tests(
            obj, var_name, dict(), True, ipython
        )
        result = assertions
    if len(result) <= 20:  # Arbitrary
        return result
    return [f"assert str({var_name}) == {str(obj)}"]  # Too lengthy!


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
        self.stack: dict[str, tuple[str, str]] = dict()

    def visit_For(
        self, node
    ):  # method is quite scuffed. There's quite a load of ways ppl can write scuffed
        if not (
            node.lineno <= self.lineno <= node.end_lineno
        ):  # The loop actually contains the desired print statement
            self.generic_visit(node)
            return
        self.stack.update(extract_loop_params(node.target, node.iter, self.caller_frame))
        self.generic_visit(node)

    def visit_Call(self, node):
        if node.lineno == self.lineno and getattr(node.func, "id", "") == "print":
            name_replacer = ReplaceNamesWithSuffix(self.stack)
            parsed_obj_name = name_replacer.visit(node.args[1])
            self.expression = ast.unparse(parsed_obj_name)


def extract_loop_params(target_node, iterator_node, caller_frame) -> dict[str, tuple[str, str]]:
    match target_node, iterator_node:
        case (ast.Name(), ast.Call(func=ast.Name(id="range"))):
            return {
                target_node.id: (
                    str(
                        eval(
                            target_node.id,
                            caller_frame.f_globals,
                            caller_frame.f_locals,
                        )
                    ),
                    "",
                )
            }
        case (ast.Name(), _):
            unparsed_iterator = ast.unparse(iterator_node)
            evaluated_iterator = eval(unparsed_iterator, caller_frame.f_globals, caller_frame.f_locals)
            obj = eval(
                target_node.id,
                caller_frame.f_globals,
                caller_frame.f_locals,
            )
            if isinstance(evaluated_iterator, typing.Sequence):
                return {
                    target_node.id: (
                        f"{unparsed_iterator}",
                        f"[{evaluated_iterator.index(obj)}]",  # TODO: support nonunique lists
                    )
                }
            if isinstance(evaluated_iterator, dict):
                return {
                    target_node.id: (
                        f'"{obj}"', ""
                    )
                }
            try:  # handles sets, hopefully
                iterator_node_list = list(evaluated_iterator)
                return {
                    target_node.id: (
                        f"list({unparsed_iterator})",
                        f"[{iterator_node_list.index(obj)}]",  # TODO: support nonunique lists
                    )
                }
            except Exception as e:
                print(e)
                pass
            return dict()
        case (ast.Tuple() | ast.List(), ast.Call(func=ast.Attribute(attr="items"))):
            key, value_node = target_node.elts
            key_str = eval(
                ast.unparse(key),
                caller_frame.f_globals,
                caller_frame.f_locals,
            )
            return {
                key.id: (f"'{key_str}'", ""),
                value_node.id: (
                    f"{ast.unparse(iterator_node.func.value)}",
                    f'["{key_str}"]'
                )
            }
        case (ast.Tuple() | ast.List(), ast.Call(func=ast.Name(id="enumerate"))):
            index_node, value_node = target_node.elts
            index_str = eval(
                ast.unparse(index_node),
                caller_frame.f_globals,
                caller_frame.f_locals,
            )
            result = {
                index_node.id: (f"{index_str}", "")
            }
            result.update(extract_loop_params(value_node, iterator_node.args[0], caller_frame))
            return result
        case (ast.Tuple() | ast.List(), ast.Call(func=ast.Name(id="zip"))):
            assert isinstance(target_node, ast.Tuple)
            assert len(target_node.elts) == len(iterator_node.args)
            result = dict()
            for item, item_list in zip(target_node.elts, iterator_node.args):
                result.update(extract_loop_params(item, item_list, caller_frame))
            return result
        case _:
            raise Exception("unhandled", ast.dump(target_node), ast.dump(iterator_node))


class ReplaceNames(ast.NodeTransformer):
    def __init__(self, names: dict[str, str]):
        self.names = names

    def visit_Name(self, node):
        temp_id = node.id
        if temp_id in self.names:
            temp_id = self.names[temp_id]
        node.id = temp_id
        return node


class ReplaceNamesWithSuffix(ast.NodeTransformer):
    def __init__(self, names: dict[str, tuple[str, str]]):
        self.names = names

    def visit_Name(self, node):
        print(self.names)
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


def return_hijacked_print(original_print, buffer, lin, ipython, verbose):
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

        # TODO: support for multiple assignment by creating a dict instead
        assignment_target_names = ipython.ev(
            ast.unparse(name_rewriter.visit(parsed_input).targets[0])
        )
        name_replacements = match_return_with_assignment(
            assignment_target_names, ret
        )
        reparsed_var_expression = ast.parse(explore_expression)
        name_replacer = ReplaceNames(name_replacements)
        var_name = ast.unparse(name_replacer.visit(reparsed_var_expression))
        buffer.extend(
            generate_tests(obj, var_name, ipython, verbose)
        )  # can't return, so forced to do this

    return hijack_print


def match_return_with_assignment(
    assign_to: str or tuple[any] or list[any],
    return_from: str or tuple[any] or list[any],
) -> dict[str, str]:
    if isinstance(return_from, str):
        assert isinstance(assign_to, str)
        return {
            return_from: assign_to
        }
    if isinstance(assign_to, str):
        result = dict()
        for i, sub_ret in enumerate(return_from):
            result[sub_ret] = f"{assign_to}[{i}]"
        return result
    result = dict()
    for sub_assign, sub_ret in zip(assign_to, return_from):
        result.update(match_return_with_assignment(sub_assign, sub_ret))
    return result

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
