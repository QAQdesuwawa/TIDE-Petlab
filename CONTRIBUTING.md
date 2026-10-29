# 参与 TIDE / PetLab 开发

仓库包含TIDE通用Runtime/包接口和Windows双桌宠技术Alpha。桌宠是一个产品实例；欢迎接已有模块、开发自己的包、替换前端，或修复当前桌宠。

先读[开发者入口](docs/developer/README.md)，再选[独立Runtime](docs/developer/runtime.md)、[已有模块适配](docs/developer/integrations.md)、[包开发](docs/developer/packages.md)或[桌宠扩展](docs/developer/desktop.md)。这些链接按发行根目录CONTRIBUTING.md的位置解释。

安装requirements-pet.lock.txt后，可以先跑无需API、模型或窗口的公开示例：

```
python -B -m examples.runtime_minimal
python -B -m examples.existing_provider
python -B -m examples.existing_player
python -B -m unittest examples.test_examples
```

验证桌宠配置/素材可另运行 `python -B -m petlab_app --validate --config config.example.json`，不发送模型请求。完整内部实验测试集不随源码包分发，公开示例和离线用例覆盖的是入门通路，不等于所有产品和真实模型效果通过。

framework负责通用合同和正式接受；model_packages定义领域状态/量测/数学和投影；ops提供Runtime装配与事务宿主；product_adapters连接领域资料和实现器；petlab_app负责桌面输入、播放、真实回执与多角色产品接线。扩展动作、表情和可选讲话复用同一父反应。符号层约束合法候选，表达模型可在明确交付的合法集合内选择和编排，不修改根状态或创造行动权限。

新增功能优先接已有合同，给出来源、对象、可见性和真实结果。自己的包可以有自己的状态与数学；改当前参考包或公共协议时注明版本及旧档兼容。新UI/工具不用把像素、坐标或设备采样写成心理根量，输出实现器也不直接写状态。

提交修复时给出版本、最小复现、预期与实际结果、验证方法；涉及时间、记忆资格或数值定义时明确兼容范围和来源。不以降低门限、增加轮询或预设台词掩盖错误。报告API故障时隐藏密钥、对话和私有路径。请勿提交output、dotenv、用户配置、存档或未获授权的聊天数据。

软件源码许可证见LICENSE；角色形象、第三方资源的来源与授权独立于源码许可证。新增素材请同时说明来源和允许的使用范围。
