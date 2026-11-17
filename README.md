# IIIF 画布与注释的授权资源闭包采集资料服务

项目接收 iiif-presentation 来源资料，现有目录只核对 manifest, canvas, annotation, resource 的基本身份和引用。`capture_data.io` 冻结字节和请求来源，`capture_data.domain` 核对本领域记录结构，`capture_data.journal` 封存来源原文和版本，`capture_data.projection` 显示本地引用缺口。现有判断只覆盖本地资料，尚未推断外部服务的连续性或全量完整性。

`capture_data.fetch` 在此基础上沿 IIIF Presentation 3 清单走到画布、注释页与资源：相对地址按各自来源文档的最终地址解释，重定向逐跳留证，分页沿 next 续抓；令牌只发往登记过的来源边界，刷新令牌后强制复核清单版本，版本漂移即中止，不跨版本拼接。每一项在账本中留有原始响应、最终地址、授权范围、内容摘要与清单版本；资源撤回或暂时无权时保留已取原文与缺口说明，闭包不宣称整件封存。重试幂等，投影可核查重复与串版，`fetch.replay_client` 可由账本证据重建冻结会话回放核对。

Python 3.11 以上，无第三方依赖。样例均为合成，不访问真实站点。

```sh
python -m unittest discover -s tests -v
python -m compileall -q capture_data tests
python -m capture_data.cli inspect data/normal.json
python -m capture_data.cli walk data/walk-case.json
python -m capture_data.cli walk data/walk-gap-case.json gap.sqlite3
python -m capture_data.cli closure gap.sqlite3 exhibit-13
```

Dockerfile 提供 Linux 运行环境；`docker build -t source-capture .` 构建，`docker run --rm source-capture python -m unittest discover -s tests -v` 运行测试。

协议样例可通过 python -m capture_data.cli inspect data/protocol-case.json 查看。该命令仍只说明现有接收能力，不证明远端连续性。walk 样例 data/walk-case.json 覆盖重定向、相对地址、跨域资源与分页；data/walk-gap-case.json 演示撤回与无权缺口。
