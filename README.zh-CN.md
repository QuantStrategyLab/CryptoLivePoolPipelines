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

创建项目虚拟环境并安装锁定的运行时依赖：

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-lock.txt
```

运行单元测试：

```bash
.venv/bin/python -m unittest discover -s tests
```

在本地构建并检查一次月度 shadow 发布。`make` 目标调用 `.venv/bin/python`（见 [`Makefile`](Makefile)），因此请先按上面的步骤创建 `.venv`：

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

## 月度 review 自动化

月度发布成功后，一个独立的 review-only 作业读取同一次运行已发布的月报和 `strategy_metrics.json`，各调用一次 OpenAI 和 Anthropic API，并把单轮结果作为评论写到既有 `monthly-review` issue 上。它不再触发 AIAuditBridge 的旧 Codex Audit，不使用 Codex 或 Cursor CLI，也不修改代码或交易配置。

默认每次请求输入上限 16,000 token、输出上限 2,500 token、超时 60 秒、零重试，UTC 自然月预算 1 美元。输入按 UTF-8 字节数加固定消息开销保守计数，超限直接跳过而不截断。缺少 `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY` 时 API 作业跳过，并发布 `review unavailable` 评论。配置与预算规则见[运行手册](docs/operator-runbook.md#monthly-review-only-api-audit)。

生产发布目标如 GCP project、GCS bucket 和 Firestore document 必须从 GitHub variable 读取。本仓库不再保留 source-local `ai_review.yml`；新的 API 审查作业位于 `monthly_publish.yml`。它读取仓库和已发布报告，写入 issue 评论、归档审查结论，并更新 review 状态。两次 API 审查均 approve 时，它将 review issue 标记为 `auto-verified` 并关闭；有审查 flag 时，若配置了已批准的 AIGateway 路由，它会提交一次有界的深入 advisory 审查，除非该审查确认存在真实问题，否则关闭 issue。它不修改代码、不改动线上配置，也不授权上线或交易。

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

见 [CONTRIBUTING.md](CONTRIBUTING.md)。AI 助手与维护者遵循 [AGENTS.md](AGENTS.md)。

## 安全

见 [SECURITY.md](SECURITY.md)。安全漏洞不要开公开 issue，也不要让凭据、私有账户信息和生成数据进入 Git。

## 许可证

MIT，见 [LICENSE](LICENSE)。
