# Contributing

Keep the runtime provider-neutral and the browser controller native. Public
examples and tests use reserved example domains or local diagnostic pages.
Deployment-specific tooling, credentials, external-site acceptance tests and
captured page content belong in a separate private workspace.

Use Python 3.11 or newer. Run `python3 -m unittest discover -s tests -v` and
`python3 -m compileall -q tungtungtungbrowser scripts`. For shell changes, run
`bash -n` on the changed files. Check diagnostic JavaScript with `node --check`.
Use `python3 scripts/check_public_tree.py` before preparing a source release.
The native installer tests run on Linux with a C compiler (`cc`) and the
OpenSSL 3 or 1.1 `libcrypto` runtime. They compile the installation core against
synthetic cache fixtures and real temporary files; they need no firmware or VM.

Changes to guest control need tests for the observable request contract,
transport failures and evidence preservation. Live browser validation must
record the native screenshot and requested URL. A daemon health response or an
accepted open request alone is not successful page rendering.

While iterating, validate each fix with the smallest native case that exercises
its failure and cleanup. Keep the device warm throughout a batch, prioritizing
session startup and native focus. Reserve full browser workflows for milestones
after the targeted checks pass; the source-release checks above still apply.
Preserve failed attempts and compare unchanged behavior when warming could
explain a candidate's success. A warm pass does not qualify cold startup.

For resource changes, record guest and container limits, whole-cgroup peak
memory, CPU usage and OOM events during a real browser workload. Separate cold
boot, warm application launch and in-memory resume timings. Keep the original
launch clock when retrying observers.
Use the [CPU profiling guide](docs/cpu-profiling.md) when translated guest
code dominates a host profile. Keep profile dumps and derived reports outside
the source tree, and separate instrumented diagnosis from performance runs.

Update `runtime.lock.json` deliberately and review upstream license changes.
Document patches with their target revision, reason, validation and applicable
license. Do not add firmware, guest binaries, VM disks, authentication data or
third-party release archives to the repository.

Before publishing images or binary bundles, review the actual dependencies'
license and source-distribution requirements. The source repository's MIT
license does not relicense those dependencies.
