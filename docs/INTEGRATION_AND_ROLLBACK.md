# Integration and rollback

## Integration state

The overlay is **not deployed**. It is designed for an isolated branch created from base commit:

```text
4c77401deb159e8ee2a2daf3c6e2702ce48876e3
```

The existing WLS plugin loader is the only integration point. Enabling the organ requires explicitly adding `wls.examiner_plugin` to a copied/test configuration. Installation alone does not enable it.

## Dry-run installation

```bash
python source/scripts/install_examiner_overlay.py /path/to/workstation-living-system-private
```

The installer refuses:

- `main` or `master`;
- detached HEAD;
- dirty working tree unless explicitly overridden;
- base-commit drift unless explicitly reviewed;
- non-identical destination files;
- a second installation receipt.

## Apply on an isolated branch

```bash
python source/scripts/install_examiner_overlay.py /path/to/workstation-living-system-private --apply
```

The installer performs a complete conflict preflight before writing, records hashes and ownership of every installed file, and rolls back newly created files if installation raises. Pre-existing identical files are never claimed as installer-owned.

## Required post-install gates

```bash
PYTHONPATH=source/src python -m compileall -q source/src/wls source/scripts source/tests
PYTHONPATH=source/src pytest -q source/tests
PYTHONPATH=source/src python source/scripts/run_examiner_sandbox.py --output .examiner-sandbox
PYTHONPATH=source/src python source/scripts/run_adversarial_trials.py --output .examiner-adversarial
```

Then run the canonical WLS repository gates on the target branch. Package tests are not a substitute for those gates.

## Rollback

Dry run:

```bash
python source/scripts/uninstall_examiner_overlay.py /path/to/workstation-living-system-private
```

Apply:

```bash
python source/scripts/uninstall_examiner_overlay.py /path/to/workstation-living-system-private --apply
```

Rollback deletes only files that were created by the installer and whose hashes remain unchanged. Modified files are preserved and reported. Pre-existing identical files are preserved.

## Owner gates

Installation, plugin enablement, evaluator promotion, merge, and owner-host deployment remain separate explicit decisions. No script in this package auto-merges, deploys, changes provider configuration, or modifies owner secrets.
