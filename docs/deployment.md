# 部署说明

新手优先使用 [Windows 成品包流程](小白使用指南.md)，不需要 Docker。

## Docker 源码构建

先复制 `config.example.json` 为私密的 `config.json`，填写自己的长随机 `auth-key`。不要把这份配置提交到 Git。也可在已安装 Python 的环境运行 `python scripts/init_local_config.py` 生成随机配置。

```bash
docker compose up -d --build
```

默认从本分支源码构建，端口 `127.0.0.1:3000`，挂载 `./data` 和 `./config.json`。镜像只包含空密钥的配置模板，不烘焙本地私密配置。目录和用户图片额度均按单进程维护。

```bash
docker compose logs --tail=100 app
docker compose down
```

首次运行仍需导入自己的账号和完成模型验证；没有可用验证记录时目录为空。Docker 路径保留，但本版 Windows 包验收不等于 Docker 运行验收。

## 外部访问

默认绑定 loopback。局域网/公网使用需要自行调整端口绑定、配置 HTTPS 和访问控制。以普通用户密钥分配权限，设置限额，保留私密备份。不要直接把管理员面板和全部图片数据暴露到公网。

代理地址应从容器可达。Windows Docker Desktop 访问宿主机代理可使用 `host.docker.internal`；不要把宿主机端口误当作容器内的 loopback 端口。

`docker-compose.warp.yml` 是继承的高级代理方案，不是新手必需，也不代表本版已完成其端到端验收。
