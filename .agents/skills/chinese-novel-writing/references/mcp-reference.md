# 创作时的 MCP 证据查询

本流程只读参考库，适用于提供下列工具和字段的 NovelLens MCP 服务。首次调用先核对宿主实际工具名、前缀和 schema；缺少必要连接或工具时说明边界，不自行安装或直连数据库，不需要开发规格。输入数字使用 JSON 整数，不用字符串版本。

Work 是已导入的作品来源，Annotation 是带证据的局部分析，Relation 是多处原文的关联，StyleGuide 是有适用范围的风格导航；这些分析都应回到原文核验。新作前文、设定和输出位置由用户任务提供，参考库不会自动管理新作状态。

## 先定位，再读证据

| 目的 | 调用与关键参数 | 返回值如何使用 |
| --- | --- | --- |
| 选择参考作品 | `work_list({limit?, cursor?})` → `work_get({work_id})` | 用真实 id 固定作品范围；列表没有 query 参数，多部参考作品分别查询 |
| 恢复风格入口 | `style_guide_get({work_id})` | 先核对 scope_note、entries[].kind / applicability，再按 source_ranges 读证据 |
| 找写法标签 | `tag_search({query, namespace?, limit?, cursor?})` → `tag_get({tag_id})` | query 对名称、定义、别名做字面子串匹配；标签全局共享，后续标注查询仍须 work_id |
| 找同作品身份 | `entity_search({work_id, query, type?, limit?, cursor?})` → `entity_get({work_id, entity_id})` | 类型可为 character、location、item、organization、concept；同名可能是不同身份 |
| 找标注候选 | `annotation_list({work_id, tag_ids?, entity_ids?, source_range?, limit?, cursor?})` | 返回 note_preview、note_truncated、first_source_range 等摘要；没有 query 参数 |
| 按原文字词找候选 | `source_search({work_id, terms, match?, limit?, cursor?})` | 无标注的原文也可命中；用 source_range 回读原文，paragraph_id 可补上下文 |
| 按写法说明找候选 | `annotation_search({work_id, terms, match?, limit?, cursor?})` | 只搜 Annotation.note；用 annotation_id 读取完整标注，不能把说明摘要当原文 |
| 读取完整标注 | `annotation_get({work_id, annotation_id})` | 获取完整 note、source_ranges 及关联 ID，再选择必要原文 |
| 找关联候选 | `relation_search({work_id, query?, relation_type?, source_range?, tag_ids?, entity_ids?, status?, limit?, cursor?})` | 字面查询关系标题、类型和说明；默认 active，不能当作原文搜索或语义排名 |
| 读取当前关系 | `relation_get({work_id, relation_id})` | 核对完整 note、status、version；此结果不含节点列表 |
| 展开证据节点 | `relation_expand({work_id, relation_id, expected_version, limit?, cursor?})` | 使用当前关系版本，items 含 source_range 和 role；再读所需节点原文，不必无目的展开全部节点 |
| 按已知范围读原文 | `source_read({source_range, limit?, cursor?})` | items 是原文，actual_range 是本页实际范围，requested_range 不代表已全部读取 |
| 补读上下文 | `source_get_context({work_id, section_id, paragraph_id, before, after})` | before / after 各 0–100，不跨章节；判断完成以实际返回内容为准 |
| 用户直接指定章节 | `source_sections({work_id, limit?, cursor?})` → `source_paragraphs({work_id, section_id, limit?, cursor?})` | 核对重名章节的 ordinal，空章节无正文；段落结果已含完整 text 与稳定 id |

SourceRange 的四项为 work_id、section_id、start_paragraph_id、end_paragraph_id；包含两端且不跨 Section。不能凭名称、模型记忆或估算字节偏移生成坐标。

普通查询分页默认 limit=100、最大 1000；source_search / annotation_search 默认 20、最大 100。next_cursor 非 null 表示有后续。继续同一查询时保持过滤条件，将返回的游标传入 cursor。可以在证据充分时停止候选查询，但应准确说明实际检查范围，不能声称已穷尽全库。阅读某一证据范围时，继续读取必要续页才能形成覆盖该范围的判断。

两个全文工具的 terms 是 1–8 个普通关键词，每词最多 128 字符，不写查询表达式；match 为 all / any，默认 all，all 必须在同一段原文或同一条说明中全部命中。中文支持单字、连续词，ASCII 按词且忽略大小写，兼容字符会归一化。按原文顺序或标注创建顺序返回，不按相似度排名。excerpt 是不超过 200 字符的真实子串；match_located=false 表示未定位到命中位置，返回的是段首摘要。说明修订可能改变后续页的命中集合。

## 可选原文语义查询

已连接服务提供语义工具时，先用 `semantic_index_get({work_id, kind?})` 查看 active / target 和 coverage；该读取不需要模型在线。已有完整索引可用 `source_semantic_search({work_id, kind?, query, limit?})` 按自然语言查询。kind 默认 fulltext，用于全文原文；annotation 用于已标注案例的引用原文，两层分别查询。query 为 1—8192 字符的非空白文本，加模型指令后的 token 数仍受服务预算限制；limit 默认 10、最大 50，无分页。

结果 items 包含 source_range、excerpt、excerpt_truncated 和 score，用 source_read 回读实际证据。score 是余弦相似度，不代表文学质量；candidate_window_limited 表示候选窗口受限，少量结果不证明穷尽全书。fulltext 的 coverage 按唯一段落计数，annotation 按当前标注计数；两者均与分析进度无关。标注候选另含 annotation_id、annotation_version、range_ordinal，先 annotation_get 读最新说明和全部引用，再回读原文；分数仍只表示原文相似。

默认只查询当前完整的 active。标注新增或改范围后，generation_status 可能仍为 ready，但 coverage.complete 为 false；关注 stale、not_indexed、blocked、pending，不能按历史状态宣称当前完整。只改说明或标签不使向量失效。任务允许接受缺口时，才显式使用 `allow_partial=true`，并说明返回的 partial 与 coverage；必要时指定 get 返回的 index_id，不混用其他作品或代。缺少完整索引、模型不可用或契约不符时，可改用关键词与已有坐标，不能伪装为没有相关内容。本 Skill 默认只读，不因写作自动调用 semantic_index_create / semantic_index_build，也不自行部署或升级数据库。

## 当前检索策略

先把创作问题分为“新作处境需要什么”与“希望观察什么表达方式”，形成少量有区分度的字面线索。例如，用户需要写一次道歉，可以分别查“道歉”和“回应”，再核对定义及相关原文；不要预设原作或库中已有这些标签。

- 单个 annotation_list / relation_search 中多个 tag_ids、entity_ids 是“全部匹配”，不是任选其一。需要替代线索时分别查询，再按对象 ID 和真实原文范围去重。
- source_range 过滤表示范围相交，不表示全文关键词匹配。relation_search 的 query 也不能检索所有 Annotation.note。
- 按情境字词使用 source_search，按写法术语使用 annotation_search；可并用标签、实体与关系查询，再按对象 ID 和真实范围去重。关键词无结果只说明当前字词没有命中，可改用同义表达或沿已知证据补读，不能据此宣布原作不存在相关表达。
- 摘要与裁剪说明不是完整证据。选择候选后读取对应 get 与原文，尤其核对条件、解释边界和另一种可能读法。
- Relation 默认查 active；若读取到 withdrawn，不将它作为有效结论。可以回到原文独立核验，但不能把历史 asset_write_get 快照当作当前关系恢复有效的证明。
- 两层原文语义查询与关键词、标注和关系入口分开使用，当前没有跨层统一 Search。工具发现中未提供的能力不能通过编造调用补齐。

## 调用模板

以下 JSON 使用 `{tool, arguments}` 表示调用；只把 arguments 传给宿主对应工具。`$WORK_ID`、`$TAG_ID`、`$RELATION_ID`、`$RELATION_VERSION` 和 `$READ_RANGE` 都应替换为本次返回的真实值；模板本身不能原样提交。

先查一个写法线索，核对标签定义后再使用返回的 ID：

```json
{
  "tool": "tag_search",
  "arguments": {"query": "回应", "limit": 10}
}
```

按作品和选定标签过滤标注，随后按候选 id 调用 annotation_get，而不是只依赖本页摘要：

```json
{
  "tool": "annotation_list",
  "arguments": {"work_id": "$WORK_ID", "tag_ids": ["$TAG_ID"], "limit": 10}
}
```

关系已通过 relation_get 核对当前状态与版本时，展开同版本的节点。页大小仅为示例，不是固定检索预算：

```json
{
  "tool": "relation_expand",
  "arguments": {"work_id": "$WORK_ID", "relation_id": "$RELATION_ID", "expected_version": "$RELATION_VERSION", "limit": 10}
}
```

对选中的标注、关系节点或导航证据读取原文。若 next_cursor 不为空，后续读取继续传入原 source_range 和返回的 cursor；只能对已经阅读的 actual_range 作出判断：

```json
{
  "tool": "source_read",
  "arguments": {"source_range": "$READ_RANGE", "limit": 10}
}
```

## 查询失败与停止条件

- isError=true 时读取结构化 code，不能把错误文本当成原文。健康检查或工具枚举成功不证明参考作品存在或已经取得证据。
- STYLE_GUIDE_NOT_FOUND：没有导航，可以使用用户已指定的原文或其他现有资产。不要为继续写作自动创建空导航。
- SEARCH_UNAVAILABLE：全文能力尚未启用，说明缺口并使用可用的标签、实体、关系或坐标读取；不要反复调用或自行升级数据库。
- WORK_NOT_FOUND 或其他对象不存在：核实作品和 ID；多作品查询保持隔离，不换一个同名对象冒充原目标。
- VERSION_CONFLICT：对关系重新 get，并从新版本的节点首页读取，丢弃旧版本分页拼接结果。INVALID_CURSOR 则重新查当前过滤条件首页。
- RESULT_TOO_LARGE：缩小 limit、补读数量或范围；单段仍超限时明确能力限制，不截断成伪完整原文。
- 连接或数据库不可用：说明未完成检索，停止重复失败的调用。用户允许独立于参考创作时可继续，但不能声称已用参考库核验；必须依赖该参考的任务保留阻塞。
- 证据足够时停止：能明确当前写法的原文依据、必要条件及新作适用边界。后续查询只重复相同证据时没有必要继续。

创作只读查询不会改变 Coverage；不调用 analysis_job_create、coverage_mark、analysis_checkpoint 或其他写工具记录“已读”。用户要求的正文另按指定位置输出，不能写进 Annotation.note 当作参考原作分析。

字段类型、必填项和枚举查当前连接工具的 schema；若与本包说明冲突，报告具体能力差异，不猜接口。文学判断遵循包内写作原则与参考读取方法，不用命中数量代替证据，不需要开发仓库。
