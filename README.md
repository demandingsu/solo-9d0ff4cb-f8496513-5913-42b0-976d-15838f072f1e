# 湖泊测温档案更正系统

记录湖泊监测员上传记录仪 CSV、按帧序号查看还原温度、提交更正并下载归档 CSV 的网页应用。
技术栈：Python + Flask + SQLite（标准库 `sqlite3`），用 Docker Compose 启动。

## 启动

```bash
docker compose up --build
```

打开 <http://127.0.0.1:8000/>。数据库保存在命名卷 `archive-data`（容器内 `/data/archive.sqlite3`）。

不用 Docker 直接跑：

```bash
pip install -r requirements.txt
LAKE_DB=./data/archive.sqlite3 LAKE_HOST=127.0.0.1 LAKE_PORT=8000 python3 -m lake_archive.web
```

## CSV 格式

示例见 `sample/recorder_sample.csv`。每行一帧：

| 列 | 含义 |
| --- | --- |
| `seq` | 帧序号（整数，唯一） |
| `sampled_at_utc` | UTC 采样时间 |
| `value_millidegrees_c` | 整数毫摄氏度：绝对值或相对某更早帧的差值 |
| `kind` | `absolute` 或 `delta`（可省略，用 `base_seq` 是否存在判断） |
| `base_seq` | `delta` 帧所引用的更早帧序号 |

表头也接受中英文常见别名（`序号`/`采样时间`/`温度`/`基准`/`类型` 等），
逗号、分号、制表符分隔均可识别。文件中的其他列会原样保留并随下载带回。

## 存储与改写模型

- 每帧的**还原温度**（`restored_mc`）在导入时算出并持久化，是权威数据。
- 每帧的**存储值**是派生的：
  - 绝对帧：`存储值 = 还原温度`
  - 相对帧：`存储值 = 还原温度 − 基准帧的还原温度`
- 因此更正基准帧时：
  - 只写被更正的那一帧；
  - 其他帧的还原温度**和基准引用**都不变；
  - 只有引用它的那些帧的**存储差值**随之变化（下载时按新差值导出）。

更正语句为单条 `UPDATE ... WHERE archive_id = ? AND seq = ?`，在
`BEGIN IMMEDIATE` 事务内完成读取校验与写入；另写一条 `revision` 审计记录。
不使用级联更新或全表重算。

## 旧页面提交

单帧页面的表单会带上页面上显示的温度 `seen_restored_mc`。提交时在事务内比对当前还原值：

- 不一致 → 返回 409、页面提示更正被拒绝，**不写任何数据**，现档保持不变；
- 一致 → 接受。即使存储差值已因基准帧被改而不同（页面显示的温度没变），仍可提交。

## 下载

`/archives/<id>/download.csv` 在单个读事务里取该档案全部帧，保证整份文件来自同一已提交状态；
沿用原文件表头、列序、分隔符、换行符和时间写法。

## 测试

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
```
