# CyberGym 部署资产

这个目录是完整部署包的一部分。建议先读上一级的
[`deploy/README.md`](../README.md)；那里有从导入镜像到启动跑批的完整步骤。

| 文件 | 用途 |
| --- | --- |
| `.env.example` | 全部配置和密钥模板；复制成 `.env` |
| `compose.yaml` | 单服务 Compose，仅运行 batch；server 在外部 |
| `compose.build.yaml` | 可选覆盖：从源码重建 runner 镜像 |
| `compose-preflight.sh` | 启动前自检 |
| `Dockerfile.runner` | runner 镜像构建文件 |
| `Dockerfile.runner.dockerignore` | 构建上下文排除规则 |
| `docker-entrypoint.sh` | 容器入口，校验 `.env` 并启动 launcher |
| `qitos-cybergym-server@.service` | 可选：systemd 验证服务 |
| `qitos-cybergym-batch@.service` | 可选：systemd 跑批 |

## 最短启动

```bash
# 在 deploy/ 目录
docker load -i images/cage-claude-code-cyberdebug.tar.gz
docker load -i images/qitos-cybergym-runner-local.tar.gz

cp cybergym/.env.example cybergym/.env
chmod 600 cybergym/.env
$EDITOR cybergym/.env

bash cybergym/compose-preflight.sh cybergym/.env

docker compose --env-file cybergym/.env \
  -f cybergym/compose.yaml up -d
```

验证服务由你在别处启动，`.env` 里的 `SERVER_URL` 指向它。同机使用时用
`http://host.docker.internal:6441`，server 必须监听 `0.0.0.0`，不能只绑
`127.0.0.1`。

## 从源码重建

```bash
# 在仓库根目录
docker compose --env-file deploy/cybergym/.env \
  -f deploy/cybergym/compose.yaml \
  -f deploy/cybergym/compose.build.yaml build
```

## systemd（可选）

两个单元调用
`qitos/benchmark/cybergym/agent/scripts/launch_v14_gdb_149.sh`，路径默认是
149 布局，可用环境变量覆盖。复制同一份模板到每个组：

```bash
sudo install -d -m 0750 -o root -g qitos /etc/qitos/cybergym
sudo install -m 0640 -o root -g qitos \
  deploy/cybergym/.env.example /etc/qitos/cybergym/level1-a.env
```

```bash
sudo ln -sf /opt/qitos-bench/qitos/qitos/benchmark/cybergym/agent/scripts/launch_v14_gdb_149.sh \
  /usr/local/bin/qitos-cybergym-launch

sudo cp deploy/cybergym/qitos-cybergym-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qitos-cybergym-batch@level1-a.service
```

batch 单元对同名 server 单元声明了 `Requires=`。
