# Note 编码词典与参考规范

> 本文件从 SKILL.md 中提取的纯参考数据，迁移操作时 Read 查阅。

---

## 1. note 编码词典

note 列承载所有**非表格列**的管道参数。编码顺序：渲染技术 → 内容描述 → 特殊字段 → 反爬/代理/认证。

| note 短语 | 映射的隐藏字段 |
|----------|--------------|
| `SSR` / `SSR HTML可提取` | crawl_method=requests |
| `requests可达` | crawl_method=requests（显式确认） |
| `Next.js SSR` | crawl_method=requests + crawl_json_path=__NEXT_DATA__.xxx |
| `JSON-LD articleBody` | crawl_json_path=articleBody |
| `crawl_json_path=xxx` | 显式声明 crawl_json_path 值 |
| `content:encoded含完整文章` | crawl_mode=feed_only, RSS 全文 |
| `纯RSS即可` | crawl_mode=feed_only |
| `摘要~N字；Web全文SSR` | crawl_mode=feed_enrich（旧格式，兼容） |
| `摘要~N字；跟随link正文可达(M/T)；selector={sel}` | crawl_mode=feed_enrich + 验证通过 + content_selector |
| `摘要~N字；跟随link正文可达(M/T)` | crawl_mode=feed_enrich + 验证通过（readability） |
| `摘要~N字；link正文需playwright` | crawl_mode=feed_enrich + crawl_method=playwright |
| `RSS仅摘要~N字；link正文不可达({reason})` | feed_enrich验证失败，评估summary条件 |
| `content_level=summary；摘要~N字` | content_level=summary, crawl_mode=feed_only（仅消费摘要，p≥10） |
| `content_level=summary；摘要~N字；Cloudflare封锁正文` | content_level=summary + 正文反爬原因 |
| `首页N篇文章链接` | crawl_mode=list_detail |
| `N条/页；?before=分页` | 分页机制=cursor |
| `Drupal` / `WordPress` / `Webflow` | CMS 平台 |
| `无封锁` / `无paywall` | login_requires=0, crawl_use_proxy=0 |
| `Cloudflare` / `DataDome` | crawl_method=playwright |
| `EU地区封锁` / `需住宅代理` | crawl_use_proxy=1 |
| `无需认证` / `公开可达` | api_auth_method=none |
| `免费key可达` | api_auth_method=api_key |
| `需UA+Referer` | cookie_headers 需配置 |
| `仅需User-Agent` | cookie_user_agent 需配置 |
| `scope=subscription` | scope=subscription |
| `scope=subscription(per {target})` | scope=subscription + 订阅目标类型 |
| `Atom格式` / `RDF格式` | RSS 解析器变体 |
| `第三方RSS镜像({domain})` | §2.3 第三方桥接源，URL 列填实际采集地址 |
| `Syndication端点` | §3.5 L2 级，平台官方嵌入式内容端点 |
| `N条/请求含{fields}` | 单次请求返回的条目数和关键字段 |
| `部分高量账号返回热门而非最新` | §3.6 subscription 质量差异记录 |

### 选择器类型编码

| note 短语 | 含义 |
|----------|------|
| ``selector=`.article-body` `` | crawl_content_selector 为标准 CSS class |
| ``selector=`[data-component="text-block"]` `` | crawl_content_selector 为 data 属性选择器 |
| ``selector=`<article>`标签`` | crawl_content_selector 为 HTML 标签 |
| ``selector=`.post-content`(非p标签，div直接文本)`` | 正文不在 `<p>` 标签中，需提取 div 纯文本 |
| `premium标记但正文SSR可提取` | HTML 含 premium/paywall class 但实际正文完整可达 |
| `metered paywall但正文SSR完整可提取` | JSON-LD 声明非免费但 SSR 正文完整 |

---

## 2. 子文档表格规范

<!-- SYNC: feedList_subdoc_format REPLICA 子文档规范（§4.1表头+§4.2格式），主表见feedList_db.md §4 -->

**表头规则**：🔒恒定列（feedList_id/name/prefix）+ 🧑人工列（name_cn/impact/authority/status）+ type专属列 + note

| type分节 | type专属列 |
|----------|-----------|
| WEB | url + login_url + crawl_content_selector |
| RSS | url |
| API | api_provider |
| 未通过 | 无 status/type 列，仅 note |
| 废弃 | 无 impact/authority/status/note 列，仅 废弃原因 |
| 所有 shape | 已通过区均含 `subtype` 列（prefix 之后） |

**格式规则**：
- 排序：impact+authority DESC，同分按 feedList_id ASC
- URL格式：`[字段名](url)` 超链接
- 登录凭证：`[login_url](url "name / password")` 嵌入 title
- crawl_content_selector：CSS选择器用反引号，`-` 表示 readability
- note 列：非表格列的管道参数简述，分号分隔

---

## 3. 测试与留档规范

<!-- SYNC: feedList_subdoc_format REPLICA 测试与完整性（§4.3），主表见feedList_db.md §4 -->

- 未通过测试验证的数据源不分配 type，归入「未通过」
- text 须获取正文（仅标题/摘要不算），event/scalar/geo/transaction/ranked 须获取结构化数据
- 一端点一行：同一 provider 的不同端点必须拆为独立行
- 分配 type 后须在 `samples/feed/{data_shape}/raw/` 下保留样本
- 需认证但当前无凭据的源（API key/OAuth/付费订阅）：留未通过区，note 标注所需凭据类型；获取凭据并保存样本后方可迁移
- 样本命名：`{feedList_id}_{name}_{type}_{YYYYMMDD}.{ext}`
- 样本格式化：JSON 用 `json.dump(indent=4)`、XML/RSS 用 `xmllint --format`、HTML 用 prettier
- 样本完整性：原始文件不截断，通过 `curl -o` 或 `probe_source.py` 保存（禁止用 Write 工具保存 HTML）
- WEB 样本内容：`list_detail` 源必须保存文章详情页（非首页/列表页）
- feedList_id/name/prefix 在全部 6 个 shape 子文档中不重复（含废弃区，废弃标识永不复用）

---

## 4. 废弃区规范

<!-- SYNC: feedList_subdoc_format REPLICA 废弃区规范（§4.4），主表见feedList_db.md §4 -->

**位置**：「未通过」之后、「统计」之前

**列格式**：

所有 shape 统一格式：
```
| feedList_id | name | prefix | subtype | name_cn | 废弃原因 |
```

**排序**：feedList_id ASC（归档顺序）

**废弃条件**（满足任一）：源永久关闭 / 数据质量持续低劣 / 被其他源完全替代 / 法律合规风险

**操作流程**：从原分区删除该行 → 追加到废弃区 → 更新原分区和废弃区标题数量 → 更新统计表

**核心规则**：废弃源的 feedList_id / name / prefix **永不复用**
