# Javis Logs

当前目录只保留项目总账和两份主运行日志。

## Project Ledger

- `JAVIS_MASTER_PLAN_AND_LOG_INDEX_2026-07-27.md`: 当前状态、App 路线、恢复入口和重大验证记录。

## Runtime Logs

- `javis_8080.log`: 最近保留的主服务标准输出。
- `javis_8080.err.log`: 最近保留的主服务错误输出。

旧时间戳 `server_*.log` 已于 2026-07-27 批准删除。它们不再作为项目状态来源。

## Future Location

App V1 完成数据外置后，运行日志写入:

```text
%APPDATA%\Javis\logs\app
%APPDATA%\Javis\logs\runtime
%APPDATA%\Javis\logs\audit
%APPDATA%\Javis\logs\crash
```

日志不得包含 API Key、密码、令牌、原始音频、原始截图或未脱敏敏感输出。

