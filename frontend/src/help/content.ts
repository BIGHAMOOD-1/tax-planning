// 帮助文档内容（Update 6.2）。定位：**概念与术语说明**（系统特有含义），
// 不写操作步骤、不解释税务常识。
// 支持块类型：p / note / list / steps / defs / table

export type Block =
  | { kind: 'p'; text: string }
  | { kind: 'note'; text: string }
  | { kind: 'list'; items: string[] }
  | { kind: 'steps'; items: string[] }
  | { kind: 'defs'; items: { term: string; code?: string; desc: string }[] }
  | { kind: 'table'; head: string[]; rows: string[][] }

export interface Section {
  id: string
  title: string
  summary: string
  blocks: Block[]
}

export const SECTIONS: Section[] = [
  {
    id: 'overview',
    title: '一、系统概览',
    summary: '这个系统做什么、核心原则、结论四态',
    blocks: [
      { kind: 'p', text: '系统基于企业财务数据、政策知识与可追溯证据，给出税务筹划方向、证据链与合规意见。' },
      { kind: 'p', text: '核心原则：程序负责确定性判定与计算，AI 只负责解释与组织；「确认影响」与「情景测算」严格分离；证据不足时不妄下结论。' },
      { kind: 'defs', items: [
        { term: '结论四态', desc: '已确认 / 情景待确认 / 证据不足 / 不推荐。系统宁可标注「证据不足」，也不会把不确定的事写成「已确认」。' },
      ] },
    ],
  },
  {
    id: 'amount',
    title: '二、金额语义 calculation_type',
    summary: '一个金额在税务上代表什么（6 类）',
    blocks: [
      { kind: 'table', head: ['取值', '含义'], rows: [
        ['BASELINE_TAX', '不采取筹划措施时的基准税负'],
        ['SCENARIO_TAX', '基于假设条件的情景税负'],
        ['INCREMENTAL_TAX_BENEFIT', '相对基准新增的税收收益（才算"筹划价值"）'],
        ['TAX_SHIELD', '已存在的扣除/优惠产生的税盾（不属于新增收益）'],
        ['NOT_CALCULABLE', '理论上需要计算，但当前证据/数据不足'],
        ['NO_CALCULATION', '该方向本身不要求金额计算'],
      ] },
      { kind: 'note', text: '只有 INCREMENTAL_TAX_BENEFIT 且满足全部硬条件时，才产出「确认影响」。' },
    ],
  },
  {
    id: 'status',
    title: '三、结论状态与影响类型',
    summary: 'status_key（四态）与 impact_type',
    blocks: [
      { kind: 'p', text: '结论状态 status_key：' },
      { kind: 'table', head: ['取值', '含义'], rows: [
        ['CONFIRMED', '已确认：适用条件满足且已完成确定性计算（或确认资格/合规等结论）'],
        ['SCENARIO', '情景/待确认：金额依赖未验证口径或假设'],
        ['DATA_GAP', '证据不足：关键条件/数据缺失（不等于"不符合"）'],
        ['NOT_RECOMMEND', '不推荐：存在未满足的适用条件'],
      ] },
      { kind: 'p', text: '影响类型 impact_type（结论是否带金额）：' },
      { kind: 'table', head: ['取值', '含义'], rows: [
        ['CONFIRMED_IMPACT', '确认影响（可信）'],
        ['SCENARIO_IMPACT', '情景测算（非结论）'],
        ['TAX_SHIELD', '税盾（单独呈现，不计收益）'],
        ['NO_CALCULATION', '无需计算'],
        ['NOT_CALCULABLE', '不可计算（缺关键输入）'],
      ] },
    ],
  },
  {
    id: 'confirmed',
    title: '四、确认影响 · 情景测算 · 税盾',
    summary: '三个金额口径的区别',
    blocks: [
      { kind: 'defs', items: [
        { term: '确认税务影响', desc: '满足全部硬条件（关键数据为直接口径 + 公式确定 + 适用条件通过 + 所引政策当前有效）时才给出的金额，可信、可追溯。' },
        { term: '情景测算（非结论）', desc: '基于假设或代理口径的估算，仅供参照，不代表可实现收益。' },
        { term: '税盾', desc: '已存在的扣除/优惠带来的少缴税（如折旧、工资薪金扣除、优惠税率），不属于筹划新增的收益，系统单独呈现。' },
      ] },
      { kind: 'note', text: '「确认影响」与「情景测算」不可相加。' },
    ],
  },
  {
    id: 'resolution',
    title: '五、口径与代理 resolution',
    summary: '数据能否直接用于税法计算',
    blocks: [
      { kind: 'table', head: ['取值', '含义'], rows: [
        ['DIRECT', '直接口径，可直接用于税法计算'],
        ['PROXY', '代理口径：用近似指标替代税法口径'],
        ['INSUFFICIENT', '口径不足'],
        ['MISSING', '缺失'],
      ] },
      { kind: 'p', text: '代理示例：结构化数据中的「研发投入」是会计口径，税法可加计的研发费用还需按归集范围调整、剔除不适用支出、做「其他相关费用 10% 限额」，二者不等价，因此「研发投入」被登记为代理口径。' },
      { kind: 'note', text: '代理口径不能支撑「已确认」，只能产生「情景测算」；补充直接证据后可升级为直接口径。' },
    ],
  },
  {
    id: 'validity',
    title: '六、政策时效 policy_validity',
    summary: '所引政策当前是否有效',
    blocks: [
      { kind: 'table', head: ['取值', '含义'], rows: [
        ['VALID', '当前有效'],
        ['EXPIRED', '已失效'],
        ['NOT_YET_EFFECTIVE', '尚未生效'],
        ['UNKNOWN', '未知（需人工核验）'],
      ] },
      { kind: 'note', text: '时效为 UNKNOWN 的政策，不得作为「确认影响」的依据。' },
    ],
  },
  {
    id: 'gate',
    title: '七、证据门槛 Gate',
    summary: '判断某方向是否"值得查"',
    blocks: [
      { kind: 'defs', items: [
        { term: '级别', desc: 'L2（重点分析）/ L1（待观察）/ L0（候选）；由命中信号数量与数据完整度决定。' },
        { term: '状态', desc: 'PASS（有事实基础）/ WEAK（有信号但缺数据）/ NO_BASIS（无依据，进入"已排除方向"附录）。' },
      ] },
    ],
  },
  {
    id: 'evidence',
    title: '八、证据：可信度与采用',
    summary: 'verification_status 与 resolution_status',
    blocks: [
      { kind: 'table', head: ['字段', '取值', '含义'], rows: [
        ['verification_status', 'candidate / confirmed / rejected', '证据可信度：候选 / 已确认 / 已驳回'],
        ['resolution_status', 'pending / accepted / superseded / conflict', '证据采用：待定 / 已采用 / 被覆盖(保留旧值) / 与画像冲突(未覆盖)'],
      ] },
      { kind: 'note', text: '「被采用」与「被验证」是两个维度：采用＝当前是否进入画像；验证＝证据是否可信。' },
    ],
  },
  {
    id: 'chain',
    title: '九、证据链 7 节点',
    summary: '从企业事实到最终结论的可追溯链路',
    blocks: [
      { kind: 'steps', items: [
        '① 企业事实：本次用到的字段与来源（F*，含计算式与数据来源表）。',
        '② 诊断信号：该方向命中的观察/异常（见第十节）。',
        '③ 税务方向：本次筹划方向与对应 Skill。',
        '④ 政策依据：检索到的政策（文号/标题/日期/时效，可点开原文链接）。',
        '⑤ 条件核验：适用条件的 通过 / 不满足 / 未知。',
        '⑥ 税额计算：计算分层（输入→公式→结果）与金额语义。',
        '⑦ 最终结论：结论与理由。',
      ] },
      { kind: 'p', text: '两个旁支：审查说明（机会分析 / 合规审查，可展开）与 Evidence/来源汇总。' },
    ],
  },
  {
    id: 'diag',
    title: '十、诊断信号（系统观察）',
    summary: '什么是诊断、三档严重度、为什么有的方向"0 项"',
    blocks: [
      { kind: 'defs', items: [
        { term: '诊断信号是什么？', desc: '系统对企业财务数据的事实性观察（不是结论），用于指出"值得关注"之处，例如"应收账款占比偏高"。' },
        { term: '三档严重度', desc: '提示 / 观察 / 关注（关注最高）。严重度由偏差相对阈值的分位决定。' },
        { term: '四类诊断', desc: '勾稽类（报表/明细对应关系）、推算类（用基准推算理论值对比）、确认类、趋势类（同比变化）。' },
        { term: '「0 项 / 该方向无命中诊断」是什么意思？', desc: '表示该方向没有命中任何诊断信号——既不代表有问题，也不代表没问题，只是本次没有触发观察。' },
        { term: '诊断与结论的关系', desc: '诊断用于"发现方向"，不直接决定结论；结论由 Skill 条件 + 证据 + 计算共同判定。' },
      ] },
    ],
  },
  {
    id: 'agents',
    title: '十一、多代理角色',
    summary: '机会分析 / 合规审查 / 综合决策 / 税务顾问',
    blocks: [
      { kind: 'defs', items: [
        { term: '机会分析', desc: '从企业角度寻找机会，给出主张、依据（P*/F*/C*）与措施。' },
        { term: '合规审查', desc: '审查合规性：适用条件是否满足、证据是否充分、风险与质疑。' },
        { term: '综合决策', desc: '整合各方给出最终结论（四态）与理由。' },
        { term: '税务顾问', desc: '在结论之后给出"真要落地该方向应当怎么做"的合规实施意见。' },
      ] },
    ],
  },
  {
    id: 'review',
    title: '十二、审查模块',
    summary: 'Skill 审查 / 审查引擎 / 案例评价 / 证据冲突',
    blocks: [
      { kind: 'defs', items: [
        { term: 'Skill 审查', desc: '查看各 Tax Skill 的适用场景、数据依赖、口径与结论。' },
        { term: '审查引擎', desc: '查看/编辑 Gate 开关、信号阈值、诊断严重度阈值、诊断规则、字段 Resolution。' },
        { term: '案例评价', desc: '对系统生成的方案（历史决策案例）进行采纳 / 驳回 / 修改。' },
        { term: '证据冲突', desc: '当证据与画像不一致时，决定「以证据为准」（覆盖画像）或「保留画像」（驳回证据）。' },
      ] },
    ],
  },
  {
    id: 'pages',
    title: '十三、页面导览',
    summary: '各页面展示的内容',
    blocks: [
      { kind: 'defs', items: [
        { term: '生成', desc: '发起一次分析。' },
        { term: '生成结果', desc: '企业总览：基本情况、多年趋势、方向影响对比、系统观察、筹划方向。' },
        { term: '方向详情', desc: '单方向的结论、合规税务意见、证据链、必需要素、计算分层与审查过程。' },
        { term: '审查', desc: 'Skill 审查 / 审查引擎 / 案例评价 / 证据冲突。' },
        { term: '数据中心', desc: '数据与知识库规模统计。' },
        { term: '知识库', desc: '政策/案例检索与索引概况。' },
        { term: '设置', desc: '对话模型与向量服务配置。' },
      ] },
    ],
  },
  {
    id: 'faq',
    title: '十四、常见问题',
    summary: '概念类疑问速查',
    blocks: [
      { kind: 'defs', items: [
        { term: '「确认税务影响」和「情景测算」能相加吗？', desc: '不能。情景测算是假设性估算，不代表可实现收益。' },
        { term: '为什么某方向没有金额？', desc: '可能属于"税盾/情景/无需计算"，或关键证据不足。' },
        { term: '「0 项诊断 / 无命中诊断」是不是就没问题？', desc: '不是。它只表示本次没有触发观察信号；结论仍由条件、证据与计算决定。' },
        { term: '政策显示「时效未知」怎么办？', desc: '需人工核验有效性；未知政策不能作为"确认"依据。' },
        { term: '为什么同一方向有时是"代理"？', desc: '所用数据是近似税法口径的（如会计研发投入）；补上直接证据后可升级为"直接口径"。' },
        { term: '「完整度」低说明什么？', desc: '该方向必需事实缺失较多，较可能是"证据不足"；补齐材料后再分析即可改善。' },
      ] },
    ],
  },
]
