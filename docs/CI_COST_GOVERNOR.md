# WLS CI Cost Governor

## Purpose

WLS uses GitHub as an evolution control plane, but repeated model commits must not multiply the same tests across many workflow families. CI is evidence infrastructure, not a token or runner sink.

## Gate ownership

| Gate | Sole owner on pull requests |
|---|---|
| full tests, lint, types, security, build | `WLS CI` |
| one project/package/version authority | `Verify Repository Invariants` |
| ET001–ET003 vertical evidence scripts | `Verify Evolution Regressions` |
| `.evolution` contracts and packets | `Verify Repo-Native Evolution Chain` |
| feature-specific behavior | the feature's focused workflow |

A focused feature workflow must not repeat the full test, lint, type, security and build stack. It runs only the tests uniquely required by that feature and relies on `WLS CI` for shared regression gates.

## Pull-request budget

For ordinary source-code pull requests:

- WLS CI: Ubuntu and Windows on Python 3.11;
- repository invariants: one Ubuntu job plus one root-install smoke job;
- evolution regressions: one Ubuntu job when relevant paths change;
- control-plane verification: one Ubuntu job only when `.evolution` changes;
- feature workflow: at most Ubuntu and Windows on Python 3.11.

Python 3.13 and the full four-cell operating-system matrix are reserved for `main`, manual release verification, or an explicitly approved high-risk target.

## Concurrency

Every recurring workflow uses a pull-request/ref concurrency group with `cancel-in-progress: true`. A new commit supersedes obsolete in-flight work on the same branch.

## Evidence boundary

Cost reduction must not mean weaker truth claims:

- pull requests still require cross-platform execution through WLS CI;
- `main` still receives the full Python 3.11/3.13 and Ubuntu/Windows matrix;
- vertical evolution scripts remain independent evidence;
- focused workflows remain available for feature-specific failure modes;
- owner-host evidence remains outside CI where required.

## Prohibited patterns

- copying the full test/lint/type/security/build sequence into every feature workflow;
- running the same evolution verifier in three separate workflow files;
- four-cell matrices on every draft commit without a risk justification;
- leaving obsolete jobs running after a newer branch commit exists;
- using job count or green badges as a substitute for discriminative tests;
- retry storms when jobs fail before their first step starts.

## Platform-capacity failure

A workflow result with no executed steps and no retrievable logs is classified as:

```text
CI_PLATFORM_NOT_STARTED
```

It is neither code `PASS` nor code `FAIL`. The response is:

1. stop repeated reruns;
2. close or pause noisy draft pull requests;
3. preserve the exact head;
4. inspect account minutes, billing, runner availability and platform status outside the code path;
5. rerun once capacity is restored.

## Promotion rule

A feature may be merged only when its exact head has:

- required shared gates;
- required focused gates;
- no unresolved code failure;
- no `CI_PLATFORM_NOT_STARTED` result being presented as success;
- an explicit Claim Ceiling and rollback path.
