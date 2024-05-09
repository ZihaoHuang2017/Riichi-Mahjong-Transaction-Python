import ast
import enum
import os
import sys
import jsons
from io import open

import IPython
from IPython.core.error import StdinNotImplementedError
from IPython.core.magic import register_line_magic
from IPython.core.magic_arguments import magic_arguments, argument, parse_argstring
from IPython.utils import io

SET_UP_KEYWORDS = ["from", "import", "%"]


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

        import_statements = ["import jsons"]
        normal_statements = []
        histories = ipython.history_manager.get_range(output=True)
        for session, line, (lin, lout) in histories:
            try:
                if lin.startswith("%"):  # magic methods
                    continue
                if lin.startswith("from ") or lin.startswith("import "):
                    import_statements.append(lin)
                    continue
                if not lout:
                    ipython.ex(lin)
                    # not the most ideal way if we have some weird crap going on (remote apis???)
                    normal_statements.append(lin)
                else:
                    obj_result = ipython.ev(lin)
                    normal_statements.append(f"_{line} = {lin}")
                    if obj_result is True:
                        normal_statements.append(f"assert _{line}")
                    elif obj_result is False:
                        normal_statements.append(f"assert not _{line}")
                    elif type(type(obj_result)) is enum.EnumMeta:
                        normal_statements.append(
                            f"assert _{line} == {str(obj_result)}"
                        )
                    elif type(obj_result) is type:
                        normal_statements.append(
                            f"assert _{line} is {obj_result.__name__}"
                        )
                    else:
                        try:
                            ipython.ev(repr(obj_result))
                            normal_statements.append(
                                f"assert _{line} == {repr(obj_result)}"
                            )
                        except SyntaxError:
                            normal_statements.append(
                                f"assert type(_{line}) is {obj_result.__class__.__name__}"
                            )
                            normal_statements.append(
                                f"assert jsons.dump(_{line}) == {jsons.dump(obj_result)}"
                            )
            except (SyntaxError, NameError):
                continue

        print(*import_statements, sep="\n", file=outfile)
        print("\n", file=outfile)
        print("def test_func():", file=outfile)
        for statement in normal_statements:
            print(" " * 4 + statement, file=outfile)
        if close_at_end:
            outfile.close()
