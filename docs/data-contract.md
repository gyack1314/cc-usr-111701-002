# iiif-presentation 来源契约

来源清单中的资产长度与 SHA-256 用于核对本地原文字节。文档的 collection 与 protocol 必须匹配清单，现有记录类型包括 manifest, canvas, annotation, resource。每条记录以 kind、id、version 定位，refs 指向同集合的其他记录，payload 原样留存。来源版本冲突会中断接收；本地引用闭合只表示当前收到的引用能定位，不表示远端序列或授权资料已经完整。

## 已接收的协议资料
现有入口识别 IIIF Presentation 3 的 Manifest、Canvas、AnnotationPage 和 Annotation 嵌套身份，并保存 painting 资源的 target 与 body。外部引用页、授权和版本闭包仍由业务扩展。


同集合第二次来源接收保存在 data/conflict-case.json。冲突事务必须保持第一份收据和旧投影。

## 清单走查与版本闭包

walk 计划（kind 为 walk-plan）给出 collection、manifest_url、tokens 与冻结 session。走查器从 Manifest 走到 Canvas、AnnotationPage、Annotation 与资源字节：

- 相对地址按各自来源文档的最终地址解释；重定向逐跳记录进证据 chain，跳转后相对地址以新最终地址为基准；AnnotationPage 的 next 分页沿各自页地址续抓，循环即报错。
- 令牌按 TokenStore 登记的起源边界发送，跨域或匿名地址不携带任何令牌；401 时先刷新令牌、复核清单版本再原址重试一次，版本漂移抛 VersionDrift，不得悄悄拼出跨版本资料。
- evidence 表封存每次原始响应（状态、头、正文、摘要、逐跳链、授权范围）；items 表按 (kind, id, manifest_version) 定位每项的内容摘要、最终地址与授权范围，同身份不同内容即版本冲突；closures 表登记每次闭包是否完整及缺口清单。
- 资源撤回（404/410）或暂时无权（401/403）记为 gap 项，原始响应照常封存，闭包 complete 为假，发布判定不得宣称整件封存。
- 重试幂等：同版本同内容不重复入账；closure_view 的 verify 列出重复与串版（同一项出现在多个清单版本），fetch.replay_client 可由证据重建冻结会话回放核对。
