# RNAHarness 设计计划

> 草稿，2026-11-02。用于讨论，不是定稿。
> 关联：模型侧 `C:\qwen-api\notebooktmp`（训练代码）与
> `C:\qwen-api\RNAQwen\PLAN_v2.md` §31/§32（任务定义、数据、彻查记录）。

## 0. 定位

**harness 是中心，模型是工具。**

不是「一个会回答 RNA 问题的模型 + 一些辅助工具」，而是「一个能查文献、能算结构、
能判断关系的 agent」，其中「判断关系」由一个专门工具完成，该工具内部才是 RNAQwen
适配器。

按可计算性分三层：

| 层 | 归属 | 例子 |
|---|---|---|
| 可计算 | 工具 | ViennaRNA、Rfam/HMMER、序列统计、seed match |
| 可检索 | RAG | 文献中的实验证据、功能描述、已知互作 |
| 两者都不是 | 模型工具 | 两条序列之间有没有关系（无文献时的先验） |

## 1. 现状盘点（2026-11-02 实测本仓库）

- `SKILL.md`：Claude Code skill 定义，`name: romancohort-lab`
- `scripts/lab.py`：仅标准库实现的 CLI（约 15 KB）
- `references/resources.md`：各工具的参数与注意事项
- `examples/`：6 个工具示例
- 后端：`https://www.romancohort.cloud/api/v1`，`Authorization: Bearer rc_lab_...`
- 异步契约：
  - `POST /runs` `{tool, params}` → 200/202 `{run_id}`；409 表示已有任务，复用
  - `GET /runs/{id}` 轮询；`GET /runs/{id}/events` SSE
  - `GET /runs/{id}/files?hash=1` → manifest + sha256，CLI 校验后落盘
  - 429 = 当日配额用尽；401/403 = key/scope 问题；5xx = 后端不可用
- 输出纪律：保留后端字段名（`pred_norm`、`log2FoldChange`、`padj`、`HR`），
  不把 docking 分数或生存 p 值说成因果

### 现有工具（14 个）

| 与 RNA 关系任务相关 | 工具 |
|---|---|
| 直接相关 | `vienna`（折叠/ensemble）、`rhofold`（3D）、`ncbp`（非经典碱基对）、`gatsite`（结合位点） |
| 间接 | `gemorna`（mRNA/UTR 设计）、`rnadvisor`（3D 打分） |
| 与本项目主线无关 | `rdock`、`haddock3`、`signald`、`bioease`、`esm2`、`vaxseer`、`physicell`、`lnpdb` |

平台通用没问题，但 **RNA 子集要显式划出来**，否则 agent 会调到不相关的工具。

### 1.1 一体化 RNA 工具地图（扩展目标，2026-11-03）

按研究流程分五段。现状一列：**已有** = lab 目录里在线；**新增** = 要补的。

| 阶段 | 工具 | 现状 | 说明 |
|---|---|---|---|
| **实体** | RNAcentral 识别 / ID 映射 | 新增 | 序列 -> URS / miRBase / Ensembl |
| | Rfam / HMMER 家族 | 新增 | 家族分类归工具，不归模型 |
| | miRBase 名称归一 | 新增 | `miR-21` / `hsa-miR-21-5p` / `MIR21` |
| | HGNC 基因归一 | 新增 | 符号 + 同义词 + 旧名 |
| **结构** | `vienna` | 已有 | 二级结构 / ensemble |
| | `rhofold` | 已有 | 3D 预测 |
| | `rnadvisor` | 已有 | 3D 打分 |
| | `ncbp` | 已有 | 非经典碱基对 |
| | 结构比对（USalign / TM-score） | 新增 | 预测结构与参考结构比较 |
| **相互作用** | `vienna` duplex / cofold | 已有 | RNA-RNA 杂交 |
| | `gatsite` | 已有 | 结合位点概率（输入是 PDB） |
| | `rna_relation` / `rna_scan` | 新增 | RNAQwen，私有工具 |
| | seed match / 靶点扫描 | 新增 | 可解释的粗筛，供 rna_scan 用 |
| **功能 / 设计** | `gemorna` | 已有 | CDS / UTR 设计 |
| | `esm2` | 已有 | 蛋白侧 embedding |
| **药物 / 临床** | `rdock` / `haddock3` / `signald` / `lnpdb` | 已有 | 非 RNA 主线 |
| | `bioease` / `vaxseer` / `physicell` | 已有 | 非 RNA 主线 |
| **文献** | `paper_search` | 已有 | 发现 / 精确检索 |
| | `literature_search`（RAG） | 新增 | 证据层 |

**判断**：现有目录偏「结构 + 药物设计」，而一体化 RNA 研究最缺的恰好是
**实体层**（序列与命名归一）和**文献证据层**——这两块是我们必须自己补的。
相互作用层有 `vienna duplex` 和 `gatsite`，但缺一个直接回答「这两条序列
有没有关系」的工具，也就是 RNAQwen 的位置。

**优先级**：

1. P0：把已有 14 个包成 MCP（通用层 + RNA 子集类型化）
2. P1：实体层四个工具（RNAcentral / Rfam / miRBase / HGNC）
3. P2：文献层（paper_search 内部通道 + RAG）
4. P3：`rna_relation` / `rna_scan`（等模型训出）
5. P4：结构比对、靶点扫描等补全

**范围说明**：`references/resources.md` 末尾写着 TorusFold 与 RNAQwen
「remain outside this skill's scope」。本 harness 要**主动把 RNAQwen 纳进来**，
这句话需要随工具目录一起更新。

### 1.2 待补工具清单（2026-11-03）

按「地基 -> 证据 -> 相互作用 -> 补全」排，P 越小人越先做。

**P1 实体层（地基，缺了后面全是歧义）**

| 工具 | 输入 -> 输出 | 数据源 |
|---|---|---|
| `rna_identify` | 序列 -> URS / miRBase / 家族 / 物种 / xref | RNAcentral（本地） |
| `rna_fetch` | accession -> 序列 + 注释 | RNAcentral / Ensembl / NCBI |
| `rfam_scan` | 序列 -> 家族命中 + score/E-value | Rfam + HMMER |
| `mirna_resolve` | 名称 -> mature id / seed / 物种 / 序列 | miRBase |
| `gene_resolve` | 符号/旧名 -> HGNC id / Ensembl / Entrez | HGNC |
| `id_map` | 一批 id + from/to -> 映射表 | Ensembl / NCBI / UniProt |
| `taxonomy_resolve` | 物种名 -> taxid + 谱系 | NCBI Taxonomy |

**P1.5 序列操作 + 数据获取层**

| 工具 | 输入 -> 输出 |
|---|---|
| `seq_align` | 多条序列 -> MSA（MAFFT / MMseqs2） |
| `blast_search` | 序列 -> 同源命中（先对 RNAcentral/Rfam 子集，不上 nt 全库） |
| `motif_scan` | 序列 + motif/PWM -> 位点（FIMO；miRNA seed match） |
| `orf_find` | 序列 -> ORF / 翻译 |
| `dataset_search` | 源 + 查询 -> 数据集列表（GEO / SRA / GDC / GTEx / ClinVar / Zenodo…） |
| `dataset_fetch` | 源 + id -> 文件 + sha256 + 许可证 + 版本清单 |

`dataset_fetch` 的设计约束（医学数据库散落，这是最容易出事的一层）：

- **白名单源**，不做任意 URL 抓取（SSRF 和许可证风险）
- 每个源一个适配器：GEO、SRA/ENA、ArrayExpress/BioStudies、GDC/TCGA、
  GTEx、ClinVar、Ensembl、UCSC、NCBI Datasets、Zenodo/Figshare
- 下载必须**可续传 + sha256 校验**，并记录 `source_url / retrieved_at / license / version`
- 受控访问（dbGaP 等）**直接拒绝**，不代持凭据
- 磁盘配额与清理策略要显式，不能把服务器塞满
- 下载清单进证据账本的 `tool` 类条目（`tool + params + result hash`）

**P2 证据层**

| 工具 | 输入 -> 输出 |
|---|---|
| `literature_search` | 实体对 -> 段落级证据（RAG） |
| `paper_search` | 关键词/一句话 -> 论文（已有） |
| `evidence_lookup` | 实体对 -> 结构化互作记录（NPInter v4 / ENCORI / RAID） |
| `citation_verify` | pmid + span -> 原文是否包含该句（防幻觉引用） |

**P3 相互作用层**

| 工具 | 输入 -> 输出 |
|---|---|
| `rna_relation` | miRNA + 50 nt 窗口 -> 分数 / abstain（RNAQwen） |
| `rna_scan` | miRNA + 整条转录本 -> 最优窗口 + 聚合分数 |
| `rna_duplex` | 两条序列 -> 杂交自由能（vienna duplex 的类型化封装） |
| `targetscan_lookup` | miRNA + 基因 -> 预测位点（TargetScan 静态表） |

**P4 补全（按需）**

| 工具 | 用途 |
|---|---|
| `struct_align` | USalign / TM-score，预测与参考结构比较 |
| `modification_predict` | m6A / m5C 位点预测 |
| `enrichment` | GO / KEGG 富集 |
| `geo_query` | GEO 数据集检索与元数据 |
| `sirna_aso_design` | siRNA / ASO 设计 |
| `primer_design` | Primer3 引物设计 |

**明确不做的**：蛋白结构预测（已有 `rhofold` 之外的 AlphaFold 类）、单细胞全流程、
临床统计（`bioease` 已覆盖 TCGA，其他队列不扩）。

### 1.3 工具归属：lab API vs 本地（2026-11-03 确认扩展范围）

现有 14 个（用户列了 13 个，漏了 `bioease`）：

`RhoFold+`、`PhysiCell`、`ViennaRNA`、`LNPDB`、`GEMORNA`、`RNAdvisor`、
`rDock`、`HADDOCK3`、`GATRsite`、`SIGNALD`、`NC-Bench/NCfold`、`ESM-2`、
`VaxSeer`、`BioEase`

扩展范围已确认。但不是所有新工具都该走 lab API——按「数据在哪、算力在哪」分：

| 归属 | 工具 | 理由 |
|---|---|---|
| **lab API（现有）** | 上面 14 个 | 已经在跑，GPU/专业后端，不要重复造 |
| **本地：实体层** | `rna_identify`、`rna_fetch`、`rfam_scan`、`mirna_resolve`、`gene_resolve`、`id_map`、`taxonomy_resolve` | 数据在本地，延迟要低，查询量最大 |
| **本地：序列操作** | `seq_align`、`blast_search`、`motif_scan`、`orf_find` | CPU 即可，没必要绕公网 |
| **本地：证据层** | `literature_search`、`evidence_lookup`、`citation_verify` | 依赖本地索引/数据库 |
| **本地：模型** | `rna_relation`、`rna_scan` | GPU 服务在本机 |
| **本地：读表** | `targetscan_lookup` | 静态表，SQL 查询 |
| **本地：补全** | `struct_align`、`enrichment`、`geo_query` 等 | 按需，CPU 为主 |

结论：MCP 侧至少分两个 server——`lab_mcp`（转发 14 个）和 `local_mcp`
（实体/序列/证据/模型）。对外工具名统一，内核不需要知道背后在哪跑。

### 1.4 任务清单（2026-11-03）

- [ ] **P0 骨架**：gateway（TS）+ OpenHarness 内核 + `lab_mcp`（通用 3 个 + RNA 子集 6 个）
- [ ] **P1 实体层**：数据模型 + 7 个工具 + RNAcentral/Rfam/miRBase/HGNC 入库
- [ ] **P1.5 序列操作 + 数据获取**：`seq_align` / `blast_search` / `motif_scan` /
      `orf_find` / `dataset_search` / `dataset_fetch`
- [ ] **P2 证据层**：RAG 索引 + `literature_search` / `evidence_lookup` /
      `citation_verify`
- [ ] **P3 相互作用层**：`rna_relation` / `rna_scan` / `rna_duplex` /
      `targetscan_lookup`
- [ ] **P4 补全**：`struct_align` / `modification_predict` / `enrichment` /
      `geo_query` / `sirna_aso_design` / `primer_design`
- [ ] **评测**：实体归一准确率、检索召回、引用可验证率、工具端到端成功率
- [ ] **文档**：工具目录、各源许可证、覆盖范围声明（哪些物种/哪些库没覆盖）

### 1.5 答案呈现契约（2026-11-03）

chat 里只有纯文本，所以三种结论必须用**不同的措辞**，不能混：

| 情形 | 措辞模板 |
|---|---|
| 有实验证据 | 「PMID 12345 的 Results 报告了 hsa-miR-21 靶向 PTEN（CLASH）；本库快照 2026-11-03。」 |
| 只有模型预测 | 「模型预测存在靶向关系（分数 0.87，已校准）；**未在检索到的文献中找到实验证据**。」 |
| 未检索到 | 「在收录的 N 篇 miRNA-mRNA 相关文献中未检索到该关系（覆盖范围：…；快照 2026-11-03）。」 |

规则：

- **「未检索到」绝不表述为「不存在」**，必须带覆盖范围与快照
- 预测必须显式标注，不能与文献证据并列呈现
- 证据与预测冲突时**并列展示**，不擅自裁决
- 引用必须是可点击/可核对的（pmid + 段落），不允许只给期刊名
- 分数显示为校准后的概率，并同时给出阈值与 abstain 带

### 缺口

| 需要 | 现状 |
|---|---|
| 家族/类型查询（Rfam/HMMER） | **没有**——而按分层原则它属于工具 |
| 文献检索与证据片段 | **完全没有** |
| 关系判断（RNAQwen） | **没有接口**，模型也还没训出 AUROC |


### 1.6 安全 / 运行 / 呈现 三节的位置

上面 1.5 是**答案呈现**（用户看到什么）。系统侧的另外两块在 §2：

- **§2.9 安全与内容边界**：untrusted 内容、参数来源隔离、下载白名单、隐私
- **§2.10 运行质量**：单卡调度与优先级、确定性、成本配额、缓存分层、
  回归回放、工具描述质量（`use_when` / `not_for`）

## 2. 架构（本轮已定）

```
agent（BioClaw / OpenHarness）
  │
  ├─ compute 工具   vienna / rhofold / ncbp / gatsite / rfam
  ├─ retrieve 工具  literature_search（RAG）
  └─ infer 工具     rna_relation / rna_scan  ──► RNAQwen 适配器（自定义服务）
  │
  └─ 融合：文献证据优先，模型只在没有证据时提供先验；中间分数 → abstain
```

三条已定的原则：

- **D1 模型不做中心**，它是一次工具调用。
- **D2 工具返回分数而不是硬判**，并带 `uncertain` 带。
- **D3 文献优先于模型**：一条明确的 CLASH/eCLIP 证据不能被 0.9 的模型分数推翻。

### 2.0 内核 / 壳（2026-11-02 定）

**多 agent 是内核，BioClaw 是壳。**

BioClaw 的价值在渠道：WhatsApp、飞书、企业微信、Slack、Discord、微信、QQ、本地 web
（`src/channels/` 里 8 个适配器）。科研用户可以在地铁上发一条消息，不用打开 IDE。

```
渠道（BioClaw：飞书 / 企微 / Slack / WhatsApp …）
   │  消息 + 附件
   ▼
壳：会话管理、鉴权、文件收发、进度提示
   │  harness.ask(task, session)
   ▼
内核：OpenHarness 多 agent（Orchestrator / Retriever / Analyst / Verifier）
   │  MCP
   ▼
工具：lab.py 的 14 个工具 + literature_search + rna_relation / rna_scan
   │
   ▼
GPU：RNAQwen 自定义推理服务（inputs_embeds）
```

边界规则：

- **科研工具只注册在内核**。壳那一侧的 agent 不挂 `vienna` / `rna_relation`，
  否则会出现两个地方都在做科学判断
- 壳只做三件事：会话、文件、进度。它调内核就一个工具 `harness.ask`
- 会话映射：`chat_id → kernel session_id`，内核侧状态可查、可恢复
- 产物走共享卷：内核写文件 → 壳用 `send_file` 发回
- 渠道 UX 要求「秒级应答」：先 ack，再进度（内核有背景任务/SSE），最后出结果

**BioClaw 只取渠道层**（2026-11-02 读完代码后定）。

它的 agent loop 是 Claude Agent SDK 的 loop，和 OpenHarness 重复；直接整包接入会出现
两个大脑。但它的渠道层耦合很干净，实测：

- 每个 channel 只 import `config` / `logger` / `types`（WhatsApp 多一个 `workspace`）
- 统一接口：`Channel{connect, sendMessage, isConnected, ownsJid, disconnect,
  setTyping?, sendImage?}`
- 唯一入口是回调 `OnInboundMessage(chatJid, NewMessage)`，注册表是工厂模式
- 依赖按渠道拆开：飞书 `@larksuiteoapi`、Slack `@slack/bolt`、企微
  `@wecom/aibot-node-sdk`、WhatsApp `baileys`、Discord `discord.js`、微信
  `weixin-agent-sdk`

所以取：`src/channels/` + `src/types.ts` + `src/logger.ts` + 最小 config。
丢：`container/agent-runner`（SDK loop）、`agent-admin`、skills 路由、定时任务、
local-web UI（除非需要）、它的 group/DB 模型。

新的三层：

```
channels（TS，BioClaw 的适配器，原样拿来）
   │  onMessage(chatJid, NewMessage)
gateway（我们自己的 ~200 行：session 映射、ack/进度、产物上传下载）
   │  HTTP：submit + poll / SSE
kernel（OpenHarness：会话、记忆、多 agent、MCP）
```

结果是**一个 loop**：壳不再思考，对话状态由内核持有。代价是要自己维护 gateway
和渠道 SDK 的版本；换来的是没有双 loop、没有两套权限、没有重复规划。

**安全边界**：只取渠道层之后，**没有 BioClaw 的 Docker 隔离了**。边界变成两点：

- gateway 持有渠道凭据（飞书/Slack/企微 token），只能收消息、发消息，不执行代码
- 内核的工具执行走 OpenHarness 自己的 `sandbox/`，工作区只读 + 网络白名单

### 2.1 多 agent 拓扑（后期选项，不是第一步）

OpenHarness 原生支持 subagent（`agent` 工具 + 后台任务），所以这套拓扑是可行的：

```
Orchestrator（主 agent，拥有最终答案和证据账本）
 ├─ Retriever  文献检索 → {snippet, pmid, span}
 ├─ Analyst    工具调用 / rna_scan → 结构化结果
 └─ Verifier   独立核对：每条结论对应哪个证据；模型输出只能标「预测」
```

三条约束：

- **agent 之间传结构化证据，不传自由文本**——文本转两圈就会长出原文没有的东西
- **Verifier 必须能读原始证据**，不能只读其他 agent 的总结
- **模型是工具，不是参与辩论的 agent**；AUROC 未知的分类器没有资格「讨论」

但**不建议一开始就拆**。多 agent 的代价是真实的：错误跨 agent 传播、成本翻倍、
延迟叠加。先单 agent + 一组结构化工具跑真实任务，只有当某类子任务反复把上下文
撑爆、或需要不同权限/模型时，才把它拆出去。

### 2.2 全链路架构（草案，2026-11-03）

每层只依赖下一层的接口，跨层调用一律禁止。

```
┌──────────────────────────────────────────────────────────┐
│ 交互层  BioClaw 渠道：WhatsApp / 飞书 / 企微 / Slack / …  │  只收发
├──────────────────────────────────────────────────────────┤
│ 网关    自研：session 映射、ack/进度、产物、用户限流      │  不规划
├──────────────────────────────────────────────────────────┤
│ 内核    OpenHarness：规划、多步执行、证据账本、subagent   │  不直接读库
├──────────────────────────────────────────────────────────┤
│ 工具    MCP：compute / retrieve / infer                   │  不做多步推理
├──────────────────────────────────────────────────────────┤
│ 服务    RNAQwen 推理 · RAG 索引 · 实体库 · paper-search   │  不做对话
└──────────────────────────────────────────────────────────┘
```

| 层 | 组件 | 职责 | 明确不做 |
|---|---|---|---|
| 交互 | BioClaw 渠道适配器 | 收消息、发消息、附件 | 不注册科研工具、不做判断 |
| 网关 | 自研 gateway | 会话映射、进度、产物、限流 | 不规划、不调科研工具 |
| 内核 | OpenHarness | 规划、工具选择、证据账本 | 不直接读数据库或文件系统 |
| 工具 | MCP servers | 执行单一动作，返回结构化结果 | 不做多步推理、不拼答案 |
| 服务 | 推理 / RAG / 实体 / 检索 | 计算与数据 | 无状态或按请求无状态 |

**统一返回信封**（所有工具一致，内核不用记每个工具的字段名）：

```json
{
  "ok": true,
  "data": {},
  "error": null,
  "provenance": {"source": "...", "version": "...", "snapshot": "2026-11-03",
                 "latency_ms": 0},
  "evidence": []
}
```

**证据账本**（内核持有，落库持久化）。每条结论只能是三者之一：

1. 文献 span（`doc_id + char_span + snapshot`）
2. 工具输出（`tool + params + result hash`）
3. 模型预测（显式标 `prediction`，不允许混进 1/2）

没有第四类。最终答案必须带 provenance，缺证据的说法要么删掉，要么标成预测。

**四条架构原则**

- **执行与规划分离**：工具只做一件事，规划只在核心里
- **证据优先**：有实验证据时，模型分数只做一致性检查（D3）
- **失败关闭**：OOD / abstain / 未解析实体都要显式返回，不猜
- **版本可追溯**：数据快照、模型版本、工具版本随答案一起走

**阶段划分**

| 阶段 | 内容 | 依赖 |
|---|---|---|
| P0 骨架 | gateway + OpenHarness + lab 工具 MCP + paper-search 内部通道 | 无 |
| P1 RAG | 实体表 + 窄域语料 + 混合检索 + 证据对象 | 标注集 |
| P2 模型 | `rna_relation` 接进 MCP | 训练出 AUROC |
| P3 多 agent | 拆 Retriever / Analyst / Verifier | P1 稳定后 |
| P4 规模 | 增量更新、扩语料、跨物种 | P1–P3 |

### 2.3 部署拓扑（2026-11-03 定）

**公网 VPS 只做转发，所有计算在 GPU 主机上，各角色分进程。**

```
公网 VPS（轻量）
  nginx / TLS / webhook 入口 / 鉴权 / 限流 / 反向代理
  ✗ 不跑内核、不跑 RAG、不跑模型、不跑 paper-search 的抓取
        │  隧道（frp / Tailscale / Cloudflare Tunnel）
        ▼
GPU 主机（这台机器）
  ├─ gateway            只暴露一个 HTTP 端口
  ├─ OpenHarness 内核    规划、多步执行、证据账本
  ├─ MCP 工具服务        compute / retrieve / infer
  ├─ paper-search       同机 import，走内部通道
  ├─ RAG                PostgreSQL + pgvector
  ├─ 实体库             RNAcentral + HGNC
  └─ RNAQwen 推理服务   自定义 FastAPI（inputs_embeds）
```

规则：

- **只暴露 gateway 的端口**；RAG、推理服务、paper-search 全部绑定 `127.0.0.1`
- 每个角色独立进程（或容器），不合成单体；一个崩了不带走其他
- VPS 上不存任何数据；所有状态在 GPU 主机
- 隧道只转发 gateway 的 HTTP，不做端口级全通

**硬件（2026-11-03 确认）**：GPU 主机是 AMD AI Max+ 395，128 GB 统一内存，
最高可分配约 98 GB 给显存。35B int4（约 18 GB）+ Qwen3-Embedding + 抽取用 LLM
可以共存，容量不是瓶颈。之前的 `0xC0000005` 是驱动问题，不是能力问题；
剩下要验证的只是 ROCm 下 bitsandbytes int4 / FLA 内核能否正常跑，属于实现问题。

### 2.4 网关 ↔ 内核的协议（2026-11-03）

OpenHarness **没有自带 HTTP 服务**（`src/openharness/api` 是 LLM provider 客户端，
不是任务 API）。它给外部接入留了三条路：

| 方式 | 命令 | 优点 | 缺点 |
|---|---|---|---|
| 一次性 | `oh -p "<task>"` | 最简单，进程隔离 | 每条消息冷启动；进度靠解析 stdout |
| stdin worker | `oh --task-worker` | 长驻、可多轮、流式事件 | 隐藏参数、协议未文档化，需要跟版本 |
| 结构化后端 | `oh --backend-only` | 有结构化事件流（给 React 终端用的） | 同样未文档化 |

**建议：P0 用一次性 `-p`，跑通；P1 再换 stdin worker。** 网关只认一个
`submit(task, session) → events` 的抽象，换实现不影响上层。

网关语言选 **TypeScript**：8 个渠道适配器都是 TS，重写等于白扔。

证据账本和 RAG 共用同一个 PostgreSQL，但用**独立 schema**（`ledger.*`）：
备份和审计在一起，权限和清理由 schema 隔开；内核只能通过 `ledger.append` 工具写，
不能直接连库。

### 2.5 lab 工具接入（14 个，全部在线）

这些工具已经在跑，P0 不需要占位工具，直接包成 MCP 即可。

**包法：两层。**

1. **通用层**（3 个工具，覆盖全部 14 个）
   - `lab_run(tool, params, name?)` → `{run_id, status}`
   - `lab_status(run_id)` → 状态 / 日志尾部 / 结果
   - `lab_files(run_id, out?)` → 文件清单（sha256 校验后落盘）
2. **类型化层**（只对 RNA 子集）
   - `vienna` / `rhofold` / `ncbp` / `gatsite` / `gemorna` / `rnadvisor`
   - 每个工具的 schema 从 `lab tools --json` 的 manifest 生成，描述写清楚用途

其余工具（`rdock` / `haddock3` / `signald` / `bioease` / `esm2` / `vaxseer` /
`physicell` / `lnpdb`）**默认不进类型化列表**，但可以通过 `lab_run` 调用——
工具列表小一点，agent 不容易选错。

**异步契约**：工具可能跑几分钟。`lab_run` 立即返回 `run_id`，agent 用
`lab_status` 轮询；不要做成阻塞调用（OpenHarness 的普通工具调用没有统一超时）。

**其他约束**：

- API key 只存在 MCP server 进程里，不交给 agent，也不出现在工具参数里
- 错误映射：409 复用 run、429 配额、401/403 鉴权、5xx 下游故障，
  都要转成结构化 `error`，不要吞掉
- 参数先按 manifest 校验，不要发明字段
- 文件下载后校验 sha256，失败要报出来

P0 的范围因此调整为：**消息 → 网关 → 内核 → `lab_run(vienna)` → 回复**，
14 个工具全部可用（通用层），类型化只做 RNA 子集。

### 2.6 架构总图（2026-11-03）

**图 1：部署与组件（总图）**

```
                    用户（飞书 / 企微 / Slack / WhatsApp / 微信 / QQ）
                                      |
                                      v
+--------------------- 公网 VPS（轻量，只转发） ---------------------+
|  nginx / TLS  |  webhook  |  鉴权  |  用户限流  |  反向代理         |
+--------------------------------+------------------------------------+
                                 | 隧道（只开 gateway 一个端口）
                                 v
+-------------------- GPU 主机（AI Max+ 395 / 128 GB） ---------------+
|                                                                     |
|  +---------------------- gateway (TS) ---------------------------+  |
|  | session 映射 | ack/进度 | 产物收发 | 用户限流                 |  |
|  +-----------------------------+----------------------------------+  |
|                                | submit(task, session) -> events     |
|  +-----------------------------v----------------------------------+  |
|  | kernel: OpenHarness                                            |  |
|  | 规划 | 多步执行 | subagent（后期） | 证据账本                  |  |
|  |   账本只收三类: literature(span) | tool(hash) | prediction     |  |
|  +-----------------------------+----------------------------------+  |
|                                | MCP (req/resp) + event bus          |
|  +-----------------------------v----------------------------------+  |
|  | tools（两个 MCP server）                                       |  |
|  |  lab_mcp    lab_run / lab_status / lab_files + RNA 子集 6 个  |  |
|  |  local_mcp  entity 7 | seq/data 6 | evidence 4 | model 5      |  |
|  +-----+---------------+---------------+---------------+-----------+  |
|        |               |               |               |             |
|  +-----v------+ +------v------+ +------v------+ +------v---------+  |
|  | RNAQwen    | | RAG 索引    | | 实体库      | | 现有服务       |  |
|  | 家族       | | PostgreSQL  | | RNAcentral  | | lab API 14     |  |
|  | targeting  | | + pgvector  | | Rfam        | | paper-search   |  |
|  | region/... | | passages    | | miRBase     | | （内部通道）   |  |
|  | GPU 服务   | | relations   | | HGNC        | |                |  |
|  +------------+ +-------------+ +-------------+ +----------------+  |
|                                                                     |
|  离线管线：Europe PMC 增量 -> 实体解析 -> 分块/embedding -> 关系抽取 |
|  数据获取：dataset_search / dataset_fetch（白名单源，可续传+校验）  |
+---------------------------------------------------------------------+
```

**图 2：一次请求的流向**

```
用户：hsa-miR-21 靶向 PTEN 吗？
  │
  ▼
gateway ──submit──> kernel
                     │
                     ├─ 1. rna_identify(sequence)  -> miRBase id / URS
                     │
                     ├─ 2. literature_search(miRNA, gene)
                     │        └─> RAG：实体解析 -> relations 查询 -> 段落检索
                     │             命中 -> {pmid, span, method} 写入证据账本
                     │
                     ├─ 3. 没有文献才调 rna_relation
                     │        └─> {score, answer, in_domain} 标记为 prediction
                     │
                     └─ 4. 组织答案：证据优先；预测显式标注；冲突照实报告
  │
  ▼
gateway ──> 回复（结论 + 引用 + 置信度 + 快照）
```

**图 3：证据账本的三类条目**

```
literature  {doc_id, pmid, section, char_span, snapshot}
tool        {tool, params, result_hash, version}
prediction  {model, model_version, score, threshold}
            ^ 只能出现这三类；最终答案的每条结论都要能指回其中一条
```

### 2.7 事件驱动（2026-11-03 定）

工具调用、进度、证据、产物全部以**事件**表达，组件之间通过事件总线通信。

**为什么适合这个系统**

- 工具本来就异步（`/runs` 能跑几分钟），轮询是权宜之计
- 同一个事件有多个消费者：gateway 要进度、账本要证据、verifier 要草稿、
  监控要指标——pub/sub 比每处轮询干净
- 事件本身就是审计日志，账本可以重放重建

**事件信封**

```json
{
  "event_id": "uuid",            // 幂等键
  "type": "tool.completed",
  "ts": "2026-11-03T12:00:00Z",
  "session_id": "...",           // 分区键，保证同会话有序
  "task_id": "...",
  "tool_call_id": "...",         // 请求/结果关联
  "source": "lab_mcp|kernel|gateway|verifier",
  "schema_version": 1,
  "payload": {}
}
```

**事件类型（第一批）**

```
message.received      message.sent
task.created          task.completed / task.failed
tool.requested        tool.started
tool.progress         tool.completed / tool.failed
evidence.added        answer.draft        answer.verified
artifact.created
```

**语义**

- **at-least-once + 幂等消费者**（用 `event_id` 去重表），不要追求 exactly-once
- **同会话有序**：按 `session_id` 分区，跨会话不需要全局顺序
- **schema 带版本**，消费者遇到不认识的版本要显式失败，不静默丢字段
- **持久化**：总线保留 7–30 天供重放；账本事件永久保存

**总线选型**

| 选项 | 适用 | 备注 |
|---|---|---|
| 进程内 asyncio.Queue | P0 | 零依赖，不能跨进程 |
| Redis Streams | P1 | 轻量，若已有 Redis 最省 |
| NATS JetStream | P1/P2 | 单二进制，持久化+重放好，适合这个规模 |
| RabbitMQ / Kafka | 过度 | 运维成本高，当前不需要 |

**与 MCP 的关系**

MCP 仍是**工具接口**（schema、调用约定），事件是**传输层**。不要让两者产生两套
真相：

- 内核侧：MCP 工具 `tool_call` 发布 `tool.requested`，然后等待同 `tool_call_id`
  的 `tool.completed`（带超时）
- 工具的 MCP 实现由总线上的 worker 承载；worker 收到请求→执行→发结果事件
- 长任务天然变成 `started/progress/completed` 三个事件，不再需要 agent 轮询

**分阶段**

1. P0：进程内事件队列 + 现有 `/runs`，先把事件类型和信封定下来
2. P1：Redis/NATS 落地，gateway 订阅 session 事件做进度
3. P2：账本与 verifier 作为独立消费者；事件重放重建审计

**风险**

- 重复消费（必须幂等）
- 事件顺序（只在会话内保证）
- schema 演进（版本 + 显式失败）
- 调试变难（trace_id 串起来，所有事件进同一日志）

### 2.7.1 长程任务的稳定性（2026-11-03）

事件驱动最容易在「跑十分钟到几小时」的任务上翻车。核心原则：
**状态在数据库里，事件只是通知；不要用总线当状态机。**

**任务表（Postgres）**

```
tasks(
  task_id, session_id, tool, params_hash,      -- 幂等：同 (tool, params_hash) 不重复提交
  state,            -- queued | running | waiting_external | done | failed | cancelled
  attempt, max_attempts,
  lease_until,      -- 租约：worker 心跳续租，崩了自动过期
  external_run_id,  -- lab API 的 run_id，重试时 attach（409）而不是重提
  result_ref,       -- 结果落盘后的引用，不把大结果塞进事件
  error, created_at, updated_at
)
```

**四条机制**

1. **写前不 ack**：结果先落盘（文件/对象存储）+ 状态入库，最后才 ack 事件。
   顺序反了就会丢结果。
2. **outbox + relay**：worker 在同一个事务里写 `tasks` 状态和 `outbox` 事件，
   由 relay 发布；发布失败可以重试，不会出现「状态变了但事件没发」。
3. **租约 + 心跳**：worker 处理中定期续租；进程崩了租约过期，调度器重新派发。
   重试幂等由 `params_hash` + `external_run_id` 保证。
4. **死信队列**：超过 `max_attempts` 进 DLQ 并告警，不要无限重试打爆上游。

**按任务时长分级，不要一刀切**

| 时长 | 例子 | 机制 |
|---|---|---|
| < 30 s | `vienna`、实体查询、`rna_relation` | 同步 MCP，直接返回 |
| 分钟级 | `rhofold`、`gatsite`、`rna_scan` | `tool.requested` -> held -> completed；内核等待带超时 |
| 小时级 / 批处理 | 文献增量入库、大规模 `dataset_fetch` | **不进 agent 循环**，独立作业队列 + 断点续跑，完成后发通知 |

**崩溃恢复**：服务启动时扫描 `state in (running, waiting_external)` 且租约过期的任务，
重新入队；内核会话状态也要持久化，否则工具跑完了 agent 却忘了自己在等什么。

**可观测性**（没有这些，长任务出问题只能靠猜）：

- 每个任务一条 `trace_id`，事件、DB、日志共用
- 指标：队列深度、任务时长的 p50/p95、重试率、DLQ 数量、外部等待时长
- 每个任务的事件时间线可直接查

**验收**：必须做故障注入——跑一半杀 worker、断总线、外部 API 超时，
任务要么完成要么明确失败，不能卡在 `running` 不动。

### 2.7.2 任务表 DDL 与状态机（2026-11-03）

**DDL（PostgreSQL）**

```sql
CREATE SCHEMA IF NOT EXISTS jobs;

CREATE TYPE jobs.task_state AS ENUM (
  'queued', 'running', 'waiting_external', 'done', 'failed', 'cancelled'
);

CREATE TABLE jobs.tasks (
  task_id         uuid PRIMARY KEY,
  idempotency_key text NOT NULL,          -- 调用方的 tool_call_id；重试复用
  session_id      text NOT NULL,
  trace_id        text NOT NULL,
  tool            text NOT NULL,
  params          jsonb NOT NULL,
  params_hash     text NOT NULL,          -- sha256(canonical json)，用于审计/去重
  state           jobs.task_state NOT NULL DEFAULT 'queued',
  attempt         int NOT NULL DEFAULT 0,
  max_attempts    int NOT NULL DEFAULT 3,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  lease_until     timestamptz,
  worker_id       text,
  external_run_id text,                   -- lab API 的 run_id
  result_ref      text,                   -- 结果落盘后的路径/对象 key
  error_code      text,
  error_message   text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (idempotency_key)
);

CREATE INDEX tasks_ready_idx
  ON jobs.tasks (next_attempt_at, created_at) WHERE state = 'queued';
CREATE INDEX tasks_lease_idx
  ON jobs.tasks (lease_until)
  WHERE state IN ('running', 'waiting_external');
CREATE INDEX tasks_session_idx ON jobs.tasks (session_id, created_at DESC);

-- outbox：状态变更与事件在同一个事务里写，relay 负责发布
CREATE TABLE jobs.outbox (
  seq          bigserial PRIMARY KEY,
  event_id     uuid NOT NULL UNIQUE,
  topic        text NOT NULL,
  payload      jsonb NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz
);
CREATE INDEX outbox_pending_idx ON jobs.outbox (seq) WHERE published_at IS NULL;

-- inbox：消费者幂等去重
CREATE TABLE jobs.inbox (
  consumer    text NOT NULL,
  event_id    uuid NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (consumer, event_id)
);
```

**状态机**

```
                      create
                        |
                        v
                  +-----------+
          +------>|  queued   |<--------------------+
          |       +-----------+                     |
          |             | claim (lease)             | lease expired
          |             v                           | (recovery)
          |       +-----------+                     |
          |       |  running  |---------------------+
          |       +-----------+                     |
          |        |        |                       |
          |  submit|        | 外呼失败              |
          |        v        v                       |
          |  +------------------+                   |
          |  | waiting_external |-------------------+
          |  +------------------+
          |        |
          | result |  attempts 用完
          v        v
      +--------+ +--------+
      |  done  | | failed |
      +--------+ +--------+
           ^
           | cancel (queued/running/waiting_external)
      +-----------+
      | cancelled |
      +-----------+
```

**关键 SQL**

```sql
-- 领取任务：SKIP LOCKED 支持多 worker 并行
UPDATE jobs.tasks
SET state = 'running', worker_id = $1,
    lease_until = now() + interval '2 minutes',
    attempt = attempt + 1, updated_at = now()
WHERE task_id = (
  SELECT task_id FROM jobs.tasks
  WHERE state = 'queued' AND next_attempt_at <= now()
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *;

-- 心跳：返回 0 行 = 租约已丢，worker 必须停手
UPDATE jobs.tasks
SET lease_until = now() + interval '2 minutes'
WHERE task_id = $1 AND worker_id = $2 AND state = 'running';

-- 恢复：租约过期的任务重回队列（带退避）
UPDATE jobs.tasks
SET state = 'queued', worker_id = NULL, lease_until = NULL,
    next_attempt_at = now() + (interval '30 seconds' * attempt),
    updated_at = now()
WHERE state IN ('running', 'waiting_external')
  AND lease_until < now()
  AND attempt < max_attempts
RETURNING task_id, tool, attempt;
```

**不变量**

- 状态变更与 outbox 写入**必须在同一事务**；先写状态后发事件会丢通知
- ack 事件之前，`result_ref` 必须已经指向一个完整、可读的产物
- `waiting_external` 的任务重试时先 `GET /runs/{run_id}`，
  只有确认不存在才重新提交（防重复跑）
- `lease_until` 过期不代表任务失败，只代表「可以重新派发」；真正的失败由
  `attempt >= max_attempts` 决定

### 2.8 部署目标：昇腾推理卡（2026-11-03）

最终部署倾向昇腾 NPU，不是 CUDA。这件事**现在就要影响设计**，因为有三样东西
一旦写死就很难改：算子、量化、推理入口。

**先分清哪些能搬、哪些搬不动**

| 组件 | 可移植性 | 说明 |
|---|---|---|
| ERNIE-RNA + resampler（前端） | 高 | 纯 PyTorch 算子，768 维小模型，CPU 都能跑 |
| SOS-LoRA | 高 | `merge_sos_layers` 烘进底座后，推理端看不到 SOS |
| 35B 底座 bf16 | 高 | 标准 safetensors，昇腾可加载 |
| bitsandbytes int4 | **低** | CUDA 专用，昇腾上没有；训练用可以，**推理不能用** |
| grouped MoE 的 bmm 路径 | 低 | 训练期自研实现，推理不需要，但别让它进导出路径 |

结论：**训练和推理的模型表示要分开**。训练可以继续 int4 + SOS 增量；
导出时合并成 bf16 全量权重，再按昇腾的量化方案重新量化。

**导出契约（现在就定，后面不改）**

```
export/
  llm/                 # 合并 SOS 后的 bf16 safetensors + config
  frontend/            # ERNIE-RNA + rs_mi + rs_mr，独立小模型
  tokenizer/
  manifest.json        # 版本、训练步数、数据快照、导出时间、精度
```

前端和底座**必须能分别加载**——昇腾侧的推理服务大概率是「前端算软提示 →
底座吃 inputs_embeds」，两个进程/两种精度。

**关键约束：推理入口必须支持 `inputs_embeds`**

我们的输入不是文本，是 8 个软提示 embedding + 文本 embedding 拼起来的张量。
所以不能指望「把模型丢给任意 serving 引擎就能跑」。三条路，按风险从低到高：

1. **纯 PyTorch + torch_npu 自己写前向**（推荐兜底）
   我们只做一次前向、取一个位置的 logits，不需要 KV cache、不需要生成。
   工作量小、行为完全可控、不依赖引擎是否支持 prompt_embeds。
   代价：吞吐不如专用引擎，但我们的序列只有约 80 token。
2. **vLLM-Ascend 的 prompt_embeds**
   需要验证：是否支持 `inputs_embeds`、是否支持非生成式单次打分、
   版本要求。不满足就只能退回 1。
3. **MindIE 自定义模型**
   最重，只在 1、2 都不行时考虑。

**量化路径（已定：一定量化，不讨论「要不要」）**

- 训练：bitsandbytes NF4（CUDA），只为省显存
- 导出：bf16 全量（中间格式，便于任何量化工具消费）
- 昇腾推理：**必须量化**。用昇腾自己的工具链（msmodelslim / MindIE 量化流程），
  目标 W8A8；如果显存或吞吐不达标，再评估 W4A8
- **不要**把训练用的 int4 权重直接搬过去（NV4 是 CUDA 布局，昇腾无反量化路径）
- 量化不是导出后的一次性动作，而是**发布流程的一环**：每次换权重都要重量化并重测

**量化后的验收（必须做，不能省）**

- AUROC / AUPRC / ECE 在**昇腾上重测**，与 CUDA 参考值对比
- 分数漂移超过阈值就要**重新校准**（Platt/isotonic），阈值和 abstain 带跟着改
- 记录：量化方案、校准集、校准后阈值，一起写进 `manifest.json`

**现在就要做的四件事（便宜，晚做很贵）**

1. 训练脚本里把「导出」做成独立步骤，不依赖 bitsandbytes 的运行时
2. 前端与底座在代码里就分开建模，别写成一个大 `nn.Module`
3. 所有自定义算子（grouped MoE、batched dequant）标成 `training_only`，
   导出路径里一个都不许出现
4. 定一个**可移植性闸门**：每个里程碑跑一次「导出 + CPU 前向 + 与训练侧
   同输入同输出比对」，误差在容差内才算过

**待验证（没有昇腾卡之前只能列出来）**

- 昇腾卡的型号与显存（910B / 910C / Atlas 300I 等）——只影响量化档位与并发数，
  不影响架构：**一律量化部署**
- torch_npu 对 Qwen3.5-MoE 结构的支持程度（MoE 路由、线性注意力层）
- `inputs_embeds` 在 torch_npu 上的行为是否与 CUDA 一致
- 昇腾侧 bf16 与 fp16 的数值差异，是否影响校准后的分数
- 量化后 AUROC/ECE 的漂移幅度——**必须重测，不能沿用 CUDA 上的数字**

**风险**

- Qwen3.6 的线性注意力（30 层）依赖 FLA / causal-conv1d 的 CUDA 内核；
  昇腾上要么有等价实现，要么退回纯 PyTorch 版本（慢但正确）
- 没有昇腾硬件就无法提前验证，所以**兜底方案（纯 PyTorch 前向）必须能跑**，
  它同时也是 CPU 上的参考实现

### 2.9 安全与内容边界（2026-11-03）

**威胁模型：所有进入 agent 上下文的外部内容都是不可信的。**

文献摘要、用户上传的 PDB/序列、`dataset_fetch` 下来的文件、lab 工具返回的
日志——任何一条都可能包含针对 agent 的指令。

**规则**

1. **标注来源**：工具返回统一带 `trusted: false`；只有系统提示和工具 schema
   是可信的
2. **证据不是指令**：`evidence` 字段里的文本只能作为引用展示，
   **不允许触发动作**
3. **参数来源隔离**：高权限工具（`dataset_fetch` 写盘、`lab_run` 花 GPU、
   任何提交长任务的工具）的参数**只能来自用户原始输入或工具返回的结构化字段**，
   不能从 untrusted 文本里解析出来
4. **不做自动执行**：从文献里提取出的“建议运行 X”只能作为建议呈现，不能直接调用
5. **下载白名单**：`dataset_fetch` 只接受枚举的源，不接受任意 URL（SSRF）
6. **受控访问不代持凭据**：dbGaP / GDC 受控数据直接拒绝

**隐私**

- 用户上传的未发表序列**不外发**到第三方 API；只在本地工具链内流转
- 下载数据记录 `source / license / retrieved_at`，进证据账本
- 明确保留期与删除接口；用户可要求删除会话与产物
- 日志中不记录完整序列（可记录 hash 与长度）

### 2.10 运行质量（2026-11-03）

**单卡调度与优先级**

同一张卡上跑 35B 推理、embedding、可能的抽取 LLM，必须定优先级：

| 优先级 | 负载 |
|---|---|
| 高 | 交互请求（`rna_relation` 单次打分） |
| 中 | 交互触发的 `rna_scan`（限并发 1–2） |
| 低 | 后台批处理（文献 embedding、关系抽取） |

规则：交互请求排队超过 N 秒，暂停后台批处理；单请求超时与 OOM 兜底要有明确
行为（返回 `error_code=resource_exhausted`，不是静默重试）。

**确定性与可复现**

- 固定 temperature / seed / 工具版本 / 数据快照
- 每个答案计算指纹：`hash(prompt + tool versions + snapshots + result hashes)`
- 同一指纹重放应得到同一证据集；不一致要能定位到是哪个版本变了

**成本与配额**

- 计量：LLM token、GPU 秒、外部 API 调用，按会话/用户记录
- **贵操作先报价**：超过阈值（如预计 > 60 s 或 > 1 GB 下载）先返回预估并请求确认
- 按用户/组配额；超出返回结构化错误，不静默降级

**缓存分层**

| 层 | 键 | 失效条件 |
|---|---|---|
| ERNIE 编码 | 序列 hash | 编码器版本 |
| passage embedding | passage id + 模型版本 | 模型或分块策略变化 |
| 工具结果 | tool + params + 数据快照 | 快照更新 |
| LLM 响应 | prompt hash + 模型版本 | 模型或提示词变化 |

缓存必须带版本，否则换模型后仍在用旧结果。

**回归测试与回放**

- 黄金对话集（20–50 条典型问题）+ 期望证据
- 事件日志可回放，重建会话
- 工具 schema 变更、模型换版本、数据更新都必须跑回归
- 回归失败要能指出是「检索变了」还是「生成变了」

**工具描述质量**

agent 选错工具多半是描述没写清楚。每个工具必须有：

- `use_when`：什么情况下用
- `not_for`：什么情况下**不要**用（比前者更重要）
- 明确的输入约束与输出规模上限

## 3. `rna_relation` 工具契约（草案）

```json
// request
{
  "mirna": "UGAGGUAGUAGGUUGUAUAGUU",
  "target": "ACGT...",            // 50 nt 窗口
  "organism": "human",
  "window_kind": "3UTR"           // 可选：3UTR / CDS / 5UTR / 未知
}

// response
{
  "score": 0.87,                  // 标定后的概率，不是 logit
  "answer": "yes",                // yes / no / uncertain
  "threshold": 0.5,
  "abstain_band": [0.4, 0.6],
  "model": "rnaqwen-v3-rel",
  "model_version": "step8000+sha256:...",
  "in_domain": true,
  "notes": ["window=50nt", "T->U normalized"]
}
```

要求：

- **分数必须标定**（Platt / isotonic，独立校准集），否则 agent 按 0.87 做决定会出错
- **显式 OOD**：非人类、非 50 nt、字符集异常、超长 → `in_domain=false`
- **abstain 带**：落在中间区间返回 `uncertain`，让上层转文献
- 版本字段可追溯：checkpoint、训练数据快照、阈值
- 延迟目标：单次前向、不生成；具体数值待测
- 支持批量与缓存（同一条 miRNA 反复出现时复用 ERNIE 编码）

## 4. `rna_scan`：窗口策略（已定 A，2026-11-02）

模型在 **50 nt 窗口**上训练，真实调用方给的是整条 3'UTR / mRNA。切窗口的责任
不能推给 agent。

**决定：方案 A —— 工具内滑窗**，聚合后返回
`{best_window, best_score, n_windows, per_window_scores}`。
方案 B（调用方切窗口）被否：把生物学细节推给 agent，它一定会切错。

工程后果（必须提前设计，否则工具会慢到没法用）：

- 3'UTR 常有 1–2 kb；窗口 50 nt、步长 10 → **100–200 个窗口**
- 每个窗口一次 35B 前向，必须**批处理**；miRNA 侧的 ERNIE 编码只算一次并缓存
- 可选的粗筛：先用 seed match / 序列统计剪掉明显不可能的区域，再送模型。
  粗筛必须是**可解释的规则**，不能是另一个黑盒
- 目标：单次 `rna_scan` 在 10 秒量级；达不到就先缩小步长或加粗筛

待定参数：窗口 50 nt、步长（10 nt 起步）、聚合方式（max / top-k 均值）。
**这些都要实验，不能拍脑袋**：

1. 窗口相对真实位点偏移 0 / 10 / 20 / 25 nt 时 AUROC 掉多少
2. max 与 top-k 均值哪个更稳
3. 同一位点被多个窗口覆盖时，分数方差多大

## 5. 模型侧前提

- **训练状态**：截至 2026-11-02 尚未在 GPU 上跑过；AUROC 未知
- **指标要求**（组件级）：AUROC、AUPRC、ECE、分来源（CLASH / Manakov /
  Klimentova）、OOD 检出率
- **服务形态**：输入是 `inputs_embeds`（ERNIE + resampler 产出的软提示），
  **vLLM / OpenAI 兼容接口接不了**；需要自定义 FastAPI 服务，挂到现有 `/runs`
  异步契约下
- **SOS 可合并进底座**（`merge_sos_layers`），但前端（ERNIE + resampler）永远在，
  所以它是分类器，不是 chat 模型
- **校准集**：必须与训练/测试分开；MiRBench 的 test 已经被用于选 checkpoint，
  不能同时当校准集

### 5.1 模型家族（2026-11-03 提议）

一个二分类适配器覆盖不了 RNA 研究的关系判断。按「共享底座 + 任务头/提示」组织
成一个家族：

| 任务 | 输入 | 标签来源 | 状态 |
|---|---|---|---|
| `targeting` | miRNA + 50 nt 窗口 | MiRBench（CLASH/eCLIP） | 模型在建 |
| `region` | miRNA + 窗口 | MiRBench region（1.28 M） | 数据已有 |
| `sponging` | lncRNA + miRNA | starBase / DIANA-LncBase / RAID | 待数据 |
| `rbp` | RBP + RNA | eCLIP | 待数据 |
| `general` | 任意两条 RNA | NPInter v4 / RNAInter | 待数据 |

**两种实现，建议 A：**

- **A. 单适配器多任务**：共享 ERNIE -> resampler -> LLM，用问题文本区分任务。
  这样问题感知支路才真正起作用；序列理解共享；显存只放一份。
- B. 每任务一个适配器：简单，但显存和训练成本线性增长，任务间不迁移。

**每个任务必须独立的东西**（不能被共享掩盖）：

- 校准集与阈值（不同任务的正负比差异很大）
- OOD 判定（物种、长度、序列类型）
- 独立评测与报告——**一个任务的 AUROC 好，不能代表另一个**

**服务形态**：一个 GPU 服务，多个工具入口：

```
rna_score(task=targeting|region|sponging|rbp|general, seq_a, seq_b)
   或者按任务拆成 rna_relation / rna_sponge / rna_rbp ...
```

对 agent 更友好的是**按任务拆开**（工具名和描述直接说明用途），
底层共用一个服务。

**路线**：`targeting` 先跑通 -> 加 `region` 当辅助任务（激活问题感知） ->
`sponging` -> `rbp` -> `general`。不要在没有一个任务跑通之前训练五个模型。

**注意**：这里的家族是「判断/推理」模型，不是检索模型。文献检索靠 embedding
索引（§6），两者不要混为一谈。

## 6. 文献层：发现工具 + RAG 证据层（2026-11-03 修订）

这是两件事，不能互相替代：

| 角色 | 谁 | 作用 |
|---|---|---|
| **发现 / 精确检索** | `paper-search-tool`（现成，服务器上在跑） | 按关键词或一句话找论文，覆盖广，现打上游 |
| **证据层（RAG）** | 自建领域索引 | 实体感知检索 + 关系边 + 段落级 span，可复现 |

**paper-search 只能当「最终精确搜索」的工具，不能当 RAG。** 原因：

- 只有摘要（≤1600 字），没有段落级 span，引用到不了原文
- 通用去重和排序，**不认识 miRBase id、基因同义词、物种别名**
- 没有结构化关系（靶向 / 海绵 / 调控），每次现算
- 现打上游，同一问题两次结果可能不同，不可复现
- 有配额和 3 s 间隔，撑不住 agent 的多步检索

所以：**RAG 是主路径，paper-search 是补充**——用来发现新文献、给召回兜底，
以及核对索引里没有的最新论文。

### 6.0 发现工具：paper-search（不是 RAG）

先说清楚一件事：站点上的 `/opensearch/` **不是 OpenSearch/Elasticsearch 引擎**，
是 `RomanCohort/paper-search-tool` 这个项目的名字——一句话需求 → LLM 转英文检索词 →
并行打 arXiv / Europe PMC / PubMed / OpenAlex / Crossref / DOAJ / RCSB PDB →
去重 → LLM 挑 3–5 条。**没有持久索引**，每次查询都是现打上游。

可以直接用的接口（代码里没有鉴权，`client_id` 只用于配额）：

```
GET /api/papers?q=<关键词>&source=all&client_id=rna-harness
    → items: {source, source_label, id, title, authors, date,
              abstract(≤1600 字), url, pdf_url}

GET /api/asksearch?q=<一句话需求>&sources=europepmc,pubmed
    → LLM 从结果里挑几条并给理由
```

限制（必须按这个设计工具）：

| 限制 | 值 | 影响 |
|---|---|---|
| 未命中缓存的两次检索最小间隔 | 3 s | agent 不能连续扇出 |
| 单 client 每日配额 | 30 次 | 必须按实体对缓存，不能一篇论文查一次 |
| 缓存 TTL | 12 h | 同一问题当天基本命中缓存 |
| 单次最多结果 | 10 条 | 召回有限，需要窄查询 |
| 粒度 | 摘要 | **没有段落 span**，引用只能到摘要级 |

所以第一阶段的 `literature_search` 工具应该：

- 按**实体对**构造查询（如 `"hsa-miR-21" AND "PTEN" targeting`），一个关系查一次
- 本地 SQLite 缓存：`(mirna, gene, snapshot_date) → 结果`，命中就不再打
- 返回的证据对象带 `coverage` 字段，说明只有摘要、快照时间、来源库

### 6.0.1 内部通道：公网限流保持不变

**对外站点的 3 s 间隔 / 30 次每日配额绝对不动**——那是给公开访客的，改它等于把
服务器和上游 API 暴露给任何人。

harness 走**独立的内部通道**，二选一：

| 方式 | 做法 | 优点 | 代价 |
|---|---|---|---|
| **A（推荐）** | 内核与 `paper-search-tool` 同机部署，直接 `from opensearch import search` 调用，不走公网 HTTP | 零额外鉴权、零网络开销、最快 | 两个服务要同机、同 Python 环境 |
| B | 在 `127.0.0.1` 上单独暴露内部端点（如 `/internal/papers`），带独立 token，nginx 不转发 | 进程隔离 | 多一层服务要维护 |

无论哪种，**内部也不做“真无限”**：

- 上游 API 自己的限制还在（NCBI 无 key 3 req/s、有 key 10 req/s；
  Europe PMC 也有配额），打太猛会被封，反而拖垮站点
- 内部配额建议放大到 **1,000–2,000 次/日**，并保留 3 s 最小间隔与缓存
- 缓存仍然必须有：同一条 miRNA 在多个问题里反复出现是常态

第一阶段的架构因此是：

```
kernel（OpenHarness）
   │  内部调用（import 或 localhost）
   ▼
paper-search-tool（公网入口仍限流；内部通道独立配额）
   │  3 s 间隔 + 12h 缓存
   ▼
arXiv / Europe PMC / PubMed / OpenAlex / Crossref / DOAJ / RCSB PDB
```

### 6.1 证据层（RAG）：自建领域索引

当 agent 跑通、需要「可复现 + 段落级引用 + 关系边」时，再上本地索引：

**不走「每次现调 PubMed API」，而是在自己的服务器上维护一个增量更新的文献库，
工具只查这个库。**

理由：

- **可复现**：同一个问题两次查询得到同样结果，并带快照日期
- **可审计**：每条证据能追到 PMID/PMCID + 段落偏移，引用不是编的
- **可结构化**：关系边是离线抽好的，不是每次现算
- **无速率限制**：NCBI 无 key 3 req/s、有 key 10 req/s，撑不住 agent 的多次检索
- **可做否定判断**：能说清"这个库里没有"，而不是"这次 API 没搜到"

### 6.2 数据模型（四层，不是只要一个向量库）

```
documents   pmid, pmcid, doi, title, journal, year, license, snapshot_date
passages    doc_id, section, text, char_span, embedding
entities    kind(mirna/gene/species), canonical_id, surface_form, synonyms
relations   mirna_id, gene_id, type(targeting/sponging/...), method,
            doc_id, evidence_span, confidence, extracted_by
```

**只有 embedding 不够。** 最终要回答的是「有没有关系」，需要 `relations` 这张表，
它由离线抽取生成，检索时先按实体对查边，再用段落做佐证。

### 6.3 更新管线（每日增量）

- 源：优先 Europe PMC REST（`since=last_success` 增量）；PubMed baseline/update
  做补漏
- 幂等：按 `pmid + version` upsert；删除不物理删，打 tombstone
- 每次入库写 `snapshot_date`，查询结果要带快照
- 关系抽取离线批处理（规则 + LLM），只对**命中实体对**的段落跑，不是全库

### 6.4 规模（粗估，必须实测）

- PubMed 约 3,700 万条题录；PMC 全文约 500 万篇（OA 子集）
- 全量向量化不现实：500 万篇 × 约 50 段 × 1024 维 float32 ≈ **1 TB**
- 所以**先做窄域**：miRNA / lncRNA 互作相关文献，按实体命中筛，
  估计 10–30 万篇，量级降到几十 GB
- 向量用半精度/量化（pgvector halfvec 或 HNSW），具体看服务器

### 6.5 检索顺序

1. **实体解析（最先做，也是关键词搜索做不到的地方）**
   - miRNA：`hsa-miR-21-5p` / `miR-21` / `MIR21` 落到同一个 miRBase mature id
   - 基因：HGNC 符号 + 同义词 + 旧名（`PTEN` / `MMAC1` / `TEP1`）
   - 物种：human / mouse / rat 不能混；跨物种同源要单独标
   - 解析失败就返回 `unresolved`，不要拿自由文本硬搜
2. 实体过滤：先在 `relations` 表按 (miRNA, gene, species) 查边
3. 混合检索：BM25 + 向量，RRF 融合（段落级，不是文档级）
4. 重排 + 片段抽取，返回带 span 的证据
5. 没有边、也没有段落时，才降级到 paper-search 做发现

### 6.6 证据对象（与 harness 的契约）

```json
{"snippet": "...", "pmid": "...", "pmcid": "...", "section": "results",
 "span": [start, end], "relation_type": "targeting",
 "confidence": "explicit|implied", "snapshot": "2026-11-02"}
```

### 6.7 融合规则（与 D3 一致）

- 有明确实验证据 → 以证据为准，模型分数只作一致性检查
- 无证据、模型高分 → 标为「预测」，必须写清楚不是文献结论
- 两者冲突 → 报告冲突，不擅自裁决

### 6.8 本地已有资产（2026-11-03）

**RNAcentral（本地）——实体层，不是证据层。**

它能做：

- 序列 ↔ URS id ↔ 外部库 xref（miRBase、Ensembl、RefSeq、PDB…）
- 物种归一、同一条 RNA 的不同命名合并
- 给定一条序列，反查它是不是已知的 miRNA / lncRNA

它**不能**做：

- 没有基因符号（gene symbol），没有文献，没有关系边
- 「miR-21 → PTEN」里的 PTEN 侧要另建词表（HGNC）

所以 RNAcentral 在 RAG 里的位置是 §6.5 第 1 步（实体解析）的 miRNA 侧，
不是检索语料。

**Qwen3-Embedding（本地）——稠密向量编码器。**

用法要点：

- query 侧要加 instruction prefix（Qwen3-Embedding 是 instruction-aware 的），
  document 侧不加；两侧前缀不一致会让分数整体偏移
- 输出做 L2 归一化，余弦相似度
- 维度可选（MRL），建议先 **1024**：300k 篇 × 约 40 段 = 1,200 万向量，
  halfvec 下约 24 GB，服务器放得下
- 它是通用文本模型，不是生物医学专用；先跑基线，如果召回不够再对比
  MedCPT / BioLORD 这类领域模型

**还缺两样，缺了 RAG 做不出来：**

1. **HGNC 基因词表**（符号 + 同义词 + 旧名 + Ensembl id）——PTEN 侧归一的必需品
2. **关系标注集**：100–200 条已知 miRNA-靶基因关系 + 文献 span，作为抽取器和
   检索的验收线

### 6.9 待定

- 服务器规格（CPU / RAM / 磁盘 / 有无 GPU）
- 起始范围：只做 miRNA-mRNA，还是同时上 lncRNA
- 全文许可：优先 PMC OA 子集，摘要与全文要分开标注
- 关系抽取的评测集：人工标 100–200 条，作为抽取器的验收线

## 7. 评测口径

- **组件**：`rna_relation` 的 AUROC/AUPRC/ECE、扫描模式的窗口鲁棒性
- **端到端**：给定一条 miRNA 和一个基因，agent 能不能给出正确关系 + 可追溯证据
- **基线**：纯 RAG / 纯模型 / 融合，三者对比
- **失败模式清单**：幻觉引用（编造 PMID）、把预测写成证据、分布外仍给高分、
  窗口切错导致漏检
- **扰动检验**：沿用模型侧的四种扰动，确认工具确实在读序列

## 8. BioClaw / OpenHarness 接入点（建议选 2，需要核对它们的实现）

三种可能的复用位置：

1. **只做 tool provider**：现有 `SKILL.md` + `lab.py` 就够了
2. **复用调度层**：由它们负责规划、选工具、多步执行、会话状态
3. **复用记忆/会话**：把自己的状态塞进它们的存储

**建议：选 2，但用 MCP 作为边界。**

理由：

- 选 1 等于没有复用——`SKILL.md` + CLI 我们自己已经有了，规划/记忆全得自己写；
  而文献维护任务恰恰是多步的（检索 → 读片段 → 判断 → 引用）
- 选 3 会把我们绑死在它们的内部数据结构上，模型或工具一换就要跟着改
- 选 2 + MCP：它们只看到标准工具，RNAHarness 只暴露工具，**两边都不 import
  对方**。换 harness 或换模型都不用重写另一边

落地形态：

- 一个 **MCP server** 包住：`lab.py` 的 14 个工具 + `literature_search` +
  `rna_relation` / `rna_scan`
- 长任务（`/runs` 那类）不能阻塞调用，MCP 工具应返回 `run_id` + 轮询接口，
  或者内部等待但设置明确超时
- 证据对象必须能穿过工具边界，原样进入最终答案（带 pmid / span）

需要核对的三个问题（读一遍它们的代码/文档就能回答）：

1. 是否支持 MCP server？不支持的话，工具适配层写在它们那边还是我们这边？
2. 是否支持长任务的 submit/poll 两段式调用？
3. 工具返回值能不能带结构化证据（而不是只有自然语言文本）？

### 实测结论（2026-11-02，读源码）

| | OpenHarness（HKUDS） | BioClaw（Runchuan-BU） |
|---|---|---|
| 语言/底座 | Python，381 个 .py | TypeScript，Claude Agent SDK |
| 定位 | **agent harness / 调度层** | 容器隔离的 bio 助手（WhatsApp 等渠道） |
| MCP | 内置客户端：`mcp_tool`、`list_mcp_resources`、`read_mcp_resource`、`mcp_auth` | 支持：SDK 的 `mcpServers`，容器内还有 IPC MCP server |
| 多 agent | ✅ `agent` 工具 + `BackgroundTaskManager`；subagent 类型有 general-purpose / Explore / Plan / worker / verification | ❌ 不是协作式多 agent；它的 "agent" 是聊天绑定的独立工作区 |
| 长任务 | 子 agent 作为后台子进程任务，**可轮询**；普通工具体调用会阻塞 | bash 5 分钟、容器 30 分钟上限；没有 submit/poll 抽象 |
| 结构化返回 | `ToolResult{output: str, is_error, metadata: dict}` | SDK 的文本 content block，结构化能力弱 |

**结论：**

- **内核用 OpenHarness**——它本来就是 harness，subagent、后台任务、MCP 客户端都是现成的
- **壳只取 BioClaw 的渠道适配器**（8 个：WhatsApp / 飞书 / 企微 / Slack / Discord /
  微信 / QQ / web），不整包接入（见 §2.0）。它的 agent loop、容器编排、skills 路由都丢掉
- RNAHarness 自己的工具全部走 **MCP**，这样两个都能接，换哪个都不用重写
- 长任务一律用 `run_id` + 轮询，不阻塞 agent 循环
- 证据要能穿过工具边界：OpenHarness 的 `metadata` 字典是天然载体；BioClaw 那边只能放在文本里，或者提升成 MCP 的 structured content


## 9. 风险与未决

- **模型未训出**：下游全部依赖它，但目前只有代码，没有 AUROC
- **窗口策略未定**：直接影响调用方体验和指标
- **许可**：MiRBench CC-BY-4.0；PubMed 摘要的使用条款；Rfam 许可
- **延迟与成本**：35B int4 推理需要 GPU 常驻
- **物种与长度 OOD**：目前只训练了人类、50 nt 窗口
- **仓库可见性**：`RomanCohort/RNAHarness` 与 `RNAQwen` 都是 public，
  PLAN 里如果有未发表数据要注意

## 10. 下一步（按依赖排序）

1. ~~定窗口策略~~ → **已定 A**（§4）；步长/聚合方式待实验
2. 模型训出，给出 AUROC/AUPRC/ECE
3. 写 `rna_relation` 服务与契约（可以先写 schema，不等模型）
4. 文献 RAG MVP（先做 PubMed 摘要级检索 + 片段抽取）
5. 融合与端到端评测
6. **MCP server**：把 14 个工具 + 文献检索 + 关系工具包成统一入口
7. 接入 **OpenHarness 作内核**（§8 已实测）；从 BioClaw **只抽渠道层**，
   自己写 gateway（§2.0 已列清楚取/丢清单）
