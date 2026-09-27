# 资料准备的 MCP 流程

本文件适用于默认提供八项业务工具的 NovelLens。使用宿主实际发现的工具名及 schema；前缀由宿主决定，不硬编码。服务负责数据和索引，AI 负责文件准备、阅读与文学判断。

## 入口与文件

`library_browse` 的 `view` 为 `works`、`tags`、`parts`、`sections`、`annotations` 或 `jobs`，默认 works。works 和全库共享 tags 不带 work_id，其他视图必须提供 work_id。章节可用 part_id 筛选。列表支持 limit/cursor；续页保留原筛选，只有 format 可在 compact/full 间切换而不改变结果。

查旧的最短路径：

1. 标签未知时用 `library_browse({view:"tags",query:"要查的名称或别名",namespace?:"分组"})`，从 tag_page.items 读取 id、完整名称、定义和别名；省略 query 浏览词表。query 是字面子串查询，不是语义召回。共享标签存在不代表本作品已经使用。
2. 用 `library_browse({view:"annotations",work_id,tag_ids:[查到的标签ID],status:"active"})` 找本作品已有观察；多个 ID 要求同时具有。也可用 source_range 查任一引用相交的标记。status 可为 withdrawn 或 null，省略保持目录原有的全部状态行为。列表 tags 只有 id/full_name，不能用预览内容覆盖修订。
3. 选定后用 `library_browse({view:"annotations",work_id,annotation_id,format:"full"})` 读取完整说明、全部引用、版本和 tags 定义；详情不混入列表筛选参数。已有明确 ID 时直接执行本步。

标签视图只接受 namespace/query；标注列表才接受 tag_ids/source_range/status。查同义称呼时先核对定义和上下文，不为相同人物或相同标签强制合并不同观察。旧说明是可修订的阅读成果；涉及本次判断时回读其证据，不把标签或旧概括直接当成新材料的解释。

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
  "note": "按需要保存可由引用核对的内容选择、表达联系与必要条件"
}
```

上述占位 UUID 必须替换为真实返回值。tags 可为空，note 可省略。标签按 namespace/name 精确复用，不覆盖已有共享定义；同义词是否同一概念仍由 AI 判断。修订还须提供 `annotation_id` 及标记自己的 `expected_version`，完整提交范围、标签与说明；撤回时 `status="withdrawn"`。不要为修改一个局部观察去全局重定义标签。

任务已 completed 时，先取得当前任务和标记详情，修订批次另传非空 `reopen_reason` 并包含至少一条标记。服务将重开、标记修订和进度保存为一次原子操作，保留已有覆盖、清除旧完成说明；失败仍为原完成状态。后续运行中批次不传 reopen_reason；修订完成后等待索引同步，再调用 prepare_finish。原因由 AI 根据修订请求填写，不增加普通步骤的用户确认。

`recovery` 最少包含非空 `next_action`，按需要补充有证据的 facts、open_questions、next_range；不复制全文或推理过程。`outcome_note` 简述本批处理结论，零新标记批次使用 `marks=[]`。单批最多 50 条标记，跨章处理分批提交，不为了填满上限制造标记。

next_action 记录具体未完成操作，例如“回读所列范围并补充 annotation_id=… 的证据，然后继续下一章”；必要坐标放在 facts/open_questions 的 source_ranges 或 next_range 中。facts 保存已核对事实，open_questions 保存原文尚不能确定的问题，不能把未写入操作伪装成文学未知。recovery 是替换保存，下一批须保留仍有效的待办，完成后再移除。已读范围的持久进度、标记保存和索引就绪分别核对。

批次的 source_range 是本次实际处理范围；每条 marks 的 source_ranges 是该观察的全部证据，可包括同作品其他章节，两者都必须提供真实坐标。补充旧观察时，在当前完整 source_ranges 基础上保留有效旧引用并加入新引用，去除完全重复范围，不拼接不连续原文。说明交代各处证据对观察的作用；需要跨段理解时保留必要上下文，不把分散引文凑成完整的假场景，也不以大范围代替具体联系。新旧标记可以在一个 prepare_batch 原子提交；回执成功才表示补充已落库。

全部成果、进度、索引待办和回执同一事务提交。本批任一校验失败整批回滚，保留之前成功批次。成功回执给出新的任务版本；继续处理下一范围。

## 保存与接续作品写法认识

原文保留表达与可回读的上下文，标注保存有原文支持的理解，标签用于发现这些材料；这些职责沿用现有字段，不新增人物、世界或场景条件表。局部观察与跨片段比较都保存为普通标注。阶段复核形成有依据、值得后续利用的比较时，在其 tags 中加入以下通用用途标签，并随 prepare_batch 原子保存；不要求每批或每本凑出固定数量，不把所有作品特点写进一条总纲：

```json
{
  "namespace": "导航",
  "name": "作品写法",
  "description": "标识可帮助理解本作品表达方式的比较观察及其原文入口。"
}
```

此标签标识可明确查找的比较认识，不给所有局部标注统一贴上。每条 note 开头直接说清比较问题，随后说明已读证据中的共同选择、差异及适用边界；作品/分部与场景限制写在说明中，不把少量证据说成全书频率。source_ranges 中每处引用都须支持这条观察，保留各自足够上下文；不同问题分开保存。标签只是用途约定，普通写法标签按实际需要复用，不建立固定技巧清单。

建立前，用 tags 视图 namespace="导航"、query="作品写法" 查找；query 是子串匹配，检查返回的 namespace/name 精确相等，必要时续页。找到后核对定义，若同名指向别的用途，记录冲突而不覆盖或制造近义替代。未找到可在批次中按上述定义创建。更新旧比较先按作品及 tag_ids、status="active" 找到相关条目，再读取完整详情；比较依据需要查已撤回条目时显式请求，不把它重新当有效结论。

后续批次可补证据、限定条件、拆分不同观察或撤回失效概括，完整替换时保留仍有效的标签（包括该用途标签）与引用。当前分部任务的 source_range 仍是本批实际处理范围；比较标注的多处引用可包含同作品已读其他分部，不能据此推进未读范围。已知待核对问题放 recovery；已成立且有用的认识要真正保存为标注，recovery 或外部报告不能替代。

创作端通过同一精确标签与作品内 active 标注查到这些认识，再回读原文；无需等待某次向量搜索碰巧命中。没有比较证据时可以暂无条目，不能为完成状态编造风格。该标签不是服务保留标识、全书质量认证或原文排除开关，改名需同步使用它的流程；旧 StyleGuide 不自动转成这里的标注。索引仍按普通标注同步。

## 疑点保存与纠错核验

来源疑点沿用现有字段：已核对的现象和位置放在 recovery.facts，原因未定或缺少版本对照放在 open_questions；需要修订的资产 ID、证据坐标及下一步放在 next_action。对后续原文理解有必要长期保留的问题，可保存带真实引用的简短标注，说明已知现象与不确定性，不用标签名代替证据，也不为每个猜测新建错误标签。

修订用 `library_browse({view:"annotations",work_id,annotation_id,format:"full"})` 取得当前完整对象，再通过 source_read 核对相应原文。按最新标记版本使用 prepare_batch 完整替换说明、标签和引用；只保留仍有依据的旧引用，不为了保留原分析而继续提交错误证据。整条失效用 status="withdrawn"，已完成任务沿用 reopen_reason。没有当前批次阅读证据时先回读，不伪造 source_range 或阅读进度。

保存后再次通过同一详情入口核对当前版本和状态，以及说明、引用、标签是否表达同一结论。列表摘要、历史回执和“索引已就绪”都不能代替这一核验。仅替换标签或追加警告而保留已否定的分析，不算完成纠错；失败或核验不一致时保留具体待办，按当前版本修正，不盲目重放旧修订。

prepare_batch 的 tags 按 namespace/name 复用；给同名标签传入新 description 不会更新共享定义。修改分类应替换标注的标签关联；确需修订共享名称、定义或别名时，只有宿主实际提供相应维护入口且任务范围涵盖该修改，才按其真实 schema、当前版本及共享影响执行并回读。默认八工具没有该能力时，记录标签 ID 与需改字段并明确尚未完成，不虚构 tag_updates 参数、不为绕过限制制造近义标签，也不自行切换服务配置或用 SQL 修改。作品专属事实应放在标注说明中。

撤回使该标注不再作为有效分析，但原文仍可召回；普通“疑似问题”或“不要参考”标签没有服务级过滤效力。不要声称标注纠错已修复原文件、删除原文或将相应段落移出检索。

## 完成与失败处理

`prepare_finish({request_id, work_id, job_id, expected_version, recovery, note})` 只在目标全部 processed 且索引同步时成功，不需要风格导航。`indexes.source_ready` 和 `clues_ready` 分别描述原文与标记索引，`state` 为 pending/ready/failed/blocked；任务阅读完成与索引完成不是同一件事。work_ready 还要求所有当前分部的准备任务完成。

索引由服务自动处理，不调用手工 create/build。阅读期间它可并行工作；阅读结束后仍 pending 时适度等待再查状态，不密集空转。failed 表示技术故障，blocked 表示存在不能处理的输入，依 `last_error` 核对；不通过省略失败资产、改用字面查询或伪造完成来绕过。

超时：`prepare_status({request_id})` 查询提交快照，此时不提供 work_id/job_id。未找到不证明失败，原请求可能仍在执行；可用原键原输入重试。成功回执是历史快照，接续仍读取当前状态。版本冲突读取新版本后判断，不能盲改版本覆盖。

`prepare_cleanup({work_id, confirm_work_name})` 只用于用户明确放弃指定整作品；普通批次失败不清理。清理不删除外部 TXT 或其他作品，失败整体回滚。遇到构建忙碌可稍后重试；不要终止未知进程。

## 创作请求插入准备期间

优先处理用户当前创作要求。原文索引已覆盖所需范围即可经 `reference_query({query, scope:[{work_id, part_ids? 或 section_ids?}]})` 查询并以 `source_read` 回读，不等待全部标记完成；未标记内容也参与召回。准确区分已读范围和准备进度，不因索引成功宣称 AI 已读完全书。

目录与原文读取默认 format="compact"，检查详细信息时可用 "full"；不改变原文、分页或准备进度。统一召回续页仅提供 search_id、cursor 和可选 format，不能混入新查询参数；搜索过期或业务变更需重新查询。创作查询的 exclude_ranges 来自本次实际阅读，不能自动使用分析 processed 进度。
