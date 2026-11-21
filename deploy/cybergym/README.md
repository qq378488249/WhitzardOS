# CyberGym 部署资产

这个目录是完整部署包的一部分。建议先读上一级的
[`deploy/README.md`](../README.md)；那里有从导入镜像到启动跑批的完整步骤。

| 文件 | 用途 |
| --- | --- |
| `.env.compose.example` | Docker Compose 部署模板；复制成 `.env` |
| `compose.yaml` | 单服务 Compose，仅运行 batch；server 在外部 |
| `compose.build.yaml` | 可选覆盖：从源码重建 runner 镜像 |
| `compose-preflight.sh` | 启动前自检 |
| `opencode_relay.py` | 可选：本地转发 OpenCode 请求并补齐客户端头 |
| `prune_run_logs.py` | 可选：磁盘紧张时清理已完成任务的轨迹与 checkpoint |
| `score.sh` | 在 runner 镜像里运行 server 判分脚本 |
| `report.sh` | 手动查看某个批次的简洁每任务报告（默认读 `*.report.jsonl`） |
| `trace_report.sh` | 在 traces 目录下查看每个已完成任务的成功/失败原因 |
| `Dockerfile.runner` | runner 镜像构建文件 |
| `Dockerfile.runner.dockerignore` | 构建上下文排除规则 |
| `docker-entrypoint.sh` | 容器入口，校验 `.env` 并启动 launcher |

## 最短启动

```bash
# 在 deploy/ 目录
docker load -i images/cage-claude-code-cyberdebug.tar.gz
docker load -i images/qitos-cybergym-runner-1.0.13.tar.gz

cp cybergym/.env.compose.example cybergym/.env
chmod 600 cybergym/.env
$EDITOR cybergym/.env

bash cybergym/compose-preflight.sh cybergym/.env

docker compose --env-file cybergym/.env \
  -f cybergym/compose.yaml up -d
```

验证服务由你在别处启动，`.env` 里的 `SERVER_URL` 指向它。同机使用时用
`http://host.docker.internal:6441`，server 必须监听 `0.0.0.0`，不能只绑
`127.0.0.1`。

## OpenCode 网关转发（可选）

OpenCode Zen/Go 会拒绝没有 coding-agent `User-Agent` 和 `x-opencode-session` 的请求。在跑批宿主机启动转发器：

```bash
RELAY_PORT=8899 python3 deploy/cybergym/opencode_relay.py
```

然后把 `.env` 里的 `BASE_URL` 写成 `http://host.docker.internal:8899/v1`。上游地址用 `OPENCODE_UPSTREAM` 覆盖；设置 `OPENCODE_SESSION` 可固定缓存会话，留空则每个转发进程生成一个。

## 磁盘空间兜底清理（可选）

长批次会在每个任务留下数百 MB 轨迹、工作区和 checkpoint。宿主机上启动清理器：

```bash
python3 deploy/cybergym/prune_run_logs.py --run-root /path/to/run --min-free-gb 3
```

它监视 `results.report.jsonl`，只在 `PRUNE_HOST_MOUNT`（默认 `/mnt/d`，Windows 宿主机上设成 `/d` 或对应挂载点）剩余空间低于阈值时才删掉**已通过任务**的 `traces/`、`workspace/` 和 `checkpoints/`；平时保留轨迹供复盘。

清理判定与打分规则一致：`stop_reason == "success"`、服务端 `vul_exit_code != 0`（且不等于 300）、`fix_exit_code == 0`。超时（`budget_time`）、失败、尚无服务端判定的任务一律不动，确保排查素材不丢。

## 从源码重建

```bash
# 在仓库根目录
docker compose --env-file deploy/cybergym/.env \
  -f deploy/cybergym/compose.yaml \
  -f deploy/cybergym/compose.build.yaml build
```

