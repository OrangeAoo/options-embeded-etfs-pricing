# JEPI JEPQ ACYN 官网补充数据与 Bloomberg 查询清单

查询日期：2026 年 11 月 23 日，纽约时间。原项目估值日：2026 年 11 月 20 日。

官网已补到 JEPI/JEPQ 的 ELN 持仓与部分发行商 delta/gamma，以及 ACYN 的三笔衍生品持仓和 24 个底层组合的部分条款。三只基金仍未达到完整定价条件。本次只新增补充资料，没有改动原始代码、Excel、`data/sheets/` 或之前的数值结果。

这份记录更新了先前说明中的“尚未取得发行商 unit delta/gamma 文件”。实现、检索和核对使用了 Codex 辅助，提交课程计算前仍须按课程要求由团队独立复核。

## 官网已经取得的资料

| 基金 | 日期 | 官网新增资料 | 尚缺的关键输入 |
| --- | --- | --- | --- |
| JEPI | 2026-11-23 | 15 条 ELN 持仓；14 条 unit delta/gamma；持仓数量、市值、净资产权重、参考指数价格和时间 | `SPX_1` 的风险行、11 月 20 日快照、单位换算定义、Vega、实际合约条款 |
| JEPQ | 2026-11-23 | 15 条 ELN 持仓；14 条 unit delta/gamma；相同字段 | `NDX_4` 的风险行、11 月 20 日快照、单位换算定义、Vega、实际合约条款 |
| ACYN | 2026-11-20 | BNP/JPMorgan/Citi 三笔 autocall 衍生品；24 个底层组合的日期、票息、障碍和三个指数的相对表现 | 每组合名义本金/权重、完整日历与合约确认、初始指数点位、资金腿、市场曲面及相关性 |

### JEPI 和 JEPQ

从各基金官网进入 **Portfolio → Supplemental Information → Download ELN Data**。下载入口位于 Portfolio 页；事实表仍指向 Documents，因此仅在 Documents 列表中搜索容易漏掉它。

- [JEPI 官网 Portfolio](https://am.jpmorgan.com/us/en/asset-management/adv/products/jpmorgan-equity-premium-income-etf-46641q332/#/portfolio)
- [JEPQ 官网 Portfolio](https://am.jpmorgan.com/us/en/asset-management/adv/products/jpmorgan-nasdaq-equity-premium-income-etf-jepq-46654q203#/portfolio)

Top 10 Holdings 附近的 Excel 下载提供完整持仓，Supplemental Information 的 Excel 提供风险数据。原样保存了四份文件：

| 文件 | 用途 |
| --- | --- |
| [JEPI 完整持仓](shuhao_jepi_holdings_20261123.xlsx) | `Holdings` 页第 8 行列名，第 9 行起为记录；筛选 Security Type = Equity Linked Notes |
| [JEPI ELN 风险表](shuhao_jepi_eln_20261123.xlsx) | `ELN DATA!A8:F22`，第 8 行为列名，14 条风险记录 |
| [JEPQ 完整持仓](shuhao_jepq_holdings_20261123.xlsx) | 相同持仓结构 |
| [JEPQ ELN 风险表](shuhao_jepq_eln_20261123.xlsx) | `ELN DATA!A8:F22`，14 条风险记录 |

风险表包含 Security ID、Security Name、Estimated Equivalent Unit Delta、Estimated Equivalent Unit Gamma、DeltaGamma Reference Price 和 DeltaGamma Reference Time。JEPI 的名称使用 SPX，JEPQ 使用 NDX。参考时间原文为 `20:00 PM EST: 11/23/2026`，保留原文，需确认它与所用市场价格的估值时点一致。

同一基金、同一日期内按唯一名称关联后，JEPI 缺 `SPX_1`，JEPQ 缺 `NDX_4`。缺失风险行保留为空，没有填零。JEPQ 某些已披露 Gamma 为零，属于源数据中的实际零，与缺失不同。

持仓表没有风险表中的 Security ID，名称匹配尚需证券主数据核实。`JPH...`、`JPNDX...` 是发行商表内标识，不能直接当成 Bloomberg FIGI、CUSIP 或 ISIN。滚动后名称可能复用，禁止跨日期仅按名称拼接。

以下限制仍影响计算：

- 官网提供的是 11 月 23 日快照，不能并入 11 月 20 日的原始账本。可以取得 11 月 20 日归档，或协调更新全部基金及市场输入到同一新日期。
- 说明没有充分定义 Gamma 的数值尺度及其与 Shares/Par 的转换。必须确认是每指数点还是每百分比变动、每票据还是其他标准单位，再换成 dollar gamma。不能套用上市期权的 100 倍乘数。
- 风险表没有 Vega。局部 Delta/Gamma 也不能唯一恢复执行价、期限、波动率和整个情景曲线。
- 通用持仓表中这些 ELN 的 Maturity Date、Effective Date、Contract Size 为空，Coupon 和 Strike Price 为零。它们不是已经验证的零票息或零执行价合约条款。
- ELN 市值或 20% 配置上限不能代替卖出期权的等效名义本金。

### ACYN

[官网 Holdings](https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker=ACYN) 在三笔国债和现金之外，还列出以下三笔衍生品。页面的 Number of Holdings 指标仍显示 3，不能用这个数字判断衍生品不存在。

| 名称 | 发行商标识 | Quantity 原值 | 市值 USD | 权重 |
| --- | --- | ---: | ---: | ---: |
| ACYN Autocall Derivative BNP | ACYNBNP01 | 879,228,611 | -44,719,334.44 | -1.62% |
| ACYN Autocall Derivative JPMorgan | ACYNJPM01 | 877,524,530 | -50,351,278.12 | -1.83% |
| ACYN Autocall Derivative Citi | ACYNCIT01 | 1,145,301,818 | -54,236,489.09 | -1.97% |

Quantity 的页面表头是 Shares / Quantity；本次没有擅自把它认定为期权张数或已验证的美元名义本金。这些标识也不保证能在 Bloomberg 搜到。

[官网 Fund Summary](https://www.ftportfolios.com/Retail/Etf/EtfSummary.aspx?Ticker=ACYN) 中的 **Underlying OTC Derivative Characteristics Relative to Initial Strike Date** 图表披露了 24 个组合。数据来自公开页面的图表 tooltip 记录，不是按产品描述虚构条款。字段包括：

- OTC 1 至 OTC 24 的组合名称。
- IssueDate、MaturityDate、NextObservationDate、两个剩余天数字段。
- Coupon、CallBarrier、CouponBarrier、MaturityBarrier。
- SPX、NDX、RTY 各自相对初始水平的回报和当前最差标的。
- MarkToMarket，含义是相对初始价值的 premium/discount 比率，**不是美元市值**。

图表障碍用相对初始日的回报坐标表示，例如 0.00 与 -0.30；不能把 -0.30 当作负的绝对执行价。完整合约中还须核实障碍、观察和支付规则。

例如 OTC 24 披露发行日 2026-08-17、到期日 2028-08-15、下次观察日 2027-02-12、年化票息 10.88%。这只是一个组合的披露，不能假定其权重为 1/24。三个对手方 swap 和 24 个底层组合之间的分配尚未取得。

24 条记录的到期和观察剩余天数都与 11 月 20 日日期核对一致。官网还披露同日基金净资产为 $2,743,733,849，与原 ETF list 中的存量 AUM 应分开核对。

历史交叉检查发现：3 月持仓报告和当前 11 月图表指向 Russell 2000/RTY，但 6 月财务报表的 swap reference 描述写 Russell 1000。应以同日实际确认书核实，不能静默替换为任一指数。

## Bloomberg 操作和字段清单

以下是基于已取得数据缺口整理的终端操作清单；本次没有连接你的 Bloomberg 终端。字段的可用性、历史日期覆盖和计量单位须在终端核实。这里的业务字段名不是一组已实测、可以直接粘贴运行的 API mnemonics。

### 1 取得三只基金的完整同日持仓

分别输入 `JEPI US Equity`、`JEPQ US Equity`、`ACYN US Equity`，先查看 `DES <GO>` 核实基金身份，再进入基金的 Portfolio/Holdings；项目原始导出流程使用 HLDR/BQL holdings。可从 `HLDR <GO>` 进入持仓视图，确认显示的是基金持有的资产，而不是谁持有基金份额。

选择 **2026-11-20**，保留全部资产类型和所有行，特别是 Structured Notes、ELN、Swap、OTC、Other；不要先筛选 Option，不要只导出 Top 10。如果历史日期不可用，保留实际报告日期，不要将最新持仓改标为旧日期。

导出至 `fund_holdings_20261120.xlsx`，每只基金一页：

| 业务字段 | 用途/要求 |
| --- | --- |
| Fund ticker / CUSIP / share class | JEPI 46641Q332；JEPQ 46654Q203；ACYN 33733E690，核实份额类别 |
| Holding Bloomberg ID / FIGI / ISIN / CUSIP | 为实际持有的证券或衍生品提供唯一标识 |
| Security name / description / type | 保留完整名称及 ELN/OTC 分类 |
| Holdings as-of / report date / source | 区分实际持仓日期与导出当天日期 |
| Long/short / quantity / position unit | 确认数量是份、面值、千面值、张数还是美元名义本金 |
| Reported market value / currency / NAV weight | 与同日 NAV/AUM 对账，负市值不能自动推成空头名义本金 |
| Notional / face value / par factor / multiplier | 数量、价格和风险换算需要的单位定义 |
| Fund net assets / NAV / shares outstanding | 同日期的分母与规模检查 |

原项目导出已经包含的列是 `ID`、`id().position.POSITION`、`SECURITY_TYP.SOURCE`、`SECURITY_TYP.POSITION_TYPE`、`SECURITY_TYP.ID_DATE`、`SECURITY_TYP`、`id().reported_mkt_val.REPORTED_MKT_VAL`、`id().weights.WEIGHTS`。保留这些列并补完整名称及单位说明，方便逐行比较；不要先把原 CSV 覆盖掉。

### 2 查 JEPI/JEPQ 实际 ELN 的条款和风险单位

用持仓导出的实际证券标识打开每笔 ELN，查看 `DES <GO>` 和关联的条款文件。找不到证券时可用 `SECF <GO>` 搜索实际 ISIN/CUSIP/完整名称；发行商 SPX_1 等别名不足以唯一定位票据。

将以下字段导出至 `jepi_jepq_eln_terms_20261120.xlsx`：

- 证券 ID、发行人/交易对手、基金持有数量、数量单位、面值、币种和美元等效名义本金。
- 真实参考标的（SPX/NDX/QQQ 等）、初始 fixing、执行价、发行日、定价日、到期日和结算日。
- 参与率、杠杆、卖出 call 的数量/overwrite 比例、票息/资金腿、支付规则、结算方式。
- 同日估值及其时间、报价来源、净价/全价、参考标的价格。
- 若有 Delta/Gamma/Vega：保留数值、估值模型、单位、币种、是否已乘仓位，以及每个底层的分项风险。
- 明确 Gamma 对应每指数点还是每 1% 变动，Vega 对应 1.00 绝对波动率还是 1 个 vol point；导出相关字段定义或设置截图。

在 `FLDS <GO>` 或 Excel Bloomberg → Find Fields 检索上述含义，打开字段说明，核实适用证券、历史覆盖、units 和 overrides 后再生成导出。`ID_BB_GLOBAL`、`ID_ISIN`、`ID_CUSIP`、`NAME`、`SECURITY_TYP`、`CRNCY`、`MATURITY`、`CPN`、`PX_LAST` 等可作为常见字段检索起点；它们并不保证对这些私人 ELN 都有值，也不保证包含嵌入期权条款。

官网风险表可以作为校准目标，但要先补 `SPX_1`、`NDX_4` 和单位定义。若 Bloomberg 也无私人 ELN 信息，需要向 JPMorgan ETF product desk 索取指定日期的 holdings + unit risk 归档、风险单位说明及 payoff/option-equivalent notional。无需从 JEPI/JEPQ 份额的 `OMON` 期权链取数代替内部 ELN。

### 3 查 ACYN 的 24 个组合和 swap 确认

先在完整 ACYN 持仓/OTC 视图找 BNP、JPMorgan、Citi 三笔。用实际 Bloomberg/ISIN 标识进入条款与参考篮子；官网 `ACYNBNP01` 等仅作为名称匹配线索。若终端只有三笔国债和现金，与当前官网三行衍生品对照后，确认该数据集是否遗漏 OTC。

导出 `acyn_components_20261120.xlsx`，至少一行对应一个底层组合，保留对手方拆分：

| 字段组 | 具体字段 |
| --- | --- |
| 身份与规模 | Swap ID、component ID、counterparty、component notional、currency、weight、三笔 swap 的 component allocation |
| 标的与起点 | 每个参考指数/ETF 的准确名称、初始 fixing 日、绝对初始水平、当前水平、worst-of 定义 |
| 生命周期 | Issue/trade/valuation/maturity/settlement dates、当前是否已被 autocall、历史观察和支付状态 |
| 观察日历 | 全部 call/coupon observation dates、payment dates、首次可赎回日、call protection、营业日调整 |
| 收益条款 | Coupon rate/frequency/day count、coupon barrier、coupon memory、call trigger/step-down、maturity barrier、终值损失/杠杆公式 |
| 资金和估值 | SOFR funding spread、费用、accrual、upfront payment、MTM、结算方式、collateral/discounting convention |
| 风险 | 各指数 delta/gamma/vega、cross-gamma、correlation sensitivity、单位和所用模型 |

`DLIB <GO>` 可作为条款齐全后的复杂衍生品建模入口；先核实终端是否开放适合的 worst-of autocall 模板。`OVME <GO>` 可用于适用的期权策略/ELN 简化结构校准。模型页面提供的是在指定条款和市场输入下的估值能力，不能自动发现基金的私人 swap 确认书。

若这些私人合约在 Bloomberg 没有覆盖，向 First Trust/Vest 索取上表缺失项，特别是 **24 个组合的本金/权重与三笔 swap 的对应表**。仅有 24 个图表点无法确定总风险规模，不能默认等权。

### 4 导出同日指数波动率曲面

分别输入：

```text
SPX Index OVDV <GO>
NDX Index OVDV <GO>
RTY Index OVDV <GO>
```

选择与持仓一致的历史日期和估值时点，记录来源及曲面设置。若实际 ELN 参考 QQQ/SPY，应另取其准确标的曲面，不能直接把指数点数按 100 倍换成 ETF 份额。

导出 `index_vol_surfaces_20261120.xlsx`，至少包括 underlying、as-of/timezone、spot、forward、expiry/tenor、strike 或 moneyness/delta、call/put、bid/mid/ask IV、报价来源、日计数和波动率单位。优先覆盖实际合约期限；ELN 需要短端，ACYN 需要覆盖其约两年的期限及障碍附近的偏斜。

如果只能导出链，使用上述**指数**的 `OMON <GO>` 取证券 ID、call/put、strike、expiry、AM/PM settlement、multiplier、bid/ask、报价时间、IV。历史链须包含当时存在的合约，不能用今天的链冒充历史 universe。OI/volume 可留作后续市场规模归一化。字段和历史可用性仍需用 FLDS/终端帮助确认。

### 5 保存曲线、远期、分红和相关性

从估值页面使用的市场输入/设置导出以下数据至 `curves_forwards_correlations_20261120.xlsx`：

- USD SOFR/OIS 贴现曲线：curve ID、as-of、tenor/date、discount factor、zero rate、day count、compounding、币种。
- SPX/NDX/RTY 的同日期 spot、forward/tenor、隐含分红或 dividend curve，以及使用的价格指数/总收益指数约定。
- ACYN 模型的 SPX-NDX、SPX-RTY、NDX-RTY 相关性：数值、期限、来源、日期和矩阵定义。历史收益相关性只能作为明确标记的 proxy，不能称为已取得的隐含相关性。
- 模型中的 vol bump、spot bump、sticky-strike/delta 选择及单位。风险不能一边按 1% 一边按 1 个指数点相加。

### 6 终端查不到时的提问内容

可在 Bloomberg 绿色 HELP 键连按两次后，将以下问题提供给帮助人员；这只是待发送文本，本次没有联系任何人：

> I need fund-held derivatives, not exchange-traded options on ETF shares. For JEPI US Equity, JEPQ US Equity and ACYN US Equity as of 20-Nov-2026, which holdings dataset includes private ELNs and OTC autocall swaps? Please identify the correct fields, date overrides and units for position, reported market value, notional, underlying, payoff terms and available Greeks. JPMorgan's issuer unit-risk sheets use JPH/JPNDX identifiers; First Trust lists ACYNBNP01, ACYNJPM01 and ACYNCIT01. Are these mapped to Bloomberg securities? Can the November 20 volatility surfaces and private-security terms be retrieved with our entitlement?

## 数据文件和核对范围

[shuhao_overlay_sources.json](shuhao_overlay_sources.json) 保存来源 URL、UTC 抓取时间、响应 SHA-256、原样 XLSX 文件名、30 条 ELN 持仓及其匹配结果、ACYN 三笔衍生品与 24 条图表记录。原始 CSV 哈希也保留在 validation 中。

核对完成：四份 XLSX 复制前后字节一致；ELN 名称在各自基金内唯一；匹配的风险和持仓日期一致；两条缺失风险行保留缺失；ACYN 24 条记录名称唯一，全部到期及观察天数与 11 月 20 日一致。没有重新计算或扩展基金 Gamma/Vega，也没有将未确认的单位或新日期混进旧结果。

明天 progress report 可以汇报：已从发行商取得此前遗漏的 ELN 风险表和 ACYN 底层组合资料，正在解决日期、风险单位、个别缺失行及组合名义本金分配。不能写成三只基金已经完整估值。

## 官方参考

- 上方两只 JPMorgan Portfolio 页及四份原样 Excel；具体下载 URL 见 JSON 的 sources。
- [ACYN 官网持仓](https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker=ACYN)、[组合特征与图表](https://www.ftportfolios.com/Retail/Etf/EtfSummary.aspx?Ticker=ACYN)、[News and Literature](https://www.ftportfolios.com/Retail/Etf/EtfFundNews.aspx?Ticker=ACYN)。
- [ACYN 2026-03-31 持仓报告](https://www.ftportfolios.com/Common/ContentFileLoader.aspx?ContentGUID=d156ac8a-cdba-477e-b158-7711aa06e45f)、[2026-06-30 财务报表](https://www.ftportfolios.com/Common/ContentFileLoader.aspx?ContentGUID=cd7fb286-a79b-4a84-aae0-18ea2d961f2a)、[Prospectus](https://www.ftportfolios.com/Common/ContentFileLoader.aspx?ContentGUID=3cb9857a-4673-4e93-a709-6ab614b6358a)。历史报告仅作结构交叉检查，没有用其旧仓位代替 11 月 20 日仓位。
- [Bloomberg Core API User Guide](https://data.bloomberglp.com/professional/sites/4/BLPAPI-Core-User-Guide.pdf)：字段检索、FLDS、OVDV、OMON 与参数说明。具体证券覆盖和当前页面仍以终端为准。
- [Bloomberg 学生入门指南](https://data.bloomberglp.com/professional/sites/10/Getting-Started-Guide-for-Students-English.pdf)：Excel Find Fields、导出向导及帮助入口。
- [Bloomberg DLIB 产品说明](https://data.bloomberglp.com/professional/sites/4/2015/02/DLIB_Brochure.pdf)、[2026 年波动率与复杂期权工具说明](https://professional.content.cirrus.bloomberg.com/professional2023/insights/webinar/inside-volatility-trading-hedging-positioning-winning-with-options/)。这些资料说明工具用途，不保证本账户能取得私人合约。
