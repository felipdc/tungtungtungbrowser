# Optional minimal browser service policy

`tungtungtungbrowser.minimal_services.MinimalServicePolicy` prepares an owned,
disposable iOS 26 browser VM with four background jobs disabled: `biomed`,
`dasd`, `healthappd` and `momentsd`. It checks their exact programs and the
guest's current protected-service list before changing any override. This
is an opt-in experiment; normal startup keeps its existing service policy.

Configuration does not stop already-running jobs. The caller must complete a
normal clean shutdown, verify the original exit and stopped clock worker, and
start a distinct boot in the same owned container. `verify()` then requires
all four jobs to be disabled and nonrunning before browser work starts. The
module sends no signal, reboot, native input or clock offer itself.

The orchestrator supplies a fresh `ownership` callback on every check. It must
observe the running container's ID, host PID and `/proc` start ticks, the host
boot ID, guest CID and launch timestamp. The returned dictionary uses these
keys: `container_id`, `host_pid`, `host_start_ticks`, `host_boot_id`, `cid` and
`launch_started_unix`. Never supply a constant dictionary in place of a live
ownership observation. The `closing` callback prevents further operations
after the orchestrator begins closure.

```python
from tungtungtungbrowser.guest import Guest
from tungtungtungbrowser.minimal_services import MinimalServicePolicy

# observe_owned_guest and closure_requested are supplied by your orchestrator.
policy = MinimalServicePolicy(
    "/external/unique-run/service-policy",
    guest=Guest(cid=42, timeout=15),
    ownership=observe_owned_guest,
    closing=closure_requested,
)
configured = policy.configure()
```

After the orchestrator completes and records that boot's normal shutdown,
starts the distinct owned boot and waits for its guest health and clock:

```python
verified = policy.verify(original_clean_exit_record)
```

The exit record must identify the original launch, an accepted native shutdown,
exit code zero, no forced termination, and a stopped clock worker from that
same launch with at least one completed offer. A timeout, failed worker or
missing exit is insufficient. The policy never requests another boot on the
caller's behalf.

Before the final normal shutdown, call `policy.restore()` while the verified
guest is still owned and usable. Restoration returns the four jobs to their
original enabled configuration and verifies every unrelated override,
including explicit enabled entries. It does not recreate original process
IDs or remove explicit enabled override entries added by the native API.
Use a cleanup instance with the same ownership observation if the normal
work callback already reports closure; cleanup still requires that exact
running guest and a completed prior verification.

Every mutation is journaled and flushed before its one request. Existing
configuration, verification and restoration records prevent replay. Partial
or uncertain work is terminal and is preserved for inspection. The caller
still owns clean guest shutdown, evidence retention and host shutdown.

The underlying private implementation completed configuration, clean reboot,
post-boot absence and enablement restoration on four phones. Two phones then
captured eight products each under a 75-second p95; the other two returned
login responses, leaving the four-phone success target unmet. That combined
trial also changed translation-cache size, so it does not isolate a CPU saving
from these four service overrides. This library port has focused lifecycle
tests; its exact public interface still needs native validation before adoption.
