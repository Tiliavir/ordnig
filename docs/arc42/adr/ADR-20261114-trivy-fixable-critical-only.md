# Trivy fails only on fixable critical findings

Status: Proposed (2026-11-14)

## Decision

The vulnerability scan fails the check only for fixable critical vulnerabilities.

## Why

The image is large (LibreOffice, JRE); unfixable findings would only be noise.
