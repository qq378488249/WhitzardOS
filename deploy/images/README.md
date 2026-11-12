# 镜像包

本目录包含跑批所需的两份离线镜像。都基于 `linux/amd64`。

| 文件 | 镜像 | 大小（压缩后） | 用途 |
| --- | --- | --- | --- |
| `cage-claude-code-cyberdebug.tar.gz` | `cage/claude-code:cyberdebug` | ≈ 290 MB | 每个任务一个 agent 容器 |
| `qitos-cybergym-runner-1.0.2.tar.gz` | `qitos-cybergym-runner:1.0.2`、`qitos-cybergym-runner:local` | ≈ 245 MB | 跑批控制容器 |

## 导入

```bash
docker load -i deploy/images/cage-claude-code-cyberdebug.tar.gz
docker load -i deploy/images/qitos-cybergym-runner-1.0.2.tar.gz
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
  `qitos-cybergym-runner:1.0.2` 版本标签，便于按版本核对部署。
- runner 当前版本 `1.0.2` 包含统一的 LLM 错误重试策略：HTTP `401`、网关
  `502/503/504`、超时以及模型调用抛出的其它异常都会按
  `QITOS_MAX_RECOVERIES` 重试，重试间隔渐进递增
  （`QITOS_RECOVERY_BACKOFF` 基础 1 秒、逐次翻倍，上限
  `QITOS_RECOVERY_BACKOFF_MAX` 默认 30 秒），不再立即以
  `unrecoverable_error` 终止。
- 两份镜像都包含 `linux/amd64` 平台；ARM 服务器需要重新构建或使用对应平台镜像。
- 镜像内容对应构建时的仓库工作区，不包含 CyberGym 源码、任务语料、漏洞二进制和
  验证服务；这些仍在宿主机上挂载或单独启动。
