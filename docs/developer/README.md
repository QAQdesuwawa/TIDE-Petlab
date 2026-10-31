# 开发者入口：把你已有的东西接入 TIDE

这个仓库提供通用交互 Runtime、可替换的模型/时间包，以及 PetLab 双桌宠产品。你可以只使用前两部分，也可以继续改桌宠。鲸鱼娘、六七量和 Windows 窗口都不是通用 Runtime 的必选项。

让代码 Agent 协助接入时，先读仓库根目录 [AGENTS.md](../../AGENTS.md)：其中有旧聊天量测、分层属性与来源保留、人设转包内常量的实际入口，以及可直接运行的离线合同样例。

## 先选与你的项目最近的一条路

想直接创建自己的包，可从 [可安装开发工具](devkit.md) 开始：`tide-dev new/check/run` 共用现有 Runtime 接口，提供生成、正式结算检查与恢复；不需要先启动桌宠。

| 你已经有什么 | 从哪里开始 | 通常修改哪里 |
| --- | --- | --- |
| 聊天机器人、Agent、游戏角色 | [运行独立 Runtime](runtime.md) | 在自己的宿主中装配包、提交事件、消费结果 |
| 自己的模型 SDK、分类器、语义量测 | [接入已有模块](integrations.md) | 在适配器中映射请求、返回值和错误 |
| 自己的状态模型、调度或记忆定义 | [开发模型包](packages.md) | `model_packages`、相应适配器；沿通用 Runtime 合同 |
| 动画、立绘、桌宠玩法、输入设备 | [扩展桌宠](desktop.md) | 桌宠包声明、宿主执行器与素材 |
| 先了解协作方式 | [贡献指南](../../CONTRIBUTING.md) | 最小复现、版本兼容与验证说明 |

## 十分钟离线跑通

在仓库根目录使用 Python 3.11。已有环境可直接执行下面三个示例；新环境先安装仓库锁定依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-pet.lock.txt
.\.venv\Scripts\python.exe -B -m examples.runtime_minimal
.\.venv\Scripts\python.exe -B -m examples.existing_provider
.\.venv\Scripts\python.exe -B -m examples.existing_player
.\.venv\Scripts\python.exe -B -m unittest examples.test_examples
```

这几个示例不调用真实 API，不下载 embedding 模型，不启动 Tk 窗口，也不需要旧配置或聊天记录。验证环境为 Windows/Python 3.11；独立示例按普通 Python 接口编写，其他平台请在自己的环境验证。开发示例不需要先运行 `START.cmd`；该文件是桌宠产品启动入口，会准备桌宠的本地模型。

预期能看到：

- `runtime_minimal`：独立小包的三个状态量变化、正式结算回执、序列化后恢复状态一致；未导入六七模型包或桌面程序。
- `existing_provider`：已有函数只被调用一次，返回数据经 Provider 和包的校验进入正式结算。
- `existing_player`：已有播放器的完整播放、部分播放和播放前取消分别得到对应结果。这里用内存帧代替真实动画，不宣称已经显示桌宠或写入记忆。

## 一次事件怎样经过系统

```text
你的输入/工具/界面产生实际事实
  → 领域适配器形成该包的量测输入
  → Runtime 调用包阶段，接受状态结算
  → 包给出投影、资格和合法候选
  → 按需交给语言实现器，或直接做本地表现
  → 宿主执行已经选择的结果
  → 实际完成/部分完成/打断回执回到对应产品历史
```

Runtime 不从一句文本猜出所有领域的量测，也不负责播放任意动画。包定义数据与含义，宿主负责外部事实，适配器连接你现有的实现。示例只覆盖它明确演示的步骤；完整记忆和多角色投递需选用具备相应能力的包与产品宿主。

## 当前提供方式

可以从源码导入，也可以安装仓库内的 [开发套件](devkit.md) 使用统一命令；尚未发布到 PyPI，没有桌面“安装任意包”按钮。通用装配入口由调用者显式提供模型包、时间包和 Runtime 变体。桌宠 GUI 的创建角色功能仍未开放，这不影响你在自己的 Python 宿主中开发包。

文档描述同目录源码；下载发行包时查看根目录 [VERSION](../../VERSION) 和 [CHANGELOG](../../CHANGELOG.md)。新包先开新会话；旧存档绑定原包/参数版本，升级兼容由适配器明确处理。
