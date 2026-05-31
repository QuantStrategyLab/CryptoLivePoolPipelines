# CryptoSnapshotPipelines

> ⚠️ 投资有风险，不构成投资建议，仅供学习交流用途。


## English summary

- Full English version: [`README.md`](README.md). This summary keeps an English entry point in the Chinese file.
- Purpose: this document covers `CryptoSnapshotPipelines` for `CryptoSnapshotPipelines`.
- Main topics: `当前状态`, `这个项目为什么存在`, `为什么不优先做深度学习`, `数据源`, `仓库结构`.
- Read the boundaries, inputs, outputs, and permission requirements before running commands, CI jobs, dry-runs, releases, or runtime switches.
- For live trading, secrets, Cloud Run, exchange, or broker API changes, validate in test or dry-run mode first and do not change production only from examples.
- If this summary differs from the detailed Chinese body, follow the concrete commands, configuration keys, and constraints in the body.

语言: [English](README.md) | 简体中文

`CryptoSnapshotPipelines` 是加密货币策略的上游研究、特征快照和发布流水线仓库。当前生产 artifact family 仍然是 `crypto_leader_rotation` 这条 Binance Spot leader universe。

这个仓库**不下单**、**不包含 live 执行逻辑**。它的核心交付物是一个可稳定发布的上游选择器，默认输出：

1. `data/output/latest_universe.json`
2. `data/output/latest_ranking.csv`
3. `data/output/live_pool.json`
4. `data/output/live_pool_legacy.json`
5. `data/output/artifact_manifest.json`
6. `data/output/release_manifest.json`
7. `data/output/release_status_summary.json`

## 当前状态

仓库目前明确分成两条线：

- `Production v1`
  - 数据源：仅 `Binance Spot`
  - universe mode：`core_major`
  - 发布频率：`monthly`
  - 默认输出：`latest_universe.json`、`latest_ranking.csv`、`live_pool.json`、`live_pool_legacy.json`、`artifact_manifest.json`
- `Experimental external-data track`
  - 仅用于研究、比较和验证
  - 默认不启用
  - 不属于生产发布默认路径

当前默认生产路径已经冻结在 `Production v1`。外部数据分支仍保留在仓库中，但在它没证明自己长期稳定优于 Binance-only 之前，它都只是实验路线。

为了保持下游兼容，v1 artifact namespace 仍保留为 `crypto-leader-rotation`，live profile 仍保留为 `crypto_leader_rotation`。

## 这个项目为什么存在

大多数交易系统会把三件事混在一起：

1. universe 构建
2. leader 识别与排序
3. 下单执行

这个项目只做前两件事。它的目标是作为下游量化脚本的**上游选择器**，回答一个更窄的问题：

在每个调仓时点，只使用当时可见的 Binance Spot 日线数据，哪些流动性足够的主流币值得进入候选池？它们里面谁更像未来 30/60/90 天的阶段领涨者？

这样做的好处是：

- 更容易解释
- 更容易审计
- 更容易做严格 walk-forward 验证
- 更容易接入不同的下游执行系统
- 不会把模型研究和执行细节绑死在一起

## 为什么不优先做深度学习

在只有 Binance Spot 日线 OHLCV 的条件下，深度学习通常不是第一选择：

- 信号噪声比有限
- 样本量相对模型容量偏小
- 可解释性更差
- 更容易过拟合
- walk-forward 稳健性通常更差

这个仓库走的是更务实的路线：

`硬过滤 universe + 稳健特征库 + 规则基线 + 轻量 ML + regime-aware blending + walk-forward validation`

## 数据源

当前版本只使用 Binance Spot 公开数据：

- `exchangeInfo`
- symbol 元数据
- 日线 klines
- 本地 CSV 缓存
- 增量更新
- 每个 symbol 一份原始文件

当前**不使用**：

- 市值
- 链上数据
- 资金费率
- 情绪数据
- 第三方数据源

## 仓库结构

```text
CryptoSnapshotPipelines/
  .github/
    workflows/
      monthly_publish.yml
  README.md
  README.zh-CN.md
  requirements.txt
  .gitignore
  config/
    default.yaml
  docs/
    integration_contract.md
    external_data_roadmap.md
    validation_status.md
  data/
    raw/
    cache/
    processed/
    models/
    reports/
    output/
  notebooks/
    research_notes.md
  scripts/
    download_history.py
    build_live_pool.py
    publish_release.py
    write_release_heartbeat.py
    validate_external_data.py
    run_research_backtest.py
    run_walkforward_validation.py
    debug_single_date_snapshot.py
    run_monthly_shadow_build.py
    run_monthly_build_telegram.py
    run_monthly_review_briefing.py
  src/
    ...
```

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

建议统一使用 `.venv/bin/python ...` 来运行研究、验证和月度流程，避免环境差异导致结果不可比。

## 配置

主要参数都在 `config/default.yaml` 中，包括：

- 数据目录和时间范围
- universe 过滤阈值
- rebalance 设置
- walk-forward 窗口
- 标签 horizon 和 `future_top_k`
- 规则排序方案
- regime-specific ensemble 权重
- ML backend 设置
- 输出设置
- GCS / Firestore 发布设置

## 发布契约检查

发布或回滚前，先校验本地生产产物：

```bash
.venv/bin/python scripts/validate_release_contract.py --mode core_major --expected-pool-size 5
```

生产发布链应同时要求 release manifest 和 profile-aware artifact manifest：

```bash
.venv/bin/python scripts/validate_release_contract.py --mode core_major --expected-pool-size 5 --require-manifest --require-artifact-manifest
```

## 最小可运行流程

1. 下载历史数据

```bash
.venv/bin/python scripts/download_history.py --limit 30
```

2. 跑研究回测

```bash
.venv/bin/python scripts/run_research_backtest.py
```

3. 跑 walk-forward 验证

```bash
.venv/bin/python scripts/run_walkforward_validation.py
```

4. 构建下游要消费的 live pool

```bash
.venv/bin/python scripts/build_live_pool.py
```

5. 生成月度发布 dry-run manifest

```bash
.venv/bin/python scripts/publish_release.py --dry-run
```

6. 如有需要，调试某个历史日期

```bash
.venv/bin/python scripts/debug_single_date_snapshot.py 2024-03-31
```

## 推荐验证基线

当前推荐的验证基线是：

- purged walk-forward validation
- overlap aggregation 可配置，默认保留 `mean`，也支持更严格的 `latest`
- 与 `live_pool.json` 对齐的月度 live-pool shadow validation

历史上一些更早的报告是在方法收紧前生成的，不能和现在的 hardened baseline 直接横向比较。

## 下游 live-pool 契约

下游应该依赖的是**每月发布的 live pool 契约**，不是研究报告。

下游消费者应主要依赖这些字段：

- `as_of_date`
- `version`
- `mode`
- `pool_size`
- `symbols`
- `symbol_map`
- `source_project`

这些字段会出现在：

- `data/output/live_pool.json`
- `data/output/live_pool_legacy.json`
- Firestore summary document

`data/output/artifact_manifest.json` 是 profile-aware wrapper，负责声明 artifact contract version、主 artifact、相关文件路径和校验和；它不是 `live_pool.json` 的字段复制。

一些发布期辅助字段，例如：

- `storage_prefix`
- `current_prefix`
- `live_pool_uri`
- `live_pool_legacy_uri`
- `artifact_manifest_uri`
- `latest_universe_uri`
- `latest_ranking_uri`

它们是分发元数据，不是研究特征。

更多细节见：

- `docs/integration_contract.md`

## Shadow Replay 支持

为了支持下游 end-to-end 本地 replay，这个仓库可以构建版本化的月度 shadow release 历史，输出到：

- `data/output/shadow_releases/`

每个 shadow release 目录里包含：

- `live_pool.json`
- `live_pool_legacy.json`
- `release_manifest.json`

根目录还会有 `release_index.csv`，供下游按月回放历史上游产物。

## Shadow Candidate Track

当前 baseline 仍然是官方生产参考。

`challenger_topk_60` 只作为附加的 shadow candidate 保存在：

- `data/output/shadow_candidate_tracks/`

双轨约定是：

- `official_baseline`
  - profile: `baseline_blended_rank`
  - source track: `official_baseline`
  - candidate status: `official_reference`
- `challenger_topk_60`
  - profile: `challenger_topk_60`
  - source track: `shadow_candidate`
  - candidate status: `shadow_candidate`

这些 shadow candidate 产物用于比较和 paper monitoring，不替代 `live_pool.json`，也不意味着 live 切换。

## Monthly Shadow Build

当前月度操作流程是：

1. 构建 official baseline live artifacts
2. 运行 baseline publish dry-run 检查
3. 刷新双轨 shadow candidate 历史

标准命令：

```bash
.venv/bin/python scripts/run_monthly_shadow_build.py
```

或：

```bash
make monthly-shadow-build
```

标准输出：

- official baseline
  - `data/output/live_pool.json`
  - `data/output/live_pool_legacy.json`
  - `data/output/artifact_manifest.json`
  - `data/output/release_manifest.json`
- shadow candidate tracks
  - `data/output/shadow_candidate_tracks/track_summary.csv`
  - `data/output/shadow_candidate_tracks/official_baseline/release_index.csv`
  - `data/output/shadow_candidate_tracks/challenger_topk_60/release_index.csv`
  - `data/output/monthly_shadow_build_summary.json`

baseline 始终是官方生产参考，`challenger_topk_60` 始终保持 shadow-only。

## Monthly Build Telegram Notify

可选的月度构建/发布健康度通知：

```bash
.venv/bin/python scripts/run_monthly_build_telegram.py
```

或：

```bash
make monthly-build-telegram
```

环境变量：

- `TELEGRAM_BOT_TOKEN`
- `GLOBAL_TELEGRAM_CHAT_ID`

它的行为是：

- 只发送简短的 monthly build/publish health summary
- 使用已有的 `monthly_shadow_build_summary.json`、`live_pool.json`、`release_manifest.json`、`track_summary.csv`
- 生产发布链还会检查 `artifact_manifest.json`，但 Telegram 文本只展示摘要状态
- 如果 Telegram 凭证缺失，会跳过而不是报错中断
- 不改变 monthly build 行为，也不是 review 包生成器

## Monthly Review Package

这个仓库现在也提供一份**只读月度 review 包**：

```bash
.venv/bin/python scripts/run_monthly_review_briefing.py
```

或：

```bash
make monthly-review-briefing
```

输出文件：

- `data/output/monthly_review.md`
- `data/output/monthly_review.json`
- `data/output/monthly_review_prompt.md`

它的用途是：

- 只使用上游自己的 monthly build 输出
- 汇总 official baseline 发布状态、publish manifest 状态、shadow track 覆盖情况
- 当月度产物在 `as_of_date`、`version`、`mode` 上不一致时，明确报 warning
- 生成一份结构化的人工复核 prompt / checklist
- 这是 reporting-only，不会改变 monthly build 行为

## 自动化 AI 月度审阅

月报 bundle 组装完成后，workflow 会自动创建一个 GitHub Issue，内容为完整的 `ai_review_input.md`。自动审阅路径会 dispatch `QuantStrategyLab/CodexAuditBridge`，由 bridge 统一决定 provider：

- `auto`（默认）：先跑 self-hosted Codex 路径；如果 Codex 准备或执行失败，由 bridge 回落到已配置的 API 审阅。要启用双 AI fallback，把 `OPENAI_API_KEY` 和 `ANTHROPIC_API_KEY` 都配置在 bridge；如果没有任何 API fallback key，则明确失败。
- `codex`：只跑 Codex，不使用 API fallback。
- `api`：在 bridge 内运行已配置的 API fallback reviewers，只回帖，不改代码。
- `openai`：在 bridge 内运行 API 审阅，只回帖，不改代码。
- `anthropic`：在 bridge 内运行 Claude API 审阅，只回帖，不改代码。

如果 bridge dispatch 本身失败，monthly publish workflow 会直接失败，而不是静默跳过审阅。

AI 审阅覆盖范围：

- **发布一致性**：交叉检查 `live_pool.json`、`release_manifest.json`、`release_status_summary.json` 在日期、版本、模式、池大小和币种上是否一致
- **异常检测**：标记意外的 warning、过时的产物、验证失败或可疑的排名分数
- **下游影响**：分析对 BinancePlatform（下游执行引擎）的影响，包括池子变动和降级风险
- **操作员待办事项**：汇总 checklist 并补充 AI 识别出的跟进事项
- **代码改进**：Codex 可以为低风险的 reporting、validation、workflow、test 或 documentation 问题直接创建聚焦 PR；涉及 selector、threshold、universe 或交易行为的变更仍需人工决策

审阅结果会回帖到月度 Issue。

### 可选 Bridge API Fallback

- `SELFHOSTED_CODEX_REVIEW_PROVIDER`：默认 `auto`；设置为 `codex` 可关闭 API fallback，设置为 `api` 可跑已配置的 API reviewers，设置为 `openai` / `anthropic` 可只跑单一 API 审阅。
- `OPENAI_API_KEY`：配置在 `CodexAuditBridge`，不要配置在当前 source repo。
- `ANTHROPIC_API_KEY`：配置在 `CodexAuditBridge`，不要配置在当前 source repo。
- `OPENAI_MODEL`：可选 bridge repo variable，默认 `gpt-5.4-mini`。
- `ANTHROPIC_MODEL`：可选 bridge repo variable，默认 `claude-sonnet-4-6`。

默认生产配置不需要模型 API secrets，因为默认使用 `CodexAuditBridge` 的 Codex provider。

配置方式示例：

```bash
gh variable set SELFHOSTED_CODEX_REVIEW_PROVIDER --body auto
gh secret set OPENAI_API_KEY --repo QuantStrategyLab/CodexAuditBridge --body "sk-..."
gh secret set ANTHROPIC_API_KEY --repo QuantStrategyLab/CodexAuditBridge --body "sk-ant-..."
```

本仓库不再保留 source-local `ai_review.yml` 或 Claude 自动优化 workflow。provider fallback 统一放在 `CodexAuditBridge`，因此当前 source repo 不需要配置 Anthropic/OpenAI secrets。

### Monthly Publish 的 GitHub 配置

`monthly_publish.yml` 现在这样读取配置：

- `GCP_SERVICE_ACCOUNT_KEY` 继续放在 GitHub secret
- `GCP_PROJECT_ID`、`GCS_BUCKET` 等非密发布目标必须从 GitHub variable 读取
- workflow 不再从 `secrets.GCP_PROJECT_ID` 或 `secrets.GCS_BUCKET` 读取旧 fallback

推荐配置：

```bash
gh secret set GCP_SERVICE_ACCOUNT_KEY < gcp-service-account.json

gh variable set GCP_PROJECT_ID --body "your-gcp-project"
gh variable set GCS_BUCKET --body "your-release-bucket"
gh variable set PUBLISH_ENABLED --body "true"
gh variable set PUBLISH_MODE --body "core_major"
gh variable set DOWNLOAD_TOP_LIQUID --body "90"
gh variable set FIRESTORE_COLLECTION --body "strategy"
gh variable set FIRESTORE_DOCUMENT --body "CRYPTO_LEADER_ROTATION_LIVE_POOL"
```

AI 审阅 workflow 运行在 `ubuntu-latest`（不需要 self-hosted runner），每月运行一次费用约 $0.01-0.05。

## Dynamic Universe Logic

universe 是硬过滤层，不是最终持仓集合。

每个历史时点都只使用当时可见的数据来决定某个 symbol 是否应该进入候选 universe。

基础过滤条件：

- `status == TRADING`
- `quoteAsset == USDT`
- `isSpotTradingAllowed == True`

显式排除：

- `BTCUSDT`
- `BNBUSDT`
- 稳定币相关资产，如 `USDC`、`FDUSD`、`TUSD`、`USDP`、`DAI`、`PAX`
- 杠杆方向币，如 `UP`、`DOWN`、`BULL`、`BEAR`

## 特征库

特征库覆盖但不限于：

- 相对 BTC 强弱
- 绝对趋势质量
- 风险调整后的动量和回撤
- 流动性和可交易性
- BTC 与市场环境

完整细节仍建议以英文 README 和 `src/` 中实现为准。
