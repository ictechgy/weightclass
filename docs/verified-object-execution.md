# Verified-object execution decision

## Production status

Production still uses an admitted absolute executable path, double observation,
and the existing foreground `Popen` boundary. It does not use descriptor
execution.

## Decision

Descriptor-based verified-object execution is deferred. The compatibility
evidence is not yet sufficient to change production behavior across Linux and
macOS. In particular, native descriptor execution and shebang-script
compatibility need a proven process-status and cleanup contract on both
platforms before adoption.

The remaining boundary is path-based: an admitted executable can still be
replaced after the final observation and before path-based spawn. Admission
hardening narrows that race but does not claim to remove it.
