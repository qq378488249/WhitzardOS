# 镜像包

本目录包含跑批所需的两份离线镜像。都基于 `linux/amd64`。

| 文件 | 镜像 | 大小（压缩后） | 用途 |
| --- | --- | --- | --- |
| `cage-claude-code-cyberdebug.tar.gz` | `cage/claude-code:cyberdebug` | ≈ 290 MB | 每个任务一个 agent 容器 |
| `qitos-cybergym-runner-1.0.9.tar.gz` | `qitos-cybergym-runner:1.0.9`、`qitos-cybergym-runner:local` | ≈ 250 MB | 跑批控制容器 |

## 导入

```bash
docker load -i deploy/images/cage-claude-code-cyberdebug.tar.gz
docker load -i deploy/images/qitos-cybergym-runner-1.0.9.tar.gz
```

确认标签：

```bash
docker images | grep -E 'cage/claude-code:cyberdebug|qitos-cybergym-runner'
```

## 校验

```bash
cd deploy/images && sha256sum -c SHA256SUMS
```

## 说明

- 镜像标签已经固定，`compose.yaml` 直接使用 `qitos-cybergym-runner:local` 和
  `cage/claude-code:cyberdebug`，导入后不需要重新打 tag。runner 包同时带有
  `qitos-cybergym-runner:1.0.9` 版本标签，便于按版本核对部署；`1.0.2` 到
  `1.0.8` 的 tar 包保留，可用于回滚（`1.0.8` 是 staged-binary 可见性修复版但
  不含超时重跑，`1.0.7` 早于两者，`1.0.6` 早于断点续跑，`1.0.5` 早于服务端
  vul/fix 判定修复，`1.0.4` 早于每任务简报功能）。
- runner 当前版本 `1.0.9` 在 `1.0.7` 的基础上加入了超时任务重跑：
  `--resume --retry-timeout` 把上一轮停在 `budget_time` / `interrupt` 的任务
  重新排队，`--max-attempts`（默认 3）封顶反复超时，结果行记录 `attempts` 并
  用最新一次替换旧统计行；部署层默认 `RETRY_TIMEOUT=1`、`MAX_ATTEMPTS=3`
  （详见 [CyberGym 文档](../../docs/benchmarks/cybergym.mdx)）。注意本包从
  `qitos_cybergym_v2` 线的源码构建，**不含** `1.0.8` 的 staged-binary 可见性
  修复（该修复在 `qitos_cybergym` 线上，需要动态分析看到 `/out` 时回滚 `1.0.8`）。
- `1.0.7` 起具备断点续跑：按任务持久化 checkpoint（`--checkpoint-dir`），并在
  `--continue-from-checkpoint` 时让撞墙钟、被 OOM 杀掉或被外部中断的任务从
  最后完成的步骤继续。同时带上 `minimax` 模型家族的 64k 输出预算与 30 分钟请求
  超时，以及构建期 pip 的超时/重试参数。
- `1.0.6` 的能力继续保留：统一 LLM 错误重试策略（HTTP `401`、网关
  `502/503/504`、超时等按 `QITOS_MAX_RECOVERIES` 重试并渐进退避）；损坏工具调用
  的防护（泄漏的 `<｜DSML｜...>` 标记在解析前剥离并按 `wait` 重试，
  连续三次失败才以 `native_text_final_degraded` 降级结束，CyberGym agent 会在
  下一轮 observation 附加一次性修复提示）；CyberGym 报表把 `final` 统计改为
  `final_without_submit`；放行用 Python 构造 PoC 的 bash 命令；跑批结果每完成
  一个任务就立即追加写入 `OUTPUT_JSONL`（flush + `fsync`），崩溃重启只会重跑
  正在进行的那个任务；同时写一份简洁的 `REPORT_JSONL`（最终结果、耗时、失败
  原因、vul/fix exit code），每完成一个任务追加一行。
- 两份镜像都包含 `linux/amd64` 平台；ARM 服务器需要重新构建或使用对应平台镜像。
- 镜像内容对应构建时的仓库工作区，不包含 CyberGym 源码、任务语料、漏洞二进制和
  验证服务；这些仍在宿主机上挂载或单独启动。
