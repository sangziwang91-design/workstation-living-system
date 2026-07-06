from __future__ import annotations

from wls.skill_compiler import SkillCompiler, SkillPackage, CompiledSkill


class TestSkillCompiler:
    def test_propose_creates_package(self):
        compiler = SkillCompiler()
        pkg = compiler.propose(
            "file-reader",
            "reads files safely",
            applicability=["coding"],
            permissions=["read_file"],
        )
        assert pkg.name == "file-reader"
        assert pkg.status == "PROPOSED"
        assert pkg.tool_permissions == ["read_file"]

    def test_compile_produces_checksum(self):
        compiler = SkillCompiler()
        pkg = compiler.propose("test-skill", "desc")
        compiled = compiler.compile(pkg, {"steps": ["a", "b"]}, tests=["t1", "t2"])
        assert compiled.body_checksum
        assert compiled.test_manifest == ["t1", "t2"]
        assert not compiled.is_validated()

    def test_validate_adds_evidence(self):
        compiler = SkillCompiler()
        pkg = compiler.propose("test-skill", "desc")
        compiled = compiler.compile(pkg, {"steps": ["a"]})
        validated = compiler.validate(compiled, {"t1": True, "t2": True})
        assert validated.is_validated()
        assert len(validated.acceptance_evidence) == 2

    def test_reject_clears_permissions(self):
        compiler = SkillCompiler()
        pkg = compiler.propose("bad-skill", "desc", permissions=["write"])
        compiled = compiler.compile(pkg, {"steps": []})
        rejected = compiler.reject(compiled, "security violation")
        assert rejected.status == "REJECTED"
        assert rejected.tool_permissions == []
        assert "REJECTED" in rejected.description

    def test_package_to_dict(self):
        pkg = SkillPackage(
            "s1", "alpha", 1, "test skill", ["coding"], ["read"],
        )
        d = pkg.to_dict()
        assert d["name"] == "alpha"
        assert d["risk_ceiling"] == "READ"

    def test_compiled_to_dict(self):
        compiler = SkillCompiler()
        pkg = compiler.propose("t", "d")
        compiled = compiler.compile(pkg, {"ok": 1})
        d = compiled.to_dict()
        assert "body_checksum" in d
        assert "acceptance_evidence" in d
