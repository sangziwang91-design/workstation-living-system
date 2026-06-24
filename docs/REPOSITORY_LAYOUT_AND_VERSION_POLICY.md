# WLS Repository Layout and Version Policy

## Status

This document defines a merge invariant for the active WLS repository. It is not optional guidance.

## Canonical locations

| Authority | Canonical location |
|---|---|
| Project root | repository root (`.`) |
| Build metadata | `pyproject.toml` |
| Python package root | `source/src/wls` |
| Runtime authority | `source/src/wls/runtime.py::LivingSystem` |
| Version authority | `source/src/wls/_version.py::__version__` |
| Tests | `source/tests` |
| Verification scripts | `source/scripts` |
| Current machine-readable state | `CURRENT_STATE.yaml` |
| Repo-native evolution control | `.evolution/` |

There must be exactly one active `pyproject.toml`, one active `wls` package tree, and one version assignment.

## Version rule

`source/src/wls/_version.py::__version__` is the sole writable version source.

`pyproject.toml` reads that value dynamically:

```toml
[project]
dynamic = ["version"]

[tool.setuptools.dynamic]
version = {attr = "wls._version.__version__"}
```

The following live-state mirrors must match it before merge:

- the current development version shown in `README.md`;
- `CURRENT_STATE.yaml -> system.development_version`;
- `.evolution/CURRENT_CHAIN.json -> canonical_baseline.development_version`.

Historical evidence files may preserve the version that produced them. They must not be edited to imitate the current version.

## Root rule

The repository root is the only install and build root:

```bash
python -m pip install -e ".[dev]"
python -m build .
python -m pytest source/tests -q
```

The `source/` directory is a source-container directory, not a second project root. It must not contain its own:

- `pyproject.toml`;
- project `README.md`;
- project `LICENSE`;
- independent `wls` package metadata;
- build or install entry point.

## Package rule

The only active package tree is:

```text
source/src/wls
```

These are forbidden in a mergeable branch:

```text
src/wls
wls/
source/wls
another-project-root/src/wls
```

A temporary feature branch may contain migration scaffolding while work is in progress, but its pull request must fail the repository-invariant gate until the duplicate is removed or an approved migration contract explicitly supersedes this policy.

## Workflow rule

GitHub Actions must:

- install from the repository root;
- build from the repository root;
- reference `source/src`, `source/tests`, and `source/scripts` explicitly;
- remain read-only with respect to repository contents unless a separately approved release workflow exists;
- never commit package metadata directly to `main`;
- never unpack an old source snapshot into `main`;
- never mutate the console entry point automatically.

Direct workflow commands such as these are forbidden:

```text
git push origin HEAD:main
python -m pip install -e "source[dev]"
python -m build source
working-directory: source
```

## Artifact rule

Built wheels, installer checksums, and release bundles are release artifacts, not source-of-truth files. They must be generated from a pinned commit and attached to a release or CI artifact store. They must not remain in the active source tree as an apparently current version.

The former bundled `1.0.0` installer files were removed from the active tree because the canonical development line is `0.9.0.dev1`. Their history remains recoverable through Git history; removal does not rewrite historical evidence.

## Enforcement

The permanent gate is:

```bash
python source/scripts/verify_packaging_layout.py
```

The `Verify Repository Invariants` workflow runs this on every pull request and on every push to `main`. It rejects:

- multiple project manifests;
- multiple package roots;
- static version duplication in `pyproject.toml`;
- version drift across current live-state surfaces;
- duplicate source-root project metadata;
- stale installer artifacts at repository root;
- retired source-bootstrap or metadata-mutator workflows;
- direct workflow pushes to `main`;
- source-directory install/build roots.

## Approved version-change procedure

1. Create an isolated branch.
2. Change only `source/src/wls/_version.py::__version__` first.
3. Update live-state mirrors and the relevant evolution packet in the same branch.
4. Run the invariant gate, full tests, build, and affected evolution verifiers.
5. Review the exact head.
6. Merge through owner approval.
7. Verify `main` after merge.

No model, workflow, installer, or external provider may create a second version authority.
