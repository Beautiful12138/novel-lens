# 创作前的原文参考

使用宿主实际发现的默认 NovelLens 业务工具与 schema。三个读入口默认 compact，full 用于诊断或详细位置检查。参考查询只读，不新建准备任务或改变进度。

## 定位、查询、阅读

1. 已知参考作品 ID 时沿用；否则 `library_browse({view:"works", limit?, cursor?})` 查看可见作品，根据用户范围及本次创作需要选择。`view:"parts"` 或 `"sections"` 加 work_id 取得分部和章节；章节视图可按 part_id 筛选。
2. `reference_query({query, scope:[{work_id, part_ids? 或 section_ids?}], terms?, exclude_ranges?, limit?, format?})` 自动同时使用原文语义、原文字面、标记线索语义与字面，无须分别选择工具或索引层。query 描述当前需要参考的具体处境、互动或表达问题；terms 可补充人名、物件、动作或写法的字面线索，每词最多 128 字符，最多八词。limit 默认 8，最大 30，仅决定每页数量。scope 最多 20 部作品，每作品仅一项；可指定非空 part_ids 或 section_ids，二者互斥，所有选择合计最多 200 个 ID。不填二者则查询该作品全部原文。
3. 依据命中的 source_range 调用 `source_read`，取得完整自然段。需要前因后果时扩大 before/after 或读取整章；同一查询可以返回未标记原文，不把有标记当作更优的先验。
4. 读懂当前需要的表达与展开，再确定依赖参考的方案并创作。阅读发现方向不合适时修正查询或改选作品，而不是不断寻找支持既定结论的例句。

作品较多时，选定适用作品后可一次多作品查询，共同排序且保留各自坐标；不自动扫描全部库。改变问题角度时另发新查。没有固定查询轮数或阅读配额；结果只涵盖当前候选窗口，不能把一个 top-k 列表说成穷尽全书。普通行文、日常对白和转场可比高潮段落更适合当前任务。

## 理解查询结果

默认 compact 包含作品名称、原文范围、部名章名、`excerpt`、`excerpt_truncated` 和标记 ID。需要诊断才请求 full，额外返回得分、命中通道及创建搜索时的诊断快照；格式不会改变同一搜索同一页的候选与顺序。score 不是文学质量、理解程度或相关概率；预览最多 200 字符，只用于挑选入口。标记短说明与标签用于定位，不能替代正文。

需要标记解释时，用 `library_browse({view:"annotations", work_id, annotation_id})` 取得当前详情及标签；不必先读完解释再读正文。已撤回的标记不当作有效判断，原文仍可独立阅读。多处引用分别核验，不把不连续原文拼成一个场景。

full 的 diagnostics 按作品区分请求范围覆盖和作品级索引状态；这些属于 snapshot_at 时刻，不是实时状态。compact 的 warnings 仅提示选定范围内的线索同步缺口，正常时不附统计。分部可用不代表其他新增分部已就绪。线索尚未同步时，过期向量不会参与，当前字面线索仍可用；不要据此宣称全书标记完成。原文检索就绪与 AI 阅读进度无关。

必要原文未完整覆盖返回 REFERENCE_NOT_READY；模型或服务失败返回实际错误，不会伪装为空结果，也不能静默改成纯关键词并宣称完成统一召回。可以阅读当前上下文中已有的必要原文；无法取得任务必需参考时说明具体缺口，不冒称参考完成。

## 继续候选与排除已读

同一问法需要更多候选时，使用 `reference_query({search_id, cursor:next_cursor, format?})`，不再提供 query、scope、terms、exclude_ranges 或 limit。仅提供 search_id 可重读首页；同一游标可重复使用，也可切 full 检查同一页。分页不重新推理或重排。next_cursor 为空表示本轮候选结束；candidate_window_limited 为 true 时，列表之外仍可能有候选，不能宣称已经穷尽参考库。

改变问法、范围或已读排除时发起新查。exclude_ranges 最多 200 条完整 SourceRange，可由 source_read 顶层 work_id/section_id 加本页 actual_range 组成，仅排除完全读过的候选；部分重叠可能保留，以免漏掉未读上下文。预览、整个 requested_range 和分析 processed 进度不能冒充本次已读。允许保留本次探索中 scope 外仍有效的已读坐标，服务只处理交集；无效或不可读条目须移除。必要时仍可直接 source_read 回读，不维护永久禁读名单。

搜索固定有效 30 分钟。REFERENCE_SEARCH_STALE 表示业务内容或模型契约改变，REFERENCE_SEARCH_UNAVAILABLE 表示不存在或过期；均须新查，可带当前有效的已读范围。后台纯索引发布不打断旧页，也不会把新候选插入旧列表。容量已满或快照过大按错误说明稍后重试或缩小范围，不把失败当无命中。

## 连续原文阅读

`source_read` 的输入是 work_id、section_id，可选 start_paragraph_id 与 end_paragraph_id（须成对），以及 before、after、limit、cursor、format（默认 compact）。可直接展开查询返回的完整 source_range。

- 无端点时整章分页读取；有端点时按范围读取，before/after 各可扩展 0–100 段。
- 默认 60 段一页，最多 200 段；这是响应预算，不是充分理解的标准。
- 返回每个完整自然段的 id、ordinal、text，顶层包含 work_id/section_id。本页的 actual_range 只含端点，结合顶层归属组成完整坐标。
- next_cursor 非空时，保持原端点、扩展参数和章节继续请求，不能把 requested_range 当作全已读。
- 章节边界不代表场景结束；必要时通过目录读相邻章节。已有真实坐标可直接读取，无需每次重新浏览目录。

例如，收到一处原文范围后补读上下文：

```json
{
  "work_id": "返回的作品UUID",
  "section_id": "返回的章节UUID",
  "start_paragraph_id": "返回的起点UUID",
  "end_paragraph_id": "返回的终点UUID",
  "before": 8,
  "after": 12,
  "limit": 60
}
```

示例数量只是演示参数，不是固定阅读量。按实际文本决定后续范围，观察人物如何接话、动作和说明如何衔接、何处展开或略过，以及整体声音。必要正文已在当前上下文时可以复用；只有旧总结时按坐标回读。

最终将理解用于新作自身的人物、处境与语言，不复制旧作句子或把旧情节改名重演。默认交付正文，不附工具清单、规则自评或冗长读书报告；用户要求解释时再说明实际参考范围。
