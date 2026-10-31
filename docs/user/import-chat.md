# 导入旧聊天记录（文本 Alpha）

当前源码提供文本导入命令，支持逐步联合估计、断点恢复和独立角色发布。先用少量副本确认结果；导入总会创建一个新身份。旧 alpha.492 Release 尚不含此入口。

## 准备角色与文本文件

先按 [创建角色教程](characters.md) 准备目标角色的人设和API配置，记下该角色的 `config.json` 路径。已有配置也可以使用；有效的完整表达资料会保留为新角色的人设约束。原角色的配置、身份和记忆不会覆盖。模型和动作素材继续沿用现有配置与动画。

首版接受UTF-8 JSON：一个消息数组，或只有 `messages` 字段的对象。每项包含 `role` 和字符串 `content`；可选唯一字符串 `id`、Unix毫秒整数 `timestamp`。`user` 是聊天中的用户，`assistant` 是要重建的角色自己。可复制 [合成样例](../../examples/chat-import.example.json)，例如：

```json
{
  "messages": [
    {"id": "m1", "role": "user", "content": "备用钥匙放在蓝盒子里了。"},
    {"id": "m2", "role": "assistant", "content": "好，我知道你放在蓝盒子里了。"}
  ]
}
```

这不是任意平台导出文件的自动识别器。ChatGPT等平台若带树形分支、系统消息、工具或多媒体项，需要先由你或Agent转换成选定会话的这份格式，明确映射双方身份。工具结果不能自动当成角色真的执行过工具；只有文字动作描写也不代表在本机执行过动画。转换时保留原始文件用于核对，不编造遗漏内容。

## 四步操作

在当前源码的解压目录打开PowerShell，用安装桌宠的同一Python。下面均使用 `.venv/Scripts/python.exe`，替换自己的文件路径：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m petlab_app.history_import_tool prepare --source "chat.json" --config "目标角色配置.json" --work "output/my-import" --time-mode order
.\.venv\Scripts\python.exe -B -X utf8 -m petlab_app.history_import_tool run "output/my-import" --max-records 1
.\.venv\Scripts\python.exe -B -X utf8 -m petlab_app.history_import_tool status "output/my-import"
```

`prepare` 只做本地验证、新身份和工作区准备，不请求模型。工作目录必须是新的。它显示待处理记录数与估计请求量，通常为一次背景量测，加每条记录的初始量测和联合修订两次请求。`run --max-records 1` 最多推进一条记录；确认结果后可按自己的费用预算增大数值。不要把费用预估理解成模型一定一次返回合法格式。

时间模式必须显式选择：

- `--time-mode original`：全部消息具备真实Unix毫秒时间且顺序正确时，沿原时间进入重建。未来时间或缺失项会报错；很久以前的记忆在后续互动中仍按原机制衰减。
- `--time-mode order`：仅保留来源顺序，使用准备工作区时冻结的、每条相隔1毫秒的重建坐标。原时间仍单独保留，缺失仍为缺失；这些坐标不是原发生时间，也不是测得了当时的真实情绪。时间衰减结果因而是这份相对重建的结果。

运行会把完整人设和必要旧聊天原文发送给配置的模型提供方。API密钥仍走原本的环境变量/本机安全保存机制，工作区和报告不应公开分享。开始前确认人设确实属于要重建的角色。

## 中断与继续

已完成的步骤写入工作区；下次执行同一 `run` 命令会从检查点继续。已成功返回的模型回执会校验后复用，不重复请求。工作区绑定原配置、时间策略和来源，不能中途改成另一份人设或数据。

请求已发送却没有完整回执时，工具会停下并保留现场；不会因为没看到结果就自动再次付费。确认失败或格式不合法也会停止，不会自动重试。保留工作区与 `providers` 下的原返回，修复/核对后再恢复；不要删掉调用记录绕过此检查。

`status` 显示 `estimates_complete` 时，仍是可修订重建估计，尚非正式记忆。联合修订可以同时调整五模板权重和当前量测，但须有可定位原文依据；完整人设作为约束，七常量由原公式生成。

## 发布成可运行的独立角色

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m petlab_app.history_import_tool publish "output/my-import" --output "output/imported-character"
```

发布冻结最终参数估计，以原正式记忆事务重建一次数值链；不会重新请求模型、生成旧回复或播放旧动作。估计过程中的每步修订仍保留。大批量发布也可用 `--max-records` 分段，重复同一路径的发布命令继续；不是每次从头重放整段历史。

发布结果包含新角色的配置与独立记忆路径。沿原单宠启动入口加载：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m petlab_app --single --setup --config "output/imported-character/config.json" --session "output/imported-character/session.json" --traces "output/imported-character/traces"
```

在设置中核对API并保存启动。启动前后都保留 `session.json` 及其同目录增量文件，不能只复制其中一个。后续记忆检索、数值召回、对状态的影响、Writer卡包交付与实际采用各走原通路；文件发布成功不保证每条记忆都会在任何问题里被提起。

真实 Flash 小样已验证：导入用户告知及角色确认两条旧话，分段发布后，新聊天正确回答钥匙位置；正式召回、卡包交付和表达采用均有回执。最终干净环境另以一条旧话完成6次真实请求，从当前合同导入到正式窗口聊天和冷恢复通过。该小样未修改角色权重；有证据修订权重与当前量测、下一条承接新估计另有离线回归。它不证明所有模型和长对话都会正确理解。

首版提供命令入口，尚无平台自动转换或图形导入向导。对于聊天中描写的动作，只保留“当时说过”的事实。导入是基于原文的重建估计，不等同于恢复原平台的隐藏状态。

## 常见情况

- `estimates_complete`：估计已完成，执行 `publish` 才生成正式历史。
- `published`：可直接按结果中的路径启动。重复发布到同一路径不会追加同一批历史。
- `reserved ...` / `recorded failure`：已保留实际调用，不会静默重发。保存工作区和原返回给维护者定位；不要删除回执或反复新建任务规避错误。
- 属性/修订证据不合法：检查源角色映射、人设和模型原返回。证据必须对应当前原文或明确指明的修订来源，不能自动补造。
- 启动后没提起某条旧话：先区分候选命中、正式召回、Writer交付和采用。历史时间越久可能自然衰减；不要改时间冒充“刚刚发生”来保证命中。

工作区用于保留原文、估计和调用来源；发布角色目录用于后续运行。两者都包含私有资料，请放在本机，不提交到代码仓库。
