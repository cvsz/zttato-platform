# Release Protection

This document defines the repository-controlled release protection baseline and the GitHub settings that must be enforced before a release can be treated as protected.

## Evidence boundary

Repository workflows can prove CI/security checks, container vulnerability scanning, SBOM generation, and source-bundle provenance. They cannot prove that GitHub branch/ruleset administration is enabled. Check GitHub's repository settings separately and record that evidence.

The `Analyze Python and Actions` status is emitted by an aggregator that fails unless every CodeQL language job succeeds. The CodeQL matrix separately analyzes GitHub Actions workflows, JavaScript/TypeScript, and Python.

## Required main-branch policy

Target: `main`.

Required behavior:

- require changes through a pull request;
- block force pushes;
- block branch deletion;
- require the branch to be up to date before merge;
- require conversation resolution;
- require the following checks before merge:
  - `python (3.12)`;
  - `python (3.13)`;
  - `repository-baseline`;
  - `container`;
  - `postgres-schema`;
  - `Analyze Python and Actions` (stable aggregator for the CodeQL matrix);
  - `dependency-review`;
  - `Container security and SBOM`.
- require at least one independent approving review when an authorized second reviewer exists. A repository owner cannot count their own approval as independent review; do not mark the review gate PASS without real reviewer evidence.
- do not add broad bypass actors for normal development. Emergency bypass must be explicitly auditable and followed by a PR/evidence review.

## Required release-tag policy

Protect tags matching `v*` against update and deletion. Release tags must use SemVer-style names such as `v1.2.3`.

The Release Protection workflow refuses a release evidence bundle when:

- the tag is not SemVer-style;
- the tagged commit is not contained in `main`;
- the container has a HIGH or CRITICAL vulnerability detected by the configured release scanner;
- the SPDX SBOM cannot be generated or validated;
- provenance attestation cannot be generated.

The workflow deliberately does **not** create or publish a GitHub Release and does not deploy production. It only produces release evidence.

## Release workflow

`.github/workflows/release-protection.yml` runs:

1. on every pull request into `main`, building the candidate image, scanning HIGH/CRITICAL vulnerabilities, and generating/validating an SPDX JSON SBOM;
2. on `v*` tag pushes, running the same security gate and then producing a deterministic source archive, SHA-256 manifest, workflow artifact, and GitHub artifact attestation;
3. on manual dispatch from `main` for a non-publishing rehearsal.

All third-party Actions in the workflow are pinned to immutable full commit SHAs.

## Pre-release evidence

Before creating a release tag, retain links or identifiers for:

- CI run;
- CodeQL run;
- Dependency Review run for the merged PR;
- Release Protection run;
- exact `main` commit SHA;
- SBOM artifact;
- vulnerability scan result;
- source bundle SHA-256;
- artifact attestation;
- isolated backup/restore evidence;
- known-good rollback evidence;
- secret/history scan result;
- external TikTok review/Sandbox evidence where that capability is part of the claimed release.

Passing this workflow is necessary supply-chain evidence, not proof that every production gate in `docs/PRODUCTION_GATES.md` has passed.
