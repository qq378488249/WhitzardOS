# CyberGym 断点续跑设计

状态：已实现（2026-11-16）
负责人：QitOS engine + CyberGym benchmark
英文版：[cybergym_checkpoint_resume.md](cybergym_checkpoint_resume.md)

## 目标

让一个在步骤边界结束的 CyberGym 任务——墙钟预算耗尽、被外部杀掉、OOM、
WSL 重启——在下一次尝试时从最后完成的步骤继续，而不是从第 0 步重跑。

## 现状

### 墙钟超时是协作式的

- `run_cybergym_agent_task` 安装 `MaxRuntimeCriteria`
  （`qitos/benchmark/cybergym/runner.py`）并设置
  `RuntimeBudget.max_runtime_seconds`。
- 引擎在每轮循环开头和 `check_stop` 处检查预算。命中后会在步骤边界产生
  `stop_reason = "budget_time"`。单步内的 LLM 或工具调用不会被抢占，它们
  各自跑到自己的超时（API 360 秒 / 推理模型 1800 秒，shell 60 秒）。
- 外部硬杀（容器被杀、OOM、主机休眠、WSL 重启）会直接终止进程，没有任何
  落盘机会。

### checkpoint 管线存在但没有接入

- `Engine(..., checkpoint_store=store)` 会在每个步骤写入 checkpoint，位置在
  `CHECK_STOP` 和 `_finalize_step` 之后，默认 `DurabilityMode.SYNC`
  （`qitos/engine/engine.py`）。
- `SqliteCheckpointStore` 使用 WAL；`CheckpointConfig(thread_id=…)` 不传
  `checkpoint_id` 时解析到该线程最新的 checkpoint。
- `Engine.resume_from_checkpoint(config)` 存在，但从未被生产路径使用。
- 除 `Engine` 和测试外，没有任何地方传入 `checkpoint_store`；CyberGym 运行
  完全没有 checkpoint。
- CyberGym 批量脚本的 `--resume` 只是任务级：跳过已有结果行的任务。被杀的
  任务没有结果行，会从第 0 步重跑。`docs/cybergym-local-batch-results.md`
  记录了这个限制。

### 现有续跑路径的缺陷

设计时发现的、必须修掉才能真正使用的问题：

1. `resume_from_checkpoint` 用 `StateSchema` 反序列化，而不是 agent 的真实
   state 类，子类字段（例如整个 `CyberGymState`）在
   `StateSchema.from_dict(..., strict=True)` 下会报错。
2. `_active_task` 取自 `metadata["run_id"]`，续跑会把 run id 当成任务文本。
3. `Engine.run` 无条件用 trace run id 或随机 id 覆盖 `_active_run_id`，导致
   每次续跑都新开一个 checkpoint 线程，下一次续跑找不到正确的链。
4. checkpoint 不记录状态类，`StepRecord` / trace 里也没有 checkpoint id，
   续跑无法从产物中发现。
5. `resume_from_checkpoint` 用 `self.run(task_text)` 继续，丢掉了原始
   `Task` 对象以及它的预算和元数据。

## 设计决策

### D1 — checkpoint 是步骤边界产物

checkpoint 是第 N 步 `CHECK_STOP` 之后的状态。步骤内崩溃最多丢失该步的部分
工作；续跑不会重复任何已完成的步骤。步骤内续跑（重放半完成的工具调用）明确
不在范围内。

checkpoint 在循环的 `advance_step()` 之前写入，所以快照里的
`current_step = N`。续跑时会把 `state.current_step` 置为
`checkpoint.step + 1`，让续跑循环恢复与全新运行相同的不变量
（`step_id == state.current_step`）。

### D2 — 续跑恢复 state，不恢复对话

引擎在每次 `run()` 开头都会重置 memory 和 history。续跑因此拿到恢复后的
`state`，但对话历史为空。对 CyberGym 可以接受：agent 是 state-first 的
（`CyberGymState` 保存候选、尝试、笔记、GDB 计数、分析 id），系统提示每步
都从 state 重建。这个限制会写进文档；恢复 memory/history 是独立的后续工作。

### D3 — 墙钟预算按每次尝试计算

每次尝试重新获得配置的 `max_runtime_seconds`。复用已用时间会让
`budget_time` 之后的续跑立刻再次停止，等于无效。

影响：

- 一个任务的总墙钟时间可以达到 `尝试次数 × max_runtime_seconds`；由批量层
  决定尝试多少次。
- 步数预算**不会**重置：`current_step` / `max_steps` 存在 state 里，跨续跑
  累计，任务不会因为续跑突破步数上限。

### D4 — 线程标识稳定且与任务内容绑定

线程 id：`cybergym:<task_id>[:<difficulty>]`——任务 id 和难度级别标识这份工作。
任务包的 checksum 不能进线程 id：每次尝试都会重新生成任务包、换一个新的
agent_id，因此 checksum 每次都变；把 checksum 放进线程 id 会让每次尝试都变成
不同的线程，任务永远续不上。续跑改为通过 agent 的 `refresh_state` 钩子采用
当前任务包的身份（agent_id、checksum）。

每个任务一个 SQLite 文件：`<checkpoint_dir>/<task_slug>.sqlite`
（例如 `arvo_1065.sqlite`）。按任务分文件避免批量并发 > 1 时 SQLite 写竞争。

默认 `checkpoint_dir` 是 `<out_root>/checkpoints`，与 `workspace/` 同级，
因此不会被 `prepare_task_dir(fresh=True)` 清掉（runner 是故意清空任务目录的）。

### D5 — 续跑是可选项，持久化与续跑是两个开关

- `checkpoint_dir` 打开写入。
- `continue_from_checkpoint` 打开任务启动时的读取。

两者默认关闭：如果想做一次干净实验的重跑，不能悄悄继承旧状态。批量脚本暴露
`--checkpoint-dir` 和 `--continue-from-checkpoint`；后者在未给目录时默认使用
`out_root/checkpoints`。

### D6 — 只有可续跑的停止原因才继续

任务启动时检查该线程最新的 checkpoint：

| 上一轮 `state.stop_reason` | 行为 |
| --- | --- |
| 缺失 / `None` | 续跑（进程在做出停止决定前死亡） |
| `budget_time` | 续跑 |
| `interrupt` | 续跑 |
| 其它（`final`、`success`、`max_steps`、`stagnation` 等） | 清空该线程的 checkpoint，重新开始 |

`resume_from_checkpoint` 在继续前会清掉过期的 `stop_reason`，避免续跑继承
终止标记。`final_result` 永远不清空。

重新开始时丢弃旧 checkpoint，保证「线程最新 checkpoint」语义无歧义；已完成
运行的历史仍然保留在 trace 里。

### D7 — 状态类随 checkpoint 一起保存

`Checkpoint` 新增 `state_type`（模块限定类名）。续跑时按以下顺序解析：
显式 `state_cls` 参数 → `checkpoint.state_type` → `type(_active_state)` →
`StateSchema`。无法导入时记录 warning 并回退到 `StateSchema`。

SQLite store 新增 `state_type` 列，并做增量迁移（由 `PRAGMA table_info` 守卫
的 `ALTER TABLE`），旧数据库仍然可用。

### D8 — 续跑留在原 checkpoint 线程上

`Engine.run` 接受内部参数 `_resume_run_id`；`resume_from_checkpoint` 传入
`checkpoint.thread_id`。每次尝试仍然有自己的 trace run id，所以 trace 是按
尝试划分的产物，而 checkpoint 形成一条链。

## API 变化

### `qitos.checkpoint`

- `Checkpoint.state_type: Optional[str] = None`，包含在 `to_dict` /
  `from_dict` 中，`fork_checkpoint` 会复制它。
- `SqliteCheckpointStore`：新增 `state_type` 列，在 `get_tuple` / `list` 中
  读取，在 `put` 中写入。
- `CheckpointStore` ABC 不变。

### `qitos.engine.Engine`

- `run(..., _resume_run_id: str | None = None)`——内部参数，用于保持线程 id。
- `resume_from_checkpoint(config, *, task=None, state_cls=None)`：
  - 用解析出的类恢复 state；
  - 任务文本按 `task` → `state.task` → 之前的 `_active_task` 顺序取值；
  - 清掉过期的 `stop_reason`；
  - 在 `checkpoint.thread_id` 上从 `checkpoint.step + 1` 继续。
- `_save_checkpoint` 记录 `state_type`，续跑时保持 run id 稳定。
- `run_meta` 新增 `checkpoint_thread_id`、`last_checkpoint_id`、
  `resumed_from_checkpoint`、`resumed_from_step`。

### `qitos.core.AgentModule.run`

- 新增关键字参数 `resume_from: CheckpointConfig | None = None`。设置后先做与
  普通运行相同的 engine/spec 装配，再调用
  `engine.resume_from_checkpoint(...)`，并传入原始任务对象和任务参数。类型是
  宽泛的，避免 `qitos.core` 依赖 `qitos.checkpoint`。
- 新增钩子 `refresh_state(state, task, **kwargs)`，在循环继续之前对恢复出来的
  state 调用。默认原样返回；CyberGym agent 用它采用当前任务包的 agent_id 和
  checksum。

### CyberGym

- `run_cybergym_agent_task(..., checkpoint_dir=None, continue_from_checkpoint=False)`
  ——创建/打开按任务的 store，决定续跑还是重来，通过 `engine_kwargs` 传
  `checkpoint_store`，通过 `agent.run` 传 `resume_from`，在 `finally` 中关闭
  store，并在返回的 execution dict 中报告 `checkpoint_db` /
  `resumed_from_step`。
- `run_cybergym_task` 读取 `run_spec.environment["checkpoint_dir"]` 和
  `["continue_from_checkpoint"]`。
- `run_cybergym_recipe_task` / recipe CLI：`--checkpoint-dir`、
  `--continue-from-checkpoint`。
- `scripts/run_cybergym_batch.py`：同样两个参数，经 `run_spec.environment`
  透传。

## 运行流程

```
batch/recipe
  └─ run_cybergym_task(task, run_spec)
      └─ run_cybergym_agent_task(..., checkpoint_dir, continue_from_checkpoint)
          ├─ 构建 Task、agent、env、stop criteria、trace writer
          ├─ 打开 SqliteCheckpointStore(<checkpoint_dir>/<slug>.sqlite)
          ├─ latest = store.list(thread)[0]
          ├─ 决定：store.resume_from（可续跑）| 清空线程 + None
          ├─ agent.run(task, resume_from=..., engine_kwargs={checkpoint_store})
          │    └─ Engine.run | Engine.resume_from_checkpoint(task=task)
          └─ finally: store.close()
```

## 失败模式

- **store 打不开**（路径错误、权限、文件损坏）：任务直接失败并抛出 store
  错误。可选持久化不能悄悄降级；批量操作者需要知道 checkpoint 没生效。
- **运行中 checkpoint 写失败**：引擎抛错，任务失败；上一个 checkpoint 仍可
  续跑。
- **任务包变化**：线程 id 里的 checksum 让旧 checkpoint 不可达，重新开始一条
  新链。
- **同一任务并发运行、共用同一个 DB**：设计上不安全。文档声明；批量 runner
  每个任务只调度一次。
- **本次改动之前写入的旧 checkpoint**（没有 `state_type`）：回退到
  `type(_active_state)` / `StateSchema`；对子类 state 会明确报错，而不是悄悄
  丢字段。

## 可观测性

- trace summary 的 `run_meta`：`checkpoint_thread_id`、`last_checkpoint_id`、
  `resumed_from_checkpoint`、`resumed_from_step`。
- runner execution dict（嵌入 benchmark metadata）：`checkpoint_db`、
  `resumed_from_step`。
- 续跑后的尝试是一条普通 trace，有普通 `run_id`；与上一次尝试的关联是
  `resumed_from_checkpoint`。

## 测试

1. `tests/test_checkpoint_store.py`：`state_type` 在两种 store 中的往返，以及
   SQLite `ALTER TABLE` 迁移路径。
2. `tests/engine/test_checkpoint_resume.py`：使用带子类 state 的计数 agent；
   跑两步后续跑，断言：
   - 从 `checkpoint.step + 1` 继续；
   - 子类字段保留；
   - 任务文本是原始任务，不是 run id；
   - 线程 id 在续跑后保持稳定；
   - 过期的 `stop_reason` 被清空，`final_result` 保留。
3. `tests/test_benchmark_cybergym_recipe.py`：mock agent；断言 store 被创建、
   全新任务的 `resume_from` 为 `None`、预置的可续跑 checkpoint 会产生
   `resume_from` 配置、不可续跑的 checkpoint 会清空线程。
4. 验证：`pytest -q`，以及对 `qitos/core`、`qitos/engine` 的 `flake8` /
   `mypy`。

## 文档与发布面

- `CHANGELOG.md`：Added——CyberGym checkpoint/resume。
- `docs/cybergym-local-batch-results.md`：用新行为及其注意事项替换
  「没有 checkpoint/resume」的已知限制。
- CyberGym 批量使用文档 / recipe 帮助文本。
- `README.md` news 条目（中英文 README 同步）。

## 不在范围内 / 后续工作

- 步骤内续跑，或对进行中的工具调用做幂等重放。
- 从 checkpoint 恢复 memory/history。
- Async engine 对齐（`AsyncEngine` 目前仍没有 checkpoint 支持）。
- 在同一个进程内对 `budget_time` 自动重试（这属于批量层的策略）。
