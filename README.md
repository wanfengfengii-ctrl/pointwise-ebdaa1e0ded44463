# 青溪观测日仓

青溪流域资料中心白班实收测站来件、编制日账和发布勘误。夜班将尚未交卷的观测分次归档。主管使用指定日版统计核账，凭原页和回执核对夜班实际收到的读数。

## 运行

需要 Python 3.11 和标准库。在项目根目录运行。

```sh
python3 -B check_project.py
python3 -B -m tests.test_workday
```

`check_project.py` 由控制台提供。完整回归也可用 `python3 -B -m unittest discover -s tests -t . -v` 运行。演示读取 `examples/workday.json`，在临时库上走完入仓、草稿、首刊、交卷、勘误、续领和两版核账。演示先核对手算结果，再打印一行 `POINTWISE_SCENARIO`。

## 一天的日账

1. 上游站送来 A＝7 L，下游站送来 B＝11 L 和 M＝缺测。白班也实收上游补单 C＝5 L，留待勘误时纳入。
2. 白班按 A 批、B/M 批建立草稿，批内保留来件原序与出处。
3. 白班发布 A、B、M 首版，凭发布回执交班。首版 3 行、1 缺测、18 L。
4. 夜班绑定当日会话并领取第一页 A＝7 L，用页回执登记实发内容。
5. 白班从首版建立勘误草稿，将 B 改为 13 L，将 A 移到 B 后，再补登 C 到最前。新版顺序为 C、B、A、M。
6. 夜班续页依次领取 C＝5 L、B＝13 L、M＝缺测。M 页含一行且小计 0 L。随后可取得空页，小计也是 0 L。
7. 主管核对新版 4 行、1 缺测、25 L。上游有 2 行、12 L，下游有 2 行、1 缺测、13 L。两版有效量差为 +7 L。
8. 主管通过原会话、原页号和原页大小复领首版 A＝7 L，得到原出处、小计和回执。

## 初始化与调用

在项目根目录运行的 Python 程序可用下列方式初始化调用方管理的数据库。

```python
import sqlite3
from pathlib import Path

from warehouse.drafts.builder import draft_day
from warehouse.intake.batches import receive
from warehouse.intake.observations import SourceObservation
from warehouse.publication.publish import publish_day
from warehouse.reconcile.daybook import read_day

with sqlite3.connect('warehouse.sqlite3', isolation_level=None) as db:
    db.executescript(Path('schema.sql').read_text(encoding='utf-8'))
    receive(db, 'upstream', '2026-10-06', 'batch-a', [SourceObservation('A', 7)])
    draft = draft_day(db, '2026-10-06', batch_ids=['batch-a'])
    published = publish_day(db, draft, 'first-publication')
    print(read_day(db, published.edition_id).totals.liters)
```

初始化应在没有活动事务的连接上运行，并可在既有库上重复执行，草稿中的人工更正、正式日版和成功页均保留。使用既有库的其他连接也须开启 `PRAGMA foreign_keys = ON`。写入口各自管理事务，调用时连接须无活动事务。用不同连接执行并发操作，连接的 SQLite 超时决定等待多久。

## 公开接口

| 模块 | 调用与结果 |
| --- | --- |
| `warehouse.intake.batches` | `receive(db, station, day, batch_id, rows)` 返回 `BatchReceipt`。`rows` 是非空的 `SourceObservation(label, liters)` 有序序列。站、批号及标签为非空白文本，日期为 `YYYY-MM-DD`。同一个源批号的同参重投返回原确认，站、日期、原行标签、读值及行序均参与核对。|
| `warehouse.drafts.builder` | `draft_day(db, day, batch_ids=[...])` 或 `draft_day(db, day, parent_id=...)` 返回草稿号。只能选择一种基准。批次须已实收、同日且互异。固定父版须存在并属于该日。|
| `warehouse.drafts.edits` | `edit_draft(db, draft_id, edits)` 返回完整更新草稿。`Amendment('correct', i, liters)` 改读值，`Amendment('move', i, j)` 移至最终行位 `j`，`Amendment('remove', i)` 撤行，`Amendment('insert', i, Origin(station, batch_id, line_no))` 在行位 `i` 新登原单读数。位置从 1 起，每一步按当时行序判断。插入可追加到末行之后，来源须为同日实收行。任一编辑无效时整组都不保存。|
| `warehouse.publication.publish` | `publish_day(db, draft_id, request_id)` 返回包含版号的 `PublicationReceipt`。首次发布要求当日无现行版，修订要求固定父版仍然现行。同号同草稿返回原回执，同号换草稿拒绝。`warehouse.publication.receipts.load_publication_receipt` 支持按原请求号或版号查回。|
| `warehouse.reconcile.daybook` | `read_day(db, edition_id)` 返回原序明细、分站统计和整版统计。明细保留来源站、源批号和一基原行位。|
| `warehouse.reconcile.comparison` | `compare_days(db, before_id, after_id)` 返回两份指定同日账单，附分站及整版的后版减前版差值。|
| `warehouse.delivery.sessions` | `open_export(db, session_id, day)` 持久绑定会话和日期，同参复开保留进度，同号换日拒绝。|
| `warehouse.delivery.paging` | `take_page(db, session_id, page_no, page_size)` 返回 `SuccessfulPage`，含版号、页号、请求大小、原序实发明细、整数升小计和回执。同参复领取原页，页号从 1 起连续，大小为正整数。|
| `warehouse.drafts.merge` | `preview_merge(db, left_id, right_id)` 预览同父两稿的合版，返回 `{'preview_id', 'rows', 'conflict'}`。成功时 `rows` 按拟发布行序排列，`DraftRow` 的 `parent_position` 保留父版一基行位，补登为 `None`。业务冲突时 `preview_id` 为 `None`、`rows` 为空，`conflict` 给出 `kind` 与 `refs`。|
| `warehouse.publication.publish` | `confirm_merge(db, preview_id, request_id, *, before_commit=None)` 确认预览，返回具名 `request_id`、`preview_id`、`edition_id`、`token` 的回执。首次确认要求两稿自预览后未成功做过非空编辑且父版仍现行；同请求号同预览重投返回原回执。`publish_day` 的说明见上。|

`None` 表示显式缺测。每行计入行数，缺测另计并贡献 0 L，有效的 0 L 不增加缺测数。有效读值允许有符号整数；布尔值、浮点和数字字符串都拒收。统计按精确整数升计算，分站结果按站名排序。两版对照按站并集计算，缺站在差值中取零。全缺测页和空页的小计都是 0 L，原明细可区分。

未知批次、草稿、日版、会话和无正式版可领的会话抛出 `LookupError`。参数非法、同号异参、过期父版、跨日比较和跳页抛出 `ValueError`。存储和提交挂点异常传给调用方。

## 发布、失败与复领

正式日版一经发布，明细固定。更正和移动延续同一观测的交卷资格。撤行后用同一出处和相同值新登，仍是另一条可交卷观测。新页按其所用正式版的原序领取当前未领行，旧页始终绑定自身的正式版及实发行位。领取空页后仍可因新版补登而领取后续页。

`publish_day` 和 `take_page` 可传可选的零参 `before_commit` 挂点。该挂点在写好待提交数据之后执行，异常导致本次事务回滚。取页还可传 `before_select`，它在首次探查没有成功页之后、锁定用于选页的正式版之前执行。可用另一连接在这里真实发布。`after_commit` 仅在新页提交后执行，挂点失答可用原号同参复领；已存在的成功页复领不会调用提交挂点。挂点不得在正在提交的连接上另行提交事务。
