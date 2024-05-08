import ast
import os
import sys
from io import open

from IPython.core.error import StdinNotImplementedError
from IPython.core.magic import register_line_magic
from IPython.core.magic_arguments import magic_arguments, argument, parse_argstring
from IPython.utils import io

SET_UP_KEYWORDS = ["from", "import", "%"]


def load_ipython_extension(ipython):
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

        import_statements = []
        normal_statements = []
        counter = 0
        histories = ipython.history_manager.get_range(output=True)
        for session, line, (lin, lout) in histories:
            if lin.startswith("%"):  # magic methods
                continue
            if lin.startswith("from ") or lin.startswith("import "):
                import_statements.append(lin)
                continue
            if not lout:
                normal_statements.append(lin)
            else:
                normal_statements.append(f"arg{counter} = {lin}")
                if is_legal_python(lout):
                    normal_statements.append(f"assert arg{counter} == {lout}")
                else:
                    normal_statements.append(f'assert repr(arg{counter}) == "{lout}"')
                counter += 1
        print(*import_statements, sep="\n", file=outfile)
        print("\n", file=outfile)
        print("def test_func():", file=outfile)
        for statement in normal_statements:
            print(" " * 4 + statement, file=outfile)
        if close_at_end:
            outfile.close()


def is_legal_python(string: str) -> bool:
    try:
        ast.parse(string)
        return True
    except Exception:
        return False
