"""Live OpenAPI catalog and instructions for the local research kernel."""
# Real tool implementations, not descriptions of tools.
# High-level tools that stay hand-written because they compose several layers.
HIGH_LEVEL_SPEC = [
    {"type": "function", "function": {
        "name": "rna_structure",
        "description": "用RhoFold+预测RNA三维结构并返回真实PDB产物（模型预测，不是实验结构）。用户要PDB或三维时用此工具，优先传sequence_ref。",
        "parameters": {"type": "object", "properties": {
            "sequence_ref": {"type": "string"}, "sequence": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "literature_evidence",
        "description": "查询某对 miRNA-gene 的文献证据与结构化断言。mirna 用规范名如 hsa-miR-21-5p。",
        "parameters": {"type": "object", "properties": {
            "mirna": {"type": "string"}, "gene": {"type": "string"},
            "species": {"type": "string"}},
            "required": ["mirna", "gene"]}}},
    {"type": "function", "function": {
        "name": "rna_fold",
        "description": "仅计算 ViennaRNA 二级结构 dot-bracket 和MFE，不能输出PDB或三维坐标。优先传 sequence_ref（如 seq:1），不要抄写序列原文。",
        "parameters": {"type": "object", "properties": {
            "sequence_ref": {"type": "string",
                             "description": "精确序列引用 ID，从提问末尾的【精确序列引用】表取"},
            "sequence": {"type": "string",
                         "description": "仅在确实没有可用 ref 时使用（不推荐）"}}}}},
    {"type": "function", "function": {
        "name": "lab_inventory",
        "description": "列出本机正在运行的 lab 计算工具及其就绪状态，返回显式计数。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "seed_score",
        "description": "计算 miRNA 与靶窗口的 seed-match 启发式分数（未校准计数，不是概率）。"
                       "优先传 *_ref 引用 ID，不要抄写长序列。",
        "parameters": {"type": "object", "properties": {
            "mirna_sequence_ref": {"type": "string"},
            "target_ref": {"type": "string"},
            "mirna_sequence": {"type": "string"},
            "target": {"type": "string"}}}}},
]

# Extra entity/evidence tools exposed through the MCP-layer plain functions.
EXTRA_SPEC = [
    {"type": "function", "function": {
        "name": "rna_identify",
        "description": "输入 RNA 序列，查 entity 库返回 URS / xref 命中（精确匹配）。",
        "parameters": {"type": "object", "properties": {
            "sequence": {"type": "string"}}, "required": ["sequence"]}}},
    {"type": "function", "function": {
        "name": "rna_fetch",
        "description": "输入 accession，返回实体与序列。",
        "parameters": {"type": "object", "properties": {
            "accession": {"type": "string"}}, "required": ["accession"]}}},
    {"type": "function", "function": {
        "name": "mirna_resolve",
        "description": "把 MIR21 / miR-21 / hsa-miR-21-5p 归一到 mature id。",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "gene_resolve",
        "description": "把基因符号/别名/旧名解析到 HGNC + Ensembl + Entrez。",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "id_map",
        "description": "批量跨库 ID 映射。",
        "parameters": {"type": "object", "properties": {
            "ids": {"type": "array", "items": {"type": "string"}},
            "to_db": {"type": "string"},
            "from_db": {"type": "string"}},
            "required": ["ids", "to_db"]}}},
    {"type": "function", "function": {
        "name": "taxonomy_resolve",
        "description": "物种名 -> taxid + lineage。",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "rfam_scan",
        "description": "序列 -> Rfam 家族（Infernal cmscan，走 WSL 包装器）。",
        "parameters": {"type": "object", "properties": {
            "sequence": {"type": "string"},
            "evalue": {"type": "number"},
            "use_ga": {"type": "boolean"}}, "required": ["sequence"]}}},
    {"type": "function", "function": {
        "name": "evidence_lookup",
        "description": "查某对 miRNA-gene 的结构化关系记录（带 provenance）。",
        "parameters": {"type": "object", "properties": {
            "mirna": {"type": "string"}, "gene": {"type": "string"},
            "species": {"type": "string"}}, "required": ["mirna", "gene"]}}},
    {"type": "function", "function": {
        "name": "citation_verify",
        "description": "核对 PMID + claim 在索引段落里的精确引文与 claim support。",
        "parameters": {"type": "object", "properties": {
            "pmid_or_doc_id": {"type": "string"}, "claim": {"type": "string"},
            "min_overlap": {"type": "number"}},
            "required": ["pmid_or_doc_id", "claim"]}}},
    {"type": "function", "function": {
        "name": "resolve_pair",
        "description": "把 miRNA/gene 名字对解析成规范实体 ID，并说明歧义/未解析原因。",
        "parameters": {"type": "object", "properties": {
            "mirna": {"type": "string"}, "gene": {"type": "string"},
            "species": {"type": "string"}}, "required": ["mirna", "gene"]}}},
]

# Worker display names for the dynamically generated tools.
WORKER_DESC = {
    "rhofold": "RhoFold+ RNA 三维结构预测",
    "physicell": "PhysiCell 多细胞仿真",
    "vienna": "ViennaRNA 二级结构/ensemble（通用入口，可用 tool 参数）",
    "gemorna": "GEMORNA mRNA CDS/UTR 设计",
    "rnadvisor": "RNAdvisor 三维结构打分",
    "rdock": "rDock 小分子对接",
    "haddock3": "HADDOCK3 分子对接",
    "gatsite": "GATRsite RNA 结合位点预测",
    "signald": "SIGNALD 配体打分",
    "bioease": "BioEase TCGA 差异表达 + 生存分析",
    "ncbp": "NC-Bench / NCfold 非经典碱基对打分",
    "esm2": "ESM-2 蛋白 embedding",
    "vaxseer": "VaxSeer 流感 HA 优势度/抗原性筛选",
}


def _json_type(t: str) -> str:
    return {"integer": "integer", "number": "number", "boolean": "boolean",
            "array": "array", "object": "object", "string": "string"}.get(t, "string")


def unavailable_workers() -> list[str]:
    from rnaharness.proto import labtools
    global _UNAVAILABLE_WORKERS
    if _UNAVAILABLE_WORKERS is None:
        inv = labtools.inventory()
        _UNAVAILABLE_WORKERS = sorted(set(inv["degraded"] + inv["unreachable"]))
    return list(_UNAVAILABLE_WORKERS)


def build_catalog(inventory: dict | None = None,
                  contract=None) -> tuple[list[dict], dict[str, dict]]:
    """Build the tool catalog, optionally from an injected discovery result.

    meta maps tool name -> {"kind": ..., "worker": ...}. Only reachable
    workers are registered; an unreachable one is reported in tool_list().
    Supplying ``inventory`` and ``contract`` makes the build fully offline.
    """
    from rnaharness.proto import adapter, labtools
    inv = inventory if inventory is not None else labtools.inventory()
    discover = contract or adapter.contract
    spec = list(HIGH_LEVEL_SPEC) + list(EXTRA_SPEC)
    meta = {s["function"]["name"]: {"kind": "builtin"} for s in spec}
    available = set(inv["ready"])
    for name in labtools.TOOLS:
        if name not in available:
            continue
        c = discover(name)
        if not c["ok"]:
            continue
        props = {}
        for key, info in c["data"]["fields"].items():
            t = _json_type(info["type"].split("/")[0])
            prop = {"type": t, "description": "来自 %s 的 %s 参数"
                    % (name, c["data"]["run_path"])}
            if info["default"] is not None:
                prop["default"] = info["default"]
            if t == "array":
                prop["items"] = {}
            props[key] = prop
        tool_name = "lab_" + name
        spec.append({"type": "function", "function": {
            "name": tool_name,
            "description": "%s（本机 worker，参数由 OpenAPI 动态发现）"
                           % WORKER_DESC.get(name, name),
            "parameters": {"type": "object", "properties": props,
                           "required": c["data"]["required"]}}})
        meta[tool_name] = {"kind": "worker", "worker": name}
    return spec, meta


TOOLS_SPEC, TOOL_META = build_catalog()
_UNAVAILABLE_WORKERS = None


def build_tools_spec() -> tuple[list[dict], dict[str, dict]]:
    """Compatibility alias for callers expecting the live catalog builder."""
    return build_catalog()


def refresh_catalog(inventory: dict | None = None,
                    contract=None) -> None:
    """Replace this module's active catalog for tests or embedded callers."""
    global TOOLS_SPEC, TOOL_META, _UNAVAILABLE_WORKERS
    TOOLS_SPEC, TOOL_META = build_catalog(inventory, contract)
    if inventory is not None:
        _UNAVAILABLE_WORKERS = sorted(set(inventory.get("degraded", [])
                                          + inventory.get("unreachable", [])))

SYSTEM = (
    "你是 RNA 研究 agent，必须全程使用中文回答。\n"
    "事实只能来自工具输出，绝不编造实验、分数或文献。\n"
    "需要事实就调用工具；工具失败要如实报告错误码。\n"
    "严格区分三类结论：文献证据、模型预测（必须标注未校准）、未检索到（不等于不存在）。\n"
    "工具返回的数字、名称列表、快照和计数必须原样照抄，不要自己重新计数或推算。\n"
    "涉及序列的工具调用一律使用提问末尾【精确序列引用】表里的 ref ID，"
    "绝不在参数里抄写序列原文。\n"
    "工具目录分四类：literature_* / evidence_* / citation_* 查文献证据；"
    "rna_identify / rna_fetch / mirna_resolve / gene_resolve / id_map / "
    "taxonomy_resolve / rfam_scan 做实体归一；lab_<name> 调用本机计算 worker；"
    "rna_fold / rna_structure / seed_score / lab_inventory 是高层封装。\n"
    "用户要求PDB/三维坐标时调用rna_structure；rna_fold仅提供二级结构，不能满足PDB请求。\n"
    "相同参数的计算只提交一次；拿到结果后直接汇总，不重复提交。没有artifact就不能声称已生成PDB。\n"
    "lab_<name> 会真实提交计算作业，可能耗时；调用前先说明你要提交哪个工具。"
    "用户没有要求计算时不要主动提交重作业。\n"
    "当用户要求分段汇总时，必须写全所有段落，并用 'A 文献证据'、'B 计算预测'、'C 资源状态' "
    "作为小标题；最后附上覆盖范围与快照等 provenance。"
)

CONTINUE_HINT = (
    "上面的回答被长度上限截断了。请从截断处接着写完，不要重复已经写过的内容，"
    "保持中文与 A/B/C 三段结构，补完尚未写完的段落和 provenance，不要重新开始。"
)
