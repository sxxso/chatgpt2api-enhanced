# ChatGPT2API Enhanced

基于 [basketikun/chatgpt2api](https://github.com/basketikun/chatgpt2api) 的自托管改进版：在线画图、参考图编辑、账号池、OpenAI 兼容接口，以及图片任务排队和恢复。

**第一次使用：看 [小白使用指南](docs/小白使用指南.md)。** 这是独立维护的社区分支，与 OpenAI 无隶属关系。原作者署名和 MIT 许可证完整保留，来源见 [NOTICE](NOTICE)。

## Windows 最短路径

1. 从 [Releases](https://github.com/sxxso/chatgpt2api-enhanced/releases/latest) 下载 `ChatGPT2API-Enhanced-v1.8.1-Windows.zip`，先完整解压。该包带有构建好的面板。
2. 安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，双击 `首次安装.cmd`。它会下载 Python 3.13、安装依赖并生成独立管理员密钥；这个包不需要 Node.js 或 Docker。
3. 双击 `启动.cmd`，打开 `http://127.0.0.1:3000`。用记事本打开本地 `config.json`，复制 `auth-key` 的值登录。
4. 在「号池管理 → 导入」添加自己的账号。仓库和发布包不提供账号、令牌或模型额度。
5. 双击 `验证默认生图模型.cmd`，阅读提示后输入 `YES`。这会使用一次真实图片请求；验证通过后刷新画图页。

GitHub 自动生成的 **Source code.zip** 不带面板，首次安装还需要 Node.js。导入账号后模型列表为空，通常表示还没有通过验证的记录，并非安装失败。

## 改进内容

| 改进 | 行为 |
| --- | --- |
| 任务可靠性 | 修复失败重复记账和槽位误释放；超时后可查询原会话 |
| 图片队列 | 全局并发、排队容量、等待超时、位置和取消排队 |
| 用户限制 | 每日图片数、独立并发、允许的图片型号和到期时间 |
| 统一模型目录 | API 和页面展示近期验证通过、没有异常回退的条目 |
| JSON 保护 | 原子替换、上一份有效 `.bak`、损坏报错和保存回滚 |
| 页面改进 | 号池额度排序、按用户分开的本地图片历史、统一默认值 |
| 首次安装 | 随机管理员密钥、一键安装/启动/停止和模型验证入口 |

配置示例默认生图为 `gpt-image-2.6-sunburst`，对话和搜索为 `gpt-6`。这是本项目发送给上游的标识；能否使用由自己的账号、网络和当时的上游服务决定，不构成对模型权重版本或可用性的保证。**发布包不附带维护者的模型实测记录，也不伪造“已验证”结果。**

## 从源码安装

已验证构建环境为 Python 3.13、Node.js 22、Windows PowerShell 5.1。运行后端使用单进程。

```powershell
git clone https://github.com/sxxso/chatgpt2api-enhanced.git
cd chatgpt2api-enhanced
.\首次安装.cmd
.\启动.cmd
```

源码安装需先准备 uv 和 Node.js。安装入口使用 `uv.lock` 和 `web/package-lock.json`，检查 TypeScript 并构建面板。日常不需要另开前端开发服务。Docker 用户见 [部署说明](docs/deployment.md)。

## API

- Base URL：`http://127.0.0.1:3000/v1`
- Authorization：`Bearer <本项目管理员或普通用户密钥>`
- 模型列表：`GET /v1/models`
- 文生图：`POST /v1/images/generations`
- 参考图编辑：`POST /v1/images/edits`
- 对话与 Responses：`POST /v1/chat/completions`、`POST /v1/responses`

生成和编辑省略 `model` 时使用默认生图型号，显式型号优先。多图、编辑、Chat/Responses 图片请求和续等共享图片执行限制。文本聊天不受“每日图片额度”控制。

## 验证与边界

```powershell
uv sync --frozen
uv run python scripts/check_reliability.py
uv run python scripts/check_release.py
```

隔离回归用假账号、临时数据和本地图片，不使用真实账号。CI 检查后端、前端构建和发布文件边界。真实账号连通性需要单独验证。

- 目前按单 Python 进程协调队列和限额，不要直接增加 Uvicorn workers。
- 2K/4K 请求不保证对应的实际像素，下载后以文件尺寸为准。
- 历史用户分区是浏览器本地存储隔离，不等于所有图片文件都有独立访问权限。
- PPT/PSD、Codex 图片路由、外部存储和 WARP 等继承功能保留代码，不属于本版完整验收范围。
- 默认仅监听本机。开源仓库不等于公开本机面板；对外部署需自行完成 HTTPS、访问控制和备份。

## 文档与参与

- [小白使用指南](docs/小白使用指南.md)：解压到第一张图、代理、权限、故障处理和备份。
- [改进说明与验证边界](docs/改进说明.md)
- [贡献指南](CONTRIBUTING.md)
- [安全与私密数据](SECURITY.md)
- [上游协议笔记](docs/upstream-sse-conversation.md)

提交问题只需版本、错误和脱敏操作步骤。不要上传 `config.json`、`data/`、账号 JSON、令牌和代理密码。请遵守上游服务规则及适用法律。
