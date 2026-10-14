# CryptoLivePoolPipelines

CryptoLivePoolPipelines 负责挑选并发布 QuantStrategyLab 加密策略所用的月度 live-pool。它对候选币种排序，决定哪些币种留在已发布的池子里、顺序如何排列，并产出下游策略代码在下单前要读取的验证产物。它本身不下单，是其它仓库依赖的上游证据与发布流水线。

[English README](README.md)

> 投资有风险。本项目不构成投资建议，仅用于学习、研究和工程审阅。

## 这个仓库是什么

CryptoLivePoolPipelines 是 QuantStrategyLab 的加密货币 live-pool 与发布流水线。它是月度加密 live-pool 成员、顺序、ranking、shadow candidate tracks 和发布产物的权威来源，供 CryptoStrategies 使用。

这是一个产出证据的仓库，不直接下单，也不应该被当作执行平台。

## 策略和证据边界

### 普通 runtime 策略

交易逻辑在 CryptoStrategies。本仓库生成策略包读取的 live-pool 和验证产物，并负责已发布池的月度选择和顺序。

### 本仓库处理的 Snapshot-backed 工作

- core_major live pool 产物
- 月度 live-pool shadow validation
- external-data 和 candidate-track 研究输出

### 下游如何使用

CryptoStrategies 和 BinancePlatform 应只消费通过 contract 检查的发布产物。下游系统应保留 `live_pool.json["symbols"]` 的顺序，不应根据本地指标重建月度池。

## 这些产物用来做什么

Live-pool artifact 的作用是让策略判断可复现：包括 ranking 输入、live-pool snapshot、manifest、validation summary 和提升证据。`live_pool.json` 和 `artifact_manifest.json` 是稳定的下游执行合约；ranking 文件和研究输出默认留在上游作为证据，除非合约明确提升它们。它们不是宣传式收益承诺。下游仓库提升 profile 前，应在适用场景下检查最新短、中、长周期产物。

## QSL 架构角色

- **层级**：`快照/证据流水线`。
- **职责**：加密 live-pool 与发布流水线。
- **事实源/归属**：月度 live-pool 成员、排序、验证 artifacts。
- **消费对象**：市场数据输入、QuantPlatformKit helpers。
- **禁止事项**：下单或绕过下游 artifact contract 检查。

## 月度 review 自动化

月度发布成功后，独立的 review-only 作业读取同一次运行已发布的月报和 `strategy_metrics.json`，各调用一次 OpenAI 和 Anthropic API，单轮审查结果写入既有 `monthly-review` issue 的评论。不再触发 AIAuditBridge 的旧 Codex Audit，不使用 Codex/Cursor CLI，也不自动修改代码或交易配置。

默认每次输入上限 16,000 token、输出上限 2,500 token、超时 60 秒、零重试，UTC 自然月预算 1 美元。输入按 UTF-8 字节数加固定消息开销保守计数，超限直接跳过，不截断材料。调用前在 issue 评论中预留两次请求的最大费用；预算记录不可读、预算不足或同月重复审查同一月报时不调用 API。缺少既有 `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY` 时，日志列明缺失名称，API 作业跳过，并发布 `review unavailable` 评论。配置和预算规则见 [运行手册](docs/operator_runbook.md#monthly-review-only-api-audit)。

生产发布目标如 GCP project、GCS bucket 和 Firestore document 必须从 GitHub variable 读取。本仓库不再保留 source-local `ai_review.yml`；新的 API 审查作业位于 `monthly_publish.yml`，只允许读取仓库、下载报告和写 issue 评论。

## 仓库结构

- `src/`：库代码和运行时代码。
- `tests/`：单元测试、契约测试和回归测试。
- `docs/`：运行手册、设计说明、证据和集成契约。
- `.github/workflows/`：CI、定时任务、发布或部署 workflow。
- `scripts/`：运维脚本和本地辅助工具。
- `config/`：运行或流水线配置。

## 快速开始

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

## 延伸文档

- [`docs/external_data_roadmap.md`](docs/external_data_roadmap.md)
- [`docs/external_data_validation.md`](docs/external_data_validation.md)
- [`docs/integration_contract.md`](docs/integration_contract.md)
- [`docs/operator_runbook.md`](docs/operator_runbook.md)
- [`docs/validation_status.md`](docs/validation_status.md)

## 安全和贡献说明

- 除非产物明确设计为公开且已有文档说明，否则不要把生成数据、凭据或私人账户信息提交到 Git。
- 优先提供可复现命令，并显式指定输出目录。
- 没有完整验证证据时，不要把研究产物提升到 live 使用。

## 社区和安全

- 贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，确认 PR 范围、本地校验和文档要求。
- 讨论、issue 和 review 请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- 涉及密钥、自动化、券商/交易所或云资源的漏洞请按 [SECURITY.md](SECURITY.md) 私密报告；不要为 secret 或实盘风险开公开 issue。

## 许可证

详见 [LICENSE](LICENSE)。
