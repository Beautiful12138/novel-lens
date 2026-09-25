# 创作时的 MCP 原文阅读与查询

本流程只读参考库，适用于提供下列工具和字段的 NovelLens MCP 服务。首次调用先核对宿主实际工具名、前缀和 schema；缺少必要连接或工具时说明边界，不自行安装或直连数据库，不需要开发规格。输入数字使用 JSON 整数，不用字符串版本。

Work 是同一主线故事的作品，Part 是其中按顺序排列、名称任意的分部，不分部小说也有一部“正文”。分析资产属于 Work，可引用多个部；阅读时区分不同部的人物阶段与情境。Annotation 是带证据的局部分析，Relation 是多处原文的关联，StyleGuide 是有适用范围的风格导航；这些分析都应回到原文核验。新作前文、设定和输出位置由用户任务提供，参考库不会自动管理新作状态。

## 按参考目标选择阅读路径

- **局部写法问题：** 围绕需要观察的具体情境或表达方式检索，定位后阅读原文及必要上下文。例如查找道歉中的对白承接，可用“道歉”“回应”等线索；不预设原作或库中已有这些标签。
- **整体气质或阅读体验：** 先用 `source_sections` 查看目录，自主选择相关章节，通过 `source_paragraphs` 连续阅读相关场景，理解语言、人物互动、场景展开与情绪变化的共同作用，再用检索补充具体疑问。不熟悉作品、尚无阅读入口时，可用导航或搜索定位起点，随后进入连续阅读，不能用零散命中拼成对整体风格的判断。

两种路径可按任务结合使用，无需先读完标注或等待用户指定章节。目录标题只帮助选择入口，实际是否适用由原文判断；必要时改选或补读不同场景。沿分页和相邻章节补齐当前所需的展开，不强制通读全书、盘点全库或固定读取若干场景。阅读所得不支持初始方案时，回到用户目标调整方案，不只寻找支持既定写法的片段。

## 原文与定位工具

选中用于创作参考的候选后，用返回的真实范围调用 `source_read`，或通过 `source_paragraphs` / `source_get_context` 取得完整段落及必要上下文。已有范围时直接进入原文阅读；仅在需要其他引用、当前解释或状态时查询资产详情。下表是按需选择的入口，不是必须依次执行的清单；取得标签、标注或导航不算完成参考。

| 目的 | 调用与关键参数 | 返回值如何使用 |
| --- | --- | --- |
| 选择参考作品 | `work_list({limit?, cursor?})` → `work_get({work_id})` | 用真实 id 固定作品范围；列表没有 query 参数，多部参考作品分别查询 |
| 查看分部 | `part_list({work_id, limit?, cursor?})` → `part_get({work_id, part_id})` | 核对名称、顺序与范围；不将同作品的各部当成独立作品 |
| 选择章节并连续阅读 | `source_sections({work_id, part_id?, limit?, cursor?})` → `source_paragraphs({work_id, section_id, limit?, cursor?, format?})` | AI 可主动选择；核对重名章节的 ordinal，空章节无正文；段落含完整 text 与稳定 id，按 next_cursor 续读 |
| 恢复风格入口 | `style_guide_get({work_id})` | 先核对 scope_note、entries[].kind / applicability，再按 source_ranges 读证据 |
| 找写法标签 | `tag_search({query, namespace?, limit?, cursor?})` → `tag_get({tag_id})` | query 对名称、定义、别名做字面子串匹配；标签全局共享，后续标注查询仍须 work_id |
| 找同作品身份 | `entity_search({work_id, query, type?, limit?, cursor?})` → `entity_get({work_id, entity_id})` | 类型可为 character、location、item、organization、concept；同名可能是不同身份 |
| 找标注候选 | `annotation_list({work_id, tag_ids?, entity_ids?, source_range?, limit?, cursor?})` | 可按 first_source_range 直接读原文；source_range_count 大于 1 时不把首个范围当作全部证据，需要其他引用再 get；没有 query 参数 |
| 按原文字词找候选 | `source_search({work_id, terms, match?, limit?, cursor?})` | 无标注的原文也可命中；用 source_range 回读原文，精简范围的 start_paragraph_id 可补上下文 |
| 按写法说明找候选 | `annotation_search({work_id, terms, match?, limit?, cursor?})` | 只搜 Annotation.note；选中后按 first_source_range 读原文，需要其他引用或当前解释再 get，不能把说明摘要当原文 |
| 按需读取完整标注 | `annotation_get({work_id, annotation_id})` | 获取当前 note、status、全部 source_ranges 及关联 ID；得到说明后仍须阅读所选原文 |
| 找关联候选 | `relation_search({work_id, query?, relation_type?, source_range?, tag_ids?, entity_ids?, status?, limit?, cursor?})` | 字面查询关系标题、类型和说明；默认 active，不能当作原文搜索或语义排名 |
| 读取当前关系 | `relation_get({work_id, relation_id})` | 核对完整 note、status、version；此结果不含节点列表 |
| 展开证据节点 | `relation_expand({work_id, relation_id, expected_version, limit?, cursor?})` | 使用当前关系版本，items 含 source_range 和 role；再读所需节点原文，不必无目的展开全部节点 |
| 按已知范围读原文 | `source_read({source_range, limit?, cursor?, format?})` | items 是原文，actual_range 是本页实际范围，requested_range 不代表已全部读取 |
| 补读上下文 | `source_get_context({work_id, section_id, paragraph_id, before, after, format?})` | before / after 各 0–100 段，不跨章节；固定小窗口不保证场景完整 |

SourceRange 的四项为 work_id、section_id、start_paragraph_id、end_paragraph_id；包含两端且不跨 Section。不能凭名称、模型记忆或估算字节偏移生成坐标。

上述三个原文读取工具默认 `format: "compact"`。精简响应顶层提供 work_id、section_id，items 每段保留 id、ordinal、text；actual_range 和 requested_range 只含两端段落 ID，与同一响应顶层归属合并才能作为下一次调用的 SourceRange。空章节的 actual_range 为 null。分页游标和章节边界标记保持原有含义。需要字节位置时显式使用 full；旧服务以实际 schema 为准，没有 format 参数时省略。

source_search、annotation_search、source_semantic_search、annotation_list、annotation_get 也默认 compact；其中的 source_range、first_source_range、source_ranges 每项保留 section_id 与两端 ID，调用原文工具前须补上同一响应顶层 work_id。source_search 的 start_paragraph_id 可用作补读锚点。精简标注详情保留完整说明与全部引用，语义查询保留覆盖不足和候选窗口提示；需要时间或索引元信息时显式 full。两种格式的候选及游标一致，不因格式变化重新开始检索。

普通查询分页默认 limit=100、最大 1000；source_search / annotation_search 默认 20、最大 100。next_cursor 非 null 表示有后续。继续同一查询时保持过滤条件，将返回的游标传入 cursor。可以在证据充分时停止候选查询，但应准确说明实际检查范围，不能声称已穷尽全库。阅读某一证据范围时，继续读取必要续页才能形成覆盖该范围的判断。

source_read 的 limit 只控制分页，不会扩大 source_range；需要理解引用范围外的互动过程时，用真实段落 ID 调用 source_get_context 补读，根据内容决定方向和数量。仍需扩展时可取返回边缘段落为新锚点继续补读，按 ID 去重；章节边界不代表场景已完整，必要时查询相邻章节继续阅读。

两个全文工具的 terms 是 1–8 个普通关键词，每词最多 128 字符，不写查询表达式；match 为 all / any，默认 all，all 必须在同一段原文或同一条说明中全部命中。中文支持单字、连续词，ASCII 按词且忽略大小写，兼容字符会归一化。按原文顺序或标注创建顺序返回，不按相似度排名。excerpt 是不超过 200 字符的真实子串；match_located=false 表示未定位到命中位置，返回的是段首摘要。说明修订可能改变后续页的命中集合。

## 可选原文语义查询

已连接服务提供语义工具时，先用 `semantic_index_get({work_id, kind?})` 查看 active / target 和 coverage；该读取不需要模型在线。已有完整索引可用 `source_semantic_search({work_id, kind?, query, limit?})` 按自然语言查询。kind 默认 fulltext，用于全文原文；annotation 用于已标注案例的引用原文，两层分别查询。query 为 1—8192 字符的非空白文本，加模型指令后的 token 数仍受服务预算限制；limit 默认 10、最大 50，无分页。

结果 items 包含 source_range、excerpt、excerpt_truncated 和 score，用 source_read 回读实际证据。score 是余弦相似度，不代表文学质量；candidate_window_limited 表示候选窗口受限，少量结果不证明穷尽全书。fulltext 的 coverage 按唯一段落计数，annotation 按当前 active 标注计数；两者均与分析进度无关。标注候选另含 annotation_id、annotation_version、range_ordinal，可直接按返回范围读原文；需要全部引用或采用标注解释时，再 annotation_get 核对当前说明与状态。单个命中不代表全部引用，分数仍只表示原文相似。

关键词与两层语义搜索均支持可选 part_id；省略则覆盖全作品，候选包含部名与章名。追加部后 semantic_index_get 报 source_stale=true，搜索报 INDEX_SOURCE_CHANGED，需要使用者另建索引；写作期间可改用关键词或连续阅读，allow_partial 不能绕过来源变化。

默认只查询当前完整的 active。标注新增或改范围后，索引历史状态可能仍为 ready，但 coverage.complete 为 false；关注 stale、not_indexed、blocked、pending，不能按历史状态宣称当前完整。只改说明或标签不使向量失效。任务允许接受缺口时，才显式使用 `allow_partial=true`，并说明返回的 partial 与 coverage；必要时指定 get 返回的 index_id，不混用其他作品或代。缺少完整索引、模型不可用或契约不符时，可改用关键词与已有坐标，不能伪装为没有相关内容。本 Skill 默认只读，不因写作自动调用 semantic_index_create / semantic_index_build，也不自行部署或升级数据库。

## 检索与资产状态注意事项

标注列表、说明搜索及标注语义召回默认只返回 active 标注；annotation_get 若返回 withdrawn，不再依据该标注说明创作，但仍可独立阅读原文。查询后资产可能被其他 AI 撤回，需要采用标注解释时以当前 get 为准；独立阅读原文不以先读解释为前提。历史快照不代表当前有效状态。恢复后的标注可能尚未完整索引，继续按实际 coverage 判断。写作流程不自行修订标签、撤回或恢复资产。

- 单个 annotation_list / relation_search 中多个 tag_ids、entity_ids 是“全部匹配”，不是任选其一。需要替代线索时分别查询，再按对象 ID 和真实原文范围去重。
- source_range 过滤表示范围相交，不表示全文关键词匹配。relation_search 的 query 也不能检索所有 Annotation.note。
- 按情境字词使用 source_search，按写法术语使用 annotation_search；可并用标签、实体与关系查询，再按对象 ID 和真实范围去重。关键词无结果只说明当前字词没有命中，可改用同义表达或沿已知证据补读，不能据此宣布原作不存在相关表达。
- 摘要与裁剪说明用于选择候选。选中后必须读取原文，尤其核对实际表达、必要展开及适用条件；需要完整解释或其他证据位置时再 get，不用更多说明代替原文。
- Relation 默认查 active；若读取到 withdrawn，不将它作为有效结论。可以回到原文独立核验，但不能把历史 asset_write_get 快照当作当前关系恢复有效的证明。
- 两层原文语义查询与关键词、标注和关系入口分开使用，当前没有跨层统一 Search。工具发现中未提供的能力不能通过编造调用补齐。

## 调用模板

以下 JSON 使用 `{tool, arguments}` 表示调用；只把 arguments 传给宿主对应工具。`$WORK_ID`、`$SECTION_ID`、`$TAG_ID`、`$RELATION_ID`、`$RELATION_VERSION` 和 `$READ_RANGE` 都应替换为本次返回的真实值；模板本身不能原样提交。

连续阅读时，先取得目录；已有真实章节 ID 时可直接读取段落：

```json
{
  "tool": "source_sections",
  "arguments": {"work_id": "$WORK_ID"}
}
```

从目录选取相关章节，用其真实 id 读取完整段落。next_cursor 非空且仍需阅读后文时，保持 work_id、section_id 和分页参数不变，附上返回的 cursor 续读；一页读完不代表场景已完整。场景跨章时按目录继续读相邻章节：

```json
{
  "tool": "source_paragraphs",
  "arguments": {"work_id": "$WORK_ID", "section_id": "$SECTION_ID", "format": "compact"}
}
```

以下标签检索是一种可选入口；已有原文坐标时直接使用后面的 source_read 模板。通过标签查找时，核对定义后再使用返回的 ID：

```json
{
  "tool": "tag_search",
  "arguments": {"query": "回应", "limit": 10}
}
```

按作品和选定标签过滤标注，选中后用 first_source_range 补全作品 ID，直接读取原文；需要其他引用或完整说明时才按候选 id 调用 annotation_get：

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
  "arguments": {"source_range": "$READ_RANGE", "limit": 10, "format": "compact"}
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
- 阅读结束：已从实际原文中理解当前所需的具体表达与必要展开后，可以停止。候选重复、标注清楚或工具调用达到某个数量都不能单独证明阅读完成；人物互动与节奏参考须补齐必要的连续上下文。
- 压缩后恢复：保存的 SourceRange 可用于重新调用 source_read；只有结论或“此前读过”的记录时，不视为仍掌握原文。缺少坐标先重新定位，原文已不在当前上下文时按入口要求回读。

创作只读查询不会改变 Coverage；不调用 analysis_job_create、coverage_mark、analysis_checkpoint 或其他写工具记录“已读”。用户要求的正文另按指定位置输出，不能写进 Annotation.note 当作参考原作分析。

字段类型、必填项和枚举查当前连接工具的 schema；若与本包说明冲突，报告具体能力差异，不猜接口。文学判断遵循包内写作原则与参考读取方法，不用命中数量代替证据，不需要开发仓库。
