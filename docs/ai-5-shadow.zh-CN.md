# AI-5：月度研究 hook 只记录

`config/ai-hook-shadow.json` 的唯一目标是 QuantAIService v2；全部绑定值由配置和已有 secret 引用注入，没有默认值。实际组织配置仍需获准接口与准确输入，不能把本文件当作已经注入成功。

工作流的 URL、audience、profile、model 通过 `AI_HOOK_URL_SECRET`、`AI_HOOK_AUDIENCE_SECRET`、`AI_HOOK_PROFILE_SECRET`、`AI_HOOK_MODEL_SECRET` 配置选择器引用已有且获准的 secret，不再直接将普通变量值放进 runner 环境输出。选择器不是实际 secret 名称；缺少选择器或对应 secret 仍安全失败，不创建凭据或扩大权限。

`apply_monthly_loop` 不再构建 DotGateway、获取工作负载 token、提交/读取远端 task、关闭 Issue 或通知交易动作；它只验证两份已有意见并写脱敏的本地记录。传入 `issue` 或 `dot` 也不能增加这些能力。缺值或目标/模式不符时记录 `blocked_configuration`，不调用备用目标、不执行正式动作，`finished=false`。现有初始双 API 评审及发布流程不在本变更中开启、重新运行或调整资金权限。

这里的 #214 是跨月预算 reservation 重放修复，不是 hook 实现 PR；实际 hook 在 `scripts/monthly_review_loop.py`。原 `scripts/advisory_review_loop.py` 的通用流程不被此 hook 调用，没有删除其他调用者的行为。

服务仓库 `docs/AI-5-Q04-SHADOW.zh-CN.md` 保存七天计划、每日检查点与模板。计划未满写“影子进行中”；源码、配置存在、观察计划启动、原生 Q04 成功及正式启用分别验收，不互相替代。
