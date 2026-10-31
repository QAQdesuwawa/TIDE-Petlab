# 安装开发工具、创建包并运行

[返回开发者入口](README.md)

开发工具只有一个命令 `tide-dev`，提供 `new`、`check`、`run`。它生成普通 Python 模型包，调用已有 `compose_runtime_packages`、注册、正式事务和恢复接口；没有新增领域协议，也不启动桌宠或模型服务。

## 安装

Python 3.11 或更新版本，在源码仓库根目录：

```powershell
python -m venv .venv-dev
.\.venv-dev\Scripts\python.exe -m pip install ./devkit
.\.venv-dev\Scripts\tide-dev.exe --help
```

不需要激活虚拟环境，也不需要修改 PowerShell 执行策略。为避免后续命令误用系统 Python，安装后在同一个 PowerShell 窗口设定：

```powershell
$devPython = (Resolve-Path .\.venv-dev\Scripts\python.exe).Path
```

建议使用独立虚拟环境。SDK 保留现有 `framework`、`ops` 等 Python 导入名称；避免与其他项目同名模块混装。Linux/macOS 使用虚拟环境 `bin/` 下的命令；本次实际验收平台为 Windows/Python 3.11。

也可以先制作 wheel，再拷贝到无网络的新环境安装。源码构建需要 setuptools 和 wheel，首次准备构建环境可能联网下载这些构建工具；安装已生成的 wheel 没有额外运行依赖，不会下载语言模型或 embedding：

```powershell
python -m pip wheel ./devkit --no-deps --wheel-dir dist
python -m pip install --no-index --find-links dist tide-runtime-devkit
```

该分发名为 `tide-runtime-devkit`，当前工具版本 `0.1.0a2`，与 PetLab 产品版本独立。尚未发布到 PyPI；不要直接执行不带本地源码路径或 wheel 的在线安装命令。SDK 是现有最小正式宿主的依赖闭包，不是整个 PetLab 产品发行包。下载源码 ZIP 后先解压，在包含 `AGENTS.md` 和 `devkit` 的目录安装；旧的 alpha.492 Release 压缩包不含这批开发工具，应下载当前仓库源码。

## 创建与检查

```powershell
& $devPython -m tide_dev new my_model --output ./my-first-package
& $devPython -m tide_dev check ./my-first-package
& $devPython -m tide_dev run ./my-first-package --output ./first-run.json
& $devPython -m tide_dev run ./my-first-package --restore ./first-run.json
```

没有把虚拟环境加入 PATH 时，使用安装环境中的完整命令路径。`python -m tide_dev` 与 `tide-dev` 使用同一个解析器。

生成内容：

| 文件 | 修改什么 |
| --- | --- |
| `tide.toml` | 显式模型/时间包工厂、工厂参数、会话与对象身份、场景文件位置 |
| `my_model/definition.py` | 包ID/版本、状态与参数字段、量测字段、事件公式与行动资格 |
| `my_model/package.py` | 原模型 API v2 阶段、schema、指纹与贡献/结算候选封装；需要更换值域或时间演化时修改这里 |
| `scenario.json` | 初态、参数和按时间排序的事件量测夹具 |
| `AGENTS.md` | 给协作 Agent 的本包修改与检查入口 |

目的目录必须不存在，已有目录与输出文件不会覆盖。名字需是小写 Python 标识符，且不能与标准库、SDK模块或设备名重名（如 `json`、`framework`、`con`）；优先使用业务名。项目内 `tide.toml` 是开发宿主配置，不是新的 Runtime 包 manifest；包仍声明原 `InteractionModelManifest`。

## 已经有自己的包时

不必复制生成的业务公式。生成器先给你一个宿主配置骨架，再将 `tide.toml` 的 `model` 改成已有工厂；例如用 SDK 自带的另一个包实现验证替换：

```toml
model = "model_packages.minimal_affinity_v1:MinimalAffinityV1Package"
temporal = "temporal_packages.wall_clock_ms_v1:WallClockMillisecondsV1"
scenario = "scenario.json"
session_id = "dev:session"
scope_ref = "dev:scope"
subject_ref = "dev:agent"
[model_kwargs]
observation_unit = "milliseconds"
```

此包与默认场景字段相同，因此可直接运行上面的 check/run。换成你自己的 `my_company_package:YourPackage` 时，把模块放在包含 `tide.toml` 的项目目录下，或安装到 `$devPython` 对应环境；将 `model_kwargs` 改为该工厂实际接受的参数（不接受参数就删掉整个表），同时按它的 schema 修改场景。`temporal_kwargs` 也沿同样规则传给时间包工厂。包实现不了 `advance_progress` 时，此检查宿主会报告缺能力；这是该宿主的范围，不意味着通用 Runtime 要求所有产品采用墙钟或时间演化。

现有算法可以保留在自己的模块内，由 `definition.py` 或标准包阶段调用。已有算法只有一个计算函数时，需用标准阶段封装其输入/输出；它尚不等于一个完整模型包。源代码接入更大产品宿主，仍使用 [Runtime 装配入口](runtime.md)，不要求绑定这份开发场景配置。

## 改一次定义

初始样例沿用最小 affinity 包公式：`affinity = clamp(before + responsiveness * affinity_stimulus)`。将 `definition.py` 中这一项改为：

```python
"affinity": clamp(before["affinity"] + response * a / 2),
```

再运行 `tide-dev check`，默认场景的 affinity 从原来的 `0.85` 变为 `0.625`。这只是你的新包业务定义，不修改现有示例包或六七包。改字段名时同时修改 `PARAMETER_NAMES`、`STATE_NAMES`、`MEASUREMENT_NAMES`、相应公式/资格函数和场景字段；schema 从这些名字生成。可以加减状态轴，生成包的阶段校验按新字段运行，不把三个样例轴写入 Kernel。

模板只演示有界数值模型：参数和状态 `[0,1]`，量测 `[-1,1]`，时间推进保持状态，没有完整记忆和语言实现器。需要别的范围、投影或时间动力学时编辑同一个普通包的标准阶段；不另造一套宿主接口。使用自己的包时将 `tide.toml` 的 `model = "module:factory"` 指向可导入工厂，声明并实现本宿主需要的基本能力与 `advance_progress`；时间包也同样显式选择。

## 检查到底做了什么

`check` 与 `run` 共用一次执行通路：包注册/能力绑定→初始化→每条事件的正式时间推进→量测/贡献/结算→Kernel 接受→共享投影→行动资格→JSON 序列化与正式恢复。`run` 的报告保留实际事件、进度和接受回执，以及投影/资格的来源回执；`check` 打印摘要。

检查依照包的验证代码及原 Runtime 合同，不替作者证明业务公式正确，也不以 `eligible=true` 冒称执行了外部动作。所有场景输入都是调用者声明的开发夹具，不是从文本推断出的真实心理值；没有 API 调用、GUI、实际动作执行、完整 History 或 RAG 验收。完整产品接入仍见 [适配器指南](integrations.md)。

错误输出带阶段，例如 `registration`、`initialization`、`progress:<事件>`、`event:<事件>`、`shared_projection`、`assess_action` 或 `restore`，退出码为1。缺能力、无效字段/值域、倒退时间、重复事件ID和不兼容恢复应先修来源/定义，再重新运行；不要用重试或静默删字段掩盖错误。

修改定义含义时更新 `PACKAGE_VERSION`；模板还将定义文件 SHA256 纳入原 manifest 的 parameter_package，使误用旧定义的恢复被原绑定校验拒绝。`--restore` 只恢复并核对保存的状态，不重播场景或外部动作。不兼容历史用新会话或显式迁移，不修改已接受的回执。

`check` 会导入并运行项目的 Python 代码：它是开发验证工具，不是安全沙箱。只执行你信任的包。报告可能含你自己的场景数据，默认只写终端，只有显式 `--output` 才保存；真实隐私资料和报告不提交到公共仓库。

## 回归命令

用已安装该 wheel 的 Python，在任意工作目录执行（替换路径）：

```powershell
& $devPython -B -m unittest discover -s .\devkit\tests -v
```

用例在临时独立目录中建包，验证字段更名、公式变更、多事件、正式恢复以及错误拒绝。实际验证应另检查 wheel 内容和导入位置，避免误从源码目录导入。公共 Agent 接入说明见 [根目录 AGENTS.md](../../AGENTS.md)。
