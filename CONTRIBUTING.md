# 贡献指南

欢迎提交可复现的 Bug、文档修正和小范围改进。先说明触发条件、预期行为、实际行为和版本，附上不含令牌的错误信息。

## 本地验证

```powershell
uv sync --frozen
uv run python scripts/check_reliability.py
uv run python scripts/check_release.py
cd web
npm ci --no-audit --no-fund
npm run build
```

回归脚本在临时目录注入假账号，不调用真实上游。不要直接运行所有历史手动探测脚本：其中部分会使用真实接口。增加测试时沿用隔离回归入口。

保持以下行为：显式型号优先；目录不能靠回显验证；失败不消耗成功额度；续等沿用原账号与会话；JSON 损坏不能被空数据覆盖；用户限制在公共图片入口执行。

新增模型请提供脱敏的真实结果和上游返回型号，避免只改下拉框名称。外部 API 功能应说明费用和验收状态。

提交前查看 `git diff --cached --name-only`；配置、账号、图片、缓存和构建包都不能提交。保留原作者署名和 LICENSE。
