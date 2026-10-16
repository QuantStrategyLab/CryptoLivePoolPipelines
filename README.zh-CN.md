# CryptoLivePoolPipelines

选取并发布 QuantStrategyLab 加密策略所用月度 live-pool 的管线。

[English](README.md) | 简体中文

## 状态

实验性项目，使用风险自负。本项目不构成投资建议，也不保证任何交易结果。它的输出是研究证据与发布产物，不是交易指令。它不提交交易。

## 简介

CryptoLivePoolPipelines 负责月度加密 live-pool：候选排名、池内成员与顺序、shadow 验证轨道，以及下游策略代码读取的发布产物。它消费行情数据和 QuantPlatformKit 辅助能力，并在有文档的契约下写出带版本号的产物。它是加密策略运行时的上游证据与发布层。

## 不适用范围

它不提交交易，也不是执行平台。它不决定策略逻辑；交易逻辑位于 `CryptoStrategies`。下游系统不应依据本地指标重建池，而应使用已发布产物。

## 快速开始

需要 Python 3.11 或更高版本。

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests
```

在本地构建并检查一次月度 shadow 发布：

```bash
make monthly-shadow-build
make monthly-shadow-check
```

## 配置

| 变量 | 必填 | 默认值 | 用途 |
|---|---|---|---|
| `PUBLISH_ENABLED` | 否 | `false` | 是否允许发布产物。 |
| `PUBLISH_MODE` | 否 | `core_major` | 发布对象的发布模式。 |
| `GCS_BUCKET` | 否 | 无 | 存放发布对象的 bucket。 |
| `CLOUD_PROJECT_ID` | 否 | 无 | 用于存储和 Firestore 的 Google Cloud 项目。 |
| `FIRESTORE_COLLECTION` | 否 | `strategy` | 接收 current-pointer 文档的 collection。 |
| `FIRESTORE_DOCUMENT` | 否 | `CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL` | 接收 current-pointer 载荷的 document。 |
| `UPLOAD_CURRENT_POINTER` | 否 | `true` | 发布后是否更新 current-pointer。 |
| `TELEGRAM_BOT_TOKEN` | 否 | 无 | 月度通知所用的 bot token。 |
| `GLOBAL_TELEGRAM_CHAT_ID` | 否 | 无 | 接收月度通知的会话。 |
| `NOTIFY_LANG` | 否 | `en` | 通知使用的语言。 |
| `REVIEW_DEADMAN_THRESHOLD` | 否 | `3` | 触发 dead-man 告警前允许的失败或跳过周期数。 |
| `QSL_DISPLAY_NAMES_JSON` | 否 | 无 | 通知显示名覆盖。 |

管线输入与默认值见 [`config/default.yaml`](config/default.yaml)。发布所用的 bucket 与项目标识默认未设置，可通过上表中的环境变量或配置文件提供。

## 仓库结构

```
src/        管线、排名、验证与发布代码
scripts/    运维脚本与月度任务
config/     管线配置
data/       本地与生成的输出
docs/       契约、路线图与运维手册
tests/      单元与契约测试
```

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 安全

见 [SECURITY.md](SECURITY.md)。安全漏洞不要开公开 issue，也不要让凭据、私有账户信息和生成数据进入 Git。

## 许可证

MIT，见 [LICENSE](LICENSE)。
