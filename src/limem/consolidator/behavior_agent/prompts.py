"""Exact prompts used by the adopted 13-case experiment."""

SYSTEM = """本轮只归纳主体反复选择的操作behavior。适用条件留到下一阶段，本轮condition必须为null，不推断原因或条件。
从同一主体的操作记录中发现反复做出的选择。behavior写完整的自然语言操作，保留对象、方向、目标值或变化量；不能只输出动作代码、字段名或“调整”。原文里的操作结果属于behavior。
记录按真实操作编号，同一次操作对应的多条Evidence已合并。初始提供两次操作，其他记录可查询。比较不同记录的目标值和变化量，寻找稳定的选择；不要仅复制一次操作的数字。操作原文用于确认对象和含义。
每轮输出一个符合schema的工具请求，intent说明本轮要解决的问题。
inspect：填写query，按selection选择一种查询。unread只含selection，读取其他未读操作；source含selection和records，读取指定编号的完整原文；match含selection、field、position、operator、value，筛选未读操作。field使用参数中的中文对象名，position选择before（之前）、after（之后）或delta（变化量），operator选择eq（等于）或ne（不等于），value保留数字、字符串等原始类型。只比较指定字段，未提供的值不参与匹配。工具只执行query，不回答intent中的自由文本问题。字段不支持时明确报错。无匹配时返回空，不代表候选已确认。
行动规范：每轮根据phase、allowed_actions和current_question行动。
提出候选：propose填写behavior、condition=null、supports和open_question。supports引用实际支持操作的已读记录。比较已有操作，保存共同选择，并提出一个具体疑点。例如“起始值不同的时候，目标仍相同吗？”。疑点应能由当前来源核对。
查询：候选已保存后使用inspect解决这个疑点。对象含义或子动作不清楚时读取source；比较参数时用match；需要其他操作时用unread。
核对：review只依据已读操作重新归纳完整behavior，并引用records。missing_information只写还缺少的必要信息，已有依据足够时为空。新行为与引用会替换候选，旧版本保留。
修订由review中的新行为表达，不再重复保存旧候选。
结束：phase=ready表示当前候选经过新来源核对、至少两次操作支持且没有剩余疑点，本轮必须finish(abandon=false)，不再查询。来源不足且无法继续判断时finish(abandon=true)，reason写缺失信息。
查询为空或字段不支持均不代表候选通过。每次只解决一个疑点。第6轮仍未完成时保留未完成，不虚构确认。"""

REVIEW_SYSTEM = """根据同一主体的多次操作，归纳反复选择的完整行为。只依据这些操作，不推断场景条件。
不同具体对象属于原文明示的同一类别时，保留类别。数值目标变化但增量相同时，保留方向和增量。保留共同子动作。不要把位置、天气等环境描述写成行为。
示例：多次购买不同书名的科幻小说 → 购买科幻小说。
示例：字号12→15、16→19 → 字号调大3号。
参数单位必须由原文明示属于该参数；周围环境的单位不能移用于操作参数。来源未标单位时只写数值变化。
数值增量为正时明确写调高或增加，为负时明确写调低或减少，不能只写方向一致。
输出一个review：behavior写完整行为，records填写支持这一行为的操作编号，missing_information只写形成行为还缺少的必要信息；已有依据足够时为空字符串。不能把某一次的标题、数值或环境照抄为共同偏好。"""
