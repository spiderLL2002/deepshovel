# DeepShovel Compiler

独立的 Python 服务，负责文档解析、证据抽取、核验和可重建的检索索引。
Web 通过固定、带鉴权的 HTTP 契约调用；Compiler 不读写 Web 的 D1/R2。

## 流程与模块

```text
MinerU → DocumentIR → 完整 PaperEvidenceGraph → 结构/来源验证
       → 冻结图及哈希 → 独立 verifier → import envelope
```

- `api.py`：HTTP、并发池、持久任务调度；`job_store.py`：SQLite/原子文件状态。
- `mineru.py`、`normalize.py`：固定解析边界与 DocumentIR。
- `source_catalog.py`、`structured_source.py`、`source_links.py`：可信来源、结构化证据与精确定位。
- `compiler.py`、`canonical.py`、`graph_contract.py`：整篇编译、稳定身份与图验证。
- `model_client.py`、`gpt_responses.py`、`job_control.py`：唯一模型边界、Responses流、重试/取消预算。
- `retrieval_contract.py`、`retrieval_task_contract.py`：检索和当前任务编译契约。
- `retrieval_service.py`、`retrieval_store.py`、`retrieval_providers.py`：检索编排、FTS/Qdrant、模型提供者。
- `retrieval_vectors.py`：可选的磁盘float16向量库（`RETRIEVAL_VECTOR_BACKEND=numpy`）及从内嵌Qdrant迁移的命令。
- `retrieval_text.py`、`retrieval_ontology.py`(+`.json`)：分词（中文单字词、Δ/δ、小数与比值）、否定判断、时间单位，以及带上下文约束与误报回归清单的本体。
- `citation_source.py`、`citation_reception.py`、`claim_comparison.py`：引用、观点对照与独立核验。
- `document_extraction.py`、`data_quality.py`、`data_quality_store.py`：正文图表和独立数据检查。
- `openalex_topics.py`：DOI对应的官方分类或本地标题/摘要分类。

上传支持 PDF、DOCX、XLSX、PNG、JPEG、TIFF。SHA-256、扩展名、MIME与文件签名必须一致。
MinerU 默认长期使用 `https://mineru.acemap.cn/file_parse` 的 `pipeline/auto`，默认一个解析槽位，不跟随重定向或响应内 URL。
当前公网安装使用 `parse` 配置，覆盖上述代码默认值为 `https://parse.iiot-shxt.xyz/jobs`、无令牌、并发4、解析总时限1800秒。该接口提交任务后轮询并领取结果；结果领取后会清理，不重放已消费的结果或结果不明的提交。兼容旧32位任务ID和新 `n1-<32位ID>` 节点前缀；节点地址只从已允许的任务ID推导，不跟随任意重定向或响应内URL。
临时使用本地转发服务时，可配置 `COMPILER_MINERU_URL=http://127.0.0.1:18006/file_parse`、`COMPILER_MINERU_API_TOKEN` 和 `COMPILER_MINERU_CONCURRENCY=4`，并保持既有 SSH 转发可用。各服务均校验 `/health`；不会自动切换服务，也不会将自托管 token 发送至实验室。
解析、编译与数据质量上传共享配置的 1–4 个槽位；持久任务先保存 DocumentIR，取消不会提前释放仍运行的解析线程槽位。
重启后不再把未完成任务全部判为失败：排队任务按原输入重新开始，解析中的任务重新解析（已保存 DocumentIR 时直接复用），
抽取或核验中的任务从已保存的 DocumentIR 继续，状态只前进不回退；同一任务最多恢复两次，缺少保存进度时仍以可重试的
`SERVICE_RESTARTED` 结束。提交接口接受 `Idempotency-Key`（Web 端任务 ID）与 `submit_not_after_ms` 截止时间：
同键重复提交返回原任务而不再付费，截止后不再建立任务；`GET /v1/compile-jobs?idempotency_key=` 与 `?ids=` 供无副作用核对。
只请求 Markdown、content list、middle JSON；几何信息只来自可信 middle JSON。

## 模型与完整性

首选模型为 `gpt-6-luna`，地址 `https://api.iiot-shxt.xyz/v1`，使用 Responses SSE、
`store=false` 和严格 JSON Schema。只接收最终完成事件中的输出文本，不读取隐藏推理。
抽取使用 `max`；验证/关系判定使用 `high`；规划/输入准备使用 `medium`。

批量处理每个逻辑阶段先调用 GPT，首次失败后重试5次；六次均因可重试的可用性故障失败才切到 `deepseek-v4-flash`
（`https://api.deepseek.com`，Chat Completions JSON模式，high思考，最多三次请求）。
两个客户端均使用 `DeepShovel/1.0` 请求标识。失败后等待（输出无效时5秒；请求失败时从5秒起指数退避，最长60秒）并检查取消，不回放被拒绝的输出。
两者共用严格校验并记录实际模型；每个新阶段重新优先GPT。SDK自动重试关闭。
不设置应用层超时；GPT不设输出上限，DeepSeek使用原生最大输出65,536 tokens；
上传、解析响应、请求体和内部任务仍有独立资源边界。MinerU最多三次请求，间隔3秒。

当前提示词只有 `compiler-extractor/1.0.0` 和 `compiler-verifier/1.0.0`。
Claim是论文的终端结论，数量不设配额；局部数值属于Observation或图表。图必须满足
身份、引用、精确quote/region、DAG、Evidence Path、哈希和核验逐项覆盖要求。
非零Claim必须核验（API默认 `verify=true`）；零Claim允许空报告。可独立隔离的坏Claim及其
独占依赖受累计20%损失预算约束（向下取整，少于5个Claim时不容忍局部删除），唯一Claim不可被删除成空成功。
表格单元格与图点必须能在对应源块的可信结构模板中找到（支持rowspan/colspan与常见行内标签），
坐标由Compiler归位并记入审计，与源数值矛盾的 `numeric_value` 被拒绝。
verifier的错误不等于Claim的错误：各次重试中有效的评估逐条保留，未知或畸形评估会被报告并重新请求，
只有用尽有限次数后才在预算内移除仍未评估的Claim并明确记录。
推理到Claim的结论保留立场（支持记为 `infers`，反驳 `contradicts`，限定 `qualifies`）。
Compiler对模型输出的每项改写、移除的Claim以及不可用的可选投影（OpenAlex、引用源）都写入信封的
`compiler_audit`。科学数值、单位和公式争议由verifier记录，代码不计算或改写原始科学结果。

## 配置和启动

公开版本不包含任何运行密钥。先运行 `python3 scripts/setup-local-config.py`，再按自己的服务配置 `compiler/.env.local`。Web 与 Compiler 的本机共享密钥只保存在根目录 `.dev.vars`。

| 配置 | 默认/用途 |
| --- | --- |
| `GPT_API_KEY` | 既有GPT服务密钥 |
| `DEEPSEEK_API_KEY` | GPT六次失败后的备用DeepSeek密钥 |
| `COMPILER_ENV` | development；production要求共享密钥 |
| `COMPILER_SHARED_SECRET` | 32–512位非空白ASCII，仅服务端 |
| `COMPILER_DATA_DIR` | `.compiler-data` |
| `COMPILER_GPT_CONCURRENCY` | 64，可取1–64 |
| `COMPILER_MINERU_URL` | 默认实验室 HTTPS 路由；可临时选用精确 loopback 18006 路由 |
| `COMPILER_MINERU_API_TOKEN` | 自托管 Bearer token，仅允许配置于 loopback 路由 |
| `COMPILER_MINERU_CONCURRENCY` | 默认 1，范围 1–4；临时四卡配置为 4 |
| `COMPILER_MINERU_TIMEOUT_SECONDS` | 900，最大3600 |
| `COMPILER_JOB_TTL_SECONDS` | 86400 |
| `RETRIEVAL_CONCURRENCY` | 64 |
| `RETRIEVAL_REMOTE_MODELS` | true；允许规划和关系模型 |
| `RETRIEVAL_DENSE_MODEL` | paraphrase-multilingual-MiniLM-L12-v2 |
| `RETRIEVAL_QDRANT_URL` / `RETRIEVAL_QDRANT_API_KEY` | 可选独立Qdrant，默认本地持久目录 |
| `RETRIEVAL_MATERIALIZED_RANKING_FEATURES` | false；可重建的排序特征，启用后在后台回填，不阻塞就绪 |
| `RETRIEVAL_VECTOR_BACKEND` | `qdrant`（默认）或 `numpy`；切换前先运行 `python -m compiler_service.retrieval_vectors migrate` |
| `OPENALEX_API_KEY` | 可选 |

仓库根执行 `npm run dev:compiler`。部署由 `make public-update` 管理，Compiler只监听回环地址。

## HTTP边界

`/ready` 是本地快速就绪检查：生产环境必须完成检索初始化，不访问远端服务，未就绪返回 503。
`/health` 保留 MinerU 等依赖诊断；两者不需鉴权且只在回环地址提供。生产 `/v1/` 需要 Bearer共享密钥，语料分析即使本地开发也需要密钥。

启动时用事务内的快照验证记录复用排序特征。源文档、特征或 manifest 的增删改由 SQLite 触发器标记失效；算法、schema 或 ontology 合同变化也会失效。首次或失效快照按每页 128 条严格验证内容和覆盖范围，未变化的快照只读元数据。升级合同后的首次启动可能需要一次特征重建；正常重启不再读取全部文献 JSON。检索模型完成预热后才接收请求。

| 路径 | 用途 |
| --- | --- |
| `POST /v1/parse`、`POST /v1/compile` | 直接解析/编译 |
| `/v1/compile-jobs` | 提交（可幂等）、按键或批量查询状态、取消/删除、结果、引用来源、正文图表 |
| `/v1/data-quality-jobs` | 独立数据检查、状态与报告 |
| `/v1/document-extractions/{version_id}` | 按不可变版本读取/删除正文图表 |
| `POST /v1/openalex/classify` | 论文官方主题分类 |
| `POST /v1/retrieval/{manifests:check,index,search,snapshots:delete}` | 检索投影与查询 |
| `/v1/retrieval/claim-relations:batch` | 批量Claim关系任务 |
| `POST /v1/citation-receptions/analyze` | 引用接受情况 |
| `POST /v1/claim-comparisons/{normalize,analyze}` | 观点拆分与证据对照 |

图和报告原子完成，取消后的迟到结果被丢弃。解析后的PDF复用DocumentIR进行数据检查，
数据检查失败不改变证据图。原件中的数据URL只作为引用，未经请求不自动访问。

## 检索与维护

`evidence-retrieval/2.0` 是索引身份；浏览器只使用2.3，观点对照在Web服务端使用2.1（预览加签名handoff）。
仅用于澄清的2.2协议已在Web（2026-10-18）和Compiler（2026-10-19，含c1令牌、2.2规划提示词与配置身份）中退役，
请求、响应和handoff中出现2.2均被拒绝。启用交互检索时，没有handoff的2.0最终请求和规划失败都直接报错，
不会用本地计划代替模型计划。BM25F、dense、精确术语召回与RRF保留各自职责。预览先返回候选，
签名handoff把最终判定绑定到相同请求、候选顺序、语料和配置；最终不重复召回。Web把handoff、n1、c2令牌
保存在服务端（D1 `retrieval_sessions`），浏览器只持有不透明的 `rs1.` 会话ID。
2.3使用当前compiled task路径；用户选择、历史和补充上下文不得被模型改写。
检索索引可重建，不是证据本体；同一paper family默认只有最新snapshot有效，包括零Claim结果。

```bash
python -m compiler_service.retrieval_warmup
python -m compiler_service.openalex_warmup
python -m compiler_service.retrieval_feature_maintenance --help
python -m compiler_service.retrieval_eval --help
python -m compiler_service.citation_reception_eval --help
python -m pytest
python -m ruff check compiler_service tests
```

测试使用独立合成夹具，不依赖已删除的历史论文、评测输出和缓存，不调用付费模型。
离线评测工具保留，但不附带历史运行结果。全栈验证在仓库根执行 `npm run verify`。

DeepSeek 仅作最后保底。生产服务首次切换到 DeepSeek 时，将累计次数和最近时间写入
`COMPILER_DATA_DIR/model-fallback.json`；控制台每7秒读取，显示“曾启用 DeepSeek 保底”
及检查GPT服务的提示。记录跨服务重启保留，GPT后续恢复也不会抹掉历史提醒。
一次保底阶段只计一次，DeepSeek自身重试不重复计数；独立诊断调用不写生产记录。
实时请求先在第 0、10、20 秒发起 GPT 尝试，不给仍在等待的请求增加总时限。
只有三次 GPT 均以连接、超时、限流或服务端暂时故障失败后，才启动 DeepSeek 保底，
并计入上述提醒。GPT 返回格式/内容校验错误、非暂时错误或仍在正常等待时，不调用 DeepSeek。
