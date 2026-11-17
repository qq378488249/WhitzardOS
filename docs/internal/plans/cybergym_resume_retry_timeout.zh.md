# CyberGym 批量 `--resume` 超时任务重跑设计

Status: implemented (2026-11-17)
Owner: QitOS benchmark + CyberGym deployment
English: [cybergym_resume_retry_timeout.md](cybergym_resume_retry_timeout.md)

## 背景

批量跑批使用 `scripts/run_cybergym_batch.py --resume`，部署启动器
`launch_v14_gdb_149.sh` 每次都固定带 `--resume`。当前
`execute_example_jobs` 的 `done` 集合直接取自 `OUTPUT_JSONL` 里的所有行，
不区分 `success` 和 `stop_reason`，因此一个停在 `budget_time` 的任务在下一
次跑批时会被当作已完成，永远不会重新排队。

与此同时，checkpoint 能力已经支持 `budget_time` / `interrupt` 的续跑
（`RESUMABLE_STOP_REASONS`），但前提是任务必须被重新调度一次。缺少的正是这
一步重新调度。

## 目标

- `--resume` 在显式开启重跑时，把上一轮停在 `budget_time` / `interrupt` 的
  任务重新排队。
- 限制每个任务的总尝试次数，避免反复超时无限消耗预算。
- 结果文件与简报文件对每个任务只保留最新一行，统计和 `report.sh` 不重复计
  数。
- 默认行为不变：其它 benchmark（cybench / gaia / tau_bench）共享同一个
  helper，不传新参数时语义与现在完全一致。

## 非目标

- 不改运行内的 LLM 重试策略（401、5xx 仍按既有恢复策略处理）。
- 不按失败原因细分重跑条件（例如“只有 401 导致的超时才重跑”）。
- 不改 checkpoint 的写入与恢复语义。

## 设计

### D1 — 重跑条件是显式的 stop_reason 集合

`execute_example_jobs` 新增两个关键字参数：

```python
retry_stop_reasons: Optional[Iterable[str]] = None,
max_attempts: int = 3,
```

- 仅在 `resume=True` 时生效。
- 一行结果参与重跑的判定：
  `str(row.stop_reason) in retry_stop_reasons and attempts(row) < max_attempts`
  - `success=True` 的行即使 stop_reason 命中也不会重跑（CyberGym 成功行的
    stop_reason 是 `success`，天然不在集合内）。
- `attempts` 存在结果行的 `metadata["attempts"]` 中，表示该任务已完成的尝试
  次数；旧结果没有这个字段时按 `1` 处理。
- 重跑产生的新行写入 `metadata["attempts"] = 旧值 + 1`；首次运行写 `1`。

CyberGym 侧传入的集合直接复用
`qitos/benchmark/cybergym/runner.py` 中的常量。为避免出现第二份定义，把
`_RESUMABLE_STOP_REASONS` 提升为公开的 `RESUMABLE_STOP_REASONS`，checkpoint
续跑与批量重跑共用同一个来源。

### D2 — 结果文件按任务去重，保留最新一行

现在的写入策略是“每完成一个任务立即 append + flush + fsync”，这对崩溃安全很
重要，必须保留。重跑会让同一个任务出现多行，因此：

- 运行结束时按 `job_key`（缺省 `task_id`）去重，保留**最后一次**出现的行，
  用临时文件 + `os.replace` 原子重写 `OUTPUT_JSONL`。
- `REPORT_JSONL` 同步按 `task_id` 去重后重写，维持部署文档承诺的“每任务一
  行”。
- 如果进程在重写前崩溃，文件里会暂时留下旧行和新行；下一次运行读入后仍然以
  最新行为准，并在结束时收敛为一行。
- 只有开启 `retry_stop_reasons` 时才做去重，未开启时完全保留现有 append 语
  义。

去重键与现有 `done` 集合一致：`metadata["job_key"]`，回退 `task_id`。

### D3 — 命令行开关

`scripts/run_cybergym_batch.py` 增加：

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `--retry-timeout` | 关闭 | 配合 `--resume`，重跑上一轮 `budget_time` / `interrupt` 的任务 |
| `--max-attempts` | `3` | 开启重跑时，每个任务最多完成的尝试次数 |

`--retry-timeout` 是布尔开关，不带阈值：超时事实已经由 `stop_reason` 精确表
达，`elapsed_seconds` 基本都贴着预算上限，再做耗时筛选没有信息量。需要控制
成本时用 `--max-attempts`。

### D4 — 部署层

`launch_v14_gdb_149.sh` 增加两个环境变量：

- `RETRY_TIMEOUT`，默认 `1`。启动器本来就固定 `--resume`，默认开启更符合
  “重启后继续推进未完成任务”的运维预期；设为 `0` 恢复旧行为。
- `MAX_ATTEMPTS`，默认 `3`。

两者写入运行时配置文件，并透传给批处理脚本。`.env.compose.example` 和
`.env.systemd.example` 同步增加说明。

### D5 — 与 checkpoint 续跑的关系

重跑只决定“任务是否重新排队”。是否从最后一步继续由现有
`CONTINUE_FROM_CHECKPOINT` 决定：

- `CONTINUE_FROM_CHECKPOINT=1` 且上一轮停在 `budget_time` / `interrupt`：
  从最后一个完成的步骤继续。
- `CONTINUE_FROM_CHECKPOINT=0`：重新开始一次全新尝试（checkpoint 链会被清
  理）。

## 行为矩阵

| 结果行状态 | 未开启重跑 | 开启重跑 |
| --- | --- | --- |
| 成功行 | 跳过 | 跳过 |
| `budget_time`，attempts < 上限 | 跳过 | 重跑（attempts + 1） |
| `budget_time`，attempts ≥ 上限 | 跳过 | 跳过 |
| `interrupt`，attempts < 上限 | 跳过 | 重跑 |
| 其它失败原因 | 跳过 | 跳过 |
| 文件里没有该任务 | 运行 | 运行 |

## 风险与权衡

- 反复超时消耗预算：由 `max_attempts` 封顶，超限后任务回到“跳过”状态。
- 去重重写是一次全文件写：只在开启重跑时发生，且用原子替换，与现有结束阶段
  写整份文件的风险相当。
- 旧结果行没有 `attempts` 字段：按 1 次处理，一次重跑后即补上计数。
- 报告文件历史被折叠：traces 目录仍保留每次尝试的完整记录，简报只服务实时
  观察。

## 验证

- `pytest -q tests/test_benchmark_incremental_results.py`
- 新增用例覆盖：超时重跑、成功行不重跑、attempts 封顶、重复行收敛、报告折
  叠、未开启时行为不变。
- 手工：在测试结果文件里写入一行 `budget_time`，用 `--resume
  --retry-timeout` 跑一次，确认任务被重新调用、文件只剩一行且
  `metadata.attempts == 2`。
