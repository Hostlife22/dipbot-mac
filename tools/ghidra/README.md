# Ghidra audit script

`AuditFunctions.java` runs inside full Ghidra, checks the reference EXE SHA-256,
labels selected public constant slots and exports seven functions for local
comparison. It does not execute the reference program. Decompiled C is a tool
approximation, not recovered Python source.

On this Mac the dedicated launchers are:

```bash
dipbot-ghidra
dipbot-ghidra-headless
```

They select `/usr/local/opt/openjdk@21` for
`/usr/local/opt/ghidra/libexec`, leaving the system Java unchanged.
Projects and raw outputs live outside Git in
`~/.local/share/dipbot-re-audit/`.

Example (use an unused project name and an explicit EXE path):

```bash
mkdir -p "$HOME/.local/share/dipbot-re-audit/projects"
dipbot-ghidra-headless "$HOME/.local/share/dipbot-re-audit/projects" Review \
  -import /path/to/original.exe -analysisTimeoutPerFile 180 -max-cpu 2 \
  -scriptPath "$PWD/tools/ghidra" -postScript AuditFunctions.java \
  "$HOME/.local/share/dipbot-re-audit/review-decompiled"
```

An analysis timeout limits coverage; it is not a successful full-program audit.
Inspect every `AUDIT_OK`/`AUDIT_FAILED` and the analysis log. The optional exports
are intentionally not stored in Git.

`NuitkaPrototypes.java` applies eight inferred helper prototypes and reruns the
seven exports. Use `-noanalysis -postScript NuitkaPrototypes.java OUTPUT` on the
existing imported project; inspect all eight `PROTOTYPE` and seven `AUDIT_OK`
markers, since script errors may not make headless return a nonzero status.
