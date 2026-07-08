from __future__ import annotations

from pathlib import Path

from wls.repo_explorer import RepoExplorer, RepoMap, RepoFile


class TestRepoExplorer:
    def test_explore_python_project(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text(
            "def hello():\n    pass\n\nclass Foo:\n    pass\n\nimport os\nfrom pathlib import Path\n"
        )
        (tmp_path / "src" / "utils.py").write_text(
            "def helper():\n    return 42\n"
        )
        (tmp_path / "README.md").write_text("# Test Project")
        (tmp_path / "__pycache__").mkdir()

        explorer = RepoExplorer()
        repo_map = explorer.explore(str(tmp_path))

        assert repo_map.root == str(tmp_path)
        main = repo_map.find_file("main.py")
        assert main is not None
        assert "hello" in main.symbols
        assert "Foo" in main.symbols
        assert main.kind == "py"

        utils = repo_map.find_file("utils.py")
        assert utils is not None
        assert "helper" in utils.symbols

        readme = repo_map.find_file("README.md")
        assert readme is not None

        pycache = repo_map.find_file("__pycache__")
        assert pycache is None

    def test_skips_junk_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("...")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg").mkdir()
        (tmp_path / "real.py").write_text("x = 1")

        explorer = RepoExplorer()
        repo_map = explorer.explore(str(tmp_path))
        assert repo_map.find_file("config") is None
        assert repo_map.find_file("real.py") is not None

    def test_instruction_discovery(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("agents")
        (tmp_path / "CLAUDE.md").write_text("claude")
        (tmp_path / "README.md").write_text("readme")

        explorer = RepoExplorer()
        repo_map = explorer.explore(str(tmp_path))
        assert "AGENTS.md" in repo_map.instruction_files
        assert "CLAUDE.md" in repo_map.instruction_files
        assert "README.md" in repo_map.instruction_files

    def test_find_symbol(self, tmp_path):
        (tmp_path / "a.py").write_text("def find_me():\n    pass\n")
        (tmp_path / "b.py").write_text("x = 1\n")

        explorer = RepoExplorer()
        repo_map = explorer.explore(str(tmp_path))
        results = repo_map.find_symbol("find_me")
        assert len(results) == 1
        assert results[0].path == "a.py"

    def test_upstream_downstream(self, tmp_path):
        (tmp_path / "a.py").write_text("import b\n\ndef foo():\n    pass\n")
        (tmp_path / "b.py").write_text("def bar():\n    pass\n")

        explorer = RepoExplorer()
        repo_map = explorer.explore(str(tmp_path))

        a = repo_map.find_file("a.py")
        assert a is not None

    def test_nested_context(self, tmp_path):
        (tmp_path / "a.py").write_text("import b\n\ndef foo():\n    pass\n")
        (tmp_path / "b.py").write_text("def bar():\n    pass\n")

        explorer = RepoExplorer()
        repo_map = explorer.explore(str(tmp_path))
        ctx = explorer.nested_context(repo_map, "a.py", depth=2, max_files=5)
        assert isinstance(ctx, list)

    def test_repo_map_to_dict(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        explorer = RepoExplorer()
        repo_map = explorer.explore(str(tmp_path))
        d = repo_map.to_dict()
        assert d["file_count"] == 1
        assert d["root"] == str(tmp_path)

    def test_empty_directory(self, tmp_path):
        explorer = RepoExplorer()
        repo_map = explorer.explore(str(tmp_path))
        assert len(repo_map.files) == 0

    def test_js_file_extraction(self, tmp_path):
        (tmp_path / "app.js").write_text(
            'function init() {}\nconst x = require("lodash");\nimport { foo } from "./bar";\n'
        )
        explorer = RepoExplorer()
        repo_map = explorer.explore(str(tmp_path))
        f = repo_map.find_file("app.js")
        assert f is not None
        assert "init" in f.symbols
