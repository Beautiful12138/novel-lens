# 资料准备的 MCP 流程

本文件适用于提供分类、标签与标注浏览的 NovelLens。使用宿主实际发现的工具名及 schema；前缀由宿主决定，不硬编码。服务负责数据与阅读入口，AI 负责文件准备、阅读与文学判断。

## 入口与文件

`library_browse` 的 view 可为 works、categories、tags、parts、sections、annotations 或 jobs，默认 works。categories、tags、annotations 列表可带 work_id 或非空 work_ids，二者互斥，均不带表示全库可见范围；parts、sections、jobs 需要单个 work_id。章节可按 part_id 筛选。分页保持原筛选，compact/full 不改变成员及排序。

查旧的最短路径：

1. 标签未知时用 `library_browse({view:"tags",query:"要查的名称或别名",namespace?:"分组"})` 查全库共享词表，包括尚未使用的标签；省略 query 浏览词表。query 是名称、定义及别名的字面子串查询，不是语义召回。指定 work_id/work_ids 时只返回范围内有效标注实际使用的标签，不适合作为确认全库没有同义标签的依据。
2. 用 `library_browse({view:"annotations",work_id,tag_ids:[查到的标签ID],status:"active"})` 找本作品旧观察；多个 ID 默认任一命中并去重，需要交集时传 `tag_match:"all"`。也可按 source_range 找任一引用与范围相交的标注，或省略标签直接浏览。part_id、section_id、source_range 要求单作品范围。可用 kind="observation" 或 "comparison" 筛选用途，省略查询两种；筛选随游标保持不变。默认 active，确需核对旧记录时显式使用 withdrawn 或 null（全部状态）。
3. 用 `library_browse({view:"annotations",work_id,annotation_id,format:"full"})` 读取完整说明、全部引用、版本和 tags 定义；详情不混入 work_ids 或其他列表筛选。已有明确 ID 时直接执行本步，列表摘要不能作为完整修订对象。

标签可用 category 筛选，`category:"unclassified"` 只取空分类，省略表示全部。categories 视图不分页，只展示当前范围有效标注实际使用的分类与未分类计数；标签的 annotation_count 也仅统计当前范围有效标注，不包含屏蔽作品，不是原文频率或质量评分。查同义称呼先核对定义和上下文，相同人物或标签不能作为合并观察的依据；旧说明涉及本次判断时回读其原文。

### 分类与共享标签

服务提供六个固定分类，AI 不能创建一级分类。新标签依据共享含义选择零个或多个 ID；空列表表示尚未分类，不填写 unclassified。分类用于导航，不要求每次分析都覆盖，也不要求每条标注有标签。

| ID | 分类 | 主要浏览问题 |
| --- | --- | --- |
| content | 内容选择与展开 | 选择什么材料，怎样展开为具体内容 |
| perspective | 视角与信息 | 谁知道什么，读者如何取得信息 |
| interaction | 人物与互动 | 行动、话语与回应怎样显出人物和关系 |
| language | 语言与声音 | 措辞、句式、比喻及不同承担者的声音 |
| structure | 节奏与结构 | 详略、快慢、转场与前后承接 |
| emotion | 情绪与氛围 | 感受怎样通过表达形成和变化 |

一个标签可以跨类显示，身份与定义不变；例如对白的节奏可同时涉及 interaction 和 structure，但不按名称机械批量套类。没有归属的标签仍可使用。comparison 是标注用途，不是第七类标签；不创建特殊导航标签。

prepare_batch 的新标签可带 categories 和 aliases；精确复用已有 namespace/name 时不会覆盖原定义、别名或分类。确需修订共享标签，用 tags 视图 `format:"full"` 取得当前对象及版本，再调用 `tag_update({request_id,tag_id,expected_version,name,description,aliases,categories?})`。提交当前有效的完整名称、定义和别名；categories 省略保留已有归类，[] 清空，非空则完整替换。版本冲突先读取当前对象；超时用原请求键、原输入重试，不盲目换键。保存后回读核对，不假报更新。

同义标签先复用，别名只补合理同义称呼，不把有关但不同的概念合并。分类按标签在全库的共享含义确定，不因本作品的一次用法覆盖其他作品需要的类别。只需纠正一条标注时改其标签关联，作品专属事实放标注说明中，不改共享定义。

同一故事的分部属于同一作品，追加须用已有 work_id；无分部的小说可用“正文”作为分部名。作品、标题和归属清楚时自行处理，存在实质歧义才询问，不要求用户命名工具模式或安排中间步骤。

输入为 UTF-8 TXT，可有 BOM。首条非空行是 `分部：名称`，后续以 `标题：章节名` 标识章节；每个文件一个分部，每个分部至少有正文。其余非空物理行作为自然段，保留行内字符、空白和标点；行首“分部：”“标题：”是结构标记。

若用户文件不是该格式，在原文件之外生成派生文件，确认真实章节与自然段，核对转换前后正文字符和顺序。不自动合并不明硬换行、不删除卷首正文、不把正文中的结构标记当标题；判断不能可靠完成时报告具体问题。空章节可以保留。不要覆盖原素材，也不要要求用户手写格式标记。

`prepare_import` 输入：`request_id`、`file_path`，以及新作品的 `name` 或追加作品的 `work_id`（二选一）。文件路径须能被服务访问，推荐绝对路径。返回 `work`、`part`、`job`。同请求同内容重试不重复写入；同作品同来源复用分部和准备任务，导入失败不会留下空作品。格式错误时修正派生文件后重试。

## 当前状态、原文与进度

`prepare_status({work_id, job_id, limit?, cursor?})` 返回当前任务和 `remaining` 范围。remaining 已排除 processed 段落，非空 `next_cursor` 表示还有范围；任务变化会使旧进度游标失效，重新读取首页即可。

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
  "kind": "observation",
  "title": "回话沿用前一句未解决的关切",
  "scope_note": "仅限本次已读的这段对白，未据此概括全书",
  "references": [{
    "evidence_range": {"work_id": "实际作品UUID", "section_id": "实际章节UUID", "start_paragraph_id": "实际起点UUID", "end_paragraph_id": "实际终点UUID"},
    "reading_range": {"work_id": "实际作品UUID", "section_id": "实际章节UUID", "start_paragraph_id": "上下文起点UUID", "end_paragraph_id": "上下文终点UUID"},
    "role_note": "回读前一句、回应与后续变化，核对话语如何接续"
  }],
  "tags": [{"namespace": "写法", "name": "对白接续", "description": "后一话语接住前一话语中尚未解决的关切", "categories": ["interaction", "structure"], "aliases": []}],
  "note": "按需要保存可由引用核对的内容选择、表达联系与必要条件"
}
```

上述占位 UUID 必须替换为真实返回值。kind、title、scope_note、note、references 必填，tags 可为空。title 最长 256 字符，说明不能空白；references 为 1–100 处，重复证据范围拒绝。reading_range 可省略并等于 evidence_range；显式提供时须与证据同作品、同章并包含证据。正文跨章时分列真实引用，不能伪造跨章范围。标签按 namespace/name 精确复用，不覆盖已有共享定义、别名或分类；同义词是否同一概念仍由 AI 判断。修订还须提供 `annotation_id` 及标记自己的 `expected_version`，完整提交 kind、title、scope_note、note、references 和标签；撤回时 `status="withdrawn"`。不要为修改一个局部观察去全局重定义标签。

任务已 completed 时，先取得当前任务和标记详情，修订批次另传非空 `reopen_reason` 并包含至少一条标记。服务将重开、标记修订和进度保存为一次原子操作，保留已有覆盖、清除旧完成说明；失败仍为原完成状态。后续运行中批次不传 reopen_reason；修订完成且必要保存核验完成后调用 prepare_finish。原因由 AI 根据修订请求填写，不增加普通步骤的用户确认。

`recovery` 最少包含非空 `next_action`，按需要补充有证据的 facts、open_questions、next_range；不复制全文或推理过程。`outcome_note` 简述本批处理结论，零新标记批次使用 `marks=[]`。单批最多 50 条标记，跨章处理分批提交，不为了填满上限制造标记。

next_action 记录具体未完成操作，例如“回读所列范围并补充 annotation_id=… 的证据，然后继续下一章”；必要坐标放在 facts/open_questions 的 source_ranges 或 next_range 中。facts 保存已核对事实，open_questions 保存原文尚不能确定的问题，不能把未写入操作伪装成文学未知。recovery 是替换保存，下一批须保留仍有效的待办，完成后再移除。已读范围的持久进度与标记保存分别核对。

批次的 source_range 是本次实际处理范围；每条 marks 的 references 保存该观察的全部证据及各自阅读范围，可包括同作品其他章节，两者都必须提供真实坐标。补充旧观察时，在当前完整 references 基础上保留有效旧引用并加入新引用，去除完全重复范围，不拼接不连续原文。说明交代各处证据对观察的作用；需要跨段理解时保留必要上下文，不把分散引文凑成完整的假场景，也不以大范围代替具体联系。新旧标记可以在一个 prepare_batch 原子提交；回执成功才表示补充已落库。

全部成果、进度和回执同一事务提交。本批任一校验失败整批回滚，保留之前成功批次。成功回执给出新的任务版本；继续处理下一范围。

## 保存与接续作品认识

具体观察用 kind="observation"；跨片段认识用 kind="comparison"，通过同一 prepare_batch 保存。有依据的认识随阅读形成和修订，不只在阶段结束写总结；也不为每批或每本凑固定数量，不把所有特点写进一条总纲。

title 说明比较问题，scope_note 说明实际已读作品范围、场合及限制，note 连接反复选择与条件差异。references 按便于理解的顺序列出真实证据，每项 role_note 说明这处为何相关，例如共同选择、不同处理或条件依据；不是所有引用都证明同一种写法。reading_range 保留理解各处表达过程所需的上下文，不以固定前后段数代替文学判断。

用 `library_browse({view:"annotations",work_id,kind:"comparison",status:"active"})` 发现已有作品认识，按 ID 读 full 详情后核对原文，再补充、限定、拆分或撤回。作品认识可关联普通标签，但不需要特殊用途标签或独立风格导航工具。没有可靠比较依据时可以暂无条目，不为完成状态编造风格。

更新时完整保留仍成立的引用、标签和说明，移除已失效的解释；新证据改变判断时真正改写认识，而非在旧概括末尾追加反例。标注可引用同作品其他已读分部，本批 source_range 仍只表示实际处理范围，不据引用推进未读段落。未完成待办放 recovery，已成立且有用的认识要保存为标注，聊天或接续摘要不能替代。

详情默认 compact：references 每项提供 evidence_range、role_note 及可直接传入 source_read 的完整 read_target。full 详情提供 evidence_range、reading_range、role_note；修改前读 full，避免将阅读目标误当精确证据或漏掉旧引用。

## 疑点保存与纠错核验

来源疑点沿用现有字段：已核对的现象和位置放在 recovery.facts，原因未定或缺少版本对照放在 open_questions；需要修订的资产 ID、证据坐标及下一步放在 next_action。对后续原文理解有必要长期保留的问题，可保存带真实引用的简短标注，说明已知现象与不确定性，不用标签名代替证据，也不为每个猜测新建错误标签。

修订用 `library_browse({view:"annotations",work_id,annotation_id,format:"full"})` 取得当前完整对象，再通过 source_read 核对相应原文。按最新标记版本使用 prepare_batch 完整替换用途、标题、适用范围、说明、标签和引用；只保留仍有依据的旧引用，不为了保留原分析而继续提交错误证据。整条失效用 status="withdrawn"，已完成任务沿用 reopen_reason。没有当前批次阅读证据时先回读，不伪造 source_range 或阅读进度。

保存后再次通过同一详情入口核对当前版本和状态，以及说明、引用、标签是否表达同一结论。列表摘要和历史回执不能代替这一核验。仅替换标签或追加警告而保留已否定的分析，不算完成纠错；失败或核验不一致时保留具体待办，按当前版本修正，不盲目重放旧修订。

标签共享定义与分类通过上面的 tag_update 修订，prepare_batch 同名复用不能替代更新。没有足够依据改动共享含义时，记录具体缺口，不为绕过限制制造近义标签，不自行切换服务配置或用 SQL 修改。

撤回使标注不再作为有效分析，但原文仍可通过目录读取；普通“疑似问题”或“不要参考”标签没有服务级过滤效力。纠错不等于修复原文件、删除原文或将相应段落隐藏。

## 完成与失败处理

`prepare_finish({request_id, work_id, job_id, expected_version, recovery, note})` 只在目标全部 processed 时成功，不需要风格导航或索引就绪。必要的标注保存与核验须先完成；work_ready 还要求当前所有分部的准备任务完成。没有新观察的已读范围可以正常推进，不以标签数量或分类覆盖证明完成。

超时：`prepare_status({request_id})` 查询提交快照，此时不提供 work_id/job_id。未找到不证明失败，原请求可能仍在执行；可用原键原输入重试。成功回执是历史快照，接续仍读取当前状态。版本冲突读取新版本后判断，不能盲改版本覆盖。

`prepare_cleanup({work_id, confirm_work_name})` 只用于用户明确放弃指定整作品；普通批次失败不清理。清理不删除外部 TXT 或其他作品，失败整体回滚。

## 创作请求插入准备期间

优先处理用户当前创作要求。通过 library_browse 按作品范围、分类、标签和有效标注寻找原文入口，再用 source_read 回读；也可以直接经部章目录连续阅读，不等待全书分析完成。标签与标注不能替代原文，比较中的不同引用分别理解。准确区分本次已读范围与分析进度，不因目录可用宣称已经读完全书。

目录与原文默认 compact，检查完整对象或详细位置时使用 full；不改变原文、准备进度或同条件分页成员。列表续页保持原筛选，改变范围或条件后从首页重新查询。创作只读，不因查不到适用标签就补写资产。
