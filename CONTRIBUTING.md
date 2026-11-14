# Contributing

## Checks

```sh
python3 -m unittest discover -s tests -v        # unit tests, stdlib only
docker build -t ordnig:test .
docker run --rm -v "$PWD/tests:/tests:ro" --entrypoint bash ordnig:test /tests/smoke.sh
```

CI runs both, the smoke test on amd64 and arm64, plus shellcheck and a Trivy
scan. `main` accepts changes only through pull requests with green checks: ruleset
[`.github/rulesets/main.json`](.github/rulesets/main.json), imported under
Settings → Rules → Rulesets → New ruleset → Import a ruleset.

## Releasing

In one pull request: add `## [x.y.z] - YYYY-MM-DD` to [CHANGELOG.md](CHANGELOG.md)
(or rename *Unreleased*). Merge it. The release workflow sees the untagged
version, runs the full check, publishes `ghcr.io/tiliavir/ordnig:x.y.z` and
`:latest` (amd64, arm64, with SBOM and provenance), and creates the tag
`vx.y.z` with a GitHub release of the changelog section. Do not push tags by hand.
