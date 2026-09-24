# 智能税务筹划系统

> 基于企业财务数据、政策知识与**可追溯证据**的税务筹划分析系统。
> 目标：让每个结论都能回溯到 **材料 → 页码 → 原文 → Evidence → 字段 → Skill → 计算 → 决策**。

核心原则：**程序负责确定性判定与计算，AI 负责解释与组织**；
"确认税务影响"与"情景测算"严格分离；证据不足时不乱下结论。

---

## 一、能力概览

```
企业 → 画像 → 税务诊断 → 筹划方向发现 → Evidence Gap → 补证
     → Tax Skill（资格/口径/计算）→ 红蓝对抗审查 → Green 结论
     → 方案 + 证据链
```

- **数据工程**：88 张 CSMAR 表标准化并入 → 主表 716 列 / 画像 843 列 / 衍生 118 列；2020–2024，5,169 家公司、23,284 公司·年。
- **Tax Skill**：24 个（研发加计、高新技术、融资、资产、政府补助、租赁、折旧摊销…）。
- **知识库**：政策 5,593 篇（15,355 chunk）+ 案例 1,021 篇（5,064 chunk）；向量 + BM25 + Reranker。
- **诊断引擎**：跨字段一致性 / 基准偏离 / 存量-流量 / 趋势，三档严重度（提示/观察/关注）。
- **证据层**：三类输入（直接输入 / 结构化数据 / 文档）→ Evidence → 采用（Resolution）→ Profile；含 PDF 抽取、引用校验。
- **可信度工程（Update 5.0）**：`calculation_type`（6 类金额语义）+ 系统不变量（**确认影响只能来自 `INCREMENTAL_TAX_BENEFIT`**，既有税盾/情景/无需计算不得伪装成收益）；`policy_validity` 政策时效门禁（UNKNOWN/失效不得支撑 CONFIRMED）。
- **证据驱动口径升级（Update 5.2）**：上传**直接口径证据**（如研发辅助账、资产损失扣除资料）后，关键输入的默认代理口径（PROXY）升级为 DIRECT，相应方向可产出 CONFIRMED（研发加计 / 资产减值）。
- **数据层回填（Update 5.2）**：低覆盖字段多源合并 + **显式代理**（`hightech_signal_proxy`，资格事实不由财务指标回填）+ 关键事实**覆盖率台账**（`data_processed/low_coverage_report.md`）。
- **证据链（Update 5.0）**：**7 节点**可解释链（企业事实 → 诊断信号 → 税务方向 → 政策依据 → 条件核验 → 税额计算 → 最终结论），Red/Blue 作审查说明旁支。
- **工程护栏（Update 5.1）**：Embedding 指纹 + 换模型强制重建；Rerank 指数退避/冷却/缓存；**版本指纹**（数据/模型/Skill/配置）；政策元数据三层来源（含 `source/confidence/verification`）；任务持久化；OpenAPI→TS 类型链。
- **体验与导出（Update 5.2）**：一键**下载报告 / 导出证据链**；方向影响对比图；响应式 + 骨架屏。
- **合规税务意见（Update 6.1）**：独立 **Advisor Agent**——与 Red/Blue/Green 同源（共用数据池 + 辩论 + 结论），**自行补充 RAG**，回答"真要落地该方向应当怎么做"（实施路径 / 资料 / 合规红线），独立卡片展示并可导出。
- **设置（Update 6.1）**：前端配置对话模型（DeepSeek/OpenAI/千问/Claude 兼容网关）与向量服务；知识库页支持**一键重建索引**。
- **UI**：FastAPI + React 工作台（生成 / 生成结果 / 审查 / 数据中心 / 知识库 / 设置）。

---

## 二、目录结构

```
项目根目录\
├── config\                     # config.yaml / thresholds.yaml / dataset_registry.yaml / fact_resolution.yaml
├── src\
│   ├── common.py               # 路径/配置/日志/.env 加载
│   ├── data\ derived\ profile\ # 数据工程（并入、衍生、画像）
│   ├── skills\                 # Tax Skill 引擎 + definitions/*.yaml（24 个）
│   ├── agents\                 # Red / Blue / Green / Opportunity
│   ├── rules\                  # calculator / diagnostics / fact_resolution
│   ├── rag\                    # 知识库检索（policy/case/document）
│   ├── evidence\               # 证据契约/存储/冲突/评审/PDF/LLM抽取/引用校验/政策元数据与时效
│   ├── report\                 # markdown 报告 + view_model（UI 轻索引）+ export（证据链导出）
│   ├── pipeline\               # plan_pipeline（端到端编排）
│   ├── versioning.py           # 版本指纹（数据/模型/Skill/配置）
│   └── api\                    # FastAPI 适配层（schemas/adapters/routers/services/jobs）
├── scripts\                    # run_plan / run_pipeline / review_evidence / build_* / gen_api_types / backfill_fixtures / 验证脚本
├── frontend\                   # React + TypeScript + Vite（UI；含 api.gen.ts 类型链）
├── data\  案例\                # 原始数据与知识库（只读）
├── data_processed\             # master / derived / features / tables / lineage / evidence.db
├── knowledge\                  # policy_corpus / cases / index（向量+BM25）
├── outputs\                    # 每家企业分析结果（result.json / plans/ / report.md）
├── validation\                 # Update 3.1 验证产物
├── tests\                      # test_smoke / test_modules
├── docs\                       # 总设计 / 数据处理 / 架构 / UI 大纲
└── *.md                        # 版本总结与迭代记录（见文末索引）
```

---

## 三、环境准备

> **可移植**：项目路径已相对化（根目录由 `src/common.py` 按文件位置推导，`config.yaml` 用相对路径），
> 可放在任意目录，无需改配置。

**Python 3.11**（已验证 3.11.0）

```powershell
pip install -r requirements.txt
```

**Node 18+**（仅 UI 需要；已验证 Node 24 / npm 11）

```powershell
cd frontend
npm install
```

### API Key（必填）

系统使用两个外部服务：
- **对话模型**：DeepSeek（OpenAI 兼容）
- **向量 / 重排**：SiliconFlow（`bge-m3` + `bge-reranker-v2-m3`）

复制模板并填入你自己的 Key：

```powershell
copy .env.example .env      # Windows
# cp .env.example .env      # macOS/Linux
```

`.env` 内容：
```ini
OPENAI_API_KEY=sk-你的DeepSeekKey
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
SILICONFLOW_API_KEY=sk-你的SiliconFlowKey
```

> 程序启动时自动加载项目根目录 `.env`；**已存在的系统环境变量优先，不会被覆盖**。
> `.env` 含密钥，**请勿提交到代码库**。
>
> **密钥落盘说明**：环境变量中的密钥**不会**被写入任何文件；但**在「设置」页手动填写的密钥**会**明文**保存在
> `config/settings.json`（已加入 `.gitignore`，请勿提交）。本系统为**本地单机演示**，API **无鉴权**（已知非目标）。

---

## 四、快速开始（UI Demo）

Demo 已内置两家 Fixture：**600004 白云机场**（证据补充型）、**000063 中兴通讯**（诊断/多方向型）。

**方式一：单源（推荐，演示用）**
```powershell
cd frontend; npm run build      # 生成 frontend/dist（仅需一次）
cd ..; python run_server.py     # 启动后访问 http://127.0.0.1:8000
```

**方式二：开发（前后端分离，热更新）**
```powershell
python run_server.py            # FastAPI :8000
cd frontend; npm run dev        # Vite :5173（/api 代理到 :8000）
# 访问 http://localhost:5173
```

Demo 主线：选择 600004 → 企业画像/诊断 → 筹划方向 → 点某方向看 Required Facts / 证据状态 → 证据链（材料→页码→原文→Evidence→字段→计算→决策）。

---

## 五、命令行使用

**单企业分析**
```powershell
python scripts\run_plan.py --stock 600004 --year 2024            # LLM 模式（分钟级）
python scripts\run_plan.py --stock 600004 --year 2024 --no-llm   # 规则模式（秒级）
```
输出：`outputs/<代码>_<年份>/`（`result.json` / `plans/*.json` / `report.md` / `profile.json` / `run_meta.json`）。

**证据评审 / 文档摄取**
```powershell
python scripts\review_evidence.py pending
python scripts\review_evidence.py act <evidence_id> a
python scripts\review_evidence.py seed --stock 600004 --year 2024
python scripts\review_evidence.py doc --stock 600004 --file speech.txt --doc-type speech
python scripts\review_evidence.py extract --stock 600004 --types rd_spend_sum,rd_person --year 2024
python scripts\ingest_pdf.py --pdf 11069309.PDF --stock 600004 --year 2024
```

**知识库 / 数据工程**
```powershell
python scripts\build_knowledge.py --rebuild     # 重建向量+BM25 索引
python scripts\run_pipeline.py                  # 数据工程流水线
```

---

## 六、API（FastAPI，前缀 `/api`）

```
GET  /api/companies?q=                          企业检索
GET  /api/companies/{code}                      画像摘要 + 可用年度
GET  /api/analyses                              已分析清单
GET  /api/analyses/{code}/{year}                分析结果（UI View Model）
GET  /api/analyses/{code}/{year}/report         下载完整报告（Markdown）
GET  /api/analyses/{code}/{year}/plans/{dir}    完整方案（含 7 节点证据链）
GET  /api/analyses/{code}/{year}/plans/{dir}/export  导出该方向证据链（Markdown）
POST /api/analyses/run                          异步生成（返回 job_id）
GET  /api/jobs/{job_id}                         任务进度（持久化，可重启恢复）
GET  /api/analyses/{code}/{year}/plans/{dir}/opinion    合规税务意见（生成/获取）
GET  /api/analyses/{code}/{year}/plans/{dir}/opinion.md 下载意见书（Markdown）
GET  /api/knowledge/index                       知识库索引概况
POST /api/knowledge/rebuild                     重建知识库索引（后台任务）
GET  /api/settings                              读取设置（Key 只回显来源/masked）
POST /api/settings                              保存设置（只写用户填写项，不落盘 env 密钥）
POST /api/settings/test                         测试 对话/向量/重排 连接
GET  /api/kb/search?q=&corpus=                  知识库检索
GET  /api/meta/stats                            数据/知识库规模
GET  /api/meta/version                          版本指纹（数据/模型/Skill/配置）
GET  /api/evidence/pending                      待评审证据
GET  /api/evidence/conflicts?code=&year=        证据冲突列表
POST /api/evidence/{id}/review                  证据评审（接受/驳回/修改）
POST /api/evidence/{id}/resolve?choice=         冲突解决（evidence=以证据为准 / profile=保留画像）
POST /api/ingest/text | file | commit           补充材料：文本/文件预览 → 提交入库
GET  /api/fact-types                            事实类型（含中文名/说明）
GET  /api/solutions                             方案库；POST /api/solutions/{id}/review 审定
GET  /api/review/skills | engine | thresholds   Skill 审查 / 阈值 / 引擎配置
GET  /api/health                                健康检查
```
交互式文档：启动后访问 `http://127.0.0.1:8000/docs`。

---

## 七、测试

```powershell
python tests\test_smoke.py      # 数据 / RAG / Skill / 证据 / 端到端回归
python tests\test_modules.py    # 分层模块（21 项：诊断/计算器/证据/四态/金额语义不变量/政策门禁/指纹/任务持久化/导出/冲突/回填…）

# 版本与类型链
python scripts\gen_api_types.py            # 由 OpenAPI 生成 frontend/src/api.gen.ts
python scripts\backfill_fixtures.py        # 回填历史 fixture 的确定性字段（calculation_type/政策时效）
python scripts\backfill_low_coverage.py    # 低覆盖字段回填 + 覆盖率台账
python scripts\calibrate_thresholds.py     # 逐诊断阈值校准（含逐项建议）

# Update 3.1 验证脚本
python scripts\skill_data_dependency.py
python scripts\audit_skills.py
python scripts\test_calculators.py
python scripts\validate_evidence.py
```

---

## 八、版本与文档索引

### 交付物（评委可直接阅读）

| 文件 | 内容 |
|---|---|
| **`技术报告.md`** | **技术报告**（初赛评审交付物）：架构、核心机制、测试与效果评估 |
| **`效果评估报告.md`** | **效果评估报告**（独立交付物）：合规精度对比 + 独立有效性验证 |
| **`数据来源与合规说明.md`** | 数据来源、合规声明与提交包整理 |

### 开发过程记录

开发过程与迭代记录保存在本地 `docs/` 目录（不随本仓库上传）。

---

## 九、已知限制

1. **外部数据缺失**：增值税申报表（销项/进项/留抵）、部分明细（职工薪酬、税金及附加、并购评估）覆盖有限；低覆盖字段采用多源回填 + 代理口径（`resolution=PROXY`）标注。
2. **PDF 仅数字版**：不做 OCR；复杂表格/跨页仍可能漏抓（Word/OCR 后置）。
3. **口径标注**：文档抽取的 `caliber` 需人工补全或从标题推断。
4. **确认影响稀缺是设计使然**：只有"关键输入全 DIRECT、公式确定、条件通过、政策时效 VALID"的方向才产出 CONFIRMED；其余为情景测算或需补证据。
5. 密钥通过环境变量/`.env` 提供，不在代码库内。
6. **未做（长期项）**：鉴权/权限/脱敏/审计、规模化压测（`struct2.md` 5.3，非 Demo 必需）。
