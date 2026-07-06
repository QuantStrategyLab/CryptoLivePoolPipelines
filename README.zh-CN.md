# CryptoLivePoolPipelines


## QSL 架构角色

- **层级**：`快照/证据流水线`。
- **职责**：加密 live-pool 与发布流水线。
- **事实源/归属**：月度 live-pool 成员、排序、验证 artifacts。
- **消费对象**：市场数据输入、QuantPlatformKit helpers。
- **禁止事项**：下单或绕过下游 artifact contract 检查。

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

## 月度 review 自动化

月度 publish workflow 通过 `AIAuditBridge` 触发自动 review 和 remediation；`CODEX_AUDIT_PROVIDER` 默认走 `auto`，优先调用 AIAuditBridge 的 HTTPS/443 service-backed Codex 路径，再按桥接仓库配置 fallback 到 API reviewer。`OPENAI_API_KEY` 和 `ANTHROPIC_API_KEY` 等 provider secret 配置在 `AIAuditBridge`，本仓库不直接读取这些 provider key。可通过 `CODEX_AUDIT_BRIDGE_REF` pin 到指定 bridge ref，默认 `main`。

生产发布目标如 GCP project、GCS bucket 和 Firestore document 必须从 GitHub variable 读取。本仓库不再保留 source-local `ai_review.yml`；旧的本仓库本地 API review workflow 已移除，provider 选择和 fallback 逻辑集中在 `AIAuditBridge`。

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
