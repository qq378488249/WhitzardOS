#!/usr/bin/env python3
"""Prune completed CyberGym task artifacts when the host disk runs low.

Long batches write multi-hundred-MB traces, workspaces, and checkpoints per
task, which can exhaust the host disk before the run finishes. This watcher
polls the batch's results file, but only deletes a completed task's traces,
workspace, and checkpoint once Windows free space drops below a threshold.
Deleting earlier than that keeps the traces available for analysis without
re-growing the disk.

  python3 prune_run_logs.py --run-root /path/to/run --min-free-gb 3
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import time

HOST_MOUNT = os.environ.get("PRUNE_HOST_MOUNT", "/mnt/d")


def _size(path: str) -> int:
    if not os.path.exists(path):
        return 0
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def _completed_task_ids(run_root: str) -> set[str]:
    ids: set[str] = set()
    path = os.path.join(run_root, "results.report.jsonl")
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                task_id = str(row.get("task_id") or "")
                if task_id.startswith("arvo:"):
                    ids.add(task_id.split(":", 1)[1])
    except FileNotFoundError:
        pass
    return ids


def _prune(run_root: str, task_id: str) -> int:
    removed = 0
    for pattern in (
        f"{run_root}/traces/*_arvo_{task_id}_*",
        f"{run_root}/workspace/arvo_{task_id}",
    ):
        for path in glob.glob(pattern):
            removed += _size(path)
            shutil.rmtree(path, ignore_errors=True)
    for path in glob.glob(f"{run_root}/checkpoints/arvo_{task_id}.sqlite*"):
        removed += _size(path)
        try:
            os.remove(path)
        except OSError:
            pass
    return removed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--min-free-gb", type=float, default=3.0)
    args = parser.parse_args()
    threshold = int(args.min_free_gb * 1024**3)

    pruned: set[str] = set()
    pending: set[str] = set()
    print(
        f"[prune] watching {args.run_root} every {args.interval}s; "
        f"prune when {HOST_MOUNT} free < {args.min_free_gb:g}GB",
        flush=True,
    )
    while True:
        pending |= _completed_task_ids(args.run_root) - pruned
        free = shutil.disk_usage(HOST_MOUNT).free
        if pending and free < threshold:
            freed = 0
            for task_id in sorted(pending):
                freed += _prune(args.run_root, task_id)
            subprocess.run(["fstrim", "-a"], check=False, capture_output=True)
            pruned |= pending
            print(
                f"[prune] free={free / 1024**3:.2f}GB < "
                f"{args.min_free_gb:g}GB; pruned {sorted(pending)} "
                f"freed={freed / 1e9:.2f}GB",
                flush=True,
            )
            pending.clear()
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
