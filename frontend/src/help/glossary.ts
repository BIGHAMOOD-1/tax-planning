// 术语释义（Update 6.3）：供行内 ⓘ 提示与帮助页复用。
export interface TermDef { label: string; desc: string }

export const GLOSSARY: Record<string, TermDef> = {
  confirm_impact: { label: '确认税务影响', desc: '满足全部硬条件（关键数据为直接口径 + 公式确定 + 适用条件通过 + 政策有效）时的金额，可信、可追溯。' },
  scenario: { label: '情景测算（非结论）', desc: '基于假设或代理口径的估算，仅供参考，不代表可实现收益；与「确认影响」不可相加。' },
  shield: { label: '税盾', desc: '已存在的扣除/优惠带来的少缴税（如折旧、工资、优惠税率），不属于筹划新增收益。' },
  calculation_type: { label: '金额语义', desc: '一个金额在税务上的含义：BASELINE_TAX / SCENARIO_TAX / INCREMENTAL_TAX_BENEFIT / TAX_SHIELD / NOT_CALCULABLE / NO_CALCULATION。' },
  impact_type: { label: '影响类型', desc: '结论是否带金额：确认影响 / 情景测算 / 税盾 / 无需计算 / 不可计算。' },
  status_key: { label: '结论状态', desc: '方向的可信度标签：已确认 / 情景待确认 / 证据不足 / 不推荐。' },
  CONFIRMED: { label: '已确认', desc: '适用条件满足且已完成确定性计算（或确认资格/合规等结论）。' },
  SCENARIO: { label: '情景/待确认', desc: '金额依赖未验证口径或假设，尚不能确认。' },
  DATA_GAP: { label: '证据不足', desc: '关键条件/数据缺失——不等于"不符合"。' },
  NOT_RECOMMEND: { label: '不推荐', desc: '存在未满足的适用条件。' },
  resolution: { label: '口径', desc: '数据能否直接用于税法计算：DIRECT（直接）/ PROXY（代理）/ INSUFFICIENT（不足）/ MISSING（缺失）。' },
  PROXY: { label: '代理口径', desc: '用近似指标替代税法口径（如会计研发投入≠税法可加计费用）；代理口径不能支撑「已确认」。' },
  DIRECT: { label: '直接口径', desc: '可直接用于税法计算的数据。' },
  policy_validity: { label: '政策时效', desc: '所引政策当前是否有效：VALID / EXPIRED / NOT_YET_EFFECTIVE / UNKNOWN；UNKNOWN 不得作为「确认」依据。' },
  verification: { label: '证据可信度', desc: 'candidate（候选）/ confirmed（已确认）/ rejected（已驳回）。' },
  gate: { label: '证据门槛', desc: '判断某方向是否"值得查"：L0/L1/L2 三级；PASS / WEAK / NO_BASIS。' },
  diagnostics: { label: '诊断信号', desc: '对企业财务数据的事实性观察（非结论），用于指出"值得关注"之处；三档严重度：提示/观察/关注。' },
  chain: { label: '证据链', desc: '企业事实 → 诊断信号 → 税务方向 → 政策依据 → 条件核验 → 税额计算 → 最终结论。' },
}
