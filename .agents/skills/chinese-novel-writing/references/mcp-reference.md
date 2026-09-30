# 创作前的原文参考

使用宿主实际发现的 NovelLens 工具与 schema，工具前缀由宿主决定。参考默认只读，通过 `library_browse` 浏览分类、标签和标注，再用 `source_read` 阅读原文；不新建准备任务、修改共享标签或改变分析进度。目录与原文默认 compact，full 用于完整对象或详细位置检查。

## 范围与目录

用户指定参考作品时遵守范围；未指定时可以全库探索，也可以先选择适用作品。不需要预先锁定新作技巧，再寻找证明该安排的例句。

- `library_browse({view:"works",limit?,cursor?})` 查看可见作品。已知作品 ID 可复用。
- `view:"categories"`、`"tags"`、`"annotations"` 均可提供单个 `work_id` 或非空 `work_ids`，二者互斥；均不提供表示全库可见范围。
- `view:"parts"`、`"sections"` 必须提供单个 work_id。章节可用 part_id 缩小范围；通过目录可直接选择章节连续阅读。
- 列表分页保持原筛选条件并传 next_cursor。改变范围或筛选时从首页重新查询；compact/full 不改变同条件列表成员。

分类目录不分页，只展示当前范围有效标注实际使用的分类和未分类计数；它不代表作品写法的全部可能。全库标签目录包含尚未使用的共享标签，指定作品范围时才只返回这些作品实际有效使用的标签。annotation_count 是当前范围的有效标注数，不是文学质量、适用程度或原文出现频率。

## 从分类和标签选择原文入口

1. 用 `library_browse({view:"categories",work_id? 或 work_ids?})` 浏览有内容的方向，再用 `view:"tags"` 查看相关标签。分类是发现材料的目录，不必遍历全部分类；可以省略分类查看全部标签，也可以直接浏览标注。
2. 标签可按 `category`、`namespace` 和 `query` 筛选。category 使用分类目录返回的 ID；`category:"unclassified"` 查尚未归类的标签，省略 category 查全部。query 对名称、定义和别名做字面子串查找，不会理解自然语言需求。先核对定义再选标签，不编造标签 ID；没有同名标签不代表没有适用原文。
3. `library_browse({view:"annotations",work_id? 或 work_ids?,tag_ids?,tag_match?,kind?,status:"active",limit?,cursor?})` 查看标注候选。tag_match 默认 any，命中任一所选标签并去重；明确需要交集时用 all。tag_ids 可以省略，直接浏览当前范围。按 part_id、section_id 或 source_range 缩小范围时必须指定单作品，并遵循实际 schema。source_range 匹配任一引用相交的标注。
4. 根据作品、部章、标题、kind、scope_note、标签和观察摘要挑选值得阅读的条目。需要完整解释、比较关系或更多引用时，用 `library_browse({view:"annotations",work_id:"该条所属作品ID",annotation_id:"该条ID",format:"full"})` 读详情；详情不混入 work_ids、tag_ids、status 或 cursor 等列表筛选。跨作品列表中的每条标注都保留自己的作品归属。
5. 沿真实引用调用 source_read，结合必要上下文理解表达，再确定依赖参考的方案。读后不适用时可以换标签、标注或作品，也可以放弃这条参考，不要求把选中的标签写进新作。

已有明确标注、原文坐标或足够适用原文时，直接接续相应步骤，不为遵守流程重扫目录。用户尚未确定故事时，可先探索吸引自己的材料，让阅读影响构思；分类与标签不构成技巧选题表。

标注列表摘要用于挑入口，完整说明帮助恢复已有认识，二者都不能代替必要原文。多处引用可能分别承担例证、对照或条件说明，不代表每处都同样适用；需要两边证据才能理解比较时分别读取。不连续原文不能拼接成一个场景，跨作品的声音也不能混成同一作者规律。已撤回标注不能当作有效结论，其原文仍可独立阅读。

没有适用条目时，可以查看全部标签、未分类、直接浏览标注或经部章目录读原文；目录空缺不证明原文没有参考价值。接口失败不等于无条目。必要原文无法取得时明确说明缺口，只有用户任务允许时才继续不依赖该参考的创作。

## 作品整体与跨片段比较

整体写法或阅读体验的参考，先查可用的比较认识，再回读适用连续原文与普通行文。局部表达问题不必每次浏览全部比较；已有足够材料可以复用。

1. 用 `library_browse({view:"annotations",kind:"comparison",status:"active",work_id? 或 work_ids?})` 直接查作品认识；不需要先取得特殊标签。省略 kind 查询两种用途，kind="observation" 只看具体观察。分页保持范围和 kind 等原筛选。
2. 选择相关标题与 scope_note，按 work_id、annotation_id 读详情；不要把 kind 当文学质量认证，也不把已读少量场景的认识扩大为全书规律。
3. 结合 note 和每处 role_note 区分共同点、对照与条件依据，沿各自 read_target 回读将要借鉴的连续原文。必要时通过目录补读普通行文、不同场景或跨章承接，避免只收集支持同一概括的句子。

没有可用作品认识时，直接沿分部与章节目录连续阅读。读后形成适用于新作的人物相处、内容展开、讲述视角和句段承接选择，再确定依赖参考的构思；不先锁定故事再补标签和修辞，也不复制参考作品的设定、事件或表面口头禅。认识条目是可修订的阅读成果，不能代替原文。

## 连续原文阅读

`source_read` 输入 work_id、section_id，可选 start_paragraph_id 与 end_paragraph_id（须成对），以及 before、after、limit、cursor、format（默认 compact）。

- 无端点时整章分页读取；有端点时按范围读取，before/after 各可扩展 0–100 段。
- 默认一页 60 段，最多 200 段，是响应预算，不是充分理解的标准。
- 响应每个自然段保留 id、ordinal、text，顶层包含 work_id/section_id；本页 actual_range 的端点与顶层归属组成完整已读坐标。
- next_cursor 非空时，保持原端点、扩展参数和章节续读。requested_range 不是本页已读证明；候选预览和分析进度也不是本次已读。
- 标注 compact 详情的 references 每项提供完整 read_target，可直接作为 source_read 参数；evidence_range 是精确证据，不是默认阅读边界。full 详情使用 references.reading_range。每处 role_note 解释其在观察中的用途，阅读目标不需要自行拼 UUID。
- 引用是阅读入口，不是默认阅读边界；场景或理解所需联系跨章时，通过目录读取有关章节。阅读范围依据实际表达与任务需要决定，不固定段数或调用次数。

将详情中 references 某项的 read_target 整体传给 source_read 即可开始阅读。需要额外上下文时，在该真实范围上添加扩展参数，例如：

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

示例只演示参数，不是阅读配额。沿实际句行辨认内容选择、表达承接及必要条件；当前上下文已有正文可以复用，只有旧总结时按坐标回读。读到材料与用好材料分别判断，不能以调用成功、标签命中或阅读数量证明成稿有效。

最终在新作自身的人物、处境与语言中重新创造，不逐句替换原作人名或物件，不把同一结构位置当作同等因果作用。默认交付正文，不附工具清单、自评或冗长读书报告；用户要求时说明实际参考范围。
