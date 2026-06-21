# 🎯 医美线索清洗工具 (Clue Cleaner)

面向医美机构数据运营、投放优化师的命令行线索清洗工具。
用最少界面完成大批量渠道名单判重，适合每周投放复盘、算真实获客成本前先把水分挤掉。

---

## ✨ 核心特性

- **6 个命令**：`import` / `precheck` / `dedup` / `review` / `export` / `compare`
- **多规则判重**：手机号、微信号、姓名+生日、姓名+城市+项目意向、姓名+手机尾号
- **老客识别**：自动命中历史成交库，识别复咨客户
- **在跟识别**：对接门店在跟名单，避免重复派单
- **疑似评分**：5 档重复等级 + 模糊相似度评分
- **渠道优先级**：按归属规则自动确定最终渠道
- **人工复核**：生成并排对比的 Excel 复核包
- **批次对比**：两批投放的差异分析与渠道变化
- **字段别名**：自动识别不同列名（手机号/手机号码/phone 等）
- **规则模板**：保存常用判重配置为命名模板

---

## 📦 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 生成示例数据（可选）

```bash
python generate_samples.py
```

### 3. 一键跑完整流程

```bash
# 方式一：一条链（推荐新手）
python clue_clean.py import ^
    -m "sample_data/6月抖音投放.xlsx ^
    -H "sample_data/历史成交客户.xlsx" ^
    -F "sample_data/门店在跟名单.xlsx" ^
    -l "抖音-618"

python clue_clean.py precheck
python clue_clean.py dedup
```

```bash
# 方式二：一步到位（dedup自带导入+判重+导出）
python clue_clean.py dedup ^
    -m "sample_data/6月抖音投放.xlsx" ^
    -H "sample_data/历史成交客户.xlsx" ^
    -F "sample_data/门店在跟名单.xlsx"
```

---

## 📖 命令详解

### 1️⃣ `import` - 导入数据

加载并合并多份投放/历史/在跟名单到内存会话。

```bash
python clue_clean.py import \
    -m 投放1.xlsx -m 投放2.xlsx \      # 多个投放文件（可通配符）
    -H 历史成交.xlsx \                  # 历史成交库
    -F 在跟名单.xlsx \                    # 门店在跟
    -l "抖音-618"                       # 渠道标签
```

### 2️⃣ `precheck` - 预检报告

输出字段识别情况、缺失手机号数量、异常微信号数量、数据质量分。

```bash
python clue_clean.py precheck \
    --errors-out 错误行.xlsx      # 可选：导出错误行定位
```

输出示例：
```
📱 手机号检查:
  - 缺失: 12
  - 格式异常: 3
💬 微信号检查:
  - 格式异常: 5
🎯 数据质量分: 82/100
```

### 3️⃣ `dedup` - 执行判重

按配置规则生成判重结果，自动导出。

```bash
python clue_clean.py dedup \
    -r 严格模式           # 使用已保存的规则模板
    --skip-export        # 仅判重不导出
```

判重等级：

| 等级 | 含义 | 默认操作 |
|------|------|----------|
| 老客复咨 | 命中历史成交库 | 剔除 |
| 在跟名单 | 命中门店在跟 | 剔除 |
| 确定重复 | 相似度≥90% | 剔除 |
| 大概率重复 | 70%-89% | 保留（人工复核） |
| 疑似重复 | 50%-69% | 保留（人工复核） |
| 独立线索 | 未匹配 | 保留 |

### 4️⃣ `review` - 生成人工复核包

```bash
python clue_clean.py review
```

生成 `人工复核包_*.xlsx，包含：
- **待复核 sheet**：每组疑似重复的A/B并排对比 + 填写「人工复核结果」列
- **复核参考 sheet**：下拉选项（A保留/B剔除 / 都保留 / 都剔除
- **确定重复 sheet**：已自动剔除的清单
- **老客_在跟 sheet**：已剔除的老客/在跟清单

### 5️⃣ `export` - 导出 & 模板

```bash
python clue_clean.py export \
    -f valid                      # 仅导出有效线索
    --apply-review 已复核.xlsx      # 应用复核结论后重新导出
    --save-template 严格模式       # 保存当前配置为模板
```

### 6️⃣ `compare` - 批次对比

```bash
python clue_clean.py compare \
    --b-file 5月小红书投放.xlsx \
    --label-a 6月抖音 \
    --label-b 5月小红书
```

对比两批投放的：新增线索数、有效线索变化、挤水率差异、渠道明细对比。

---

## 🔧 配置文件 (config.yaml)

### 字段别名扩展

在 `field_aliases` 下添加贵司表格常用列名：
```yaml
field_aliases:
  phone:
    - 我的自定义手机列名
    - 客户联系号码
```

### 新增判重规则：
```yaml
dedup_rules:
  my_rule_name:
    name: 我的自定义规则
    enabled: true
    weight: 85          # 权重/满分
    threshold: 90           # 触发分
    fields: [name, phone]
    fuzzy_fields: [name]   # 这些字段用模糊匹配
```

### 渠道归属优先级（越小越优先：
```yaml
channel_priority:
  历史成交: 1
  老客转介绍: 5
  抖音: 10
```

---

## 📂 输出文件

| 文件 | 内容 |
|------|------|
| `有效线索_*.xlsx | 剔除水分后的干净名单 |
| `判重全结果_*.xlsx | 所有行带判重标签 |
| `重复明细日志_*.xlsx | 每组重复的明细 |
| `统计汇总_*.xlsx | 整体+渠道汇总 |
| `人工复核包_*.xlsx | 并排对比的复核表 |
| `批次对比_*.xlsx | 两批数据差异 |

---

## 💡 典型工作流

```
周一早会复盘流程：
1. import 导入本周所有渠道投放名单
2. precheck 预检 → 看数据质量
3. dedup 判重 → 系统自动挤水
4. review 生成复核包 → 运营同事花10分钟过一下疑似的
5. export --apply-review 应用复核 → 得出最终有效名单
6. 用「有效线索.xlsx 算本周 CAC
7. compare 和上周批次对比看渠道效果
```

---

## ⚠️ 手机号格式：中国大陆 1[3-9]开头11位
微信号格式：字母开头，6-20位字母数字下划线减号
（均可在 config.yaml 改

---

## 🛠 技术栈

- **Python 3.8+
- **pandas** - 数据处理
- **click** - CLI框架
- **rapidfuzz** - 模糊字符串匹配
- **openpyxl** - Excel读写
- **PyYAML** - 配置文件
- **colorama** - 终端彩色输出
