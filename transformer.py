from IPython.core.magic import register_line_magic

SET_UP_KEYWORDS = ["from", "import", "%"]


def load_ipython_extension(ipython):
    @register_line_magic
    def transform_tests(line):
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
                normal_statements.append(f"assert arg{counter} == {lout}")
                counter += 1
        print(*import_statements, sep="\n")
        print("def test():")
        print(*normal_statements, sep="\n")