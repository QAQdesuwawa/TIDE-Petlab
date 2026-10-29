# TIDE PetLab · 交互框架与双桌宠示例

Windows 双桌宠技术 Alpha，基于 TIDE 框架。新安装默认启动两只鲸鱼娘：**大肥鱼、小肥鱼**，共用素材与 API 配置，各有独立身份、关系和空白记忆，不包含开发者的聊天记录。

仓库同时提供通用 Runtime、模型包与时间包接口。桌宠是可运行的展示面；你可以只复用框架，接自己的模型、输入、前端或算法，不必保留鲸鱼娘、Windows窗口或六七模型包。

## 给开发者

从[开发者入口](docs/developer/README.md)开始：运行独立Runtime、接已有模型调用、接已有播放器，再按需要[开发自己的包](docs/developer/packages.md)或[扩展桌宠](docs/developer/desktop.md)。三个离线示例无需API、模型下载或桌面窗口：

```powershell
python -B -m examples.runtime_minimal
python -B -m examples.existing_provider
python -B -m examples.existing_player
python -B -m unittest examples.test_examples
```

新环境先按开发者入口安装源码依赖。想直接体验双桌宠，按下面的快速启动操作即可。

## 快速启动

1. Windows 10/11，先安装 **64位 Python 3.11（包含 Tcl/Tk）**。
2. 解压当前源码或克隆仓库到较短的可写目录（例如 `D:\PetLab`），双击 **START.cmd**。首次安装Python依赖。
   如果下载的是源码包或克隆仓库，首次START会自动下载固定版本的本地模型与CPU运行时（约1.3 GB），可断点续下；后续启动复用本机文件。
3. 在设置窗口的 **模型** 页，将自己的密钥粘贴到 **API 密钥（填在这里）**，点击“保存并启动”。新配置默认模型为 `deepseek-flash`；**表达模型留空**，与量测共用 Flash。

其他兼容 Chat Completions 的提供方可修改完整接口地址和模型名称；“表达模型”可留空，沿用上方模型。量测与表达可以共享同一个提供方、同一份API密钥。接口需要支持结构化JSON输出。

首版暂不开放自定义角色创建、背景生成和角色参数编辑。用户无需创建角色或重新量测人设。框架中的扩展接口保留，后续再开放相关产品入口。

右键角色可选“对当前角色说（公开）”“同时对两只说（公开）”或私聊。只对一只说时，另一只是旁听者；它收到对方实际输出后，再根据自己的状态决定是否回应。点击、拖拽有本地反馈，网络等待不控制拖拽与动画。关闭后再次运行START.cmd会恢复这两只的记忆。

旧单宠启动记录继续恢复原角色，不会自动变成两只；旧 DS1/DS2 双宠也保留原名称和身份。要另开默认双宠，可运行 `.venv/Scripts/python.exe -m petlab_app --dual --setup` 并选择新的双宠存档目录。原记录保持原样。

API密钥默认仅本次使用；可勾选“在本机安全保存密钥”，由Windows按当前用户加密。密钥不写入角色配置JSON。不要上传output目录、配置或截图里的密钥。运行数据默认保存在output目录，备份范围见[隐私说明](PRIVACY.md)。

## 阅读网页

右键角色选择“阅读网页…”，粘贴公开网页链接。默认 DeepSeek 官方接口复用同一份 API 密钥，调用其既有 Fetch 工具；其他提供方需配置兼容工具适配器。读取会产生提供方费用，不包含发表评论。

读取成功后，网页来源作为外部观察进入正常反应和记忆通路，后续聊天可按现有检索规则候选召回。单次正文最多4096字符，较长页面明确标为只读开头，其余未读。网页原件与回执保存在存档旁的 `.resources` 目录，备份时一起保存。读取失败不会记成已经读过；中断可沿原“继续未完成的互动”恢复。

本地检索仍有500毫秒预算；忙碌或超时会记录实际状态，当前回复不保证带回网页。旧alpha.367下载包没有该入口。

## 安装与恢复

这是Python源码发行版，尚不是免安装EXE。依赖和虚拟环境放在安装目录，不要求管理员权限。安装后可运行`.venv/Scripts/python.exe -B check_install.py`离线检查文件与素材。

需要填写或修改API时双击 **SETUP.cmd**。旧包没有这个文件时，在解压目录打开PowerShell运行 `powershell -ExecutionPolicy Bypass -File .\quickstart.ps1 -Setup`；旧界面的密钥框叫“本次运行密钥”。若旧配置的“表达模型”为Pro，清空该栏即可沿用上方Flash。“加载配置”可恢复自己的原配置，不会自动用预置模板覆盖已有角色。

可选登录自启：运行`powershell -ExecutionPolicy Bypass -File configure_startup.ps1 -Enable`，用`-Disable`撤销。先完成一次安装和设置，移动安装目录后需重新设置。真正Windows重新登录仍待用户反馈。

## 当前限制

- 生成回复和记忆效果随模型变化，可能偶有重复或归属不准确。
- 大存档首次恢复可能超过一分钟；恢复时间随历史和机器变化。
- 默认本地RAG使用随包Qwen3-Embedding 0.6B Q8＋Qwen3-Reranker 0.6B Q8，CPU运行，无需第二份API密钥或GPU。文字检索与语义量测并行；鼠标/拖拽继续原有快速反馈和数值召回通路。
- RAG检索正文与原数值召回分别留档；相似度不会直接改变情绪、衰减或Tick。检索超出时间预算、资源缺失或失败会留下状态，当前回复沿原通路继续；Alpha不保证每次召回成功。
- 当前只提供双宠；更多角色、外部聊天历史导入和自定义角色创建暂不开放。

这些是Alpha已知限制，见[验收范围](ALPHA_ACCEPTANCE.md)。版本见[VERSION](VERSION)。

## 框架与包

framework提供通用事务、历史和生命周期；model_packages与product_adapters负责定义、量测、符号选择和表达适配；petlab_app负责桌面输入、动画及执行回执。动作、表情和可选讲话是同一父反应下的独立子项。UI反馈不改变根状态；符号层提供合法动作候选，表达模型可在该集合内组织动作顺序，执行器记录实际发生的部分。

## 隐私与许可

文字、交互摘要及选中的角色/记忆材料会发送到配置的模型提供方，并产生其API费用。运行存档与回执属于私人数据。源码许可证为[AGPL-3.0](LICENSE)；模型权重为Apache-2.0，llama.cpp为MIT，原许可证与来源随包保留，见[本地模型说明](LOCAL_MODELS.md)；第三方图像不因源码开源自动获得相同许可，素材来源与许可核实情况见[ASSET_SOURCES.md](ASSET_SOURCES.md)。
