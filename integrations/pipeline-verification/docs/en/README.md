# Pipeline Verification Environments

🌐 [日本語](../ja/README.md) | **English** (this page)

## Overview

Reproducible, CI-verifiable test environments and verification records for the
five real-time telemetry pipeline patterns. This bilingual entry point mirrors
the top-level [README](../../README.md); read that for the full reuse map and
staged order.

## Two Delivery Layers

The CI-verifiable scaffolding layer (Stage 0) needs no live AWS account and
incurs no cost. The sample-run validation layer requires a running FSx for
ONTAP file system, is billed, touches irreversible resources, and never runs in
CI.

## Staged Implementation Order

Priority follows net-new value: P2 → P5 → P4 → P1, with pattern 3 as
reuse-and-record only.

## Reuse Map

Reuse references and net-new declarations live in
[`shared/reuse-references.yaml`](../../shared/reuse-references.yaml), the single
input to the Duplication_Check gate.

## Scope Boundary

Verification environments and records only. Production-grade deployment,
multi-region HA, and load/scale benchmarks beyond a single sample run are out of
scope unless explicitly pursued.
