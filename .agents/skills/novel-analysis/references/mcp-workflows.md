# 资料准备的 MCP 流程

本文件适用于默认提供八项业务工具的 NovelLens。使用宿主实际发现的工具名及 schema；前缀由宿主决定，不硬编码。服务负责数据和索引，AI 负责文件准备、阅读与文学判断。

## 入口与文件

`library_browse` 的 `view` 为 `works`、`parts`、`sections`、`annotations` 或 `jobs`，默认 works。其他视图必须提供 `work_id`；章节可用 `part_id` 筛选，标记详情使用 `annotation_id`。列表支持 `limit` 和 `cursor`；详情返回的 `tags` 提供当前标签定义，便于修订时保留或调整。

同一故事的分部属于同一作品，追加须用已有 work_id；无分部的小说可用“正文”作为分部名。作品、标题和归属清楚时自行处理，存在实质歧义才询问，不要求用户命名工具模式或安排中间步骤。

输入为 UTF-8 TXT，可有 BOM。首条非空行是 `分部：名称`，后续以 `标题：章节名` 标识章节；每个文件一个分部，每个分部至少有正文。其余非空物理行作为自然段，保留行内字符、空白和标点；行首“分部：”“标题：”是结构标记。

若用户文件不是该格式，在原文件之外生成派生文件，确认真实章节与自然段，核对转换前后正文字符和顺序。不自动合并不明硬换行、不删除卷首正文、不把正文中的结构标记当标题；判断不能可靠完成时报告具体问题。空章节可以保留。不要覆盖原素材，也不要要求用户手写格式标记。

`prepare_import` 输入：`request_id`、`file_path`，以及新作品的 `name` 或追加作品的 `work_id`（二选一）。文件路径须能被服务访问，推荐绝对路径。返回 `work`、`part`、`job`。同请求同内容重试不重复写入；同作品同来源复用分部和准备任务，导入失败不会留下空作品。格式错误时修正派生文件后重试。

## 当前状态、原文与进度

`prepare_status({work_id, job_id, limit?, cursor?})` 返回当前任务、`remaining` 范围及 `indexes`。remaining 已排除 processed 段落，非空 `next_cursor` 表示还有范围；任务变化会使旧进度游标失效，重新读取首页即可。

`source_read({work_id, section_id, start_paragraph_id?, end_paragraph_id?, before?, after?, limit?, cursor?})`：

- 不提供端点时读取整章；提供时必须同时提供起止 ID。
- `before`、`after` 各可扩展 0–100 段，不跨章；需要时查目录读取相邻章节。
- 默认一页 60 段，最多 200 段，这是技术分页上限，不是文学阅读配额。
- `items` 保留每个完整自然段的 `id`、`ordinal`、`text`；顶层给出作品与章节。`actual_range` 是本页实际范围；`requested_range`（范围读取时）不是已读证明。
- 续页保持相同章节、端点和扩展参数，传入 `next_cursor`。SourceRange 须合并顶层的 work_id、section_id 与实际范围的 start_paragraph_id、end_paragraph_id，不编造坐标。

`prepare_batch` 输入：`request_id`、`work_id`、`job_id`、任务的 `expected_version`、本批实际处理的 `source_range`、`marks`、`recovery`、`outcome_note`。source_range 为一章内含两端的连续范围。

每条 marks 输入：

```json
{
  "source_ranges": [{"work_id": "实际作品UUID", "section_id": "实际章节UUID", "start_paragraph_id": "实际起点UUID", "end_paragraph_id": "实际终点UUID"}],
  "tags": [{"namespace": "写法", "name": "对白接续", "description": "后一话语接住前一话语中尚未解决的关切"}],
  "note": "有必要时填写可由引用核对的简短观察"
}
```

上述占位 UUID 必须替换为真实返回值。tags 可为空，note 可省略。标签按 namespace/name 精确复用，不覆盖已有共享定义；同义词是否同一概念仍由 AI 判断。修订还须提供 `annotation_id` 及标记自己的 `expected_version`，完整提交范围、标签与说明；撤回时 `status="withdrawn"`。不要为修改一个局部观察去全局重定义标签。

任务已 completed 时，先取得当前任务和标记详情，修订批次另传非空 `reopen_reason` 并包含至少一条标记。服务将重开、标记修订和进度保存为一次原子操作，保留已有覆盖、清除旧完成说明；失败仍为原完成状态。后续运行中批次不传 reopen_reason；修订完成后等待索引同步，再调用 prepare_finish。原因由 AI 根据修订请求填写，不增加普通步骤的用户确认。

`recovery` 最少包含非空 `next_action`，按需要补充有证据的 facts、open_questions、next_range；不复制全文或推理过程。`outcome_note` 简述本批处理结论，零新标记批次使用 `marks=[]`。单批最多 50 条标记，跨章处理分批提交，不为了填满上限制造标记。

全部成果、进度、索引待办和回执同一事务提交。本批任一校验失败整批回滚，保留之前成功批次。成功回执给出新的任务版本；继续处理下一范围。

## 完成与失败处理

`prepare_finish({request_id, work_id, job_id, expected_version, recovery, note})` 只在目标全部 processed 且索引同步时成功，不需要风格导航。`indexes.source_ready` 和 `clues_ready` 分别描述原文与标记索引，`state` 为 pending/ready/failed/blocked；任务阅读完成与索引完成不是同一件事。work_ready 还要求所有当前分部的准备任务完成。

索引由服务自动处理，不调用手工 create/build。阅读期间它可并行工作；阅读结束后仍 pending 时适度等待再查状态，不密集空转。failed 表示技术故障，blocked 表示存在不能处理的输入，依 `last_error` 核对；不通过省略失败资产、改用字面查询或伪造完成来绕过。

超时：`prepare_status({request_id})` 查询提交快照，此时不提供 work_id/job_id。未找到不证明失败，原请求可能仍在执行；可用原键原输入重试。成功回执是历史快照，接续仍读取当前状态。版本冲突读取新版本后判断，不能盲改版本覆盖。

`prepare_cleanup({work_id, confirm_work_name})` 只用于用户明确放弃指定整作品；普通批次失败不清理。清理不删除外部 TXT 或其他作品，失败整体回滚。遇到构建忙碌可稍后重试；不要终止未知进程。

## 创作请求插入准备期间

优先处理用户当前创作要求。原文索引已覆盖所需范围即可经 `reference_query({query, scope:[{work_id, part_ids? 或 section_ids?}]})` 查询并以 `source_read` 回读，不等待全部标记完成；未标记内容也参与召回。准确区分已读范围和准备进度，不因索引成功宣称 AI 已读完全书。

目录与原文读取默认 format="compact"，检查详细信息时可用 "full"；不改变原文、分页或准备进度。统一召回续页仅提供 search_id、cursor 和可选 format，不能混入新查询参数；搜索过期或业务变更需重新查询。创作查询的 exclude_ranges 来自本次实际阅读，不能自动使用分析 processed 进度。
