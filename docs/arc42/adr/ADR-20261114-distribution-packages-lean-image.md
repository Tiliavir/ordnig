# Distribution packages, lean image

Status: Proposed (2026-11-14)

## Decision

Tools come as Ubuntu packages, not pinned pip versions. Base image: Ubuntu LTS only (26.04 since 0.8.1), pinned by digest (Dependabot), `apt-get upgrade` at build. Only what ordnig calls is installed; veraPDF runs on a `jlink` runtime, installer and JDK stay in a build stage. Each build records all tool versions.

## Why

Fewer moving parts, security fixes from the distribution, smaller image (1160 → 877 MB). The smoke test on amd64 and arm64 guards every build, since veraPDF only offers a "latest" installer. LTS because an interim release (25.10) ran out of security updates after nine months.
